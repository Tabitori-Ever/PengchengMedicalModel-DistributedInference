"""
Clinic Pod Service (v2.0).

Each clinic pod exposes a single task: query the memory usage of a pod.

* Default (target_pod omitted / empty / "self") -> reports this clinic pod's
  own memory usage, read directly from the container cgroup (always works).
* Explicit target_pod          -> reports that pod's memory usage via the
  Kubernetes metrics-server API (requires the cluster RBAC granted by
  k8s/clinic-rbac.yaml).

Response:
  {pod, namespace, usage_bytes, limit_bytes|null, usage_percent|null,
   containers:[{name, usage_bytes, limit_bytes|null}], measured_at, source}

usage_percent = sum(container usage) / sum(container memory limit).
If a pod has no memory limits, usage_percent is null and absolute bytes are
returned instead.
"""
import json
import os
import ssl
import time
import urllib.request
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel
from prometheus_client import Counter, Histogram, Gauge, generate_latest
from typing import List, Optional

CLINIC_NAME = os.environ.get("CLINIC_NAME", "clinic-1")
NODE_NAME = os.environ.get("NODE_NAME", "unknown")
POD_NAME = os.environ.get("POD_NAME", "")
NAMESPACE = os.environ.get("NAMESPACE", "default")

# In-cluster service account (mounted by Kubernetes automatically)
SA_TOKEN_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/token"
SA_CA_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
KUBE_HOST = "https://kubernetes.default.svc"

app = FastAPI(title=f"Clinic Pod ({CLINIC_NAME}) - Memory Monitor")

# Prometheus
queries_total = Counter("clinic_mem_queries_total", "Total memory query requests",
                        ["target"])
self_usage_gauge = Gauge("clinic_self_memory_usage_percent",
                         "This clinic pod memory usage percent (0-100)",
                         ["pod"])
query_latency = Histogram("clinic_mem_query_latency_seconds",
                          "Memory query latency")


# ============================== cgroup helpers ================================
def _read_cgroup_file(path: str) -> Optional[int]:
    try:
        with open(path, "r") as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return None


def _is_cgroup_v2() -> bool:
    return os.path.exists("/sys/fs/cgroup/cgroup.controllers")


def read_own_memory() -> dict:
    """Read this container's memory usage/limit from cgroup."""
    if _is_cgroup_v2():
        usage = _read_cgroup_file("/sys/fs/cgroup/memory.current")
        limit = _read_cgroup_file("/sys/fs/cgroup/memory.max")
    else:
        usage = _read_cgroup_file(
            "/sys/fs/cgroup/memory/memory.usage_in_bytes")
        limit = _read_cgroup_file(
            "/sys/fs/cgroup/memory/memory.limit_in_bytes")

    # cgroup v2 "max" (or v1 "limit_in_bytes" with no limit) -> huge number
    NO_LIMIT = 1 << 62
    if limit is not None and limit >= NO_LIMIT:
        limit = None

    percent = None
    if usage is not None and limit:
        percent = round(usage / limit * 100, 2)

    return {
        "usage_bytes": usage,
        "limit_bytes": limit,
        "usage_percent": percent,
    }


# ========================== metrics-server helpers ============================
def _parse_mem(value: str) -> Optional[int]:
    """Parse a Kubernetes memory quantity ('478Mi', '1Gi', '1234', …) to bytes."""
    if value is None:
        return None
    v = str(value).strip()
    if not v:
        return None
    mult = 1
    if v.endswith("Ki"):
        mult = 1024; v = v[:-2]
    elif v.endswith("Mi"):
        mult = 1024 ** 2; v = v[:-2]
    elif v.endswith("Gi"):
        mult = 1024 ** 3; v = v[:-2]
    elif v.endswith("Ti"):
        mult = 1024 ** 4; v = v[:-2]
    elif v.endswith("K"):
        mult = 1000; v = v[:-1]
    elif v.endswith("M"):
        mult = 1000 ** 2; v = v[:-1]
    elif v.endswith("G"):
        mult = 1000 ** 3; v = v[:-1]
    try:
        return int(float(v) * mult)
    except ValueError:
        try:
            return int(v)
        except ValueError:
            return None


def _kube_request(path: str) -> dict:
    """GET a path on the kube-apiserver with the pod service-account token."""
    if not os.path.exists(SA_TOKEN_PATH):
        raise HTTPException(
            status_code=503,
            detail="Not running inside a cluster (no service-account token); "
                   "only self memory query is available.")

    with open(SA_TOKEN_PATH, "r") as f:
        token = f.read().strip()

    ctx = ssl.create_default_context()
    if os.path.exists(SA_CA_PATH):
        ctx.load_verify_locations(SA_CA_PATH)
    else:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

    req = urllib.request.Request(KUBE_HOST + path)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/json")

    try:
        with urllib.request.urlopen(req, context=ctx, timeout=5) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:300]
        raise HTTPException(
            status_code=503,
            detail=f"Kube API {e.code} for {path}: {body}")
    except Exception as e:
        raise HTTPException(
            status_code=503,
            detail=f"Kube API unreachable for {path}: {str(e)[:200]}")


def query_pod_memory(target_pod: str, namespace: str) -> dict:
    """Query a specific pod's memory via metrics-server + pod spec."""
    pod = _kube_request(f"/api/v1/namespaces/{namespace}/pods/{target_pod}")
    metrics = _kube_request(
        f"/apis/metrics.k8s.io/v1beta1/namespaces/{namespace}/pods/{target_pod}")

    # map container name -> memory limit (from pod spec)
    limits = {}
    for c in pod.get("spec", {}).get("containers", []):
        res = c.get("resources", {})
        lim = (res.get("limits") or {}).get("memory")
        parsed = _parse_mem(lim) if lim else None
        if parsed:
            limits[c["name"]] = parsed

    containers = []
    total_usage = 0
    total_limit = 0
    for cm in metrics.get("containers", []):
        usage = _parse_mem(cm.get("usage", {}).get("memory", "0")) or 0
        lim = limits.get(cm.get("name"), None)
        total_usage += usage
        if lim:
            total_limit += lim
        containers.append({
            "name": cm.get("name"),
            "usage_bytes": usage,
            "limit_bytes": lim,
        })

    percent = None
    if total_limit > 0:
        percent = round(total_usage / total_limit * 100, 2)

    return {
        "pod": target_pod,
        "namespace": namespace,
        "usage_bytes": total_usage,
        "limit_bytes": total_limit or None,
        "usage_percent": percent,
        "containers": containers,
        "node": pod.get("spec", {}).get("nodeName"),
    }


# ============================== API ==========================================
class MemoryQueryRequest(BaseModel):
    target_pod: Optional[str] = None
    namespace: str = "default"


@app.get("/")
def root():
    return {
        "service": "clinic",
        "clinic": CLINIC_NAME,
        "node": NODE_NAME,
        "pod": POD_NAME,
        "status": "running",
        "tasks": ["pod-memory-usage"],
    }


@app.get("/health")
def health_check():
    return {
        "service": "clinic",
        "clinic": CLINIC_NAME,
        "node": NODE_NAME,
        "pod": POD_NAME,
        "status": "ok",
    }


@app.post("/query/mem")
def query_memory(req: MemoryQueryRequest):
    """Query memory usage of a pod. Default: this clinic pod (self)."""
    target = (req.target_pod or "").strip()
    namespace = (req.namespace or "default").strip()

    start = time.perf_counter()
    is_self = (not target) or target in ("self", POD_NAME)

    try:
        if is_self:
            queries_total.labels(target="self").inc()
            own = read_own_memory()
            pod_name = POD_NAME or CLINIC_NAME
            if own.get("usage_percent") is not None:
                self_usage_gauge.labels(pod=pod_name).set(own["usage_percent"])
            return {
                "pod": pod_name,
                "namespace": NAMESPACE,
                "usage_bytes": own.get("usage_bytes"),
                "limit_bytes": own.get("limit_bytes"),
                "usage_percent": own.get("usage_percent"),
                "containers": [],
                "measured_at": datetime.now(timezone.utc).isoformat(),
                "source": "cgroup",
            }

        # explicit target pod
        queries_total.labels(target="other").inc()
        result = query_pod_memory(target, namespace)
        result["measured_at"] = datetime.now(timezone.utc).isoformat()
        result["source"] = "metrics-api"
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)[:300])
    finally:
        query_latency.observe(time.perf_counter() - start)


@app.get("/metrics")
def metrics():
    return Response(content=generate_latest(), media_type="text/plain")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8007)
