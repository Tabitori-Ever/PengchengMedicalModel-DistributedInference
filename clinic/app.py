"""
Clinic Pod Service (v3.0 - Terminal/Edge endpoint of the platform).

Clinic pods initiate diagnosis (forwarded to a hospital), compute and sync
tasks, and act as P2P data peers. Also keeps a lightweight pod memory utility
endpoint for operations (not a submission type in v3.0).
"""
import json
import os
import ssl
import sys
import time
import urllib.request
from pathlib import Path

from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field
from prometheus_client import Counter, Histogram, Gauge, generate_latest
from typing import Dict, List, Optional

sys.path.insert(0, "/app")
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path and Path(_ROOT).is_dir():
    sys.path.insert(0, str(_ROOT))

from common import v3_common as v3           # noqa: E402
from common import local_exec as lx          # noqa: E402

CLINIC_NAME = os.environ.get("CLINIC_NAME", "clinic-1")
NODE_NAME = os.environ.get("NODE_NAME", "unknown")
POD_NAME = os.environ.get("POD_NAME", "")
NAMESPACE = os.environ.get("NAMESPACE", "default")

DB_VERSION = os.environ.get("DB_VERSION", "v3-db-1")
DB_ITEM_COUNT = int(os.environ.get("DB_ITEM_COUNT", "64"))
HOLD_RATIO = float(os.environ.get("HOLD_RATIO", "0.5"))

app = FastAPI(title=f"Clinic Pod ({CLINIC_NAME}) v3.0")

queries_total = Counter("clinic_mem_queries_total", "memory queries", ["target"])
compute_requests = Counter("v3_compute_requests_total", "v3 compute partitions", ["entity"])
sync_requests = Counter("v3_sync_requests_total", "v3 sync calls", ["entity"])

_accepted: set = set()

# ====================== v3.2 local execution runtime ========================
# A clinic has no PyTorch / no weights, so "local" for diagnosis means this pod
# drives the pipeline and takes the shortest hop: the nearest hospital runs the
# full model in place. compute / sync / routine run entirely on this pod.
local_store = lx.ResultStore()
local_exec_requests = Counter("local_exec_requests_total",
                              "local executions", ["entity", "kind"])
local_exec_latency = Histogram("local_exec_latency_seconds",
                               "local execution latency", ["kind"])

local_runner = lx.queue_runner_factory(local_store)   # diagnosis -> forward
local_queue = lx.LocalQueue(local_runner, store=local_store)


# ----------------------------- memory (ops tool) ----------------------------
def _read_cg(f: str):
    try:
        with open(f) as fh:
            return int(fh.read().strip())
    except Exception:
        return None


@app.get("/query/mem")
def query_mem():
    """Ops-only utility: this clinic pod memory usage (kept from v2, not a
    submission type in v3.0)."""
    queries_total.labels(target="self").inc()
    v2 = os.path.exists("/sys/fs/cgroup/cgroup.controllers")
    usage = _read_cg("/sys/fs/cgroup/memory.current" if v2 else
                     "/sys/fs/cgroup/memory/memory.usage_in_bytes")
    limit = _read_cg("/sys/fs/cgroup/memory.max" if v2 else
                     "/sys/fs/cgroup/memory/memory.limit_in_bytes")
    if limit and limit >= (1 << 62):
        limit = None
    percent = round(usage / limit * 100, 2) if (usage and limit) else None
    return {"pod": POD_NAME or CLINIC_NAME, "usage_bytes": usage,
            "limit_bytes": limit, "usage_percent": percent, "source": "cgroup"}


# -------------------------------- models ------------------------------------
class ComputeRequest(BaseModel):
    rows: int = 256
    instruments: int = 4
    intensity: int = 40
    seed: int = 1
    partition: int = 0
    count: int = 1


class AcceptSyncRequest(BaseModel):
    ids: List[str] = []


# -------------------------------- endpoints ---------------------------------
@app.get("/")
def root():
    return {"service": "clinic", "clinic": CLINIC_NAME, "node": NODE_NAME,
            "pod": POD_NAME, "role": "terminal", "version": "3.0",
            "status": "running"}


@app.get("/health")
def health_check():
    return {"service": "clinic", "clinic": CLINIC_NAME, "node": NODE_NAME,
            "pod": POD_NAME, "status": "ok",
            "capabilities": lx.capabilities("clinic"), "local_mode": True}


@app.post("/v3/compute")
def v3_compute(req: ComputeRequest):
    compute_requests.labels(entity=CLINIC_NAME).inc()
    res = v3.compute_partition(rows=req.rows, instruments=req.instruments,
                               intensity=req.intensity, seed=req.seed,
                               partition=req.partition, count=req.count)
    res.update({"actor": CLINIC_NAME, "node": NODE_NAME, "role": "terminal",
                "pod": POD_NAME or CLINIC_NAME})
    return res


@app.get("/v3/sync/local")
def v3_sync_local():
    sync_requests.labels(entity=CLINIC_NAME).inc()
    all_ids = v3.item_ids(DB_VERSION, DB_ITEM_COUNT)
    secret = f"{CLINIC_NAME}:{NODE_NAME}"
    holds = v3.hold_subset(all_ids, secret, HOLD_RATIO)
    pending = []
    for i in range(3):
        uid = f"upd-{CLINIC_NAME}-{i}"
        blob = v3.chunk_blob(uid, 512)
        meta = v3.item_meta(uid, blob)
        pending.append({"uid": uid, "size": meta["size"], "hash": meta["hash"]})
    return {"pod": POD_NAME or CLINIC_NAME, "entity": CLINIC_NAME,
            "node": NODE_NAME, "role": "terminal", "db_version": DB_VERSION,
            "hold_ratio": HOLD_RATIO, "holds": holds[:200],
            "pending_updates": pending, "accepted": sorted(_accepted)}


@app.post("/v3/sync/accepted")
def v3_sync_accepted(req: AcceptSyncRequest):
    sync_requests.labels(entity=CLINIC_NAME).inc()
    for i in req.ids:
        _accepted.add(i)
    return {"ok": True, "entity": CLINIC_NAME, "accepted": len(req.ids),
            "total": len(_accepted)}


@app.get("/v3/sync/chunk/{item_id}")
def v3_sync_chunk(item_id: str):
    """Serve any db item (deterministic payload) as a P2P data peer."""
    blob = v3.chunk_blob(item_id, int(os.environ.get("CHUNK_KB", "4")) * 1024)
    meta = v3.item_meta(item_id, blob)
    return {"id": item_id, "size": meta["size"], "hash": meta["hash"],
            "blob": blob, "peer": CLINIC_NAME}


# ====================== v3.2 local execution endpoints ======================
class LocalExecuteRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    task_id: str
    kind: str = "compute"
    source: Optional[str] = None
    params: Dict = {}
    input: Dict = {}
    degraded: bool = False
    degrade_reason: Optional[str] = None
    mode_requested: str = "local"
    is_async: bool = Field(default=False, alias="async")


class LocalDiagnosisRequest(BaseModel):
    task_id: str
    input: Dict = {}
    params: Dict = {}
    source: Optional[str] = None
    degraded: bool = False
    degrade_reason: Optional[str] = None
    mode_requested: str = "local"


def _run_local(kind: str, req) -> dict:
    try:
        res = lx.run_local_request(
            kind, req.task_id, getattr(req, "params", {}) or {},
            getattr(req, "input", {}) or {}, source=req.source,
            degraded=req.degraded, degrade_reason=req.degrade_reason,
            mode_requested=req.mode_requested,
            is_async=getattr(req, "is_async", False),
            store=local_store, queue=local_queue, runner=local_runner)
    except lx.QueueFull as e:
        raise HTTPException(status_code=429, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    local_exec_requests.labels(entity=CLINIC_NAME, kind=kind).inc()
    ms = ((res.get("metrics") or {}).get("pipeline_total_ms") or 0) / 1000.0
    if ms:
        local_exec_latency.labels(kind=kind).observe(ms)
    return res


@app.post("/local/execute")
def local_execute(req: LocalExecuteRequest):
    """Local execution / degraded execution entry point (see 调度模式与降级-设计方案.md §4)."""
    return _run_local(req.kind, req)


@app.post("/local/diagnosis")
def local_diagnosis(req: LocalDiagnosisRequest):
    """Clinic-side diagnosis: capability=forward - this pod orchestrates, the
    nearest hospital executes the full model in place."""
    return _run_local("diagnosis", req)


@app.get("/local/result/{task_id}")
def local_result(task_id: str):
    env = local_store.get(task_id)
    if env is not None:
        return env
    state = local_queue.state(task_id)
    if state:
        return {"task_id": task_id, "status": state, "local": True}
    raise HTTPException(status_code=404, detail=f"本地任务 {task_id} 不存在")


@app.get("/local/results")
def local_results(limit: int = 50):
    return local_store.recent(limit=max(1, min(int(limit), 200)))


@app.get("/local/queue")
def local_queue_status():
    return local_queue.status()


@app.get("/local/health")
def local_health():
    return lx.health_payload(local_store, local_queue)


@app.get("/metrics")
def metrics():
    return Response(content=generate_latest(), media_type="text/plain")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8007)
