"""
Hospital Pod Service (v2.0).

Single hospital pod that merges the roles previously served by two separate
deployments:
  1. medical-worker  (ResNet conv1->maxpool front-end + clinical/radiomics
                      encoders of the DoubleTower model)  ->  /medical/infer
  2. AlexNet part1   (Conv1-Conv5 + avgpool feature extractor)  ->  /alexnet/infer

Each hospital-a / hospital-b pod is pinned to its own node (node1/node2).
Raw patient data stays inside the hospital pod / node; only intermediate
features travel to the cloud medical-server / part2.
"""
import os
import time

import numpy as np
import torch
import torch.nn as nn
from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel
from typing import List, Optional
from prometheus_client import Counter, Histogram, Gauge, generate_latest
from torchvision.models import alexnet

# Make shared packages importable: models_my (medical) + common (alexnet part1)
# In the container these live under /app; for local dev they live in the repo
# (medical-worker/ holds models_my.py + models/, common/ holds model.py).
import sys
from pathlib import Path

sys.path.insert(0, "/app")
_HOSPITAL_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _HOSPITAL_DIR.parent
for _candidate in (str(_HOSPITAL_DIR.parent / "medical-worker"),
                   str(_REPO_ROOT)):
    if _candidate not in sys.path and Path(_candidate).is_dir():
        sys.path.insert(0, _candidate)

from models_my import DoubleTower  # noqa: E402
from common.model import AlexNetPart1  # noqa: E402

HOSPITAL_NAME = os.environ.get("HOSPITAL_NAME", "hospital-a")
NODE_NAME = os.environ.get("NODE_NAME", "unknown")
MODEL_PATH = os.environ.get("MODEL_PATH", "/app/weights/best_model_fold1.pth")
DEVICE = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")

app = FastAPI(title=f"Hospital Pod ({HOSPITAL_NAME}) - Medical Worker + AlexNet Part1")


# ============================== Medical front-end ==============================
class DoubleTowerWorkerFront(nn.Module):
    """DoubleTower front-end: DCE/DWI ResNet front + clinical/radiomics encoders."""

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


worker_front = None
medical_loaded = False


def load_medical_model():
    """Load DoubleTower weights and keep only the worker (front) half."""
    global worker_front, medical_loaded
    print(f"[Hospital {HOSPITAL_NAME}] Loading medical weights from {MODEL_PATH}")
    print(f"[Hospital {HOSPITAL_NAME}] Device: {DEVICE}")

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
    medical_loaded = True

    del full_model
    if DEVICE.type == "cuda":
        torch.cuda.empty_cache()
    print(f"[Hospital {HOSPITAL_NAME}] Medical worker model loaded")


# ============================== AlexNet part1 =================================
alexnet_part1 = None
part1_loaded = False
PART1_MODEL_PATH = os.environ.get(
    "PART1_MODEL_PATH",
    "/app/common/part1.pt"
)


def load_alexnet_part1():
    """Load AlexNet Part1 (Conv1-Conv5 + avgpool).

    Uses alexnet(weights=None) and overwrites with part1.pt state dict so the
    pod never needs to download ImageNet weights at runtime.
    """
    global alexnet_part1, part1_loaded
    full_net = alexnet(weights=None)          # random init, no network download
    model = AlexNetPart1(full_net)
    if os.path.exists(PART1_MODEL_PATH):
        model.load_state_dict(torch.load(PART1_MODEL_PATH, map_location="cpu"))
    model.eval()
    alexnet_part1 = model
    part1_loaded = True
    print(f"[Hospital {HOSPITAL_NAME}] AlexNet part1 model loaded")


# Startup model loading (same guard as legacy worker: pod may boot w/o weights)
try:
    if os.path.exists(MODEL_PATH):
        load_medical_model()
except Exception as e:
    print(f"[Hospital {HOSPITAL_NAME}] Medical model NOT loaded: {e}")

try:
    load_alexnet_part1()
except Exception as e:
    print(f"[Hospital {HOSPITAL_NAME}] AlexNet part1 NOT loaded: {e}")


# ============================== Prometheus ====================================
# Keep the legacy metric names so ServiceMonitor / monitoring queries and
# Grafana panels that reference worker/part1 metrics keep working.
hospital_info = Gauge(
    "hospital_info",
    "Hospital pod identity",
    ["hospital", "node", "roles"]
)
hospital_info.labels(HOSPITAL_NAME, NODE_NAME, "worker,part1").set(1)

worker_requests = Counter("medical_worker_requests_total", "Total medical worker requests")
worker_latency = Histogram("medical_worker_latency_seconds", "Worker inference latency")
worker_running = Gauge("medical_worker_running_tasks", "Running medical worker tasks")

part1_requests = Counter("alexnet_part1_requests_total", "Total Part1 requests")
part1_latency = Histogram("alexnet_part1_latency_seconds", "Part1 (Conv) inference latency")
part1_running = Gauge("alexnet_part1_running_tasks", "Running Part1 tasks")


# ============================== Request models ================================
class MedicalWorkerRequest(BaseModel):
    dce_image: List[List[List[float]]]  # [B, 1, 224, 224]
    dwi_image: List[List[List[float]]]  # [B, 1, 224, 224]
    clinical: List[List[float]]          # [B, 23]
    radiomics: List[List[float]]         # [B, 2264]
    patient_ids: Optional[List[str]] = None


class AlexNetPart1Request(BaseModel):
    image: list


# ============================== Endpoints =====================================
@app.get("/")
def root():
    return {
        "service": "hospital",
        "hospital": HOSPITAL_NAME,
        "node": NODE_NAME,
        "status": "running",
        "roles": ["worker", "part1"],
        "model_loaded": {"medical": medical_loaded, "alexnet_part1": part1_loaded},
        "device": str(DEVICE),
    }


@app.get("/health")
def health_check():
    return {
        "service": "hospital",
        "hospital": HOSPITAL_NAME,
        "node": NODE_NAME,
        "status": "ok",
        "model_loaded": {"medical": medical_loaded, "alexnet_part1": part1_loaded},
        "device": str(DEVICE),
    }


@app.post("/medical/infer")
def medical_infer(req: MedicalWorkerRequest):
    """Medical worker (front-end) inference. Payload identical to legacy
    medical-worker /infer; response identical (intermediate features)."""
    if not medical_loaded:
        raise HTTPException(status_code=503, detail="Medical model not loaded")

    worker_requests.inc()
    worker_running.inc()

    try:
        start = time.perf_counter()

        dce = torch.tensor(req.dce_image, dtype=torch.float32).to(DEVICE)
        dwi = torch.tensor(req.dwi_image, dtype=torch.float32).to(DEVICE)
        clinical = torch.tensor(req.clinical, dtype=torch.float32).to(DEVICE)
        radiomics = torch.tensor(req.radiomics, dtype=torch.float32).to(DEVICE)

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
            "hospital": HOSPITAL_NAME,
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


@app.post("/alexnet/infer")
def alexnet_part1_infer(req: AlexNetPart1Request):
    """AlexNet Part1 (Conv) inference. Payload identical to legacy part1 /infer."""
    if not part1_loaded:
        raise HTTPException(status_code=503, detail="AlexNet part1 model not loaded")

    part1_requests.inc()
    part1_running.inc()

    try:
        start = time.perf_counter()

        image = np.asarray(req.image, dtype=np.float32)
        image = torch.tensor(image)
        if image.ndim == 3:
            image = image.unsqueeze(0)

        with torch.no_grad():
            feature = alexnet_part1(image)

        conv_time = (time.perf_counter() - start) * 1000
        part1_latency.observe(conv_time / 1000)

        return {
            "feature": feature.numpy().tolist(),
            "latency_ms": conv_time,
            "hospital": HOSPITAL_NAME,
        }
    finally:
        part1_running.dec()


@app.get("/metrics")
def metrics():
    return Response(content=generate_latest(), media_type="text/plain")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8006)
