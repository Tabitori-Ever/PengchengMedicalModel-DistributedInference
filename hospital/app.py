"""
Hospital Pod Service (v3.0 - Edge).

Roles:
  1. medical worker front-end (DoubleTower) for the diagnosis task
     (/medical/infer) - raw patient data never leaves the hospital pod.
  2. v3 shared worker: participates in multi-pod compute partitions and
     patient-db P2P sync as a data peer.
No AlexNet / part1 functionality in v3.0.
"""
import os
import sys
import time
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn
from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel
from typing import List, Optional
from prometheus_client import Counter, Histogram, Gauge, generate_latest

# ---- import paths: container (/app) + repo (local dev) ----
sys.path.insert(0, "/app")
_HOME = Path(__file__).resolve().parent
for _c in (str(_HOME.parent / "medical-worker"), str(_HOME.parent)):
    if _c not in sys.path and Path(_c).is_dir():
        sys.path.insert(0, _c)

from models_my import DoubleTower            # noqa: E402
from common import v3_common as v3           # noqa: E402

HOSPITAL_NAME = os.environ.get("HOSPITAL_NAME", "hospital-a")
NODE_NAME = os.environ.get("NODE_NAME", "unknown")
MODEL_PATH = os.environ.get("MODEL_PATH", "/app/weights/best_model_fold1.pth")
DEVICE = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")

DB_VERSION = os.environ.get("DB_VERSION", "v3-db-1")
DB_ITEM_COUNT = int(os.environ.get("DB_ITEM_COUNT", "64"))
HOLD_RATIO = float(os.environ.get("HOLD_RATIO", "0.7"))

app = FastAPI(title=f"Hospital Pod ({HOSPITAL_NAME}) v3.0")

# ========================== medical front-end ===============================
class DoubleTowerWorkerFront(nn.Module):
    def __init__(self, full_model: DoubleTower):
        super().__init__()
        dce = full_model.image_processor.encoder_dce.model
        dwi = full_model.image_processor.encoder_dwi.model
        self.dce_front = nn.Sequential(dce.conv1, dce.bn1, dce.relu, dce.maxpool)
        self.dwi_front = nn.Sequential(dwi.conv1, dwi.bn1, dwi.relu, dwi.maxpool)
        self.clinical_processor = full_model.clin_processor
        self.radiomics_processor = full_model.rad_processor

    def forward(self, pre_dce, pre_dwi, clinical, radiomics):
        return (self.dce_front(pre_dce), self.dwi_front(pre_dwi),
                self.clinical_processor(clinical),
                self.radiomics_processor(radiomics))


worker_front = None
medical_loaded = False


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
    worker_front = DoubleTowerWorkerFront(full_model).to(DEVICE)
    worker_front.eval()
    medical_loaded = True
    print(f"[{HOSPITAL_NAME}] medical worker model loaded")


try:
    if os.path.exists(MODEL_PATH):
        load_medical_model()
except Exception as e:
    print(f"[{HOSPITAL_NAME}] medical model NOT loaded: {e}")

# ============================ metrics =======================================
hospital_info = Gauge("hospital_info", "hospital identity", ["hospital", "node", "role"])
hospital_info.labels(HOSPITAL_NAME, NODE_NAME, "edge").set(1)
worker_requests = Counter("medical_worker_requests_total", "medical worker requests")
worker_latency = Histogram("medical_worker_latency_seconds", "worker latency")
worker_running = Gauge("medical_worker_running_tasks", "running worker tasks")
v3_compute_requests = Counter("v3_compute_requests_total", "v3 compute partitions", ["entity"])
v3_sync_requests = Counter("v3_sync_requests_total", "v3 sync calls", ["entity"])

_accepted: set = set()

# ============================== request models ===============================
class MedicalWorkerRequest(BaseModel):
    dce_image: List[List[List[float]]]
    dwi_image: List[List[List[float]]]
    clinical: List[List[float]]
    radiomics: List[List[float]]
    patient_ids: Optional[List[str]] = None


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
            "status": "ok", "model_loaded": {"medical": medical_loaded}}


@app.post("/medical/infer")
def medical_infer(req: MedicalWorkerRequest):
    if not medical_loaded:
        raise HTTPException(status_code=503, detail="Medical model not loaded")
    worker_requests.inc()
    worker_running.inc()
    try:
        start = time.perf_counter()
        dce = torch.tensor(req.dce_image, dtype=torch.float32).to(DEVICE)
        dwi = torch.tensor(req.dwi_image, dtype=torch.float32).to(DEVICE)
        clin = torch.tensor(req.clinical, dtype=torch.float32).to(DEVICE)
        rad = torch.tensor(req.radiomics, dtype=torch.float32).to(DEVICE)
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
        return {"dce_features": dce_f.cpu().numpy().tolist(),
                "dwi_features": dwi_f.cpu().numpy().tolist(),
                "clinical_features": clin_f.cpu().numpy().tolist(),
                "radiomics_features": rad_f.cpu().numpy().tolist(),
                "patient_ids": ids, "latency_ms": latency_ms,
                "hospital": HOSPITAL_NAME}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        worker_running.dec()


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


@app.get("/metrics")
def metrics():
    return Response(content=generate_latest(), media_type="text/plain")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8006)
