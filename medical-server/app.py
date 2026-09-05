"""
Medical Server Service (Cloud - Data Center).
Implements the model back-end: ResNet layer1-4 + fusion + classifier.
Deployed on node3 (cloud node with role=cloud).
Handles high-computation inference.
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

app = FastAPI(title="Medical Server - Cloud Model Backend")

# ---- Model Loading ----
MODEL_PATH = os.environ.get("MODEL_PATH", "/app/weights/best_model_fold1.pth")
DEVICE = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")

server_model = None


class DoubleTowerServerBack(nn.Module):
    """Model back-end deployed on cloud server.
    Runs ResNet layer1-4, multi-modal fusion, and classifier."""

    def __init__(self, full_model: DoubleTower):
        super().__init__()
        dce_resnet = full_model.image_processor.encoder_dce.model
        dwi_resnet = full_model.image_processor.encoder_dwi.model

        self.dce_backbone = nn.Sequential(
            dce_resnet.layer1, dce_resnet.layer2,
            dce_resnet.layer3, dce_resnet.layer4
        )
        self.dwi_backbone = nn.Sequential(
            dwi_resnet.layer1, dwi_resnet.layer2,
            dwi_resnet.layer3, dwi_resnet.layer4
        )

        img_proc = full_model.image_processor
        self.down_dce = img_proc.down_dce
        self.down_dwi = img_proc.down_dwi
        self.fusion_module = img_proc.fusion_module
        self.spectral_fusion = img_proc.spectral_fusion
        self.final_fusion_conv = img_proc.final_fusion_conv
        self.global_pool = img_proc.global_pool

        self.cross_attn = full_model.cross_attn
        self.table_fusion = full_model.fusion
        self.cmfa = full_model.cmfa
        self.classifier = full_model.classifier

    def forward(self, dce_front, dwi_front, clinical_token, radiomics_token):
        dce_feat = self.dce_backbone(dce_front)
        dwi_feat = self.dwi_backbone(dwi_front)

        dce_feat = self.down_dce(dce_feat)
        dwi_feat = self.down_dwi(dwi_feat)

        spatial_fused = self.fusion_module(dce_feat, dwi_feat)
        spectral_fused = self.spectral_fusion(dce_feat, dwi_feat)
        combined = torch.cat([spatial_fused, spectral_fused], dim=1)
        final_image_map = self.final_fusion_conv(combined)

        image_token = self.global_pool(final_image_map).squeeze()
        if image_token.dim() == 1:
            image_token = image_token.unsqueeze(0)

        attention_enhanced = self.cross_attn(clinical_token, radiomics_token)
        table_token = self.table_fusion(clinical_token, attention_enhanced)

        fused_token = self.cmfa(image_token, table_token)
        return self.classifier(fused_token)


def load_model():
    global server_model
    print(f"[Medical Server] Loading model from {MODEL_PATH}")
    print(f"[Medical Server] Device: {DEVICE}")

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

    server_model = DoubleTowerServerBack(full_model).to(DEVICE)
    server_model.eval()

    del full_model
    if DEVICE.type == "cuda":
        torch.cuda.empty_cache()

    print("[Medical Server] Model loaded successfully")


model_loaded = False
try:
    if os.path.exists(MODEL_PATH):
        load_model()
        model_loaded = True
except Exception as e:
    print(f"[Medical Server] Model not loaded: {e}")


# ---- Prometheus Metrics ----
server_requests = Counter("medical_server_requests_total", "Total requests")
server_latency = Histogram("medical_server_latency_seconds", "Server inference latency")
server_running = Gauge("medical_server_running_tasks", "Running tasks")


# ---- Request Models ----
class MedicalServerRequest(BaseModel):
    dce_features: List[List[List[List[float]]]]   # [B, 64, 56, 56]
    dwi_features: List[List[List[List[float]]]]   # [B, 64, 56, 56]
    clinical_features: List[List[float]]          # [B, 256]
    radiomics_features: List[List[float]]         # [B, 256]
    patient_ids: Optional[List[str]] = None


# ---- API Endpoints ----
@app.get("/")
def health():
    return {
        "service": "medical-server",
        "status": "running",
        "model_loaded": model_loaded,
        "device": str(DEVICE),
        "role": "cloud"
    }


@app.get("/health")
def health_check():
    return {
        "service": "medical-server",
        "status": "ok",
        "model_loaded": model_loaded
    }


@app.post("/infer")
def infer(req: MedicalServerRequest):
    """Run medical model server (back-end) inference.
    Input: Intermediate features from edge worker
    Output: bpCR probability predictions"""
    if not model_loaded:
        raise HTTPException(status_code=503, detail="Model not loaded")

    server_requests.inc()
    server_running.inc()

    try:
        start = time.perf_counter()

        dce = torch.tensor(req.dce_features, dtype=torch.float32).to(DEVICE)
        dwi = torch.tensor(req.dwi_features, dtype=torch.float32).to(DEVICE)
        clinical = torch.tensor(req.clinical_features, dtype=torch.float32).to(DEVICE)
        radiomics = torch.tensor(req.radiomics_features, dtype=torch.float32).to(DEVICE)

        # Ensure batch dim
        if dce.ndim == 3:
            dce = dce.unsqueeze(0)
        if dwi.ndim == 3:
            dwi = dwi.unsqueeze(0)
        if clinical.ndim == 1:
            clinical = clinical.unsqueeze(0)
        if radiomics.ndim == 1:
            radiomics = radiomics.unsqueeze(0)

        with torch.inference_mode():
            logits = server_model(dce, dwi, clinical, radiomics)
            probabilities = torch.softmax(logits, dim=1)[:, 1]

        latency_ms = (time.perf_counter() - start) * 1000
        server_latency.observe(latency_ms / 1000)

        probs = probabilities.cpu().numpy().tolist()
        patient_ids = req.patient_ids or [f"p{i}" for i in range(len(probs))]

        predictions = []
        for pid, prob in zip(patient_ids, probs):
            predictions.append({
                "patient_id": str(pid),
                "bpCR_probability": round(prob, 6),
                "prediction": int(prob >= 0.5)
            })

        return {
            "predictions": predictions,
            "bpCR_probability": round(probs[0], 6) if probs else None,
            "latency_ms": latency_ms,
            "num_patients": len(predictions)
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        server_running.dec()


@app.get("/metrics")
def metrics():
    from fastapi import Response
    return Response(content=generate_latest(), media_type="text/plain")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=9001)
