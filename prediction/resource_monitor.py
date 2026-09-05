import os
import time
import requests
from typing import Dict, Any

PROMETHEUS_URL = os.environ.get("PROMETHEUS_URL", "http://prometheus.kube-system.svc.cluster.local:9090")
MONITORING_URL = os.environ.get("MONITORING_URL", "http://monitoring-service:8005")
KUBE_API_URL = os.environ.get("KUBE_API_URL", "https://kubernetes.default.svc.cluster.local")


class ResourceMonitor:
    def __init__(self):
        self.prometheus_url = PROMETHEUS_URL
        self.monitoring_url = MONITORING_URL
        self.cache = {}
        self.cache_time = 0
        self.cache_ttl = 10

    def _fetch_from_prometheus(self, query: str) -> float:
        try:
            url = f"{self.prometheus_url}/api/v1/query"
            response = requests.get(url, params={"query": query}, timeout=10)
            if response.status_code == 200:
                data = response.json()
                if data.get("status") == "success" and data.get("data", {}).get("result"):
                    result = data["data"]["result"][0]
                    return float(result["value"][1])
        except Exception:
            pass
        return 0.0

    def get_node_cpu_usage(self, node_name: str) -> float:
        query = f"1 - sum(rate(node_cpu_seconds_total{{mode='idle',instance='{node_name}:9100'}}[5m])) / sum(rate(node_cpu_seconds_total{{instance='{node_name}:9100'}}[5m]))"
        return self._fetch_from_prometheus(query)

    def get_node_memory_usage(self, node_name: str) -> float:
        query = f"(node_memory_MemTotal_bytes{{instance='{node_name}:9100'}} - node_memory_MemAvailable_bytes{{instance='{node_name}:9100'}}) / node_memory_MemTotal_bytes{{instance='{node_name}:9100'}}"
        return self._fetch_from_prometheus(query)

    def get_node_gpu_usage(self, node_name: str) -> float:
        query = f"sum(nvidia_smi_utilization_gpu{{instance='{node_name}:9445'}}) / 100"
        return self._fetch_from_prometheus(query)

    def get_nodes_from_prometheus(self) -> list:
        try:
            url = f"{self.prometheus_url}/api/v1/label/instance/values"
            response = requests.get(url, timeout=10)
            if response.status_code == 200:
                data = response.json()
                if data.get("status") == "success":
                    nodes = []
                    for instance in data["data"]:
                        if ":9100" in instance:
                            node_name = instance.replace(":9100", "")
                            nodes.append(node_name)
                    return list(set(nodes))
        except Exception:
            pass
        return []

    def get_nodes(self) -> Dict[str, Dict[str, float]]:
        current_time = time.time()
        if current_time - self.cache_time < self.cache_ttl and self.cache:
            return self.cache

        nodes_status = {}
        prometheus_nodes = self.get_nodes_from_prometheus()

        if prometheus_nodes:
            for node in prometheus_nodes:
                if node in ("localhost", "127.0.0.1"):
                    continue
                cpu = self.get_node_cpu_usage(node)
                memory = self.get_node_memory_usage(node)
                gpu = self.get_node_gpu_usage(node)

                nodes_status[node] = {
                    "cpu": round(cpu, 3),
                    "memory": round(memory, 3),
                    "gpu": round(gpu, 3)
                }
        else:
            nodes_status = {
                "node1": {"cpu": 0.5, "memory": 0.6, "gpu": 0.0},
                "node2": {"cpu": 0.3, "memory": 0.4, "gpu": 0.0}
            }

        self.cache = nodes_status
        self.cache_time = current_time
        return nodes_status

    def get_queue_length(self) -> int:
        try:
            response = requests.get("http://scheduler-service:8000/tasks/stats", timeout=5)
            if response.status_code == 200:
                data = response.json()
                return data.get("total", 0)
        except Exception:
            pass
        return 0

    def get_overall_status(self) -> Dict[str, Any]:
        nodes = self.get_nodes()
        return {
            "nodes": nodes,
            "queue_length": self.get_queue_length(),
            "timestamp": time.time()
        }

    def get_latency_data(self) -> Dict[str, float]:
        try:
            response = requests.get(f"{self.monitoring_url}/latency/all", timeout=10)
            if response.status_code == 200:
                return response.json()
        except Exception:
            pass
        return {
            "conv_ms": 0,
            "fc_ms": 0,
            "communication_ms": 0,
            "total_ms": 0
        }

    def get_conv_latency(self) -> float:
        try:
            response = requests.get(f"{self.monitoring_url}/latency/conv", timeout=10)
            if response.status_code == 200:
                return response.json().get("latency_ms", 0)
        except Exception:
            pass
        return 0

    def get_fc_latency(self) -> float:
        try:
            response = requests.get(f"{self.monitoring_url}/latency/fc", timeout=10)
            if response.status_code == 200:
                return response.json().get("latency_ms", 0)
        except Exception:
            pass
        return 0

    def get_total_latency(self) -> float:
        try:
            response = requests.get(f"{self.monitoring_url}/latency/total", timeout=10)
            if response.status_code == 200:
                return response.json().get("latency_ms", 0)
        except Exception:
            pass
        return 0