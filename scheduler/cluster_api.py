"""
Cluster management API (/cluster/*) - v2.0 cluster editor backend.

Reads live cluster state (nodes, editable hospital/clinic deployments and
their pods) through the Kubernetes API, validates desired models against
the topology rules in cluster_config.py, and applies edits (patch/create/
delete deployments + services) with the scheduler's own service account
(RBAC granted by k8s/scheduler-cluster-rbac.yaml).

Endpoints:
    GET  /cluster/status   - live topology + editable entities + readonly pods
    GET  /cluster/default  - default (current version) cluster model
    POST /cluster/validate - dry-run validation of a desired model
    PUT  /cluster/apply    - apply a desired model (patch/create/delete)
    POST /cluster/reset    - apply the default model of the current version
    POST /cluster/restart  - rollout restart of one editable deployment
"""
import json
import os
import time
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Any, Dict, List, Optional

from . import cluster_config as cc
from .service_registry import register_clinic, unregister_clinic

router = APIRouter()

NAMESPACE = cc.NAMESPACE

# --------------------------------------------------------------------------- #
# Kubernetes client (lazy, cached).  Absent in pure-local dev => 503 on use.
# --------------------------------------------------------------------------- #
_KUBE_CLIENTS: Dict[str, Any] = {}
_KUBE_ERROR: Optional[str] = None


def _reset_kube_cache():
    """For tests: drop cached clients."""
    global _KUBE_CLIENTS, _KUBE_ERROR
    _KUBE_CLIENTS.clear()
    _KUBE_ERROR = None


def get_kube_clients() -> tuple:
    """Return (apps_api, core_api, error). Cache clients after first load."""
    global _KUBE_ERROR
    if "apps" in _KUBE_CLIENTS:
        return _KUBE_CLIENTS["apps"], _KUBE_CLIENTS["core"], None
    try:
        import kubernetes  # noqa: F401
        try:
            kubernetes.config.load_incluster_config()
        except Exception:
            kubernetes.config.load_kube_config()
        apps = kubernetes.client.AppsV1Api()
        core = kubernetes.client.CoreV1Api()
    except Exception as e:                      # ImportError or config failure
        _KUBE_ERROR = f"Kubernetes 客户端不可用: {str(e)[:150]}"
        return None, None, _KUBE_ERROR
    _KUBE_CLIENTS["apps"] = apps
    _KUBE_CLIENTS["core"] = core
    return apps, core, None


def _kube_or_503():
    apps, core, err = get_kube_clients()
    if err:
        raise HTTPException(status_code=503, detail=err)
    return apps, core


# --------------------------------------------------------------------------- #
# Model payloads
# --------------------------------------------------------------------------- #
class DesiredModel(BaseModel):
    version: str = cc.VERSION
    editable: Dict[str, dict]


class RestartRequest(BaseModel):
    deployment: str


# --------------------------------------------------------------------------- #
# Live state helpers
# --------------------------------------------------------------------------- #
def _pod_ready(pod) -> bool:
    for cond in pod.status.conditions or []:
        if cond.type == "Ready":
            return bool(cond.status == "True")
    return False


def _deployment_affinity(dep) -> dict:
    """Classify an editable deployment into the editor's model."""
    ns = (dep.spec.template.spec.node_selector or {})
    aff = dep.spec.template.spec.affinity
    anti = (aff.pod_anti_affinity.required_during_scheduling_ignored_during_execution
            if aff and aff.pod_anti_affinity else None)

    if ns.get("kubernetes.io/hostname"):
        return {"affinity": "fixed", "node": ns["kubernetes.io/hostname"]}
    if ns.get("role") == "edge" and anti:
        return {"affinity": "spread-edge", "node": None}
    if ns.get("role") == "edge":
        return {"affinity": "role-edge", "node": None}
    return {"affinity": "fixed", "node": None}


def _selector_match(pod_labels: dict, selector: dict) -> bool:
    """True when pod labels satisfy all key=value pairs of a Deployment selector."""
    return all(pod_labels.get(k) == v for k, v in (selector or {}).items())


def _select_pods(pods_all, match_labels: dict) -> List[dict]:
    out = []
    for p in pods_all:
        if not _selector_match(p.metadata.labels or {}, match_labels):
            continue
        out.append({
            "name": p.metadata.name,
            "node": p.spec.node_name,
            "ready": _pod_ready(p),
            "phase": p.status.phase,
        })
    return out


def _live_editable(apps, core, pods_all=None) -> Dict[str, dict]:
    """Map editable deployment -> live editor model.

    Uses ONE namespace-wide pod list when provided (avoids N list-pods API
    calls per deployment which previously starved the process under load).
    """
    result: Dict[str, dict] = {}
    if pods_all is None:
        pods_all = core.list_namespaced_pod(NAMESPACE).items
    deps = apps.list_namespaced_deployment(NAMESPACE, label_selector="app in (hospital,clinic)")
    for dep in deps.items:
        eid = dep.metadata.name
        spec = dep.spec
        kind = "hospital" if "hospital" in (spec.template.metadata.labels or {}) else "clinic"
        c0 = spec.template.spec.containers[0]
        aff = _deployment_affinity(dep)
        resources = {}
        if c0.resources:
            req = c0.resources.requests or {}
            lim = c0.resources.limits or {}
            resources = {
                "requests": {"cpu": req.get("cpu"), "memory": req.get("memory")},
                "limits": {"cpu": lim.get("cpu"), "memory": lim.get("memory")},
            }
        extra_labels = {
            k: v for k, v in (spec.template.metadata.labels or {}).items()
            if k not in ("app", "hospital", "clinic", "model", "stage")
        }
        pods = _select_pods(pods_all, dep.spec.selector.match_labels or {})
        result[eid] = {
            "kind": kind,
            "affinity": aff["affinity"],
            "node": aff["node"],
            "replicas": spec.replicas or 0,
            "image": c0.image,
            "resources": resources,
            "labels": extra_labels,
            "pods": pods,
        }
    return result


def _live_readonly(apps, core, pods_all=None) -> List[dict]:
    """Read-only deployments (medical-server/dc-services/redis/scheduler/...)."""
    result: List[dict] = []
    if pods_all is None:
        pods_all = core.list_namespaced_pod(NAMESPACE).items
    deps = apps.list_namespaced_deployment(NAMESPACE)
    by_name = {d.metadata.name: d for d in deps.items}
    for app_name in cc.READONLY_APPS:
        dep = by_name.get(app_name)
        if not dep:
            continue
        pods = _select_pods(pods_all, dep.spec.selector.match_labels or {})
        result.append({
            "name": app_name,
            "replicas": dep.spec.replicas or 0,
            "image": dep.spec.template.spec.containers[0].image,
            "pods": pods,
        })
    return result


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #
# ---- node load from kube metrics-server (fast, no Prometheus dependency) ----
_tls_unverified = False


def _kube_api_get(path: str, timeout: float = 6.0):
    """GET an aggregated/core API path using the scheduler SA token.

    SA 注入的 CA 与本集群 API server 服务证书不同链，校验会失败（节点/ Pod 负载曾因此全为空），
    故首次校验失败后改用不校验的上下文，避免每次轮询都白跑一趟。
    """
    global _tls_unverified
    token_path = "/var/run/secrets/kubernetes.io/serviceaccount/token"
    ca_path = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
    if not os.path.exists(token_path):
        raise RuntimeError("not running in-cluster")
    with open(token_path, "r") as f:
        token = f.read().strip()
    import ssl
    import urllib.request
    url = "https://kubernetes.default.svc" + path

    def _ctx(verify: bool):
        ctx = ssl.create_default_context()
        if verify:
            if os.path.exists(ca_path):
                ctx.load_verify_locations(ca_path)
        else:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        return ctx

    def _call(verify: bool):
        req = urllib.request.Request(url)
        req.add_header("Authorization", f"Bearer {token}")
        req.add_header("Accept", "application/json")
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            urllib.request.HTTPSHandler(context=_ctx(verify)),
        )
        with opener.open(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    if _tls_unverified:
        return _call(False)
    try:
        return _call(True)
    except Exception as e:  # noqa: BLE001
        if "CERTIFICATE_VERIFY_FAILED" in str(e) or "certificate verify failed" in str(e):
            _tls_unverified = True
            return _call(False)
        raise


def _parse_cpu_cores(value: str) -> Optional[float]:
    """Parse a k8s CPU quantity into fractional cores."""
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
        return round(float(v) * mult, 4)
    except ValueError:
        return None


def _parse_mem_bytes(value: str) -> Optional[int]:
    """Parse a k8s memory quantity ('1234Ki'/'1Gi'/'500Mi'/'123456') to bytes."""
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


def _pod_usage() -> Dict[str, dict]:
    """每个 Pod 的实时 CPU/内存用量（metrics.k8s.io）。"""
    out: Dict[str, dict] = {}
    try:
        for item in _kube_api_get("/apis/metrics.k8s.io/v1beta1/pods").get("items", []):
            meta = item.get("metadata") or {}
            name = meta.get("name")
            if not name:
                continue
            cpu = mem = 0.0
            for c in (item.get("containers") or []):
                u = c.get("usage") or {}
                cpu += _parse_cpu_cores(u.get("cpu")) or 0.0
                mem += _parse_mem_bytes(u.get("memory")) or 0
            out[name] = {"cpu_cores": round(cpu, 4), "mem_bytes": int(mem)}
    except Exception:  # noqa: BLE001
        return {}
    return out


def _pod_limits(core) -> Dict[str, dict]:
    """每个 Pod 的 requests/limits 合计（用于换算百分比）。"""
    out: Dict[str, dict] = {}
    try:
        for pod in core.list_namespaced_pod("default").items:
            name = pod.metadata.name
            cpu_req = mem_req = cpu_lim = mem_lim = 0.0
            for c in (pod.spec.containers or []):
                res = c.resources
                if not res:
                    continue
                req = res.requests or {}
                lim = res.limits or {}
                cpu_req += _parse_cpu_cores(req.get("cpu")) or 0.0
                mem_req += _parse_mem_bytes(req.get("memory")) or 0
                cpu_lim += _parse_cpu_cores(lim.get("cpu")) or 0.0
                mem_lim += _parse_mem_bytes(lim.get("memory")) or 0
            out[name] = {
                "node": pod.spec.node_name,
                "phase": pod.status.phase,
                "cpu_req_cores": round(cpu_req, 4),
                "mem_req_bytes": int(mem_req),
                "cpu_lim_cores": round(cpu_lim, 4),
                "mem_lim_bytes": int(mem_lim),
            }
    except Exception:  # noqa: BLE001
        return {}
    return out


@router.get("/pod_metrics")
def pod_metrics():
    """集群负载页用：每个 Pod 的 CPU/内存实时用量与限额占比（4s 缓存）。"""
    def build():
        # 注意：_kube_or_503() 返回 (apps, core) 元组；此前误把它当 core 用，
        # 导致 list_namespaced_pod 抛 AttributeError 被吞掉 → Pod 负载恒为空。
        _apps, core = _kube_or_503()
        usage = _pod_usage()
        limits = _pod_limits(core)
        rows = []
        for name, info in limits.items():
            u = usage.get(name) or {}
            cpu = float(u.get("cpu_cores") or 0.0)
            mem = int(u.get("mem_bytes") or 0)
            cpu_lim = info.get("cpu_lim_cores") or 0.0
            mem_lim = info.get("mem_lim_bytes") or 0
            cpu_req = info.get("cpu_req_cores") or 0.0
            mem_req = info.get("mem_req_bytes") or 0
            rows.append({
                "pod": name,
                "node": info.get("node"),
                "phase": info.get("phase"),
                "cpu_cores": cpu,
                "mem_bytes": mem,
                "cpu_limit_cores": cpu_lim,
                "mem_limit_bytes": mem_lim,
                "cpu_request_cores": cpu_req,
                "mem_request_bytes": mem_req,
                # 有 limits 用 limits 作分母，否则退回 requests
                "cpu_pct": round(cpu / (cpu_lim or cpu_req), 4) if (cpu_lim or cpu_req) else None,
                "mem_pct": round(mem / (mem_lim or mem_req), 4) if (mem_lim or mem_req) else None,
            })
        rows.sort(key=lambda r: (-(r["cpu_pct"] or 0), r["pod"]))
        return {"ok": True, "count": len(rows), "pods": rows,
                "metrics": bool(usage)}
    return _cached(_POD_CACHE, "pods", build)


def _node_loads() -> Dict[str, dict]:
    """Node CPU/mem usage via the shared node_usage cache (3s TTL)."""
    try:
        from . import node_usage
        raw = node_usage.node_loads()
        return {n: {"cpu": v.get("cpu", 0.0), "memory": v.get("memory", 0.0),
                    "gpu": 0.0} for n, v in raw.items()}
    except Exception:
        return {}


# --- short TTL cache so many clients/polls do not re-hit k8s + metrics ---
_STATUS_CACHE: dict = {}
_SUMMARY_CACHE: dict = {}
_POD_CACHE: dict = {}
_TTL = float(os.getenv("CLUSTER_CACHE_TTL", "4"))


def _cached(cache: dict, key: str, builder):
    now = time.time()
    hit = cache.get(key)
    if hit and now - hit[0] < _TTL:
        return hit[1]
    value = builder()
    cache[key] = (now, value)
    return value


@router.get("/status")
def cluster_status():
    """Live cluster topology + editable entities + readonly deployments."""
    return _cached(_STATUS_CACHE, "status", _build_status)


def _build_status():
    nodes_loads = _node_loads()

    payload: Dict[str, Any] = {
        "version": cc.VERSION,
        "ok": True,
        "nodes": [],
        "entities": {},
        "readonly": [],
        "error": None,
    }

    apps, core, err = get_kube_clients()
    if err:
        payload["ok"] = False
        payload["error"] = err
        payload["nodes"] = [
            {"name": n, "role": nodes_loads.get(n, {}).get("role"),
             "load": nodes_loads.get(n) or {
                 "cpu": 0.0, "memory": 0.0, "gpu": 0.0}} for n in nodes_loads
        ]
        return payload

    # ---- nodes ----
    for n in core.list_node().items:
        role = None
        labels = n.metadata.labels or {}
        if labels.get("role"):
            role = labels["role"]
        elif "node-role.kubernetes.io/control-plane" in labels:
            role = "control-plane"
        ready = any(c.type == "Ready" and c.status == "True"
                    for c in n.status.conditions or [])
        load = nodes_loads.get(n.metadata.name)
        payload["nodes"].append({
            "name": n.metadata.name,
            "role": role,
            "ready": ready,
            "load": load,
            "capacity": {"cpu": n.status.allocatable.get("cpu"),
                         "memory": n.status.allocatable.get("memory")},
        })

    # ---- editable entities & readonly deployments (single pod list) ----
    pods_all = core.list_namespaced_pod(NAMESPACE).items
    payload["entities"] = _live_editable(apps, core, pods_all)
    payload["readonly"] = _live_readonly(apps, core, pods_all)
    return payload


@router.get("/summary")
def cluster_summary():
    """Lightweight cluster snapshot for dashboards (4s TTL cached)."""
    return _cached(_SUMMARY_CACHE, "summary", _build_summary)


def _build_summary():
    payload: Dict[str, Any] = {
        "version": cc.VERSION,
        "ok": True,
        "error": None,
        "nodes": [],
        "editable": 0,
        "editable_pods": 0,
        "readonly_pods": 0,
    }
    loads = _node_loads()
    apps, core, err = get_kube_clients()
    if err:
        payload["ok"] = False
        payload["error"] = err
        return payload
    for n in core.list_node().items:
        labels = n.metadata.labels or {}
        role = labels.get("role")
        if not role and "node-role.kubernetes.io/control-plane" in labels:
            role = "control-plane"
        ready = any(c.type == "Ready" and c.status == "True"
                    for c in n.status.conditions or [])
        payload["nodes"].append({
            "name": n.metadata.name, "role": role, "ready": ready,
            "load": loads.get(n.metadata.name),
            "capacity": {"cpu": n.status.allocatable.get("cpu"),
                         "memory": n.status.allocatable.get("memory")},
        })
    pods_all = core.list_namespaced_pod(NAMESPACE).items
    deps = apps.list_namespaced_deployment(
        NAMESPACE, label_selector="app in (hospital,clinic)")
    for dep in deps.items:
        payload["editable"] += 1
        payload["editable_pods"] += len(
            _select_pods(pods_all, dep.spec.selector.match_labels or {}))
    ro_deps = apps.list_namespaced_deployment(NAMESPACE)
    by_name = {d.metadata.name: d for d in ro_deps.items}
    for app_name in cc.READONLY_APPS:
        dep = by_name.get(app_name)
        if dep:
            payload["readonly_pods"] += len(
                _select_pods(pods_all, dep.spec.selector.match_labels or {}))
    return payload


@router.get("/default")
def cluster_default():
    return cc.default_model()


@router.post("/validate")
def cluster_validate(model: DesiredModel):
    """Dry-run validation (no cluster mutation)."""
    return cc.validate_model(model.dict())


# --------------------------------------------------------------------------- #
# Build manifests / patches
# --------------------------------------------------------------------------- #
def _clinic_manifest(eid: str, entity: dict) -> Dict[str, Any]:
    """Full Deployment manifest for a newly added clinic pod."""
    entity = {**entity, "id": eid}
    labels = cc.template_labels_for(entity)
    cname = cc.container_name_for(entity)
    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": eid, "labels": {"app": "clinic", "clinic": eid}},
        "spec": {
            "replicas": int(entity.get("replicas", 1)),
            "selector": {"matchLabels": {"app": "clinic", "clinic": eid}},
            "template": {
                "metadata": {"labels": labels},
                "spec": {
                    "serviceAccountName": "clinic",
                    "nodeSelector": cc.node_selector_for(entity),
                    "affinity": cc.affinity_for(entity),
                    "containers": [{
                        "name": cname,
                        "image": entity.get("image", cc.IMAGE_CLINIC),
                        "imagePullPolicy": "IfNotPresent",
                        "ports": [{"name": "http", "containerPort": 8007}],
                        "env": [
                            {"name": "CLINIC_NAME", "value": eid},
                            {"name": "POD_NAME",
                             "valueFrom": {"fieldRef": {
                                 "fieldPath": "metadata.name"}}},
                            {"name": "NAMESPACE",
                             "valueFrom": {"fieldRef": {
                                 "fieldPath": "metadata.namespace"}}},
                            {"name": "NODE_NAME",
                             "valueFrom": {"fieldRef": {
                                 "fieldPath": "spec.nodeName"}}},
                        ],
                        "readinessProbe": {
                            "httpGet": {"path": "/health", "port": 8007},
                            "initialDelaySeconds": 5, "periodSeconds": 10,
                        },
                        "livenessProbe": {
                            "httpGet": {"path": "/health", "port": 8007},
                            "initialDelaySeconds": 10, "periodSeconds": 20,
                            "failureThreshold": 3,
                        },
                        "resources": entity.get("resources") or {},
                    }],
                },
            },
        },
    }


def _clinic_service_manifest(eid: str) -> Dict[str, Any]:
    return {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {"name": f"{eid}-service",
                     "labels": {"app": "clinic", "clinic": eid}},
        "spec": {
            "selector": {"app": "clinic", "clinic": eid},
            "ports": [{"name": "http", "protocol": "TCP",
                       "port": 8007, "targetPort": 8007}],
            "type": "ClusterIP",
        },
    }


def _patch_body_for(eid: str, entity: dict) -> Dict[str, Any]:
    """Strategic-merge patch body for an existing editable deployment."""
    entity = {**entity, "id": eid}
    labels = cc.template_labels_for(entity)
    cname = cc.container_name_for(entity)
    patch: Dict[str, Any] = {
        "spec": {
            "replicas": int(entity.get("replicas", 1)),
            "template": {
                "metadata": {"labels": labels},
                "spec": {
                    "nodeSelector": cc.node_selector_for(entity),
                    "containers": [{
                        "name": cname,
                        "image": entity.get("image"),
                        "resources": entity.get("resources"),
                    }],
                },
            },
        },
    }
    aff = cc.affinity_for(entity)
    # merge-patch: null deletes the field
    patch["spec"]["template"]["spec"]["affinity"] = aff
    # nodeSelector null would be invalid JSON in merge semantics -> omit key
    if patch["spec"]["template"]["spec"]["nodeSelector"] is None:
        del patch["spec"]["template"]["spec"]["nodeSelector"]
    return patch


def _k8s_err(e: Exception, prefix: str = "") -> str:
    """Human-readable message from a kubernetes ApiException (body carries
    the real validation reason, e.g. empty image)."""
    body = getattr(e, "body", None)
    if body:
        try:
            parsed = json.loads(body)
            msg = parsed.get("message") if isinstance(parsed, dict) else str(parsed)
            if msg:
                return (prefix + msg)[:500]
        except Exception:
            return (prefix + str(body))[:500]
    return (prefix + str(e))[:500]


# --------------------------------------------------------------------------- #
# Apply / reset
# --------------------------------------------------------------------------- #
def _apply_model(model: Dict[str, Any]) -> Dict[str, Any]:
    apps, core = _kube_or_503()

    validation = cc.validate_model(model)
    if not validation["ok"]:
        return {"ok": False, "errors": validation["errors"],
                "applied": [], "warnings": validation["warnings"]}

    desired = model.get("editable", {})
    live = _live_editable(apps, core)
    report: List[dict] = []

    # --- deletions: any clinic absent from the desired model is removed
    # (default clinic-1/clinic-2 included; reset restores them) ---
    for eid in live:
        if eid not in desired and live[eid]["kind"] == "clinic":
            try:
                apps.delete_namespaced_deployment(eid, NAMESPACE)
                core.delete_namespaced_service(f"{eid}-service", NAMESPACE)
                unregister_clinic(eid)
                report.append({"name": eid, "action": "deleted",
                               "message": "已删除 clinic 部署与服务"})
            except Exception as e:
                report.append({"name": eid, "action": "delete-failed",
                               "message": _k8s_err(e, f"{eid} 删除失败: ")})

    # --- creates (new clinic pods) then patches ---
    for eid, ent in desired.items():
        ent = cc.canonicalize_entity(ent, eid)
        if eid not in live:
            if ent["kind"] != "clinic":
                report.append({"name": eid, "action": "create-failed",
                               "message": "只允许新增 clinic"})
                continue
            try:
                apps.create_namespaced_deployment(
                    NAMESPACE, _clinic_manifest(eid, ent))
                try:
                    core.create_namespaced_service(
                        NAMESPACE, _clinic_service_manifest(eid))
                except Exception:
                    pass
                register_clinic(eid)
                report.append({"name": eid, "action": "created",
                               "message": f"已创建 clinic（{ent.get('node') or 'role=edge'}）"})
            except Exception as e:
                report.append({"name": eid, "action": "create-failed",
                               "message": _k8s_err(e, f"{eid} 创建失败: ")})
            continue

        # existing editable deployment -> patch
        try:
            body = _patch_body_for(eid, ent)
            apps.patch_namespaced_deployment(eid, NAMESPACE, body)
            report.append({
                "name": eid, "action": "patched",
                "message": f"副本数={ent['replicas']}, "
                           f"亲和={ent['affinity']}"
                           + (f", 节点={ent['node']}" if ent["node"] else ""),
            })
        except Exception as e:
            report.append({"name": eid, "action": "patch-failed",
                           "message": _k8s_err(e, f"{eid} 更新失败: ")})

    failed = [r for r in report if r["action"].endswith("failed")]
    return {"ok": len(failed) == 0, "errors": [r["message"] for r in failed],
            "applied": report, "warnings": validation["warnings"]}


@router.put("/apply")
def cluster_apply(model: DesiredModel):
    """Validate + apply a desired editable model to the cluster."""
    result = _apply_model(model.dict())
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result)
    return result


@router.post("/reset")
def cluster_reset():
    """Restore the current-version default cluster configuration."""
    result = _apply_model(cc.default_model())
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result)
    result["version"] = cc.VERSION
    return result


@router.post("/restart")
def cluster_restart(req: RestartRequest):
    """Rollout restart of one editable deployment (rolling update)."""
    apps, core = _kube_or_503()
    name = req.deployment
    live = _live_editable(apps, core)
    if name not in live:
        raise HTTPException(status_code=400,
                            detail=f"{name} 不是当前可编辑的 hospital/clinic 实体")
    now = datetime.now(timezone.utc).isoformat()
    body = {"spec": {"template": {"metadata": {
        "annotations": {"kubectl.kubernetes.io/restartedAt": now}}}}}
    try:
        apps.patch_namespaced_deployment(name, NAMESPACE, body)
    except Exception as e:
        raise HTTPException(status_code=500,
                            detail=_k8s_err(e, "重启失败: "))
    return {"ok": True, "deployment": name, "restartedAt": now}
