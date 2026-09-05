"""
AlexNet Part1 Service (Conv Layers).
Can be deployed on edge or cloud nodes.
Runs AlexNet features + avgpool: Conv1-Conv5 → avgpool.
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
from common.model import AlexNetPart1

app = FastAPI(title="AlexNet Part1 - Conv Layers")

# Load model
full_model = alexnet(weights=AlexNet_Weights.IMAGENET1K_V1)
model = AlexNetPart1(full_model)

MODEL_PATH = "/app/common/part1.pt"
if not os.path.exists(MODEL_PATH):
    MODEL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "../common/part1.pt")

model.load_state_dict(torch.load(MODEL_PATH, map_location="cpu"))
model.eval()
print("[Part1] Model loaded")

# Metrics
running_tasks = Gauge("alexnet_part1_running_tasks", "Current running tasks on Part1")
requests_total = Counter("alexnet_part1_requests_total", "Total Part1 requests")
latency = Histogram("alexnet_part1_latency_seconds", "Part1 (Conv) inference latency")


class ImageRequest(BaseModel):
    image: list


@app.get("/")
def health():
    return {"service": "part1", "status": "running", "role": "edge/cloud"}


@app.get("/health")
def health_check():
    return {"service": "part1", "status": "ok"}


@app.post("/infer")
def infer(req: ImageRequest):
    requests_total.inc()
    running_tasks.inc()

    try:
        start = time.perf_counter()

        image = np.asarray(req.image, dtype=np.float32)
        image = torch.tensor(image)
        if image.ndim == 3:
            image = image.unsqueeze(0)

        with torch.no_grad():
            feature = model(image)

        conv_time = (time.perf_counter() - start) * 1000
        latency.observe(conv_time / 1000)

        return {
            "feature": feature.numpy().tolist(),
            "latency_ms": conv_time
        }
    finally:
        running_tasks.dec()


@app.get("/metrics")
def metrics():
    return Response(content=generate_latest(), media_type="text/plain")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
