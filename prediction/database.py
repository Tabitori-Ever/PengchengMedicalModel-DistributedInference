import os
import json
import redis
import time
from typing import List, Dict, Any

REDIS_HOST = os.environ.get("REDIS_HOST", "localhost")
REDIS_PORT = int(os.environ.get("REDIS_PORT", 6379))


class ProfilingDatabase:
    def __init__(self):
        self.redis_client = None
        self.connected = False
        self.connect()

    def connect(self):
        try:
            self.redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
            self.redis_client.ping()
            self.connected = True
            print("Connected to Redis")
        except Exception:
            print("Redis not available, using memory storage")
            self.redis_client = None
            self.connected = False
            self.memory_data = []

    def add_profiling_data(self, data: Dict[str, Any]):
        data["timestamp"] = time.time()
        data["id"] = f"profile:{int(time.time())}:{id(data)}"

        if self.connected:
            try:
                self.redis_client.rpush("profiling_data", json.dumps(data))
                self.redis_client.ltrim("profiling_data", -10000, -1)
            except Exception:
                pass
        else:
            self.memory_data.append(data)
            if len(self.memory_data) > 10000:
                self.memory_data = self.memory_data[-10000:]

    def get_profiling_data(self, limit: int = 100) -> List[Dict[str, Any]]:
        data = []
        if self.connected:
            try:
                raw_data = self.redis_client.lrange("profiling_data", -limit, -1)
                for item in reversed(raw_data):
                    try:
                        data.append(json.loads(item))
                    except Exception:
                        pass
            except Exception:
                pass
        else:
            data = self.memory_data[-limit:]

        return data

    def get_training_data(self) -> (List[List[float]], List[float]):
        all_data = self.get_profiling_data(1000)
        X = []
        y = []

        for item in all_data:
            flops = item.get("flops", 0) / 1e6
            params = item.get("params", 0) / 1e6
            cpu = item.get("cpu", 0.5)
            memory = item.get("memory", 0.5)
            gpu = item.get("gpu", 0.0)
            latency = item.get("latency", 0)

            if latency > 0:
                X.append([flops, params, cpu, memory, gpu])
                y.append(latency)

        return X, y

    def get_data_count(self) -> int:
        if self.connected:
            try:
                return self.redis_client.llen("profiling_data")
            except Exception:
                pass
        return len(self.memory_data) if hasattr(self, 'memory_data') else 0

    def clear_data(self):
        if self.connected:
            try:
                self.redis_client.delete("profiling_data")
            except Exception:
                pass
        else:
            self.memory_data = []

    def add_latency_record(self, model_name: str, layer_name: str, node: str, latency_ms: float,
                           cpu: float, memory: float, gpu: float = 0.0):
        data = {
            "model": model_name,
            "layer": layer_name,
            "node": node,
            "latency": latency_ms,
            "cpu": cpu,
            "memory": memory,
            "gpu": gpu,
            "flops": 0,
            "params": 0
        }

        try:
            from prediction.model_profiler import get_layer_info
            layer_info = get_layer_info(model_name, layer_name)
            if layer_info:
                data["flops"] = layer_info.get("flops", 0)
                data["params"] = layer_info.get("params", 0)
        except Exception:
            pass

        self.add_profiling_data(data)