import os
import time
import requests
from fastapi import FastAPI, HTTPException
from typing import Dict, Any, List

app = FastAPI(title="Monitoring API", version="1.0")

PROMETHEUS_URL = os.environ.get("PROMETHEUS_URL", "http://prometheus.kube-system.svc.cluster.local:9090")


def query_prometheus(query: str) -> Any:
    try:
        response = requests.get(f"{PROMETHEUS_URL}/api/v1/query", params={"query": query}, timeout=10)
        if response.status_code == 200:
            data = response.json()
            if data.get("status") == "success":
                return data.get("data", {}).get("result", [])
    except Exception as e:
        print(f"Prometheus查询失败: {e}")
    return []


def calculate_average_latency(metric_name: str) -> float:
    query = f"avg(rate({metric_name}_sum[5m])) / avg(rate({metric_name}_count[5m]))"
    results = query_prometheus(query)
    if results:
        for result in results:
            try:
                return float(result["value"][1]) * 1000
            except:
                pass
    return 0.0


def get_node_metrics(node_name: str) -> Dict[str, float]:
    cpu_query = f"1 - sum(rate(node_cpu_seconds_total{{mode='idle',instance='{node_name}:9100'}}[5m])) / sum(rate(node_cpu_seconds_total{{instance='{node_name}:9100'}}[5m]))"
    mem_query = f"(node_memory_MemTotal_bytes{{instance='{node_name}:9100'}} - node_memory_MemAvailable_bytes{{instance='{node_name}:9100'}}) / node_memory_MemTotal_bytes{{instance='{node_name}:9100'}}"
    gpu_query = f"sum(nvidia_smi_utilization_gpu{{instance='{node_name}:9445'}}) / 100"

    cpu_results = query_prometheus(cpu_query)
    mem_results = query_prometheus(mem_query)
    gpu_results = query_prometheus(gpu_query)

    cpu = float(cpu_results[0]["value"][1]) if cpu_results else 0.5
    memory = float(mem_results[0]["value"][1]) if mem_results else 0.5
    gpu = float(gpu_results[0]["value"][1]) if gpu_results else 0.0

    return {"cpu": round(cpu, 3), "memory": round(memory, 3), "gpu": round(gpu, 3)}


def get_nodes() -> List[str]:
    results = query_prometheus("label_values(instance)")
    nodes = []
    for result in results:
        instance = result.get("value", "")
        if isinstance(instance, list):
            instance = instance[1]
        if ":9100" in instance:
            node_name = instance.replace(":9100", "")
            if node_name not in ("localhost", "127.0.0.1"):
                nodes.append(node_name)
    return list(set(nodes))


@app.get("/")
def health():
    return {"service": "monitoring", "status": "running", "timestamp": time.time()}


@app.get("/latency/conv")
def get_conv_latency():
    latency_ms = calculate_average_latency("alexnet_part1_latency_seconds")
    return {
        "layer": "conv",
        "component": "Part1",
        "latency_ms": latency_ms,
        "timestamp": time.time()
    }


@app.get("/latency/fc")
def get_fc_latency():
    latency_ms = calculate_average_latency("alexnet_part2_latency_seconds")
    return {
        "layer": "fc",
        "component": "Part2",
        "latency_ms": latency_ms,
        "timestamp": time.time()
    }


@app.get("/latency/total")
def get_total_latency():
    latency_ms = calculate_average_latency("alexnet_latency_seconds")
    return {
        "layer": "total",
        "component": "Scheduler",
        "latency_ms": latency_ms,
        "timestamp": time.time()
    }


@app.get("/latency/all")
def get_all_latencies():
    conv_latency = calculate_average_latency("alexnet_part1_latency_seconds")
    fc_latency = calculate_average_latency("alexnet_part2_latency_seconds")
    total_latency = calculate_average_latency("alexnet_latency_seconds")

    if total_latency > 0 and conv_latency > 0 and fc_latency > 0:
        communication_ms = max(0, total_latency - conv_latency - fc_latency)
    else:
        communication_ms = 0

    return {
        "conv_ms": conv_latency,
        "fc_ms": fc_latency,
        "communication_ms": communication_ms,
        "total_ms": total_latency,
        "timestamp": time.time()
    }


@app.get("/nodes")
def get_nodes_status():
    nodes = get_nodes()
    if not nodes:
        nodes = ["node1", "node2"]

    nodes_status = {}
    for node in nodes:
        nodes_status[node] = get_node_metrics(node)

    return {"nodes": nodes_status, "timestamp": time.time()}


@app.get("/node/{node_name}")
def get_node_status(node_name: str):
    metrics = get_node_metrics(node_name)
    if metrics.get("cpu") == 0.5 and metrics.get("memory") == 0.5:
        raise HTTPException(status_code=404, detail=f"Node {node_name} not found")
    return {"node": node_name, "metrics": metrics, "timestamp": time.time()}


@app.get("/metrics/raw")
def get_raw_metrics(query: str):
    results = query_prometheus(query)
    return {"query": query, "results": results, "timestamp": time.time()}


@app.get("/stats")
def get_statistics():
    all_latencies = get_all_latencies()
    nodes_status = get_nodes_status()

    return {
        "latencies": all_latencies,
        "nodes": nodes_status,
        "timestamp": time.time()
    }


@app.get("/latency/{metric_name}")
def get_latency(metric_name: str):
    latency_ms = calculate_average_latency(metric_name)
    return {
        "metric": metric_name,
        "latency_ms": latency_ms,
        "timestamp": time.time()
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8005)