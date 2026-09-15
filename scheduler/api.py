"""
v3.0 data-center scheduler API (FastAPI).

Submit endpoints (initiated by hospital / clinic):
  POST /schedule/diagnosis   medical DoubleTower (hospital worker -> dc server)
  POST /schedule/compute     multi-pod collaborative compute
  POST /schedule/sync        patient-db P2P cloud sync + backup
  POST /schedule/routine     ephemeral routine Jobs created & deleted
Plus task management, test dataset, cluster editor (/cluster/*) and the
React frontend under /app.
"""
import json
import os
import threading
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from typing import Dict, List, Optional

from prometheus_client import start_http_server

from .scheduler import InferenceScheduler
from .redis_client import (
    REDIS_AVAILABLE, get_tasks, redis_keys, redis_delete, dequeue_task,
    get_queue_length as redis_queue_len,
)
from .metrics import (
    inference_queue_length, task_running, node_available_cpu,
    node_available_gpu, requests_total, failed_requests,
)
from .task_manager import get_task_stats
from .cluster_api import router as cluster_router

app = FastAPI(title="Inference Scheduler v3.0 - 云/边/端 四类任务")

FRONTEND_ORIGINS = os.getenv(
    "CORS_ORIGINS",
    "http://localhost:5173,http://127.0.0.1:5173,http://localhost:3000").split(",")
app.add_middleware(CORSMiddleware, allow_origins=FRONTEND_ORIGINS + ["*"],
                   allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
app.add_middleware(GZipMiddleware, minimum_size=1000)

threading.Thread(target=lambda: start_http_server(9000, addr="0.0.0.0"),
                 daemon=True).start()

MAX_CONCURRENT = int(os.getenv("MAX_CONCURRENT", "4"))
MAX_QUEUE_SIZE = int(os.getenv("MAX_QUEUE_SIZE", "200"))
TEST_DATASET_PATH = os.getenv(
    "TEST_DATASET_PATH",
    str(Path(__file__).resolve().parent.parent / "test" / "test_dataset.json"))

scheduler = InferenceScheduler(max_concurrent=MAX_CONCURRENT,
                               max_queue=MAX_QUEUE_SIZE)

# ------------------------------ test dataset -------------------------------
_test_dataset_cache: Optional[List[dict]] = None
_test_dataset_mtime: float = 0


def _load_test_dataset() -> List[dict]:
    global _test_dataset_cache, _test_dataset_mtime
    try:
        mtime = os.path.getmtime(TEST_DATASET_PATH)
        if _test_dataset_cache is not None and mtime == _test_dataset_mtime:
            return _test_dataset_cache
        with open(TEST_DATASET_PATH) as f:
            _test_dataset_cache = json.load(f)
        _test_dataset_mtime = mtime
        return _test_dataset_cache
    except Exception:
        return []


def _get_patient_by_id(patient_id: str) -> Optional[dict]:
    for p in _load_test_dataset():
        if p.get("patient_id") == patient_id:
            return p
    return None


# ------------------------------ request models ------------------------------
class BaseSchedule(BaseModel):
    source: str = "hospital-a"
    priority: int = 5
    deadline: str = "120s"


class DiagnosisRequest(BaseSchedule):
    patient_id: Optional[str] = None
    target_hospital: Optional[str] = None   # clinic 发起时可选转诊目标
    input: Dict = {}


class ComputeRequest(BaseSchedule):
    instruments: int = 4
    rows: int = 256
    intensity: int = 40
    partition_count: int = 3


class SyncRequest(BaseSchedule):
    bandwidth_mbps: float = 20.0
    concurrency: int = 4
    chunk_kb: int = 4


class RoutineRequest(BaseSchedule):
    jobs: int = 2
    rows: int = 128
    intensity: int = 30


# ------------------------------ background workers --------------------------
def scheduler_worker(worker_id: int):
    while True:
        try:
            task_id = dequeue_task()
            if not task_id:
                time.sleep(0.2)
                continue
            scheduler.dispatch_task({"id": task_id})
            update_metrics()
        except Exception:
            time.sleep(0.5)


def update_metrics():
    try:
        inference_queue_length.set(redis_queue_len())
        tasks = get_tasks()
        counts: Dict[str, int] = {}
        for t in tasks:
            if t.get("status") == "running":
                m = t.get("model", "?")
                counts[m] = counts.get(m, 0) + 1
        for model in ("diagnosis", "compute", "sync", "routine"):
            task_running.labels(model=model).set(counts.get(model, 0))
        try:
            from .node_usage import node_loads
            for name, info in node_loads().items():
                node_available_cpu.labels(node=name).set(info.get("free", 0))
        except Exception:
            pass
    except Exception:
        pass


for i in range(MAX_CONCURRENT):
    t = threading.Thread(target=scheduler_worker, args=(i,), daemon=True,
                         name=f"sched-wkr-{i}")
    t.start()

app.include_router(cluster_router, prefix="/cluster", tags=["cluster"])


# ------------------------------ helpers ------------------------------------
def _do_submit(model: str, req: BaseSchedule, input_data: dict):
    requests_total.labels(model=model).inc()
    try:
        result = scheduler.submit_task(model=model, source=req.source,
                                       priority=req.priority,
                                       input_data=input_data,
                                       deadline=req.deadline)
        update_metrics()
        return result
    except RuntimeError as e:
        failed_requests.labels(model=model).inc()
        raise HTTPException(status_code=429, detail=str(e))
    except Exception as e:
        failed_requests.labels(model=model).inc()
        raise HTTPException(status_code=500, detail=str(e))


# ------------------------------ schedule endpoints --------------------------
@app.post("/schedule/diagnosis")
def schedule_diagnosis(req: DiagnosisRequest):
    """诊断：hospital 发起直接执行；clinic 发起会转诊给 hospital。"""
    inp = dict(req.input) if isinstance(req.input, dict) else {}
    if req.patient_id:
        patient = _get_patient_by_id(req.patient_id)
        if not patient:
            raise HTTPException(status_code=404,
                                detail=f"患者 {req.patient_id} 不在测试数据集")
        inp = dict(patient.get("input", {}))
        inp["patient_ids"] = [req.patient_id]
    inp.setdefault("target_hospital", req.target_hospital)
    return _do_submit("diagnosis", req, inp)


@app.post("/schedule/compute")
def schedule_compute(req: ComputeRequest):
    return _do_submit("compute", req, {
        "instruments": req.instruments, "rows": req.rows,
        "intensity": req.intensity, "partition_count": req.partition_count,
    })


@app.post("/schedule/sync")
def schedule_sync(req: SyncRequest):
    return _do_submit("sync", req, {
        "bandwidth_mbps": req.bandwidth_mbps, "concurrency": req.concurrency,
        "chunk_kb": req.chunk_kb,
    })


@app.post("/schedule/routine")
def schedule_routine(req: RoutineRequest):
    return _do_submit("routine", req, {
        "jobs": req.jobs, "rows": req.rows, "intensity": req.intensity,
    })


# ------------------------------ result / tasks ------------------------------
@app.get("/task/result/{task_id}")
def get_task_result(task_id: str):
    result = scheduler.get_task_result(task_id)
    if result.get("status") == "not_found":
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
    return result


@app.get("/tasks")
def list_tasks():
    return get_tasks()


@app.get("/tasks/running")
def list_running():
    return [t for t in get_tasks() if t.get("status") == "running"]


@app.get("/tasks/stats")
def task_stats():
    stats = get_task_stats()
    stats["queue_length"] = redis_queue_len()
    return stats



# ---- light task feed for live dashboards (small payload) ----
def _slim_task(t: dict) -> dict:
    """Trim a stored task to what live dashboards need (tiny JSON)."""
    out = {k: t.get(k) for k in (
        "id", "model", "source", "priority", "status", "stage", "node",
        "progress", "start_time", "end_time", "duration_ms", "error")}
    res = t.get("result") or {}
    if not res:
        return out
    rd = res.get("result_detail") or {}
    slim: dict = {
        "initiator": res.get("initiator"),
        "forwarded_to": res.get("forwarded_to"),
        "stages": [{k: s.get(k) for k in ("name", "actor", "node", "ms")}
                   for s in (res.get("stages") or [])],
        "metrics": {k: v for k, v in (res.get("metrics") or {}).items()
                    if isinstance(v, (int, float))},
    }
    kind = t.get("model")
    if kind == "compute":
        slim["produced"] = res.get("produced")
        slim["partitions"] = [{k: p.get(k) for k in (
            "partition", "actor", "node", "ms", "cpu_ms", "bytes", "rows",
            "failed", "error")} for p in (res.get("partitions") or [])[:8]]
        slim["result_detail"] = {k: rd.get(k) for k in (
            "partitions_ok", "total_bytes", "aggregate_cpu_ms", "checksums")}
    elif kind == "sync":
        slim["result_detail"] = {k: rd.get(k) for k in (
            "db_version", "cloud_items", "missing", "pulled", "failed_chunks",
            "bytes_pulled", "pull_ms", "upload_ms", "backup_ms", "uploaded",
            "cloud_total", "peers", "backup")}
        slim["result_detail"]["chunks"] = [{k: c.get(k) for k in (
            "id", "ok", "peer", "size", "ms")}
            for c in (rd.get("chunks") or [])[:12]]
    elif kind == "routine":
        slim["result_detail"] = {
            "succeeded": rd.get("succeeded"),
            "jobs": [{k: j.get(k) for k in (
                "job", "node", "state", "deleted", "wall_ms", "pod")}
                for j in (rd.get("jobs") or [])[:8]],
        }
    else:  # diagnosis
        slim["result_detail"] = {k: rd.get(k) for k in (
            "bpCR_probability", "worker_latency_ms", "server_latency_ms")}
        slim["result_detail"]["predictions"] = (rd.get("predictions") or [])[:3]
    out["result"] = slim
    return out


@app.get("/tasks/recent")
def list_tasks_recent(limit: int = 20):
    """最新任务的精简列表（用于实时架构/轨迹轮询，负载远小于 /tasks）。"""
    tasks = get_tasks(limit=max(1, min(int(limit), 50)))
    return [_slim_task(t) for t in tasks]


@app.get("/tasks/{task_id}")
def get_task_detail(task_id: str):
    from .redis_client import get_task
    data = get_task(task_id)
    if data:
        return json.loads(data)
    raise HTTPException(status_code=404, detail=f"Task {task_id} not found")


@app.delete("/tasks/{task_id}")
def delete_task_by_id(task_id: str):
    redis_delete(f"inference:{task_id}")
    redis_delete(f"inference:result:{task_id}")
    return {"message": f"Task {task_id} deleted"}


@app.delete("/tasks/clear")
def clear_tasks():
    keys = redis_keys("inference:*")
    count = 0
    for key in keys:
        redis_delete(key)
        count += 1
    return {"count": count}



# ---- static caching: hashed assets are immutable, index must revalidate ----
@app.middleware("http")
async def static_cache_headers(request, call_next):
    response = await call_next(request)
    path = request.url.path
    if path.startswith("/app/assets/"):
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    elif path.startswith("/app/"):
        response.headers.setdefault("Cache-Control", "no-cache")
    return response


# ------------------------------ health -------------------------------------
@app.get("/")
def health():
    return {"service": "inference-scheduler-v3", "status": "running",
            "redis": REDIS_AVAILABLE,
            "queue_length": redis_queue_len(),
            "active_tasks": len(scheduler.active_tasks)}


@app.get("/health")
def full_health_check():
    import requests as req
    status = {"scheduler": "ok", "redis": REDIS_AVAILABLE}
    services = {
        "hospital-a": "http://hospital-a-service:8006/health",
        "hospital-b": "http://hospital-b-service:8006/health",
        "clinic-1": "http://clinic-1-service:8007/health",
        "clinic-2": "http://clinic-2-service:8007/health",
        "datacenter(patient-db)": "http://dc-services:8010/health",
        "medical_server": "http://medical-server-service:9001/health",
    }
    for name, url in services.items():
        try:
            r = req.get(url, timeout=1.5)
            status[name] = r.json() if r.status_code == 200 else "error"
        except Exception as e:
            status[name] = ("unavailable" if "Connection" in str(e)
                            else str(e)[:60])
    return status


# ------------------------------ test dataset --------------------------------
@app.get("/test/patients")
def list_test_patients():
    dataset = _load_test_dataset()
    if not dataset:
        raise HTTPException(status_code=503, detail="Test dataset not available")
    return [{"patient_id": p["patient_id"], "bpCR": p.get("bpCR"),
             "hospital": p.get("hospital")} for p in dataset]


@app.get("/test/patient/{patient_id}")
def get_test_patient(patient_id: str):
    patient = _get_patient_by_id(patient_id)
    if not patient:
        raise HTTPException(status_code=404,
                            detail=f"Patient {patient_id} not found")
    return patient


@app.get("/test/dataset-info")
def get_dataset_info():
    dataset = _load_test_dataset()
    if not dataset:
        return {"available": False, "count": 0}
    return {"available": True, "count": len(dataset),
            "patient_ids": [p["patient_id"] for p in dataset]}


# ------------------------------ frontend ------------------------------------
FRONTEND_DIST = os.getenv("FRONTEND_DIST_PATH",
                          str(Path(__file__).resolve().parent.parent / "frontend" / "dist"))
FRONTEND_ROOT = os.getenv("FRONTEND_ROOT_PATH",
                          str(Path(__file__).resolve().parent.parent / "frontend"))

if os.path.isdir(FRONTEND_DIST):
    app.mount("/app", StaticFiles(directory=FRONTEND_DIST, html=True),
              name="frontend")
elif os.path.isdir(FRONTEND_ROOT):
    app.mount("/app", StaticFiles(directory=FRONTEND_ROOT, html=True),
              name="frontend")


@app.get("/ui")
def frontend_redirect():
    return RedirectResponse(url="/app/")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
