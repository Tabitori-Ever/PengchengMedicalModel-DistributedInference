"""
Hospital Pod Service (v3.0 - Edge).

Roles:
  1. medical worker front-end (DoubleTower) for the diagnosis task
     (/medical/infer) - raw patient data never leaves the hospital pod.
  2. v3 shared worker: participates in multi-pod compute partitions and
     patient-db P2P sync as a data peer.
No AlexNet / part1 functionality in v3.0.
"""
import base64
import json
import os
import struct
import sys
import threading
import time
import urllib.request
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn
from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field
from typing import Any, Dict, List, Optional
from prometheus_client import Counter, Histogram, Gauge, generate_latest

# ---- import paths: container (/app) + repo (local dev) ----
# DoubleTower 代码随镜像放在 /app/models_my.py；本地开发时从 medical-server/ 复用
# （两份逐字节相同，medical-worker/ 已在 v3.6 清理中移除）。
sys.path.insert(0, "/app")
_HOME = Path(__file__).resolve().parent
for _c in (str(_HOME), str(_HOME.parent / "medical-server"), str(_HOME.parent)):
    if _c not in sys.path and Path(_c).is_dir():
        sys.path.insert(0, _c)

from models_my import DoubleTower            # noqa: E402
from common import v3_common as v3           # noqa: E402
from common import local_exec as lx          # noqa: E402

HOSPITAL_NAME = os.environ.get("HOSPITAL_NAME", "hospital-a")
NODE_NAME = os.environ.get("NODE_NAME", "unknown")
MODEL_PATH = os.environ.get("MODEL_PATH", "/app/weights/best_model_fold1.pth")
MEDICAL_SERVER_URL = os.environ.get("MEDICAL_SERVER_URL",
                                    "http://medical-server-service:9001").rstrip("/")
DEVICE = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")

DB_VERSION = os.environ.get("DB_VERSION", "v3-db-1")
DB_ITEM_COUNT = int(os.environ.get("DB_ITEM_COUNT", "64"))
HOLD_RATIO = float(os.environ.get("HOLD_RATIO", "0.7"))

app = FastAPI(title=f"Hospital Pod ({HOSPITAL_NAME}) v3.0")

# ========================== medical front-end ===============================
class DoubleTowerWorkerFront(nn.Module):
    """边端前端。

    v3.4：切分点从 conv1+maxpool 之后**下移到 layer3 之后**。
    原因是数据体积：conv1+maxpool 之后的双视图中间特征是 1.61MB（输入的 4 倍！），
    而 layer3 之后只有 0.40MB（等于输入大小）——切得越浅，跨节点搬得越多。
    同时端侧承担的算力份额从 8% 升到 38%，使两段更均衡（流水线的前提）。
    """

    def __init__(self, full_model: DoubleTower):
        super().__init__()
        dce = full_model.image_processor.encoder_dce.model
        dwi = full_model.image_processor.encoder_dwi.model
        self.dce_front = nn.Sequential(dce.conv1, dce.bn1, dce.relu, dce.maxpool,
                                       dce.layer1, dce.layer2, dce.layer3)
        self.dwi_front = nn.Sequential(dwi.conv1, dwi.bn1, dwi.relu, dwi.maxpool,
                                       dwi.layer1, dwi.layer2, dwi.layer3)
        self.clinical_processor = full_model.clin_processor
        self.radiomics_processor = full_model.rad_processor

    def forward(self, pre_dce, pre_dwi, clinical, radiomics):
        return (self.dce_front(pre_dce), self.dwi_front(pre_dwi),
                self.clinical_processor(clinical),
                self.radiomics_processor(radiomics))


worker_front = None
medical_loaded = False
# v3.14: 医疗中心只能执行模型前端。完整 DoubleTower 不再常驻本 Pod：后段
# （layer4 + 融合 + 分类器）只有数据中心 medical-server 能执行，因此诊断没有
# 本地执行策略，本 Pod 只保留前端子模块 worker_front。


def load_medical_model():
    global worker_front, medical_loaded
    full_model = DoubleTower(in_channel=1, clinical_dim=23, rad_dim=2264,
                             d_model=256, dropout_rate=0.25, num_classes=2,
                             weights_path=None, focal_alpha=[0.7, 0.3],
                             focal_gamma=2.0, device=DEVICE).to(DEVICE)
    ckpt = torch.load(MODEL_PATH, map_location=DEVICE)
    sd = ckpt["state_dict"] if isinstance(ckpt, dict) and "state_dict" in ckpt else ckpt
    if sd and all(k.startswith("module.") for k in sd):
        sd = {k[len("module."):]: v for k, v in sd.items()}
    full_model.load_state_dict(sd, strict=True)
    full_model.eval()
    # 只把前端子模块挂到 worker_front；full_model 出栈后，后段权重随即释放，
    # 本 Pod 不再具备完整模型（也无法本地完成诊断）。
    worker_front = DoubleTowerWorkerFront(full_model).to(DEVICE)
    worker_front.eval()
    del full_model
    medical_loaded = True
    print(f"[{HOSPITAL_NAME}] medical worker front-end loaded "
          f"(server half runs in 数据中心 only, no local diagnosis)")


try:
    if os.path.exists(MODEL_PATH):
        load_medical_model()
except Exception as e:
    print(f"[{HOSPITAL_NAME}] medical model NOT loaded: {e}")

def _decode_f32b64(value):
    """{"__f32b64__","shape"} -> float32 ndarray; anything else passes through."""
    if isinstance(value, dict) and "__f32b64__" in value:
        flat = np.frombuffer(base64.b64decode(value["__f32b64__"]), dtype="<f4")
        shape = tuple(value.get("shape") or (flat.size,))
        return flat.reshape(shape).copy()
    return value


def _as_tensor(value, device):
    """Accept either nested lists (legacy) or the compact encoding."""
    if isinstance(value, dict) and "__f32b64__" in value:
        return torch.from_numpy(_decode_f32b64(value)).to(device)
    return torch.tensor(value, dtype=torch.float32).to(device)


# ---- 云端后端直连：长连接 + 原始 float32 传输（v3.4）-------------------------
# 实测 pod 间每请求固定开销 42ms、逐请求建连会把中间特征搬运的成本放大一大截；
# base64 还会再多 33% 体积。这里改为长连接 + application/octet-stream。
_SERVER_HOST = MEDICAL_SERVER_URL.split("://", 1)[-1]
_TLS = threading.local()   # 每个工作线程一条长连接：既避免每请求建连，
                           # 又不会把并发请求串行化（早期版本用全局锁保护单条连接）


def _server_conn():
    import http.client
    conn = getattr(_TLS, "conn", None)
    if conn is None:
        host, _, port = _SERVER_HOST.partition(":")
        conn = http.client.HTTPConnection(host, int(port or 80), timeout=300)
        _TLS.conn = conn
    return conn


def _server_infer(payload: dict) -> dict:
    """把中间特征以「包头 JSON + 原始 float32 缓冲」一次性 POST 给云端后端。"""
    import struct
    buffers, meta = [], {}
    for key in ("dce_features", "dwi_features", "clinical_features",
                "radiomics_features"):
        blob = payload[key]
        if isinstance(blob, dict) and "__f32b64__" in blob:
            raw = base64.b64decode(blob["__f32b64__"])
            shape = list(blob.get("shape") or ())
        else:  # 未编码（极少见）：就地编码
            enc = _encode_f32b64(blob if torch.is_tensor(blob)
                                 else torch.tensor(blob, dtype=torch.float32))
            raw = base64.b64decode(enc["__f32b64__"])
            shape = enc["shape"]
        meta[key] = {"offset": sum(len(b) for b in buffers), "length": len(raw),
                     "shape": shape}
        buffers.append(raw)
    header = json.dumps({"encoding": "f32raw", "patient_ids": payload.get("patient_ids"),
                         "fields": meta}).encode()
    body = struct.pack("<II", len(header), 0) + header + b"".join(buffers)
    last_err = None
    for _ in (1, 2):  # 长连接偶发失效时重连一次
        try:
            conn = _server_conn()
            conn.request("POST", "/infer_raw", body=body, headers={
                "Content-Type": "application/octet-stream",
                "Content-Length": str(len(body))})
            resp = conn.getresponse()
            data = resp.read()
            if resp.status != 200:
                raise RuntimeError(f"HTTP {resp.status}: {data[:120]!r}")
            return json.loads(data.decode())
        except Exception as e:  # noqa: BLE001
            last_err = e
            _TLS.conn = None
    raise RuntimeError(str(last_err)[:160])


def _encode_f32b64(tensor) -> dict:
    """float32 tensor -> {"__f32b64__": base64, "shape": [...]} (stdlib + numpy)."""
    arr = np.ascontiguousarray(tensor.detach().cpu().numpy(), dtype="<f4")
    return {"__f32b64__": base64.b64encode(arr.tobytes()).decode(),
            "shape": list(arr.shape),
            "dtype": "float32"}


# ============================ metrics =======================================
hospital_info = Gauge("hospital_info", "hospital identity", ["hospital", "node", "role"])
hospital_info.labels(HOSPITAL_NAME, NODE_NAME, "edge").set(1)
worker_requests = Counter("medical_worker_requests_total", "medical worker requests")
worker_latency = Histogram("medical_worker_latency_seconds", "worker latency")
worker_running = Gauge("medical_worker_running_tasks", "running worker tasks")
v3_compute_requests = Counter("v3_compute_requests_total", "v3 compute partitions", ["entity"])
v3_sync_requests = Counter("v3_sync_requests_total", "v3 sync calls", ["entity"])

_accepted: set = set()

# ====================== v3.2 local execution runtime ========================
# Local execution = this pod takes over the scheduler's orchestration role.
# 诊断没有本地执行策略（医疗中心无法执行模型的 server 半段），因此 diagnosis
# 一律拒绝；compute/sync/routine 是 common/local_exec.py 的共享实现。Work runs
# on a bounded background queue so request threads, probes and /health stay
# responsive.
local_store = lx.ResultStore()
local_compute_requests = Counter("local_exec_requests_total",
                                 "local executions", ["entity", "kind"])
local_compute_latency = Histogram("local_exec_latency_seconds",
                                  "local execution latency", ["kind"])


local_runner = lx.queue_runner_factory(local_store)
local_queue = lx.LocalQueue(local_runner, store=local_store)

# ============================== request models ===============================
class MedicalWorkerRequest(BaseModel):
    # v3.3: each field is either nested float lists (legacy) or a compact
    # {"__f32b64__", "shape"} object - the imaging input is ~2MB of float text
    # per patient and was parsed 3x per collaborative diagnosis.
    dce_image: Any
    dwi_image: Any
    clinical: Any
    radiomics: Any
    patient_ids: Optional[List[str]] = None
    # v3.3: "f32b64" makes the response carry the intermediate features as
    # base64 float32 instead of nested float lists. Sending ~8MB of float text
    # per diagnosis was costing seconds of JSON encode/decode on both sides -
    # far more than the inference it was transporting.
    features_encoding: Optional[str] = None


class ComputeRequest(BaseModel):
    rows: int = 256
    instruments: int = 4
    intensity: int = 40
    seed: int = 1
    partition: int = 0
    count: int = 1


class AcceptSyncRequest(BaseModel):
    ids: List[str] = []


# ============================== endpoints ====================================
@app.get("/")
def root():
    return {"service": "hospital", "hospital": HOSPITAL_NAME, "node": NODE_NAME,
            "role": "edge", "version": "3.0",
            "status": "running",
            "model_loaded": {"medical": medical_loaded}}


@app.get("/health")
def health_check():
    return {"service": "hospital", "hospital": HOSPITAL_NAME, "node": NODE_NAME,
            "status": "ok", "model_loaded": {"medical": medical_loaded},
            "capabilities": lx.capabilities("hospital"), "local_mode": True}


@app.post("/medical/infer")
def medical_infer(req: MedicalWorkerRequest):
    if not medical_loaded:
        raise HTTPException(status_code=503, detail="Medical model not loaded")
    worker_requests.inc()
    worker_running.inc()
    try:
        start = time.perf_counter()
        dce = _as_tensor(req.dce_image, DEVICE)
        dwi = _as_tensor(req.dwi_image, DEVICE)
        clin = _as_tensor(req.clinical, DEVICE)
        rad = _as_tensor(req.radiomics, DEVICE)
        if dce.ndim == 3:
            dce = dce.unsqueeze(0)
        if dwi.ndim == 3:
            dwi = dwi.unsqueeze(0)
        if clin.ndim == 1:
            clin = clin.unsqueeze(0)
        if rad.ndim == 1:
            rad = rad.unsqueeze(0)
        with torch.inference_mode():
            dce_f, dwi_f, clin_f, rad_f = worker_front(dce, dwi, clin, rad)
        latency_ms = (time.perf_counter() - start) * 1000
        worker_latency.observe(latency_ms / 1000)
        ids = req.patient_ids or [f"p{i}" for i in range(dce.shape[0])]
        feats = {"dce_features": dce_f, "dwi_features": dwi_f,
                 "clinical_features": clin_f, "radiomics_features": rad_f}
        if (req.features_encoding or "").lower() in ("f32b64", "base64"):
            out = {k: _encode_f32b64(t) for k, t in feats.items()}
            out["encoding"] = "f32b64"
        else:
            out = {k: t.cpu().numpy().tolist() for k, t in feats.items()}
        out.update({"patient_ids": ids, "latency_ms": latency_ms,
                    "hospital": HOSPITAL_NAME})
        return out
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        worker_running.dec()


@app.post("/medical/infer_full")
def medical_infer_full(req: MedicalWorkerRequest):
    """v3.14: 拒绝医院整段执行完整模型。

    医疗中心只能执行 DoubleTower 前端（conv1..layer3），后段（layer4 + 融合 +
    分类器）只有数据中心 medical-server 能执行，因此数据并行的「医院整段跑」
    路径不再成立。诊断必须走云边端协同（前端 /medical/infer_forward → 云端后端）。
    """
    raise HTTPException(status_code=409, detail=lx.DIAGNOSIS_NO_LOCAL_EXEC)


@app.post("/medical/infer_forward")
def medical_infer_forward(req: MedicalWorkerRequest):
    """v3.3: 边端做完前端后**直接把中间特征交给云端后端**，不再经调度器中转。

    调度器仍在控制面决策（选医院、记账、降级），但 2MB 级的中间特征属于数据面：
    原先它要经过「医院 → 调度器 → medical-server」两次跨节点搬运与两次 JSON
    编解码，把拆分本身的收益吃掉了。此端点让数据面只走一跳。
    """
    if not medical_loaded:
        raise HTTPException(status_code=503, detail="Medical model not loaded")
    worker_requests.inc()
    t_front = time.perf_counter()
    try:
        dce = _as_tensor(req.dce_image, DEVICE)
        dwi = _as_tensor(req.dwi_image, DEVICE)
        clin = _as_tensor(req.clinical, DEVICE)
        rad = _as_tensor(req.radiomics, DEVICE)
        for t in (dce, dwi, clin, rad):
            if t.ndim == 3:
                t.unsqueeze_(0)
        if clin.ndim == 1:
            clin.unsqueeze_(0)
        if rad.ndim == 1:
            rad.unsqueeze_(0)
        with torch.inference_mode():
            dce_f, dwi_f, clin_f, rad_f = worker_front(dce, dwi, clin, rad)
        front_ms = (time.perf_counter() - t_front) * 1000
        worker_latency.observe(front_ms / 1000)
        feats = {"dce_features": dce_f, "dwi_features": dwi_f,
                 "clinical_features": clin_f, "radiomics_features": rad_f}
        payload = {k: _encode_f32b64(t) for k, t in feats.items()}
        payload["encoding"] = "f32b64"
        payload["patient_ids"] = req.patient_ids or [
            f"p{i}" for i in range(dce.shape[0])]
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(e))

    t_cloud = time.perf_counter()
    try:
        server = _server_infer(payload)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502,
                            detail=f"云端后端不可达({MEDICAL_SERVER_URL}): {str(e)[:160]}")
    cloud_rtt_ms = (time.perf_counter() - t_cloud) * 1000
    ids = payload["patient_ids"]
    return {
        "predictions": server.get("predictions", []),
        "bpCR_probability": server.get("bpCR_probability"),
        "patient_ids": ids,
        "front_latency_ms": front_ms,
        "server_latency_ms": server.get("latency_ms"),
        "cloud_rtt_ms": cloud_rtt_ms,
        "latency_ms": front_ms + cloud_rtt_ms,
        "hospital": HOSPITAL_NAME,
        "handoff": "direct",
    }


# ------------------------------ v3 compute ----------------------------------
@app.post("/v3/compute")
def v3_compute(req: ComputeRequest):
    v3_compute_requests.labels(entity=HOSPITAL_NAME).inc()
    res = v3.compute_partition(rows=req.rows, instruments=req.instruments,
                               intensity=req.intensity, seed=req.seed,
                               partition=req.partition, count=req.count)
    res.update({"actor": HOSPITAL_NAME, "node": NODE_NAME, "role": "edge",
                "pod": os.environ.get("POD_NAME", HOSPITAL_NAME)})
    return res


# ------------------------------ v3 sync (peer) ------------------------------
@app.get("/v3/sync/local")
def v3_sync_local():
    """Local replica state: held item ids (deterministic subset) + pending
    update items this entity wants to push to the cloud."""
    v3_sync_requests.labels(entity=HOSPITAL_NAME).inc()
    all_ids = v3.item_ids(DB_VERSION, DB_ITEM_COUNT)
    secret = f"{HOSPITAL_NAME}:{NODE_NAME}"
    holds = v3.hold_subset(all_ids, secret, HOLD_RATIO)
    pending = []
    for i in range(2):  # local updates generated at this entity
        uid = f"upd-{HOSPITAL_NAME}-{i}"
        blob = v3.chunk_blob(uid, 512)
        meta = v3.item_meta(uid, blob)
        pending.append({"uid": uid, "size": meta["size"], "hash": meta["hash"]})
    return {"pod": os.environ.get("POD_NAME", HOSPITAL_NAME),
            "entity": HOSPITAL_NAME, "node": NODE_NAME, "role": "edge",
            "db_version": DB_VERSION, "hold_ratio": HOLD_RATIO,
            "holds": holds[:200], "pending_updates": pending,
            "accepted": sorted(_accepted)}


@app.post("/v3/sync/accepted")
def v3_sync_accepted(req: AcceptSyncRequest):
    v3_sync_requests.labels(entity=HOSPITAL_NAME).inc()
    for i in req.ids:
        _accepted.add(i)
    return {"ok": True, "entity": HOSPITAL_NAME, "accepted": len(req.ids),
            "total": len(_accepted)}


@app.get("/v3/sync/chunk/{item_id}")
def v3_sync_chunk(item_id: str):
    """Serve any db item (deterministic payload) as a P2P data peer."""
    blob = v3.chunk_blob(item_id, int(os.environ.get("CHUNK_KB", "4")) * 1024)
    meta = v3.item_meta(item_id, blob)
    return {"id": item_id, "size": meta["size"], "hash": meta["hash"],
            "blob": blob, "peer": HOSPITAL_NAME}


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
    input: Dict = {}          # values may be nested lists or {"__f32b64__"}
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
    local_compute_requests.labels(entity=HOSPITAL_NAME, kind=kind).inc()
    ms = ((res.get("metrics") or {}).get("pipeline_total_ms") or 0) / 1000.0
    if ms:
        local_compute_latency.labels(kind=kind).observe(ms)
    return res


@app.post("/local/execute")
def local_execute(req: LocalExecuteRequest):
    """Local execution / degraded execution entry point (see 调度模式与降级-设计方案.md §4)."""
    return _run_local(req.kind, req)


@app.post("/local/diagnosis")
def local_diagnosis(req: LocalDiagnosisRequest):
    """v3.14: 医疗中心无法本地完成诊断，明确拒绝。

    本 Pod 只保留 DoubleTower 前端，后段（layer4 + 融合 + 分类器）只有数据中心
    medical-server 能执行，因此诊断没有本地执行策略；此前诊所转诊到医院就地
    跑完整模型的 capability=forward 路径同样不再成立。
    """
    raise HTTPException(status_code=409, detail=lx.DIAGNOSIS_NO_LOCAL_EXEC)


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
    uvicorn.run(app, host="0.0.0.0", port=8006)
