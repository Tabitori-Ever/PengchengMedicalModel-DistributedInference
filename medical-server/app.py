"""
Medical Server Service (Cloud - Data Center).
Implements the model back-end: ResNet layer1-4 + fusion + classifier.
Deployed on node3 (cloud node with role=cloud).
Handles high-computation inference.
"""
import base64
import json
import os
import time
from typing import Any

import numpy as np
import torch
import torch.nn as nn
from fastapi import FastAPI, HTTPException, Request
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel
from typing import List, Optional
from prometheus_client import Counter, Histogram, Gauge, generate_latest

from models_my import DoubleTower

app = FastAPI(title="Medical Server - Cloud Model Backend")

# ---- Model Loading ----
MODEL_PATH = os.environ.get("MODEL_PATH", "/app/weights/best_model_fold1.pth")
DEVICE = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")

server_model = None
FULL_MODEL = None


class DoubleTowerServerBack(nn.Module):
    """Model back-end deployed on cloud server.
    Runs ResNet layer1-4, multi-modal fusion, and classifier."""

    def __init__(self, full_model: DoubleTower):
        super().__init__()
        dce_resnet = full_model.image_processor.encoder_dce.model
        dwi_resnet = full_model.image_processor.encoder_dwi.model

        # v1.2: 前端已在边端完成 conv1..layer3（切分点下移以缩小中间特征体积），
        # 因此后端只接 layer4 及其后的融合/分类部分。
        self.dce_backbone = nn.Sequential(dce_resnet.layer4)
        self.dwi_backbone = nn.Sequential(dwi_resnet.layer4)

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
    # v1.4: 保留完整模型——数据中心也能整段跑完诊断，用于「数据并行」分片
    # （把批量患者分别交给医院与云端各跑完整模型，聚合两端算力）。
    global FULL_MODEL
    FULL_MODEL = full_model
    FULL_MODEL.eval()
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
# v3.3: each feature field may be either the legacy nested list or a compact
# {"__f32b64__": "<base64 float32>", "shape": [...]} object. The edge worker
# sends ~8MB of nested float text per diagnosis, whose JSON encode/decode cost
# dwarfed the actual inference; base64 float32 keeps the payload 4x smaller and
# moves the conversion into C.
class MedicalServerRequest(BaseModel):
    # 后端（拆分模式）用 dce_features/...；整段模式（/infer_full）用 dce_image/...
    dce_features: Any = None                     # [B, 64, 56, 56]
    dwi_features: Any = None                     # [B, 64, 56, 56]
    clinical_features: Any = None                # [B, 256]
    radiomics_features: Any = None               # [B, 256]
    dce_image: Any = None                        # 整段模式输入（与原图同形）
    dwi_image: Any = None
    clinical: Any = None
    radiomics: Any = None
    patient_ids: Optional[List[str]] = None


def _to_tensor(value, device):
    """Nested list or {"__f32b64__", "shape"} -> float32 tensor."""
    if isinstance(value, dict) and "__f32b64__" in value:
        raw = base64.b64decode(value["__f32b64__"])
        flat = np.frombuffer(raw, dtype=np.float32)
        shape = tuple(value.get("shape") or (flat.size,))
        return torch.from_numpy(flat.reshape(shape).copy()).to(device)
    return torch.tensor(value, dtype=torch.float32).to(device)


# ---- API Endpoints ----
@app.post("/infer_full")
def infer_full(req: MedicalServerRequest):
    """v1.4: 数据中心整段执行完整模型（数据并行分片用）。

    与边端的 /medical/infer 同构：接收原始影像/临床/组学输入，返回预测。
    用于把「批量诊断」的患者分片给医院与云端各自独立完成，
    从而聚合两端算力，而不是把同一个模型切成前后两段串行执行。
    """
    if FULL_MODEL is None:
        raise HTTPException(status_code=503, detail="Full model not loaded")
    server_requests.inc()
    server_running.inc()
    try:
        start = time.perf_counter()
        dce = _to_tensor(req.dce_image, DEVICE)
        dwi = _to_tensor(req.dwi_image, DEVICE)
        clin = _to_tensor(req.clinical, DEVICE)
        rad = _to_tensor(req.radiomics, DEVICE)
        for t in (dce, dwi, clin, rad):
            if t.ndim == 3:
                t.unsqueeze_(0)
        if clin.ndim == 1:
            clin.unsqueeze_(0)
        if rad.ndim == 1:
            rad.unsqueeze_(0)
        with torch.inference_mode():
            logits = FULL_MODEL(dce, dwi, clin, rad)
            probs = torch.softmax(logits, dim=1)[:, 1]
        latency_ms = (time.perf_counter() - start) * 1000
        server_latency.observe(latency_ms / 1000)
        probs = probs.cpu().numpy().tolist()
        ids = req.patient_ids or [f"p{i}" for i in range(len(probs))]
        predictions = [{"patient_id": str(pid), "bpCR_probability": round(float(p), 6),
                        "prediction": int(float(p) >= 0.5)}
                       for pid, p in zip(ids, probs)]
        return {"predictions": predictions,
                "bpCR_probability": round(float(probs[0]), 6) if probs else None,
                "latency_ms": latency_ms, "num_patients": len(predictions),
                "executor": "datacenter", "model": "full"}
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        server_running.dec()



@app.post("/infer_raw")
async def infer_raw(request: Request):
    # 读 body 是 IO（async），张量转换 + 推理是阻塞 CPU 计算，必须交给线程池，
    # 否则单个 async 端点会把整个 uvicorn 事件循环卡住、并发请求被迫排队。
    body = await request.body()
    return await run_in_threadpool(_infer_raw_sync, body)


def _infer_raw_sync(body: bytes) -> dict:
    """v1.2: 接收「包头 JSON + 原始 float32 缓冲」的中间特征（无 base64、长连接）。

    实测 pod 间每请求固定开销约 42ms，base64 还会把体积放大 33%；
    边端把特征以原始字节直接发来，这里零解码开销地切成张量。
    """
    if not model_loaded:
        raise HTTPException(status_code=503, detail="Model not loaded")
    server_requests.inc()
    server_running.inc()
    try:
        start = time.perf_counter()
        if len(body) < 8:
            raise HTTPException(status_code=400, detail="payload too short")
        header_len = int.from_bytes(body[0:4], "little")
        header = json.loads(body[8:8 + header_len].decode())
        blob = body[8 + header_len:]
        fields = header.get("fields") or {}
        tensors = {}
        for key, meta in fields.items():
            off, ln = int(meta["offset"]), int(meta["length"])
            arr = np.frombuffer(blob[off:off + ln], dtype="<f4")
            shape = tuple(meta.get("shape") or (arr.size,))
            tensors[key] = torch.from_numpy(arr.reshape(shape).copy()).to(DEVICE)
        dce, dwi = tensors["dce_features"], tensors["dwi_features"]
        clinical, radiomics = tensors["clinical_features"], tensors["radiomics_features"]
        with torch.inference_mode():
            logits = server_model(dce, dwi, clinical, radiomics)
            probabilities = torch.softmax(logits, dim=1)[:, 1]
        latency_ms = (time.perf_counter() - start) * 1000
        server_latency.observe(latency_ms / 1000)
        probs = probabilities.cpu().numpy().tolist()
        ids = header.get("patient_ids") or [f"p{i}" for i in range(len(probs))]
        predictions = [{"patient_id": str(pid), "bpCR_probability": round(float(p), 6),
                        "prediction": int(float(p) >= 0.5)}
                       for pid, p in zip(ids, probs)]
        return {"predictions": predictions,
                "bpCR_probability": round(float(probs[0]), 6) if probs else None,
                "latency_ms": latency_ms, "num_patients": len(predictions),
                "transport": "f32raw"}
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        server_running.dec()



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

        dce = _to_tensor(req.dce_features, DEVICE)
        dwi = _to_tensor(req.dwi_features, DEVICE)
        clinical = _to_tensor(req.clinical_features, DEVICE)
        radiomics = _to_tensor(req.radiomics_features, DEVICE)

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
