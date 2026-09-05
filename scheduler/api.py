"""
Unified Inference Scheduler API (FastAPI).
Implements FromGPT.txt API specification:
  POST /schedule/task  - submit task
  GET  /task/result/{id} - query result
Plus health check, metrics, task management endpoints,
test dataset management, and frontend serving.
"""
import os
import json
import time
import threading
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from typing import Optional, Dict, Any, List
from prometheus_client import start_http_server

from .scheduler import InferenceScheduler
from .redis_client import REDIS_AVAILABLE, get_tasks, redis_keys, redis_delete, get_queue_length as redis_queue_len, dequeue_task
from .metrics import (
    inference_queue_length,
    medical_task_running,
    alexnet_task_running,
    clinic_task_running,
    node_available_cpu,
    node_available_gpu,
    requests_total,
    failed_requests,
)
from .task_manager import get_task_stats
from .cluster_api import router as cluster_router

app = FastAPI(title="Inference Scheduler - Multi-Model Edge/Cloud")

# CORS — allow frontend dev server and any origin (configurable)
FRONTEND_ORIGINS = os.getenv(
    "CORS_ORIGINS",
    "http://localhost:5173,http://127.0.0.1:5173,http://localhost:3000"
).split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=FRONTEND_ORIGINS + ["*"],  # fallback wildcard
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# GZip compression for large JSON responses (patient data)
app.add_middleware(GZipMiddleware, minimum_size=1000)

# Start Prometheus metrics server
threading.Thread(
    target=lambda: start_http_server(9000, addr="0.0.0.0"),
    daemon=True
).start()
print("[Scheduler] Metrics server started on port 9000")

# Configuration
MAX_CONCURRENT = int(os.getenv("MAX_CONCURRENT", "4"))
MAX_QUEUE_SIZE = int(os.getenv("MAX_QUEUE_SIZE", "200"))
TEST_DATASET_PATH = os.getenv(
    "TEST_DATASET_PATH",
    str(Path(__file__).resolve().parent.parent / "test" / "test_dataset.json")
)

# Initialize unified scheduler
scheduler = InferenceScheduler(
    max_concurrent=MAX_CONCURRENT,
    max_queue=MAX_QUEUE_SIZE
)

# ---- Test Dataset Cache ----
_test_dataset_cache: Optional[List[dict]] = None
_test_dataset_mtime: float = 0


def _load_test_dataset() -> List[dict]:
    """Load test dataset from JSON file with caching."""
    global _test_dataset_cache, _test_dataset_mtime
    try:
        mtime = os.path.getmtime(TEST_DATASET_PATH)
        if _test_dataset_cache is not None and mtime == _test_dataset_mtime:
            return _test_dataset_cache
        with open(TEST_DATASET_PATH, "r") as f:
            _test_dataset_cache = json.load(f)
        _test_dataset_mtime = mtime
        print(f"[Scheduler] Loaded {len(_test_dataset_cache)} patients from test dataset")
        return _test_dataset_cache
    except FileNotFoundError:
        print(f"[Scheduler] Test dataset not found at {TEST_DATASET_PATH}")
        return []
    except Exception as e:
        print(f"[Scheduler] Error loading test dataset: {e}")
        return []


def _get_patient_by_id(patient_id: str) -> Optional[dict]:
    """Get a single patient from the test dataset by ID."""
    dataset = _load_test_dataset()
    for p in dataset:
        if p.get("patient_id") == patient_id:
            return p
    return None


# ---- Metrics Update Helper ----

# ---- Request Models ----
class ScheduleTaskRequest(BaseModel):
    hospital: str = "hospital-a"
    model: str = "medical"
    priority: int = 5
    input: Dict[str, Any] = {}
    deadline: str = "5s"


class AlexNetImageRequest(BaseModel):
    hospital: str = "hospital-a"
    model: str = "alexnet"
    priority: int = 5
    input: Dict[str, Any]
    deadline: str = "2s"


class SchedulePreprocessedRequest(BaseModel):
    """Submit a medical task using a preprocessed test patient ID."""
    patient_id: str
    hospital: str = "hospital-a"
    model: str = "medical"
    priority: int = 5
    deadline: str = "10s"


class ScheduleClinicRequest(BaseModel):
    """Submit a clinic (pod memory-monitor) task."""
    clinic: str = "clinic-1"
    target_pod: Optional[str] = None
    namespace: str = "default"
    priority: int = 5
    deadline: str = "30s"


# Register cluster management endpoints (/cluster/*)
app.include_router(cluster_router, prefix="/cluster", tags=["cluster"])


# ---- Background Worker Threads ----

def scheduler_worker(worker_id: int):
    """Background worker that polls the Redis priority queue and dispatches tasks.

    Runs as a daemon thread in the same process as the API server.
    I/O-bound work (HTTP calls to inference services) releases the GIL,
    so health checks remain responsive under normal operation. Combined
    with connection pooling, retry logic, and lightweight queue entries,
    this is stable for the cluster's concurrency level (4 workers).
    """
    print(f"[Scheduler Worker {worker_id}] Started (thread)")

    while True:
        try:
            # Poll Redis priority queue for the next task
            task_id = dequeue_task()
            if task_id:
                task_ref = {"id": task_id}
            else:
                time.sleep(0.2)
                continue

            task_id_str = task_ref.get("id", "unknown")
            print(f"[Scheduler Worker {worker_id}] Dispatching {task_id_str}")

            scheduler.dispatch_task(task_ref)
            update_metrics()

        except Exception as e:
            print(f"[Scheduler Worker {worker_id}] Error: {e}")
            import traceback
            traceback.print_exc()
            time.sleep(0.5)


def update_metrics():
    """Update Prometheus gauge metrics."""
    try:
        inference_queue_length.set(redis_queue_len())

        tasks = get_tasks()
        medical_count = sum(1 for t in tasks
                            if t.get("model") == "medical" and t.get("status") == "running")
        alexnet_count = sum(1 for t in tasks
                           if t.get("model") == "alexnet" and t.get("status") == "running")
        clinic_count = sum(1 for t in tasks
                           if t.get("model") == "clinic" and t.get("status") == "running")
        medical_task_running.set(medical_count)
        alexnet_task_running.set(alexnet_count)
        clinic_task_running.set(clinic_count)

        nodes = scheduler.resource_monitor.get_all_nodes()
        for name, info in nodes.items():
            node_available_cpu.labels(node=name).set(info.get("cpu_free", 0))
            node_available_gpu.labels(node=name).set(info.get("gpu_free", 0))
    except Exception:
        pass


# Start worker threads
for i in range(MAX_CONCURRENT):
    t = threading.Thread(target=scheduler_worker, args=(i,), daemon=True, name=f"sched-wkr-{i}")
    t.start()
    print(f"[API] Started scheduler worker thread {i}")


# ---- API Endpoints ----
@app.get("/")
def health():
    return {
        "service": "inference-scheduler",
        "status": "running",
        "redis": REDIS_AVAILABLE,
        "queue_length": scheduler.task_queue.qsize(),
        "active_tasks": len(scheduler.active_tasks),
        "max_concurrent": MAX_CONCURRENT
    }


@app.get("/health")
def full_health_check():
    import requests as req
    status = {"scheduler": "ok", "redis": REDIS_AVAILABLE}

    services = {
        "hospital-a": "http://hospital-a-service:8006/health",
        "hospital-b": "http://hospital-b-service:8006/health",
        "clinic-1": "http://clinic-1-service:8007/health",
        "clinic-2": "http://clinic-2-service:8007/health",
        "medical_server": "http://medical-server-service:9001/health",
        "part2": "http://part2-service:8002/health",
    }

    for name, url in services.items():
        try:
            r = req.get(url, timeout=1)  # fast fail if service not available
            status[name] = r.json() if r.status_code == 200 else "error"
        except Exception as e:
            status[name] = "unavailable" if "Connection" in str(e) else str(e)[:60]

    return status


@app.post("/schedule/task")
def schedule_task(req: ScheduleTaskRequest):
    """Submit an inference task. FromGPT.txt POST /schedule/task."""
    requests_total.labels(model=req.model).inc()

    try:
        result = scheduler.submit_task(
            hospital=req.hospital,
            model=req.model,
            priority=req.priority,
            input_data=req.input,
            deadline=req.deadline
        )
        update_metrics()
        return result
    except RuntimeError as e:
        failed_requests.labels(model=req.model).inc()
        raise HTTPException(status_code=429, detail=str(e))
    except Exception as e:
        failed_requests.labels(model=req.model).inc()
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/task/result/{task_id}")
def get_task_result(task_id: str):
    """Query inference result. FromGPT.txt GET /task/result/{id}."""
    result = scheduler.get_task_result(task_id)

    if result.get("status") == "not_found":
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

    return result


# ---- Task Management Endpoints (AlexNet-K8s compatible) ----
@app.get("/tasks")
def list_tasks():
    return get_tasks()


@app.get("/tasks/running")
def list_running():
    tasks = get_tasks()
    return [t for t in tasks if t.get("status") == "running"]


@app.get("/tasks/stats")
def task_stats():
    stats = get_task_stats()
    stats["queue_length"] = scheduler.task_queue.qsize()
    return stats


@app.get("/tasks/{task_id}")
def get_task_detail(task_id: str):
    from .redis_client import get_task as redis_get_task
    data = redis_get_task(task_id)
    if data:
        return json.loads(data)
    raise HTTPException(status_code=404, detail=f"Task {task_id} not found")


@app.delete("/tasks/{task_id}")
def delete_task_by_id(task_id: str):
    redis_delete(f"inference:{task_id}")
    redis_delete(f"inference:result:{task_id}")
    scheduler.task_queue.remove(task_id)
    return {"message": f"Task {task_id} deleted"}


@app.delete("/tasks/clear")
def clear_tasks():
    keys = redis_keys("inference:*")
    count = 0
    for key in keys:
        redis_delete(key)
        count += 1
    # Also clear any stuck active tasks and drain the queue
    scheduler.active_tasks.clear()
    while not scheduler.task_queue.empty():
        try:
            scheduler.task_queue.get()
        except Exception:
            break
    return {"count": count}


# ---- Resource Monitoring Endpoints ----
@app.get("/nodes")
def get_nodes_status():
    """Get all nodes with resource availability scores."""
    nodes = scheduler.resource_monitor.get_all_nodes()
    result = {}
    for name, info in nodes.items():
        result[name] = {
            **info,
            "score": scheduler.resource_monitor.get_node_score(name)
        }
    return {"nodes": result}


@app.get("/nodes/edge")
def get_edge_nodes():
    return {"edge_nodes": scheduler.resource_monitor.get_edge_nodes()}


@app.get("/nodes/cloud")
def get_cloud_nodes():
    return {"cloud_nodes": scheduler.resource_monitor.get_cloud_nodes()}


# ---- Legacy Compatible Endpoints (AlexNet-K8s format) ----
@app.post("/predict/image")
async def predict_alexnet_image(req: AlexNetImageRequest):
    """Legacy-compatible AlexNet image prediction endpoint."""
    requests_total.labels(model="alexnet").inc()

    try:
        result = scheduler.submit_task(
            hospital=req.hospital,
            model="alexnet",
            priority=req.priority,
            input_data=req.input,
            deadline=req.deadline
        )
        update_metrics()
        return result
    except RuntimeError as e:
        failed_requests.labels(model="alexnet").inc()
        raise HTTPException(status_code=429, detail=str(e))
    except Exception as e:
        failed_requests.labels(model="alexnet").inc()
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/predict/result/{task_id}")
def get_predict_result(task_id: str):
    """Legacy-compatible result query (maps to /task/result/{id})."""
    return get_task_result(task_id)


# ---- Test Dataset Endpoints ----
@app.get("/test/patients")
def list_test_patients():
    """Return list of test patients (metadata only, no tensor data)."""
    dataset = _load_test_dataset()
    if not dataset:
        raise HTTPException(status_code=503, detail="Test dataset not available")
    return [
        {
            "patient_id": p["patient_id"],
            "bpCR": p.get("bpCR"),
            "hospital": p.get("hospital"),
        }
        for p in dataset
    ]


@app.get("/test/patient/{patient_id}")
def get_test_patient(patient_id: str):
    """Return preprocessed input data for a single test patient."""
    patient = _get_patient_by_id(patient_id)
    if not patient:
        raise HTTPException(status_code=404, detail=f"Patient {patient_id} not found")
    return patient


@app.get("/test/dataset-info")
def get_dataset_info():
    """Return metadata about the available test dataset."""
    dataset = _load_test_dataset()
    if not dataset:
        return {"available": False, "count": 0, "message": "No test dataset loaded"}
    sample = dataset[0].get("input", {})
    dce = sample.get("dce_image", [[[]]])
    return {
        "available": True,
        "count": len(dataset),
        "patient_ids": [p["patient_id"] for p in dataset],
        "input_shape": {
            "dce_image": [len(dce), len(dce[0]) if dce else 0, len(dce[0][0]) if dce and dce[0] else 0],
            "dwi_image": [len(dce), len(dce[0]) if dce else 0, len(dce[0][0]) if dce and dce[0] else 0],
        },
    }


@app.post("/schedule/preprocessed")
def schedule_preprocessed_task(req: SchedulePreprocessedRequest):
    """Submit a medical inference task using a preprocessed test patient.

    Looks up the patient's preprocessed data and submits it to the scheduler
    with the correct input format for the medical worker.
    """
    patient = _get_patient_by_id(req.patient_id)
    if not patient:
        raise HTTPException(status_code=404,
                            detail=f"Patient {req.patient_id} not found in test dataset")

    input_data = patient.get("input", {})
    if not input_data:
        raise HTTPException(status_code=400,
                            detail=f"No input data for patient {req.patient_id}")

    # Add patient_ids field expected by the medical worker
    input_data["patient_ids"] = [req.patient_id]

    requests_total.labels(model=req.model).inc()

    try:
        result = scheduler.submit_task(
            hospital=req.hospital,
            model=req.model,
            priority=req.priority,
            input_data=input_data,
            deadline=req.deadline,
        )
        update_metrics()
        return result
    except RuntimeError as e:
        failed_requests.labels(model=req.model).inc()
        raise HTTPException(status_code=429, detail=str(e))
    except Exception as e:
        failed_requests.labels(model=req.model).inc()
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/schedule/clinic")
def schedule_clinic_task(req: ScheduleClinicRequest):
    """Submit a clinic task: query a pod's memory usage.

    clinic   - which clinic pod runs the query (clinic-1 / clinic-2)
    target_pod - optional explicit pod; default = the clinic pod itself
    """
    requests_total.labels(model="clinic").inc()

    input_data = {
        "target_pod": req.target_pod,
        "namespace": req.namespace,
    }

    try:
        result = scheduler.submit_task(
            hospital=req.clinic,
            model="clinic",
            priority=req.priority,
            input_data=input_data,
            deadline=req.deadline,
        )
        update_metrics()
        return result
    except RuntimeError as e:
        failed_requests.labels(model="clinic").inc()
        raise HTTPException(status_code=429, detail=str(e))
    except Exception as e:
        failed_requests.labels(model="clinic").inc()
        raise HTTPException(status_code=500, detail=str(e))


# ---- Frontend Static Files (Production) ----
FRONTEND_DIST = os.getenv("FRONTEND_DIST_PATH",
                          str(Path(__file__).resolve().parent.parent / "frontend" / "dist"))
FRONTEND_ROOT = os.getenv("FRONTEND_ROOT_PATH",
                          str(Path(__file__).resolve().parent.parent / "frontend"))

# Serve the frontend — prefer dist/ if built, otherwise serve the standalone index.html
if os.path.isdir(FRONTEND_DIST):
    app.mount("/app", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")
    print(f"[Scheduler] Serving frontend from {FRONTEND_DIST}")
elif os.path.isfile(os.path.join(FRONTEND_ROOT, "index.html")):
    # Mount the frontend directory as static, with index.html as default
    app.mount("/app", StaticFiles(directory=FRONTEND_ROOT, html=True), name="frontend")
    print(f"[Scheduler] Serving frontend from {FRONTEND_ROOT}")
else:
    print(f"[Scheduler] No frontend found at {FRONTEND_DIST} or {FRONTEND_ROOT}")

# Also redirect root to frontend
from fastapi.responses import RedirectResponse

@app.get("/ui")
def frontend_redirect():
    """Redirect to the frontend app."""
    return RedirectResponse(url="/app/index.html")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
