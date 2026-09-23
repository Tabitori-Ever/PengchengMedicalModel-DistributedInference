"""Kubernetes operations for the v3.0 routine (ephemeral Job) pipeline."""
import json
import os
import time
from typing import List, Optional

NAMESPACE = "default"

_clients = {}
_client_error = None


def _get():
    global _client_error
    if "batch" in _clients:
        return _clients
    try:
        import kubernetes
        try:
            kubernetes.config.load_incluster_config()
        except Exception:
            kubernetes.config.load_kube_config()
        _clients["batch"] = kubernetes.client.BatchV1Api()
        _clients["core"] = kubernetes.client.CoreV1Api()
    except Exception as e:
        _client_error = str(e)[:200]
        return {}
    return _clients


def available() -> bool:
    return bool(_get())


def create_routine_job(name: str, node: str, args: dict,
                       image: str = None, backoff: int = 0) -> Optional[str]:
    """Create a short-lived Job `name` pinned to `node` running the routine
    entrypoint of the clinic image. Returns created job name or None."""
    c = _get()
    if not c.get("batch"):
        return None
    image = image or os.environ.get("ROUTINE_IMAGE",
                                    "k8s-master:5000/k8s-repo/clinic:v3.0")
    body = {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {"name": name, "namespace": NAMESPACE,
                     "labels": {"app": "routine", "job": name}},
        "spec": {
            "backoffLimit": backoff,
            "ttlSecondsAfterFinished": 120,
            "template": {
                "metadata": {"labels": {"app": "routine", "job": name}},
                "spec": {
                    "restartPolicy": "Never",
                    "nodeSelector": {"kubernetes.io/hostname": node},
                    "containers": [{
                        "name": "routine",
                        "image": image,
                        "imagePullPolicy": "IfNotPresent",
                        "command": ["python", "/app/run_routine.py",
                                    json.dumps(args, ensure_ascii=False)],
                        "env": [
                            {"name": "POD_NAME",
                             "valueFrom": {"fieldRef": {"fieldPath": "metadata.name"}}},
                            {"name": "NODE_NAME",
                             "valueFrom": {"fieldRef": {"fieldPath": "spec.nodeName"}}},
                        ],
                        "resources": {
                            "requests": {"cpu": "100m", "memory": "64Mi"},
                            "limits": {"cpu": "1", "memory": "256Mi"},
                        },
                    }],
                },
            },
        },
    }
    try:
        c["batch"].create_namespaced_job(NAMESPACE, body)
        return name
    except Exception:
        return None


def job_status(name: str) -> dict:
    """Return {state: running|succeeded|failed, pod_name?, conditions}."""
    c = _get()
    if not c.get("batch"):
        return {"state": "unknown"}
    try:
        job = c["batch"].read_namespaced_job(name, NAMESPACE)
    except Exception:
        return {"state": "missing"}
    st = job.status
    if st.succeeded and st.succeeded >= 1:
        pods = c["core"].list_namespaced_pod(
            NAMESPACE, label_selector=f"job-name={name}").items
        pod = pods[0].metadata.name if pods else None
        return {"state": "succeeded", "pod_name": pod,
                "completion_time": str(st.completion_time or "")}
    if st.failed:
        return {"state": "failed", "failed": st.failed}
    return {"state": "running"}


def read_pod_log(pod_name: str) -> str:
    c = _get()
    if not c.get("core"):
        return ""
    try:
        return c["core"].read_namespaced_pod_log(pod_name, NAMESPACE,
                                                 tail_lines=50) or ""
    except Exception:
        return ""


def delete_job(name: str) -> bool:
    c = _get()
    if not c.get("batch"):
        return False
    try:
        c["batch"].delete_namespaced_job(
            name, NAMESPACE, propagation_policy="Background")
        return True
    except Exception:
        return False


def list_routine_jobs() -> List[dict]:
    c = _get()
    if not c.get("batch"):
        return []
    try:
        jobs = c["batch"].list_namespaced_job(
            NAMESPACE, label_selector="app=routine").items
    except Exception:
        return []
    out = []
    for j in jobs:
        st = j.status
        out.append({
            "name": j.metadata.name,
            "node": (j.spec.template.spec.node_selector or {}).get(
                "kubernetes.io/hostname"),
            "succeeded": bool(st and st.succeeded),
            "failed": (st.failed if st else 0),
            "active": (st.active if st else 0),
        })
    return out
