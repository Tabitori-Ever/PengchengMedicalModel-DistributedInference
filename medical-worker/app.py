"""
Medical Worker Service (Edge - Hospital Nodes).
Implements the model front-end: ResNet conv1→bn1→relu→maxpool
Deployed on node1/node2 (edge nodes with role=edge).
Patients' raw data never leaves the hospital.
"""
import os
import time
import numpy as np
import torch
import torch.nn as nn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from prometheus_client import Counter, Histogram, Gauge, generate_latest

from models_my import DoubleTower

app = FastAPI(title="Medical Worker - Edge Model Frontend")

# ---- Model Loading ----
MODEL_PATH = os.environ.get("MODEL_PATH", "/app/weights/best_model_fold1.pth")
DEVICE = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")

full_model = None
worker_front = None


class DoubleTowerWorkerFront(nn.Module):
    """Model front-end deployed on edge hospital nodes.
    Extracts DCE/DWI ResNet front features + clinical/radiomics encodings."""

    def __init__(self, full_model: DoubleTower):
        super().__init__()
        dce_resnet = full_model.image_processor.encoder_dce.model
        dwi_resnet = full_model.image_processor.encoder_dwi.model

        self.dce_front = nn.Sequential(
            dce_resnet.conv1, dce_resnet.bn1,
            dce_resnet.relu, dce_resnet.maxpool
        )
        self.dwi_front = nn.Sequential(
            dwi_resnet.conv1, dwi_resnet.bn1,
            dwi_resnet.relu, dwi_resnet.maxpool
        )
        self.clinical_processor = full_model.clin_processor
        self.radiomics_processor = full_model.rad_processor

    def forward(self, pre_dce, pre_dwi, clinical, radiomics):
        dce_feat = self.dce_front(pre_dce)
        dwi_feat = self.dwi_front(pre_dwi)
        clinical_feat = self.clinical_processor(clinical)
        radiomics_feat = self.radiomics_processor(radiomics)
        return dce_feat, dwi_feat, clinical_feat, radiomics_feat


def load_model():
    global full_model, worker_front
    print(f"[Medical Worker] Loading model from {MODEL_PATH}")
    print(f"[Medical Worker] Device: {DEVICE}")

    full_model = DoubleTower(
        in_channel=1,
        clinical_dim=23,
        rad_dim=2264,
        d_model=256,
        dropout_rate=0.25,
        num_classes=2,
        weights_path=None,
        focal_alpha=[0.7, 0.3],
        focal_gamma=2.0,
        device=DEVICE,
    ).to(DEVICE)

    checkpoint = torch.load(MODEL_PATH, map_location=DEVICE)
    if isinstance(checkpoint, dict) and "state_dict" in checkpoint:
        state_dict = checkpoint["state_dict"]
    else:
        state_dict = checkpoint

    if state_dict and all(k.startswith("module.") for k in state_dict):
        state_dict = {k[len("module."):]: v for k, v in state_dict.items()}

    full_model.load_state_dict(state_dict, strict=True)
    full_model.eval()

    worker_front = DoubleTowerWorkerFront(full_model).to(DEVICE)
    worker_front.eval()

    del full_model
    if DEVICE.type == "cuda":
        torch.cuda.empty_cache()

    print("[Medical Worker] Model loaded successfully")


# Try loading model at startup
model_loaded = False
try:
    if os.path.exists(MODEL_PATH):
        load_model()
        model_loaded = True
except Exception as e:
    print(f"[Medical Worker] Model not loaded: {e}")


# ---- Prometheus Metrics ----
worker_requests = Counter("medical_worker_requests_total", "Total requests")
worker_latency = Histogram("medical_worker_latency_seconds", "Worker inference latency")
worker_running = Gauge("medical_worker_running_tasks", "Running tasks")


# ---- Request Models ----
class MedicalWorkerRequest(BaseModel):
    dce_image: List[List[List[float]]]  # [B, 1, 224, 224]
    dwi_image: List[List[List[float]]]  # [B, 1, 224, 224]
    clinical: List[List[float]]          # [B, 23]
    radiomics: List[List[float]]         # [B, 2264]
    patient_ids: Optional[List[str]] = None


class MedicalWorkerSimpleRequest(BaseModel):
    """Simplified request: raw feature arrays."""
    dce: List[List[List[List[float]]]]  # [B, 1, 224, 224]
    dwi: List[List[List[List[float]]]]
    clinical: List[List[float]]
    radiomics: List[List[float]]


# ---- API Endpoints ----
@app.get("/")
def health():
    return {
        "service": "medical-worker",
        "status": "running",
        "model_loaded": model_loaded,
        "device": str(DEVICE),
        "role": "edge"
    }


@app.get("/health")
def health_check():
    return {
        "service": "medical-worker",
        "status": "ok",
        "model_loaded": model_loaded,
        "device": str(DEVICE)
    }


@app.post("/infer")
def infer(req: MedicalWorkerRequest):
    """Run medical model worker (front-end) inference.
    Input: DCE/DWI images + clinical + radiomics features
    Output: Intermediate features (sent to cloud server)"""
    if not model_loaded:
        raise HTTPException(status_code=503, detail="Model not loaded")

    worker_requests.inc()
    worker_running.inc()

    try:
        start = time.perf_counter()

        dce = torch.tensor(req.dce_image, dtype=torch.float32).to(DEVICE)
        dwi = torch.tensor(req.dwi_image, dtype=torch.float32).to(DEVICE)
        clinical = torch.tensor(req.clinical, dtype=torch.float32).to(DEVICE)
        radiomics = torch.tensor(req.radiomics, dtype=torch.float32).to(DEVICE)

        # Ensure batch dimension
        if dce.ndim == 3:
            dce = dce.unsqueeze(0)
        if dwi.ndim == 3:
            dwi = dwi.unsqueeze(0)
        if clinical.ndim == 1:
            clinical = clinical.unsqueeze(0)
        if radiomics.ndim == 1:
            radiomics = radiomics.unsqueeze(0)

        with torch.inference_mode():
            dce_feat, dwi_feat, clin_feat, rad_feat = worker_front(
                dce, dwi, clinical, radiomics
            )

        latency_ms = (time.perf_counter() - start) * 1000
        worker_latency.observe(latency_ms / 1000)

        patient_ids = req.patient_ids or [f"p{i}" for i in range(dce.shape[0])]

        return {
            "dce_features": dce_feat.cpu().numpy().tolist(),
            "dwi_features": dwi_feat.cpu().numpy().tolist(),
            "clinical_features": clin_feat.cpu().numpy().tolist(),
            "radiomics_features": rad_feat.cpu().numpy().tolist(),
            "patient_ids": patient_ids,
            "latency_ms": latency_ms,
            "shapes": {
                "dce": list(dce_feat.shape),
                "dwi": list(dwi_feat.shape),
                "clinical": list(clin_feat.shape),
                "radiomics": list(rad_feat.shape),
            }
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        worker_running.dec()


@app.post("/infer/simple")
def infer_simple(req: MedicalWorkerSimpleRequest):
    """Simplified inference accepting direct feature arrays."""
    if not model_loaded:
        raise HTTPException(status_code=503, detail="Model not loaded")

    worker_requests.inc()
    worker_running.inc()

    try:
        start = time.perf_counter()

        dce = torch.tensor(req.dce, dtype=torch.float32).to(DEVICE)
        dwi = torch.tensor(req.dwi, dtype=torch.float32).to(DEVICE)
        clinical = torch.tensor(req.clinical, dtype=torch.float32).to(DEVICE)
        radiomics = torch.tensor(req.radiomics, dtype=torch.float32).to(DEVICE)

        if dce.ndim == 3:
            dce = dce.unsqueeze(0)
        if dwi.ndim == 3:
            dwi = dwi.unsqueeze(0)

        with torch.inference_mode():
            dce_feat, dwi_feat, clin_feat, rad_feat = worker_front(
                dce, dwi, clinical, radiomics
            )

        latency_ms = (time.perf_counter() - start) * 1000
        worker_latency.observe(latency_ms / 1000)

        return {
            "dce_features": dce_feat.cpu().numpy().tolist(),
            "dwi_features": dwi_feat.cpu().numpy().tolist(),
            "clinical_features": clin_feat.cpu().numpy().tolist(),
            "radiomics_features": rad_feat.cpu().numpy().tolist(),
            "latency_ms": latency_ms
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        worker_running.dec()


@app.get("/metrics")
def metrics():
    from fastapi import Response
    return Response(content=generate_latest(), media_type="text/plain")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8006)
