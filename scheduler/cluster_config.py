"""
Cluster topology configuration (v2.0) - hospital / clinic pod architecture.

This module is the single source of truth for the *editable* cluster model:
which pods exist, on which nodes they sit by default, how many replicas,
affinity strategy, images and resources.  It mirrors the K8s manifests in
k8s/ (hospital-*.yaml / clinic-*.yaml) - keep both in sync manually.

Entity model (per editable pod):
    id        - e.g. hospital-a / clinic-1
    kind      - "hospital" | "clinic"
    affinity  - "fixed" (nodeSelector hostname, replicas==1)
              | "role-edge" (nodeSelector role=edge)
              | "spread-edge" (role=edge + required per-hostname anti-affinity)
    node      - host node name (only meaningful for affinity=="fixed")
    replicas  - 1 .. max replicas allowed by the affinity
    image     - container image
    resources - k8s requests/limits (cpu / memory)
    labels    - extra pod-template labels (selector labels are protected)
"""
import hashlib
import json
from typing import Dict, Any, List, Optional

VERSION = "2.0"
NAMESPACE = "default"
REGISTRY = "10.29.182.66:5000/k8s-repo"

EDGE_NODES = ["node1", "node2"]
FIXED_NODES_READONLY = ["node3", "desktop-jm5iec6"]
ALL_NODES = EDGE_NODES + FIXED_NODES_READONLY

IMAGE_HOSPITAL = f"{REGISTRY}/hospital:v2.0"
# 注意：clinic/scheduler 的 v2.0.1 修复了 metrics 用量解析/part2 探测，
# 默认模型必须指向修复版，否则 reset 会把运行中的镜像“降级”回有 bug 的 v2.0。
IMAGE_CLINIC = f"{REGISTRY}/clinic:v2.0.1"
IMAGE_SCHEDULER = f"{REGISTRY}/inference-scheduler:v2.0.5"

DEFAULT_RESOURCES = {
    "hospital": {
        "requests": {"cpu": "1500m", "memory": "2Gi"},
        "limits": {"cpu": "4", "memory": "6Gi"},
    },
    "clinic": {
        "requests": {"cpu": "100m", "memory": "128Mi"},
        "limits": {"cpu": "500m", "memory": "512Mi"},
    },
}

# Default editable entities (the four pods of the v2.0 platform)
DEFAULT_EDITABLE: Dict[str, dict] = {
    "hospital-a": {
        "kind": "hospital",
        "affinity": "fixed",
        "node": "node1",
        "replicas": 1,
        "image": IMAGE_HOSPITAL,
        "resources": DEFAULT_RESOURCES["hospital"],
        "labels": {},
    },
    "hospital-b": {
        "kind": "hospital",
        "affinity": "fixed",
        "node": "node2",
        "replicas": 1,
        "image": IMAGE_HOSPITAL,
        "resources": DEFAULT_RESOURCES["hospital"],
        "labels": {},
    },
    "clinic-1": {
        "kind": "clinic",
        "affinity": "fixed",
        "node": "node1",
        "replicas": 1,
        "image": IMAGE_CLINIC,
        "resources": DEFAULT_RESOURCES["clinic"],
        "labels": {},
    },
    "clinic-2": {
        "kind": "clinic",
        "affinity": "fixed",
        "node": "node2",
        "replicas": 1,
        "image": IMAGE_CLINIC,
        "resources": DEFAULT_RESOURCES["clinic"],
        "labels": {},
    },
}

# Read-only deployments shown on the cluster map (not editable)
READONLY_APPS = ["medical-server", "part2", "redis", "scheduler",
                 "monitoring", "prediction"]

SELECTOR_LABELS = {
    "hospital": {"app": "hospital"},
    "clinic": {"app": "clinic"},
}

MAX_EDGE_REPLICAS = len(EDGE_NODES)  # role-edge / spread-edge


def default_model() -> Dict[str, Any]:
    return {
        "version": VERSION,
        "editable": json.loads(json.dumps(DEFAULT_EDITABLE)),
    }


def max_replicas(entity: dict) -> int:
    affinity = entity.get("affinity", "fixed")
    if affinity == "fixed":
        return 1
    return MAX_EDGE_REPLICAS


def eligible_nodes(entity: dict) -> List[str]:
    affinity = entity.get("affinity", "fixed")
    if affinity == "fixed":
        node = entity.get("node")
        return [node] if node in EDGE_NODES else []
    return list(EDGE_NODES)


def validate_model(model: Dict[str, Any]) -> Dict[str, Any]:
    """Validate a desired editable model.

    Returns {"ok": bool, "errors": [...], "warnings": [...]}.
    Default entities (hospital-a/b, clinic-1/2) must all be present.
    Extra clinic-N entities may be added; extra hospitals are rejected.
    """
    errors: List[str] = []
    warnings: List[str] = []

    editable = (model or {}).get("editable", {})
    if not isinstance(editable, dict):
        return {"ok": False, "errors": ["模型缺少 editable 对象"], "warnings": []}

    # 1) default entities cannot be removed
    for eid in DEFAULT_EDITABLE:
        if eid not in editable:
            errors.append(f"默认实体 {eid} 不能移除")

    # 2) per-entity checks
    for eid, ent in editable.items():
        kind = ent.get("kind")
        affinity = ent.get("affinity", "fixed")
        replicas = int(ent.get("replicas", 1))
        image = ent.get("image", "")
        node = ent.get("node")

        if eid in DEFAULT_EDITABLE:
            default_kind = DEFAULT_EDITABLE[eid]["kind"]
            if kind != default_kind:
                errors.append(f"{eid}: 类型与默认不一致({default_kind})")
        else:
            # newly added entities: only clinics allowed
            if kind != "clinic" or not str(eid).startswith("clinic-"):
                errors.append(
                    f"{eid}: 只允许新增 clinic 实体（当前版本 hospital 固定为 hospital-a/hospital-b）")
            continue

        if affinity not in ("fixed", "role-edge", "spread-edge"):
            errors.append(f"{eid}: 未知亲和策略 {affinity}")
            continue

        if affinity == "fixed":
            if node not in EDGE_NODES:
                errors.append(f"{eid}: 固定策略下节点必须为边缘节点 {EDGE_NODES}（当前 {node}）")
            if replicas != 1:
                errors.append(f"{eid}: 固定节点策略下副本数必须为 1（当前 {replicas}）")
        else:
            if replicas < 1 or replicas > MAX_EDGE_REPLICAS:
                errors.append(
                    f"{eid}: 边缘角色/分散策略副本数须在 1..{MAX_EDGE_REPLICAS}（当前 {replicas}）")

        if not image:
            errors.append(f"{eid}: 镜像不能为空")

    return {
        "ok": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
    }


# ---------------------------------------------------------------- affinity/NS
def node_selector_for(entity: dict) -> Optional[Dict[str, str]]:
    affinity = entity.get("affinity", "fixed")
    if affinity == "fixed":
        node = entity.get("node")
        if node in EDGE_NODES:
            return {"kubernetes.io/hostname": node}
        return None
    if affinity in ("role-edge", "spread-edge"):
        return {"role": "edge"}
    return None


def affinity_for(entity: dict) -> Optional[Dict[str, Any]]:
    """Build the pod affinity fragment for a deployment template spec.

    spread-edge: required per-hostname pod anti-affinity keyed on the
    *entity* (hospital=<id> / clinic=<id>), so replicas of one deployment
    never share a node while other deployments are unaffected.
    """
    affinity = entity.get("affinity", "fixed")
    eid = entity.get("id", "?")
    kind = entity.get("kind", "clinic")
    if affinity == "spread-edge":
        key = "hospital" if kind == "hospital" else "clinic"
        return {
            "podAntiAffinity": {
                "requiredDuringSchedulingIgnoredDuringExecution": [{
                    "topologyKey": "kubernetes.io/hostname",
                    "labelSelector": {"matchLabels": {key: eid}},
                }]
            }
        }
    return None


def template_labels_for(entity: dict) -> Dict[str, str]:
    """Labels applied on the pod template.

    Always preserves the deployment selector labels (app/hospital/clinic),
    then merges extra user labels on top (never allowed to overwrite
    selector labels).
    """
    eid_entity = entity.get("id", "?")
    kind = entity.get("kind", "clinic")
    labels = dict(SELECTOR_LABELS.get(kind, {"app": kind}))
    if kind == "hospital":
        labels["hospital"] = eid_entity
    else:
        labels["clinic"] = eid_entity
    extra = entity.get("labels") or {}
    for k, v in extra.items():
        if k not in ("app", "hospital", "clinic", "model", "stage"):
            labels[k] = str(v)
    return labels


def container_name_for(entity: dict) -> str:
    return "hospital" if entity.get("kind") == "hospital" else "clinic"


def canonicalize_entity(entity: dict, eid: str) -> Dict[str, Any]:
    """Normalize an entity dict (fill defaults) for fingerprinting/diff."""
    defaults = DEFAULT_EDITABLE.get(eid, {})
    merged = {**defaults, **{k: v for k, v in entity.items() if v is not None}}
    merged["id"] = eid
    merged["kind"] = merged.get("kind") or defaults.get("kind", "clinic")
    merged["affinity"] = merged.get("affinity") or defaults.get("affinity", "fixed")
    merged["node"] = merged.get("node") or defaults.get("node")
    merged["replicas"] = int(merged.get("replicas", 1))
    merged["image"] = (merged.get("image") or defaults.get("image")
                       or (IMAGE_HOSPITAL if merged["kind"] == "hospital"
                           else IMAGE_CLINIC))
    merged["resources"] = merged.get("resources") or defaults.get("resources")
    merged["labels"] = merged.get("labels") or {}
    return merged


def canonical_model(model: Dict[str, Any]) -> Dict[str, Any]:
    """Canonical, sortable representation used for client/server diffing."""
    editable = {}
    for eid in sorted((model or {}).get("editable", {}).keys()):
        editable[eid] = canonicalize_entity(
            (model or {}).get("editable", {})[eid], eid)
    return {"version": VERSION, "editable": editable}


def fingerprint(model: Dict[str, Any]) -> str:
    """Stable fingerprint of a canonical model."""
    canon = canonical_model(model)
    raw = json.dumps(canon, ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
