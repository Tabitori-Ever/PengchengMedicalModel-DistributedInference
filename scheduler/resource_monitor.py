"""
Resource monitor using Prometheus metrics for node-aware scheduling.
"""
import os
import time
import threading
import requests
from typing import Dict, Any, List

PROMETHEUS_URL = os.environ.get(
    "PROMETHEUS_URL",
    "http://monitoring-kube-prometheus-prometheus.monitoring.svc.cluster.local:9090"
)

# Node role mapping for edge/cloud topology
NODE_ROLES = {
    "node1": {"role": "edge", "hospital": "hospital-a"},
    "node2": {"role": "edge", "hospital": "hospital-b"},
    "node3": {"role": "cloud", "zone": "cloud-dc"},
    "desktop-jm5iec6": {"role": "control-plane"},
}


class ResourceMonitor:
    """Monitor node resources via Prometheus for scheduling decisions."""

    def __init__(self):
        self.prometheus_url = PROMETHEUS_URL
        self.cache = {}
        self.cache_time = 0
        self.cache_ttl = 5  # seconds
        self._cache_lock = threading.Lock()

    def _query_prometheus(self, query: str) -> float:
        try:
            url = f"{self.prometheus_url}/api/v1/query"
            response = requests.get(url, params={"query": query}, timeout=1)
            if response.status_code == 200:
                data = response.json()
                if data.get("status") == "success" and data.get("data", {}).get("result"):
                    return float(data["data"]["result"][0]["value"][1])
        except Exception:
            pass
        return 0.0

    def _quick_prometheus_check(self) -> bool:
        """Check if Prometheus is reachable (1s timeout)."""
        try:
            response = requests.get(f"{self.prometheus_url}/api/v1/status/config", timeout=1)
            return response.status_code == 200
        except Exception:
            return False

    def get_node_cpu_usage(self, node_name: str) -> float:
        query = (
            f'1 - sum(rate(node_cpu_seconds_total{{mode="idle",instance=~"{node_name}:.*"}}[5m])) '
            f'/ sum(rate(node_cpu_seconds_total{{instance=~"{node_name}:.*"}}[5m]))'
        )
        return self._query_prometheus(query)

    def get_node_memory_usage(self, node_name: str) -> float:
        query = (
            f'(node_memory_MemTotal_bytes{{instance=~"{node_name}:.*"}} '
            f'- node_memory_MemAvailable_bytes{{instance=~"{node_name}:.*"}}) '
            f'/ node_memory_MemTotal_bytes{{instance=~"{node_name}:.*"}}'
        )
        return self._query_prometheus(query)

    def get_node_gpu_usage(self, node_name: str) -> float:
        query = f'sum(nvidia_smi_utilization_gpu{{instance=~"{node_name}:.*"}}) / 100'
        return self._query_prometheus(query)

    def get_all_nodes(self) -> Dict[str, Dict[str, Any]]:
        """Get all nodes with resource usage and roles (thread-safe cached).

        When Prometheus is unreachable (local dev), returns sensible defaults
        immediately without blocking on timeouts.
        """
        # Fast path: return cached data if still fresh
        with self._cache_lock:
            if time.time() - self.cache_time < self.cache_ttl and self.cache:
                return dict(self.cache)

        # Check if Prometheus is reachable (1s timeout)
        prom_available = self._quick_prometheus_check()

        nodes_status = {}
        for node_name, role_info in NODE_ROLES.items():
            if prom_available:
                cpu = self.get_node_cpu_usage(node_name)
                memory = self.get_node_memory_usage(node_name)
                gpu = self.get_node_gpu_usage(node_name)
            else:
                # Local dev: assume nodes are idle and fully available
                cpu = 0.0
                memory = 0.0
                gpu = 0.0

            nodes_status[node_name] = {
                "cpu": round(cpu, 3), "memory": round(memory, 3), "gpu": round(gpu, 3),
                "cpu_free": round(max(0, 1 - cpu), 3),
                "memory_free": round(max(0, 1 - memory), 3),
                "gpu_free": round(max(0, 1 - gpu), 3),
                "role": role_info.get("role", "unknown"),
                "hospital": role_info.get("hospital"),
                "zone": role_info.get("zone"),
            }

        with self._cache_lock:
            self.cache = nodes_status
            self.cache_time = time.time()

        return nodes_status

    def get_edge_nodes(self) -> Dict[str, Dict[str, Any]]:
        """Get only edge nodes (hospital nodes)."""
        all_nodes = self.get_all_nodes()
        return {k: v for k, v in all_nodes.items() if v.get("role") == "edge"}

    def get_cloud_nodes(self) -> Dict[str, Dict[str, Any]]:
        """Get only cloud nodes."""
        all_nodes = self.get_all_nodes()
        return {k: v for k, v in all_nodes.items() if v.get("role") == "cloud"}

    def get_best_edge_node(self, hospital: str = None) -> str:
        """Select best edge node based on resource availability."""
        edge_nodes = self.get_edge_nodes()
        if hospital:
            edge_nodes = {k: v for k, v in edge_nodes.items()
                          if v.get("hospital") == hospital}

        if not edge_nodes:
            return None

        best_node = max(edge_nodes.items(),
                        key=lambda x: self._compute_score(x[1]))
        return best_node[0]

    def get_best_cloud_node(self) -> str:
        """Select best cloud node."""
        cloud_nodes = self.get_cloud_nodes()
        if not cloud_nodes:
            return "node3"  # fallback
        best_node = max(cloud_nodes.items(),
                        key=lambda x: self._compute_score(x[1]))
        return best_node[0]

    def _compute_score(self, node_status: dict) -> float:
        """Compute resource availability score (higher = better)."""
        return (
            0.4 * node_status.get("cpu_free", 0) +
            0.35 * node_status.get("gpu_free", 0) +
            0.25 * node_status.get("memory_free", 0)
        )

    def get_node_score(self, node_name: str) -> float:
        nodes = self.get_all_nodes()
        if node_name in nodes:
            return self._compute_score(nodes[node_name])
        return 0.0
