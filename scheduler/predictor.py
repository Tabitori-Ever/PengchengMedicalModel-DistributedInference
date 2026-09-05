"""
Latency predictor for multi-model inference scheduling.
Extends the AlexNet predictor to support medical model layers.
"""
import numpy as np
from typing import Dict, Any, List
import json
import os

# Lazy imports for sklearn — scheduler works with defaults even without it
LinearRegression = None
StandardScaler = None

def _ensure_sklearn():
    global LinearRegression, StandardScaler
    if LinearRegression is None:
        try:
            from sklearn.linear_model import LinearRegression as LR
            from sklearn.preprocessing import StandardScaler as SS
            LinearRegression = LR
            StandardScaler = SS
            return True
        except ImportError:
            return False
    return True

DEFAULT_MODEL_PATH = "latency_model.json"

MEDICAL_MODEL_LAYERS = {
    "worker": {"name": "medical_worker", "flops": 34952512, "params": 23296},
    "server": {"name": "medical_server", "flops": 129653760, "params": 307392},
}

ALEXNET_LAYERS = {
    "part1": {"name": "alexnet_part1", "flops": 471607616, "params": 2321536},
    "part2": {"name": "alexnet_part2", "flops": 58621952, "params": 58621952},
}


class LatencyPredictor:
    """Predict inference latency for scheduling decisions."""

    def __init__(self):
        self.model = None
        self.scaler = None
        self.is_trained = False
        self._sklearn_ok = _ensure_sklearn()
        if self._sklearn_ok:
            self.model = LinearRegression()
            self.scaler = StandardScaler()

        self.default_latencies = {
            "alexnet_part1": {"node1": 15, "node2": 18, "node3": 12},
            "alexnet_part2": {"node1": 20, "node2": 22, "node3": 10},
            "medical_worker": {"node1": 45, "node2": 48, "node3": 35},
            "medical_server": {"node1": 120, "node2": 125, "node3": 40},
        }
        self.load_model()

    def load_model(self):
        if not self._sklearn_ok or self.model is None:
            return
        if os.path.exists(DEFAULT_MODEL_PATH):
            try:
                with open(DEFAULT_MODEL_PATH, "r") as f:
                    data = json.load(f)
                    if "weights" in data:
                        self.model.coef_ = np.array(data["weights"])
                        self.model.intercept_ = data["intercept"]
                        self.is_trained = True
                        print("[Predictor] Loaded trained model")
            except Exception:
                pass

    def train(self, X: List[List[float]], y: List[float]):
        if not self._sklearn_ok:
            print("[Predictor] sklearn not available — skipping training")
            return
        if len(X) < 2:
            return
        X_array = np.array(X)
        y_array = np.array(y)
        self.scaler.fit(X_array)
        self.model.fit(self.scaler.transform(X_array), y_array)
        self.is_trained = True
        self.save_model()
        print(f"[Predictor] Trained with {len(X)} samples")

    def save_model(self):
        if self.is_trained and self._sklearn_ok:
            data = {
                "weights": self.model.coef_.tolist(),
                "intercept": self.model.intercept_
            }
            with open(DEFAULT_MODEL_PATH, "w") as f:
                json.dump(data, f)

    def train(self, X: List[List[float]], y: List[float]):
        if len(X) < 2:
            return
        X_array = np.array(X)
        y_array = np.array(y)
        self.scaler.fit(X_array)
        self.model.fit(self.scaler.transform(X_array), y_array)
        self.is_trained = True
        self.save_model()
        print(f"[Predictor] Trained with {len(X)} samples")

    def predict_latency(self, model: str, stage: str, node_name: str,
                        node_status: dict = None) -> float:
        """Predict latency for a model stage on a specific node."""
        layer_name = f"{model}_{stage}"

        if self.is_trained and node_status:
            flops = 0
            if model == "alexnet":
                flops = ALEXNET_LAYERS.get(stage, {}).get("flops", 0)
            elif model == "medical":
                flops = MEDICAL_MODEL_LAYERS.get(stage, {}).get("flops", 0)

            features = [
                flops / 1e6,
                0,
                node_status.get("cpu", 0.5),
                node_status.get("memory", 0.5),
                node_status.get("gpu", 0.0),
            ]
            try:
                X = np.array([features])
                X_scaled = self.scaler.transform(X)
                return float(self.model.predict(X_scaled)[0])
            except Exception:
                pass

        # Fallback defaults
        return self.default_latencies.get(layer_name, {}).get(node_name, 50)

    def predict_pipeline_latency(self, model: str, worker_node: str,
                                  server_node: str, nodes: dict) -> dict:
        """Predict full pipeline latency for a model."""
        if model == "medical":
            worker_latency = self.predict_latency(
                "medical", "worker", worker_node,
                nodes.get(worker_node, {})
            )
            server_latency = self.predict_latency(
                "medical", "server", server_node,
                nodes.get(server_node, {})
            )
            return {
                "worker_ms": worker_latency,
                "server_ms": server_latency,
                "transfer_ms": 10,
                "total_ms": worker_latency + server_latency + 10
            }
        elif model == "alexnet":
            part1_latency = self.predict_latency(
                "alexnet", "part1", worker_node,
                nodes.get(worker_node, {})
            )
            part2_latency = self.predict_latency(
                "alexnet", "part2", server_node,
                nodes.get(server_node, {})
            )
            return {
                "part1_ms": part1_latency,
                "part2_ms": part2_latency,
                "transfer_ms": 5,
                "total_ms": part1_latency + part2_latency + 5
            }

    def get_model_info(self) -> dict:
        if self.is_trained:
            return {"status": "trained"}
        return {"status": "default", "message": "Using default latency values"}
