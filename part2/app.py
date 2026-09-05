"""
AlexNet Part2 Service (FC Layers).
Can be deployed on edge or cloud nodes.
Runs classification head: Flatten → FC1-FC3 → output.
"""
import os
import time
import numpy as np
import torch
from fastapi import FastAPI, Response
from pydantic import BaseModel
from torchvision.models import alexnet, AlexNet_Weights
from prometheus_client import Counter, Histogram, Gauge, generate_latest

import sys
sys.path.insert(0, "/app")
from common.model import AlexNetPart2

app = FastAPI(title="AlexNet Part2 - FC Layers")

categories = AlexNet_Weights.IMAGENET1K_V1.meta["categories"]

# Load model
full_model = alexnet(weights=AlexNet_Weights.IMAGENET1K_V1)
model = AlexNetPart2(full_model)

MODEL_PATH = "/app/common/part2.pt"
if not os.path.exists(MODEL_PATH):
    MODEL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "../common/part2.pt")

model.load_state_dict(torch.load(MODEL_PATH, map_location="cpu"))
model.eval()
print("[Part2] Model loaded")

# Metrics
running_tasks = Gauge("alexnet_part2_running_tasks", "Current running tasks on Part2")
requests_total = Counter("alexnet_part2_requests_total", "Total Part2 requests")
latency = Histogram("alexnet_part2_latency_seconds", "Part2 (FC) inference latency")


class FeatureRequest(BaseModel):
    feature: list


@app.get("/")
def health():
    return {"service": "part2", "status": "running", "role": "edge/cloud"}


@app.get("/health")
def health_check():
    return {"service": "part2", "status": "ok"}


@app.post("/infer")
def infer(req: FeatureRequest):
    requests_total.inc()
    running_tasks.inc()

    try:
        feature = np.asarray(req.feature, dtype=np.float32)
        feature = torch.tensor(feature)

        start = time.perf_counter()
        with torch.no_grad():
            output = model(feature)
        infer_time = (time.perf_counter() - start) * 1000
        latency.observe(infer_time / 1000)

        logits = output.numpy()[0]
        # stable softmax -> real probability in [0,1] (was: raw logit, e.g.
        # 4.86 -> displayed as "485.7%")
        exp = np.exp(logits - logits.max())
        probs = exp / exp.sum()
        idx = int(probs.argmax())

        return {
            "class_id": int(idx),
            "class_name": categories[idx],
            "score": float(probs[idx]),
            "logits": logits.tolist(),
            "latency_ms": infer_time
        }
    finally:
        running_tasks.dec()


@app.get("/metrics")
def metrics():
    return Response(content=generate_latest(), media_type="text/plain")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8002)
