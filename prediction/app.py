import os
import time
import threading
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Dict, Any, List

from prediction.resource_monitor import ResourceMonitor
from prediction.model_profiler import get_model_layers, get_model_partition_points
from prediction.latency_predictor import LatencyPredictor
from prediction.database import ProfilingDatabase

app = FastAPI(title="AlexNet Latency Prediction Service")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

monitor = ResourceMonitor()
predictor = LatencyPredictor()
database = ProfilingDatabase()


class PredictionRequest(BaseModel):
    model: str = "alexnet"


class ProfilingDataRequest(BaseModel):
    model: str = "alexnet"
    layer: str
    node: str
    latency: float
    cpu: float
    memory: float
    gpu: float = 0.0


class PartitionOptimizationRequest(BaseModel):
    model: str = "alexnet"
    communication_cost: float = 5.0


@app.get("/")
def health_check():
    return {
        "service": "prediction",
        "status": "running",
        "model_trained": predictor.is_trained,
        "profiling_data_count": database.get_data_count()
    }


@app.get("/nodes")
def get_nodes_status():
    return monitor.get_overall_status()


@app.get("/model/{model_name}")
def get_model_info(model_name: str):
    layers = get_model_layers(model_name)
    partition_points = get_model_partition_points(model_name)
    return {
        "model": model_name,
        "layers": layers,
        "partition_points": partition_points,
        "total_layers": len(layers)
    }


@app.get("/predict/{model_name}")
def predict_latency(model_name: str):
    nodes_status = monitor.get_nodes()

    if not nodes_status:
        raise HTTPException(status_code=400, detail="No nodes available")

    latency_matrix = predictor.predict_latency_matrix(model_name, nodes_status)

    return {
        "model": model_name,
        "nodes": nodes_status,
        "latency_matrix": latency_matrix,
        "prediction_model": predictor.get_prediction_model_info(),
        "timestamp": time.time()
    }


@app.post("/profiling")
def add_profiling_data(data: ProfilingDataRequest):
    database.add_latency_record(
        model_name=data.model,
        layer_name=data.layer,
        node=data.node,
        latency_ms=data.latency,
        cpu=data.cpu,
        memory=data.memory,
        gpu=data.gpu
    )

    return {
        "status": "success",
        "message": "Profiling data added",
        "total_records": database.get_data_count()
    }


@app.get("/profiling")
def get_profiling_data(limit: int = 100):
    data = database.get_profiling_data(limit)
    return {
        "count": len(data),
        "data": data
    }


@app.post("/train")
def train_model():
    X, y = database.get_training_data()

    if len(X) < 2:
        return {
            "status": "skipped",
            "message": f"Not enough training data (need at least 2 samples, got {len(X)})",
            "using_default": True
        }

    predictor.train(X, y)

    return {
        "status": "success",
        "message": f"Model trained with {len(X)} samples",
        "model_info": predictor.get_prediction_model_info()
    }


@app.post("/optimize")
def optimize_partition(request: PartitionOptimizationRequest):
    nodes_status = monitor.get_nodes()

    if not nodes_status:
        raise HTTPException(status_code=400, detail="No nodes available")

    latency_matrix = predictor.predict_latency_matrix(request.model, nodes_status)
    layers = get_model_layers(request.model)

    if len(layers) < 2:
        raise HTTPException(status_code=400, detail="Model has too few layers for partitioning")

    nodes = list(nodes_status.keys())
    best_partition = None
    best_latency = float("inf")

    for cut_point in range(1, len(layers)):
        part1_layers = layers[:cut_point]
        part2_layers = layers[cut_point:]

        for node1 in nodes:
            for node2 in nodes:
                part1_time = sum(latency_matrix.get(l["name"], {}).get(node1, 0) for l in part1_layers)
                part2_time = sum(latency_matrix.get(l["name"], {}).get(node2, 0) for l in part2_layers)
                total_time = max(part1_time, part2_time) + request.communication_cost

                if total_time < best_latency:
                    best_latency = total_time
                    best_partition = {
                        "cut_point": cut_point,
                        "part1_node": node1,
                        "part2_node": node2,
                        "part1_layers": [l["name"] for l in part1_layers],
                        "part2_layers": [l["name"] for l in part2_layers],
                        "part1_latency": part1_time,
                        "part2_latency": part2_time,
                        "communication_cost": request.communication_cost,
                        "total_latency": total_time
                    }

    return {
        "model": request.model,
        "latency_matrix": latency_matrix,
        "best_partition": best_partition,
        "nodes_status": nodes_status
    }


@app.get("/metrics")
def get_metrics():
    from prometheus_client import generate_latest
    return generate_latest()


@app.get("/latency/conv")
def get_monitoring_conv_latency():
    latency = monitor.get_conv_latency()
    return {
        "source": "monitoring-api",
        "layer": "conv",
        "latency_ms": latency,
        "timestamp": time.time()
    }


@app.get("/latency/fc")
def get_monitoring_fc_latency():
    latency = monitor.get_fc_latency()
    return {
        "source": "monitoring-api",
        "layer": "fc",
        "latency_ms": latency,
        "timestamp": time.time()
    }


@app.get("/latency/total")
def get_monitoring_total_latency():
    latency = monitor.get_total_latency()
    return {
        "source": "monitoring-api",
        "layer": "total",
        "latency_ms": latency,
        "timestamp": time.time()
    }


@app.get("/latency/all")
def get_monitoring_all_latencies():
    latency_data = monitor.get_latency_data()
    latency_data["source"] = "monitoring-api"
    latency_data["timestamp"] = time.time()
    return latency_data


def scheduled_training():
    while True:
        time.sleep(300)
        X, y = database.get_training_data()
        if len(X) >= 10:
            predictor.train(X, y)


if __name__ == "__main__":
    import uvicorn

    training_thread = threading.Thread(target=scheduled_training, daemon=True)
    training_thread.start()

    uvicorn.run(app, host="0.0.0.0", port=8003)