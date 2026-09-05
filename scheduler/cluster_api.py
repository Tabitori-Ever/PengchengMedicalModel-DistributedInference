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


def _live_editable(apps, core) -> Dict[str, dict]:
    """Map editable deployment -> live editor model."""
    result: Dict[str, dict] = {}
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
        pods = []
        selector = ",".join(f"{k}={v}" for k, v in
                            (dep.spec.selector.match_labels or {}).items())
        plist = core.list_namespaced_pod(NAMESPACE, label_selector=selector)
        for p in plist.items:
            pods.append({
                "name": p.metadata.name,
                "node": p.spec.node_name,
                "ready": _pod_ready(p),
                "phase": p.status.phase,
            })
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


def _live_readonly(apps, core) -> List[dict]:
    """Read-only deployments (medical-server/part2/redis/scheduler/...)."""
    result: List[dict] = []
    deps = apps.list_namespaced_deployment(NAMESPACE)
    by_name = {d.metadata.name: d for d in deps.items}
    for app_name in cc.READONLY_APPS:
        dep = by_name.get(app_name)
        if not dep:
            continue
        pods = []
        selector = ",".join(f"{k}={v}" for k, v in
                            (dep.spec.selector.match_labels or {}).items())
        plist = core.list_namespaced_pod(NAMESPACE, label_selector=selector)
        for p in plist.items:
            pods.append({
                "name": p.metadata.name,
                "node": p.spec.node_name,
                "ready": _pod_ready(p),
                "phase": p.status.phase,
            })
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
@router.get("/status")
def cluster_status():
    """Live cluster topology + editable entities + readonly deployments."""
    nodes_status = {}
    try:
        from .resource_monitor import ResourceMonitor
        rm = ResourceMonitor()
        nodes_status = rm.get_all_nodes()
    except Exception:
        pass

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
            {"name": n, "role": info.get("role"), "load": {
                "cpu": info.get("cpu", 0), "memory": info.get("memory", 0),
                "gpu": info.get("gpu", 0)}} for n, info in nodes_status.items()
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
        info = nodes_status.get(n.metadata.name, {})
        ready = any(c.type == "Ready" and c.status == "True"
                    for c in n.status.conditions or [])
        load = None
        if info:
            load = {
                "cpu": round(float(info.get("cpu", 0)), 3),
                "memory": round(float(info.get("memory", 0)), 3),
                "gpu": round(float(info.get("gpu", 0)), 3),
            }
        payload["nodes"].append({
            "name": n.metadata.name,
            "role": role,
            "ready": ready,
            "load": load,
            "capacity": {"cpu": n.status.allocatable.get("cpu"),
                         "memory": n.status.allocatable.get("memory")},
        })

    # ---- editable entities & readonly deployments ----
    payload["entities"] = _live_editable(apps, core)
    payload["readonly"] = _live_readonly(apps, core)
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

    # --- deletions: extra clinics that disappeared from the desired model ---
    for eid in live:
        if eid not in cc.DEFAULT_EDITABLE and eid not in desired and live[eid]["kind"] == "clinic":
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
