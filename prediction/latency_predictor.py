import numpy as np
from typing import Dict, Any, List
import json
import os

DEFAULT_MODEL_PATH = "latency_model.json"

# Lazy sklearn import — works with defaults without it
try:
    from sklearn.linear_model import LinearRegression
    from sklearn.preprocessing import StandardScaler
    _HAS_SKLEARN = True
except ImportError:
    LinearRegression = None
    StandardScaler = None
    _HAS_SKLEARN = False


class LatencyPredictor:
    def __init__(self):
        self.model = LinearRegression() if _HAS_SKLEARN else None
        self.scaler = StandardScaler() if _HAS_SKLEARN else None
        self.is_trained = False
        self.default_latencies = {
            "conv1": {"node1": 12, "node2": 18},
            "conv2": {"node1": 15, "node2": 21},
            "conv3": {"node1": 18, "node2": 25},
            "conv4": {"node1": 20, "node2": 28},
            "conv5": {"node1": 25, "node2": 31},
            "fc1": {"node1": 35, "node2": 22},
            "fc2": {"node1": 20, "node2": 15},
            "fc3": {"node1": 10, "node2": 8},
        }
        self.load_model()

    def load_model(self):
        if os.path.exists(DEFAULT_MODEL_PATH):
            try:
                with open(DEFAULT_MODEL_PATH, "r") as f:
                    data = json.load(f)
                    if "weights" in data:
                        self.model.coef_ = np.array(data["weights"])
                        self.model.intercept_ = data["intercept"]
                        self.is_trained = True
                        print("Loaded trained model")
            except Exception:
                pass

    def save_model(self):
        if self.is_trained:
            data = {
                "weights": self.model.coef_.tolist(),
                "intercept": self.model.intercept_
            }
            with open(DEFAULT_MODEL_PATH, "w") as f:
                json.dump(data, f)

    def train(self, X: List[List[float]], y: List[float]):
        if not _HAS_SKLEARN or self.model is None:
            print("[Predictor] sklearn not available — skipping training")
            return
        if len(X) < 2:
            print("Not enough data for training")
            return

        X_array = np.array(X)
        y_array = np.array(y)

        self.scaler.fit(X_array)
        X_scaled = self.scaler.transform(X_array)

        self.model.fit(X_scaled, y_array)
        self.is_trained = True
        self.save_model()
        print(f"Model trained with {len(X)} samples")

    def predict(self, features: List[float]) -> float:
        if not self.is_trained:
            return 0.0

        try:
            X = np.array([features])
            X_scaled = self.scaler.transform(X)
            return float(self.model.predict(X_scaled)[0])
        except Exception:
            return 0.0

    def predict_layer_latency(self, layer_info: Dict[str, Any], node_status: Dict[str, float]) -> float:
        flops = layer_info.get("flops", 0) / 1e6
        params = layer_info.get("params", 0) / 1e6
        cpu = node_status.get("cpu", 0.5)
        memory = node_status.get("memory", 0.5)
        gpu = node_status.get("gpu", 0.0)

        features = [flops, params, cpu, memory, gpu]
        predicted = self.predict(features)

        if predicted > 0:
            return round(predicted, 2)
        else:
            base_latency = 10 + flops * 0.001
            cpu_factor = 1 + cpu * 2
            return round(base_latency * cpu_factor, 2)

    def predict_latency_matrix(self, model_name: str, nodes_status: Dict[str, Dict[str, float]]) -> Dict[str, Dict[str, float]]:
        from prediction.model_profiler import get_model_layers

        layers = get_model_layers(model_name)
        latency_matrix = {}

        for layer in layers:
            latency_matrix[layer["name"]] = {}
            for node, status in nodes_status.items():
                if self.is_trained:
                    latency = self.predict_layer_latency(layer, status)
                else:
                    latency = self.default_latencies.get(layer["name"], {}).get(node, 10)
                    cpu_factor = 1 + status.get("cpu", 0.5) * 1.5
                    latency = round(latency * cpu_factor, 2)

                latency_matrix[layer["name"]][node] = latency

        return latency_matrix

    def get_prediction_model_info(self) -> Dict[str, Any]:
        if self.is_trained:
            return {
                "status": "trained",
                "weights": self.model.coef_.tolist(),
                "intercept": float(self.model.intercept_)
            }
        else:
            return {
                "status": "default",
                "message": "Using default latency values, model not trained"
            }