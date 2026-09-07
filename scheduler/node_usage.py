"""Node free-resource utility (v3.0).

Loads node CPU/memory usage from the Kubernetes metrics-server and computes
free-capacity scores. Used by the data-center scheduler to pick hospital
targets (diagnosis forwarding), compute partition workers and routine Job
nodes. Fast single aggregated API call; degrades to defaults when
metrics-server is unavailable.
"""
import json
import os
import ssl
import threading
import time
import urllib.request
from typing import Dict, Optional


def _parse_cpu_cores(value: str) -> Optional[float]:
    if not value:
        return None
    v = str(value).strip()
    mult = 1.0
    if v.endswith("n"):
        mult, v = 1e-9, v[:-1]
    elif v.endswith("u"):
        mult, v = 1e-6, v[:-1]
    elif v.endswith("m"):
        mult, v = 1e-3, v[:-1]
    try:
        return float(v) * mult
    except ValueError:
        return None


def _parse_mem_bytes(value: str) -> Optional[int]:
    if not value:
        return None
    v = str(value).strip()
    mult = 1
    for suf, m in (("Ki", 1024), ("Mi", 1024 ** 2), ("Gi", 1024 ** 3),
                   ("Ti", 1024 ** 4), ("K", 1000), ("M", 1000 ** 2),
                   ("G", 1000 ** 3), ("T", 1000 ** 4)):
        if v.endswith(suf):
            mult, v = m, v[:-len(suf)]
            break
    try:
        return int(float(v) * mult)
    except ValueError:
        return None


def _kube_api_get(path: str, timeout: float = 5.0):
    token_path = "/var/run/secrets/kubernetes.io/serviceaccount/token"
    ca_path = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
    if not os.path.exists(token_path):
        raise RuntimeError("not running in-cluster")
    with open(token_path) as f:
        token = f.read().strip()
    ctx = ssl.create_default_context()
    if os.path.exists(ca_path):
        ctx.load_verify_locations(ca_path)
    else:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request("https://kubernetes.default.svc" + path)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/json")
    with urllib.request.urlopen(req, context=ctx, timeout=timeout) as r:
        return json.loads(r.read().decode())


_lock = threading.Lock()
_cache: Dict = {}
_cache_ts = 0.0
_TTL = 3.0


def node_loads(force: bool = False) -> Dict[str, dict]:
    """{node: {cpu, memory (0..1 usage), free_score (0..1)}}."""
    global _cache_ts, _cache
    now = time.time()
    with _lock:
        if not force and _cache and now - _cache_ts < _TTL:
            return dict(_cache)
    out: Dict[str, dict] = {}
    try:
        usage_by_node = {}
        for item in _kube_api_get("/apis/metrics.k8s.io/v1beta1/nodes").get("items", []):
            u = item.get("usage", {})
            cpu = _parse_cpu_cores(u.get("cpu"))
            mem = _parse_mem_bytes(u.get("memory"))
            if cpu is not None and mem is not None:
                usage_by_node[item["metadata"]["name"]] = (cpu, mem)

        try:
            from kubernetes import client, config  # noqa
            try:
                config.load_incluster_config()
            except Exception:
                config.load_kube_config()
            for n in client.CoreV1Api().list_node().items:
                a = n.status.allocatable or {}
                name = n.metadata.name
                u = usage_by_node.get(name)
                if not u:
                    continue
                ca = _parse_cpu_cores(a.get("cpu"))
                ma = _parse_mem_bytes(a.get("memory"))
                cpu_u = max(0.0, min(1.0, (u[0] / ca) if ca else 0.0))
                mem_u = max(0.0, min(1.0, (u[1] / ma) if ma else 0.0))
                free = 0.5 * (1 - cpu_u) + 0.5 * (1 - mem_u)
                out[name] = {"cpu": round(cpu_u, 3), "memory": round(mem_u, 3),
                             "free": round(free, 3)}
        except Exception:
            pass
    except Exception:
        pass
    with _lock:
        _cache = out
        _cache_ts = time.time()
    return dict(out)


def free_edge_nodes() -> list:
    """Free-capacity ordering of edge nodes (used for forward/partition)."""
    loads = node_loads()
    return sorted(["node1", "node2"], key=lambda n: -loads.get(n, {}).get("free", 0.5))


def most_free_edge() -> str:
    edge = free_edge_nodes()
    return edge[0] if edge else "node1"
