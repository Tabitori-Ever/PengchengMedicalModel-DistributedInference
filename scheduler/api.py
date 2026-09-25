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

from .scheduler import InferenceScheduler, UnsupportedLocalMode
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

# v3.3: warm the node-load cache in a background thread at startup so no task
# ever pays the Kubernetes API round trip on its critical path.
try:
    from .node_usage import node_loads as _warm_node_loads
    _warm_node_loads()
except Exception:
    pass

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
    # v3.2 execution-mode selection (see 调度模式与降级-设计方案.md):
    #   collaborative | local | auto
    mode: str = "collaborative"
    force_degraded: bool = False


class DiagnosisRequest(BaseSchedule):
    patient_id: Optional[str] = None
    # v3.3 批量诊断：患者列表（按 id 从测试数据集解析）或直接给 batch 输入
    patient_ids: Optional[List[str]] = None
    # v3.5 控制面/数据面分离：deliver="direct" 时调度器只返回执行计划，
    # 输入数据由调用方直投执行者（数据面绕开控制面）
    deliver: Optional[str] = None
    target_hospital: Optional[str] = None   # clinic 发起时可选转诊目标
    input: Dict = {}


class ComputeRequest(BaseSchedule):
    instruments: int = 4
    rows: int = 256
    intensity: int = 40
    partition_count: int = 3
    seed: Optional[int] = None              # 固定种子 → 本地/协同结果可比对


class SyncRequest(BaseSchedule):
    bandwidth_mbps: float = 20.0
    concurrency: int = 4
    chunk_kb: int = 4


class RoutineRequest(BaseSchedule):
    jobs: int = 2
    rows: int = 128
    intensity: int = 30
    seed: Optional[int] = None
    # v3.3 日常任务的两种执行器：
    #   warm（默认）把作业并发派发到空闲节点上已运行的 Pod（"按节点空闲调度"）
    #   job        为每个作业创建一次性 Kubernetes Job（serverless 式，有冷启动）
    executor: Optional[str] = None


# ------------------------------ background workers --------------------------
def scheduler_worker(worker_id: int):
    while True:
        try:
            task_id = dequeue_task()
            if not task_id:
                # v3.3: shorter poll so the async queue adds ~25ms average
                # latency instead of ~100ms (only the collaborative path has a
                # queue; local execution starts immediately, so this is part of
                # the honest architecture comparison).
                time.sleep(0.05)
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
                                       deadline=req.deadline,
                                       mode=getattr(req, "mode", "collaborative"),
                                       force_degraded=getattr(req, "force_degraded", False))
        update_metrics()
        return result
    except UnsupportedLocalMode as e:
        # 诊断没有本地执行策略：明确 400，而不是队列满的 429
        failed_requests.labels(model=model).inc()
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        failed_requests.labels(model=model).inc()
        raise HTTPException(status_code=429, detail=str(e))
    except Exception as e:
        failed_requests.labels(model=model).inc()
        raise HTTPException(status_code=500, detail=str(e))


# ------------------------------ schedule endpoints --------------------------
def _batch_from_ids(ids: List[str]):
    """Resolve patient ids into one batch payload (list of per-patient inputs)."""
    batch = []
    for pid in ids:
        patient = _get_patient_by_id(pid)
        if not patient:
            raise HTTPException(status_code=404,
                                detail=f"患者 {pid} 不在测试数据集")
        item = dict(patient.get("input", {}))
        item["patient_id"] = pid
        item.setdefault("patient_ids", [pid])
        batch.append(item)
    return batch


@app.post("/schedule/diagnosis")
def schedule_diagnosis(req: DiagnosisRequest):
    """诊断：hospital 发起直接执行；clinic 发起会转诊给 hospital。

    给 `patient_ids` 即批量诊断（协同侧走边端前端 / 云侧后端两段流水线）。"""
    inp = dict(req.input) if isinstance(req.input, dict) else {}
    if (req.deliver or "").lower() == "direct" and req.patient_ids \
            and (req.mode or "") != "local":
        try:
            return scheduler.plan_diagnosis(
                source=req.source, patient_ids=list(req.patient_ids),
                priority=req.priority, deadline=req.deadline,
                mode=req.mode or "collaborative")
        except RuntimeError as e:
            raise HTTPException(status_code=400, detail=str(e))
    if req.patient_ids:
        inp["batch"] = _batch_from_ids(list(req.patient_ids))
        inp["patient_ids"] = list(req.patient_ids)
        inp["target_hospital"] = req.target_hospital
        return _do_submit("diagnosis", req, inp)
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
    data = {"instruments": req.instruments, "rows": req.rows,
            "intensity": req.intensity, "partition_count": req.partition_count}
    if req.seed is not None:
        data["seed"] = req.seed
    return _do_submit("compute", req, data)


@app.post("/schedule/sync")
def schedule_sync(req: SyncRequest):
    return _do_submit("sync", req, {
        "bandwidth_mbps": req.bandwidth_mbps, "concurrency": req.concurrency,
        "chunk_kb": req.chunk_kb,
    })


@app.post("/schedule/routine")
def schedule_routine(req: RoutineRequest):
    data = {"jobs": req.jobs, "rows": req.rows, "intensity": req.intensity}
    if req.seed is not None:
        data["seed"] = req.seed
    if req.executor:
        data["executor"] = req.executor
    return _do_submit("routine", req, data)


class DirectReportRequest(BaseModel):
    results: List[Dict] = []
    client_total_ms: Optional[float] = None


@app.post("/task/{task_id}/report")
def report_direct_result(task_id: str, req: DirectReportRequest):
    """直投模式的记账入口：调用方把各执行者的结果回传，调度器归档并统计。"""
    out = scheduler.report_diagnosis(task_id, req.model_dump())
    if out.get("status") == "not_found":
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
    return out


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
        "progress", "start_time", "end_time", "duration_ms", "error",
        "mode", "force_degraded")}
    res = t.get("result") or {}
    if not res:
        return out
    rd = res.get("result_detail") or {}
    slim: dict = {
        # v3.2 execution-mode bookkeeping (collaborative / local / degraded)
        "mode": res.get("mode"),
        "mode_requested": res.get("mode_requested"),
        "degraded": res.get("degraded"),
        "degrade_reason": res.get("degrade_reason"),
        "orchestrator": res.get("orchestrator"),
        "executor": res.get("executor"),
        "initiator": res.get("initiator"),
        "forwarded_to": res.get("forwarded_to"),
        "stages": [{k: s.get(k) for k in (
            "name", "actor", "node", "ms", "detail", "compute_ms", "network_ms")}
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



# ---------------------- 数据集真实目录（Database/） -------------------------
# 用户平台「上传」弹窗读的是真实文件夹，而不是前端打包的虚拟目录：
#   GET /files/tree            列出目录树（子目录 = 任务类型，文件 = 一份数据）
#   GET /files/get?path=...    读取单个文件内容（JSON 自描述：kind/name/description/params）
# 目录可用 DATABASE_DIR 覆盖；容器内默认为 /app/Database。
DATABASE_DIR = Path(os.getenv("DATABASE_DIR", "/app/Database"))
_KIND_BY_DIR = {
    "计算": "compute", "compute": "compute",
    "通信": "sync", "sync": "sync",
    "日常": "routine", "routine": "routine",
    "诊断": "diagnosis", "diagnosis": "diagnosis",
}
_KIND_ORDER = ["compute", "sync", "routine", "diagnosis"]


def _safe_dataset_path(rel: str) -> Optional[Path]:
    """把请求里的相对路径安全地解析到 DATABASE_DIR 之内（拒绝越界与绝对路径）。"""
    raw = str(rel or "").strip().replace("\\", "/")
    if not raw or raw.startswith("/") or ".." in raw.split("/"):
        return None
    target = (DATABASE_DIR / raw).resolve()
    root = DATABASE_DIR.resolve()
    try:
        target.relative_to(root)
    except ValueError:
        return None
    return target


def _dir_kind(name: str) -> Optional[str]:
    return _KIND_BY_DIR.get(name) or _KIND_BY_DIR.get(name.lower())


def _file_entry(folder: str, path: Path) -> Optional[dict]:
    """一个数据文件的轻量元信息（说明优先取文件自带的 description）。"""
    kind = _dir_kind(folder)
    if kind is None or path.suffix.lower() != ".json":
        return None
    desc = ""
    name = path.stem
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            desc = str(data.get("description") or "")
            name = str(data.get("name") or name)
            if data.get("kind") and str(data["kind"]) != kind:
                return None
    except Exception:
        desc = ""
    if not desc:
        desc = "JSON 数据文件"
    return {"path": f"{folder}/{path.name}", "name": name,
            "description": desc, "bytes": path.stat().st_size}


@app.get("/files/tree")
def files_tree():
    """真实数据集目录树（Database/ 下的子目录与 JSON 文件）。"""
    root = DATABASE_DIR
    if not root.is_dir():
        raise HTTPException(status_code=503,
                            detail=f"数据集目录不存在：{root}（可设置 DATABASE_DIR）")
    folders = []
    for d in sorted([x for x in root.iterdir() if x.is_dir()], key=lambda x: x.name):
        kind = _dir_kind(d.name)
        if kind is None:
            continue
        files = []
        for f in sorted(d.iterdir(), key=lambda x: x.name):
            if not f.is_file():
                continue
            entry = _file_entry(d.name, f)
            if entry:
                files.append(entry)
        folders.append({"id": kind, "name": d.name, "files": files})
    folders.sort(key=lambda f: _KIND_ORDER.index(f["id"]) if f["id"] in _KIND_ORDER else 99)
    return {"root": root.name or "Database", "path": str(root), "folders": folders}


@app.get("/files/get")
def files_get(path: str):
    """读取真实目录里的一个数据文件（返回其自描述 JSON）。"""
    target = _safe_dataset_path(path)
    if target is None:
        raise HTTPException(status_code=400, detail="非法路径")
    if not target.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"无法解析：{str(e)[:120]}")
    if not isinstance(data, dict):
        raise HTTPException(status_code=422, detail="文件内容不是 JSON 对象")
    return data


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
