#!/usr/bin/env python3
"""Import the inference monitoring dashboard into Grafana."""
import json, urllib.request, urllib.error, sys

GRAFANA_URL = "http://localhost:38080"

# ---- Panel definitions ----
PANELS = [
    # Row 1: Summary stats
    {"id": 1, "gridPos": {"x": 0, "y": 0, "w": 4, "h": 3}, "type": "stat",
     "title": "总请求数", "fieldConfig": {"defaults": {"thresholds": {"mode": "absolute", "steps": [{"color": "blue"}]}}},
     "targets": [{"expr": "sum(inference_requests_total)", "legendFormat": "Total", "refId": "A"}]},
    {"id": 2, "gridPos": {"x": 4, "y": 0, "w": 4, "h": 3}, "type": "stat",
     "title": "Medical 请求", "fieldConfig": {"defaults": {"color": {"mode": "thresholds"}, "thresholds": {"mode": "absolute", "steps": [{"color": "green"}]}}},
     "targets": [{"expr": 'sum(inference_requests_total{model="medical"})', "legendFormat": "Medical", "refId": "A"}]},
    {"id": 3, "gridPos": {"x": 8, "y": 0, "w": 4, "h": 3}, "type": "stat",
     "title": "AlexNet 请求", "fieldConfig": {"defaults": {"color": {"mode": "thresholds"}, "thresholds": {"mode": "absolute", "steps": [{"color": "orange"}]}}},
     "targets": [{"expr": 'sum(inference_requests_total{model="alexnet"})', "legendFormat": "AlexNet", "refId": "A"}]},
    {"id": 4, "gridPos": {"x": 12, "y": 0, "w": 4, "h": 3}, "type": "stat",
     "title": "队列长度", "fieldConfig": {"defaults": {"thresholds": {"mode": "absolute", "steps": [{"color": "green"}, {"color": "orange", "value": 10}, {"color": "red", "value": 50}]}}},
     "targets": [{"expr": "inference_queue_length", "legendFormat": "Queue", "refId": "A"}]},
    {"id": 5, "gridPos": {"x": 16, "y": 0, "w": 4, "h": 3}, "type": "stat",
     "title": "失败请求", "fieldConfig": {"defaults": {"thresholds": {"mode": "absolute", "steps": [{"color": "green"}, {"color": "red", "value": 1}]}}},
     "targets": [{"expr": "sum(inference_failed_requests_total)", "legendFormat": "Failed", "refId": "A"}]},
    {"id": 6, "gridPos": {"x": 20, "y": 0, "w": 4, "h": 3}, "type": "stat",
     "title": "正在运行任务", "fieldConfig": {"defaults": {"thresholds": {"mode": "absolute", "steps": [{"color": "blue"}]}}},
     "targets": [{"expr": "medical_task_running + alexnet_task_running", "legendFormat": "Running", "refId": "A"}]},

    # Row 2: Throughput
    {"id": 10, "gridPos": {"x": 0, "y": 3, "w": 12, "h": 8}, "type": "timeseries",
     "title": "实时吞吐量 (请求/秒)", "fieldConfig": {"defaults": {"unit": "reqps", "custom": {"fillOpacity": 15, "lineWidth": 2, "showPoints": "never"}}},
     "targets": [
         {"expr": "sum(rate(inference_requests_total[1m]))", "legendFormat": "总吞吐量", "refId": "A"},
         {"expr": 'rate(inference_requests_total{model="medical"}[1m])', "legendFormat": "Medical", "refId": "B"},
         {"expr": 'rate(inference_requests_total{model="alexnet"}[1m])', "legendFormat": "AlexNet", "refId": "C"}]},
    {"id": 11, "gridPos": {"x": 12, "y": 3, "w": 12, "h": 8}, "type": "timeseries",
     "title": "累计请求趋势", "fieldConfig": {"defaults": {"unit": "none", "custom": {"fillOpacity": 10, "lineWidth": 2, "showPoints": "never"}}},
     "targets": [
         {"expr": "sum(inference_requests_total)", "legendFormat": "总计", "refId": "A"},
         {"expr": 'inference_requests_total{model="medical"}', "legendFormat": "Medical", "refId": "B"},
         {"expr": 'inference_requests_total{model="alexnet"}', "legendFormat": "AlexNet", "refId": "C"}]},

    # Row 3: Queue & Running
    {"id": 20, "gridPos": {"x": 0, "y": 11, "w": 8, "h": 6}, "type": "timeseries",
     "title": "队列长度", "fieldConfig": {"defaults": {"unit": "none", "custom": {"fillOpacity": 20, "lineWidth": 2, "showPoints": "never"}, "thresholds": {"mode": "absolute", "steps": [{"color": "green"}, {"color": "orange", "value": 10}, {"color": "red", "value": 50}]}}},
     "targets": [{"expr": "inference_queue_length", "legendFormat": "Queue", "refId": "A"}]},
    {"id": 21, "gridPos": {"x": 8, "y": 11, "w": 8, "h": 6}, "type": "timeseries",
     "title": "正在运行的任务数", "fieldConfig": {"defaults": {"unit": "none", "custom": {"fillOpacity": 20, "lineWidth": 2, "showPoints": "never"}}},
     "targets": [
         {"expr": "medical_task_running", "legendFormat": "Medical", "refId": "A"},
         {"expr": "alexnet_task_running", "legendFormat": "AlexNet", "refId": "B"}]},
    {"id": 22, "gridPos": {"x": 16, "y": 11, "w": 8, "h": 6}, "type": "timeseries",
     "title": "失败请求趋势", "fieldConfig": {"defaults": {"unit": "none", "custom": {"fillOpacity": 20, "lineWidth": 2, "showPoints": "never"}}},
     "targets": [
         {"expr": 'rate(inference_failed_requests_total{model="medical"}[1m])', "legendFormat": "Medical 失败率", "refId": "A"},
         {"expr": 'rate(inference_failed_requests_total{model="alexnet"}[1m])', "legendFormat": "AlexNet 失败率", "refId": "B"}]},

    # Row 4: Latency
    {"id": 30, "gridPos": {"x": 0, "y": 17, "w": 12, "h": 8}, "type": "timeseries",
     "title": "推理时延 P50/P95/P99", "fieldConfig": {"defaults": {"unit": "s", "custom": {"fillOpacity": 10, "lineWidth": 2, "showPoints": "never"}}},
     "targets": [
         {"expr": 'histogram_quantile(0.50, sum(rate(inference_latency_seconds_bucket{model="medical"}[2m])) by (le))', "legendFormat": "Medical P50", "refId": "A"},
         {"expr": 'histogram_quantile(0.95, sum(rate(inference_latency_seconds_bucket{model="medical"}[2m])) by (le))', "legendFormat": "Medical P95", "refId": "B"},
         {"expr": 'histogram_quantile(0.99, sum(rate(inference_latency_seconds_bucket{model="medical"}[2m])) by (le))', "legendFormat": "Medical P99", "refId": "C"},
         {"expr": 'histogram_quantile(0.50, sum(rate(inference_latency_seconds_bucket{model="alexnet"}[2m])) by (le))', "legendFormat": "AlexNet P50", "refId": "D"},
         {"expr": 'histogram_quantile(0.95, sum(rate(inference_latency_seconds_bucket{model="alexnet"}[2m])) by (le))', "legendFormat": "AlexNet P95", "refId": "E"},
         {"expr": 'histogram_quantile(0.99, sum(rate(inference_latency_seconds_bucket{model="alexnet"}[2m])) by (le))', "legendFormat": "AlexNet P99", "refId": "F"}]},
    {"id": 31, "gridPos": {"x": 12, "y": 17, "w": 12, "h": 8}, "type": "timeseries",
     "title": "各阶段时延 P95", "fieldConfig": {"defaults": {"unit": "s", "custom": {"fillOpacity": 10, "lineWidth": 2, "showPoints": "never"}}},
     "targets": [
         {"expr": 'histogram_quantile(0.95, sum(rate(inference_stage_latency_seconds_bucket{stage="worker"}[2m])) by (le))', "legendFormat": "Medical Worker", "refId": "A"},
         {"expr": 'histogram_quantile(0.95, sum(rate(inference_stage_latency_seconds_bucket{stage="server"}[2m])) by (le))', "legendFormat": "Medical Server", "refId": "B"},
         {"expr": 'histogram_quantile(0.95, sum(rate(inference_stage_latency_seconds_bucket{stage="part1"}[2m])) by (le))', "legendFormat": "AlexNet Part1", "refId": "C"},
         {"expr": 'histogram_quantile(0.95, sum(rate(inference_stage_latency_seconds_bucket{stage="part2"}[2m])) by (le))', "legendFormat": "AlexNet Part2", "refId": "D"}]},

    # Row 5: Per-service metrics
    {"id": 40, "gridPos": {"x": 0, "y": 25, "w": 6, "h": 6}, "type": "timeseries",
     "title": "Worker 请求速率", "fieldConfig": {"defaults": {"unit": "reqps", "custom": {"fillOpacity": 15, "lineWidth": 2, "showPoints": "never"}}},
     "targets": [{"expr": "rate(medical_worker_requests_total[1m])", "legendFormat": "Worker req/s", "refId": "A"}]},
    {"id": 41, "gridPos": {"x": 6, "y": 25, "w": 6, "h": 6}, "type": "timeseries",
     "title": "Server 请求速率", "fieldConfig": {"defaults": {"unit": "reqps", "custom": {"fillOpacity": 15, "lineWidth": 2, "showPoints": "never"}}},
     "targets": [{"expr": "rate(medical_server_requests_total[1m])", "legendFormat": "Server req/s", "refId": "A"}]},
    {"id": 42, "gridPos": {"x": 12, "y": 25, "w": 6, "h": 6}, "type": "timeseries",
     "title": "Part1 请求速率", "fieldConfig": {"defaults": {"unit": "reqps", "custom": {"fillOpacity": 15, "lineWidth": 2, "showPoints": "never"}}},
     "targets": [{"expr": "rate(alexnet_part1_requests_total[1m])", "legendFormat": "Part1 req/s", "refId": "A"}]},
    {"id": 43, "gridPos": {"x": 18, "y": 25, "w": 6, "h": 6}, "type": "timeseries",
     "title": "Part2 请求速率", "fieldConfig": {"defaults": {"unit": "reqps", "custom": {"fillOpacity": 15, "lineWidth": 2, "showPoints": "never"}}},
     "targets": [{"expr": "rate(alexnet_part2_requests_total[1m])", "legendFormat": "Part2 req/s", "refId": "A"}]},

    {"id": 44, "gridPos": {"x": 0, "y": 31, "w": 12, "h": 6}, "type": "timeseries",
     "title": "各服务正在处理的任务数", "fieldConfig": {"defaults": {"unit": "none", "custom": {"fillOpacity": 20, "lineWidth": 2, "showPoints": "never"}}},
     "targets": [
         {"expr": "medical_worker_running_tasks", "legendFormat": "Worker", "refId": "A"},
         {"expr": "medical_server_running_tasks", "legendFormat": "Server", "refId": "B"},
         {"expr": "alexnet_part1_running_tasks", "legendFormat": "Part1", "refId": "C"},
         {"expr": "alexnet_part2_running_tasks", "legendFormat": "Part2", "refId": "D"}]},
    {"id": 45, "gridPos": {"x": 12, "y": 31, "w": 12, "h": 6}, "type": "timeseries",
     "title": "各服务推理时延 P95", "fieldConfig": {"defaults": {"unit": "s", "custom": {"fillOpacity": 10, "lineWidth": 2, "showPoints": "never"}}},
     "targets": [
         {"expr": "histogram_quantile(0.95, sum(rate(medical_worker_latency_seconds_bucket[2m])) by (le))", "legendFormat": "Worker", "refId": "A"},
         {"expr": "histogram_quantile(0.95, sum(rate(medical_server_latency_seconds_bucket[2m])) by (le))", "legendFormat": "Server", "refId": "B"},
         {"expr": "histogram_quantile(0.95, sum(rate(alexnet_part1_latency_seconds_bucket[2m])) by (le))", "legendFormat": "Part1", "refId": "C"},
         {"expr": "histogram_quantile(0.95, sum(rate(alexnet_part2_latency_seconds_bucket[2m])) by (le))", "legendFormat": "Part2", "refId": "D"}]},

    # Task info panel
    {"id": 90, "gridPos": {"x": 0, "y": 37, "w": 24, "h": 6}, "type": "text",
     "title": "任务详情查询",
     "options": {"content": "# 查看运行中的任务\n\nGrafana 不直接存储 task_id（Prometheus 不适合高基数标签），请通过以下 API 查询：\n\n- **任务列表**: `kubectl exec deploy/scheduler -- curl -s http://localhost:8000/tasks`\n- **运行中的任务**: `kubectl exec deploy/scheduler -- curl -s http://localhost:8000/tasks/running`\n- **任务统计**: `kubectl exec deploy/scheduler -- curl -s http://localhost:8000/tasks/stats`\n- **单个任务**: `kubectl exec deploy/scheduler -- curl -s http://localhost:8000/task/result/{task_id}`", "mode": "markdown"}}
]

DASHBOARD = {
    "dashboard": {
        "title": "Edge Inference Platform — Real-time Monitoring",
        "uid": "edge-inference-monitoring",
        "timezone": "browser",
        "refresh": "5s",
        "tags": ["inference", "edge", "medical", "alexnet"],
        "panels": PANELS,
        "schemaVersion": 39,
        "time": {"from": "now-15m", "to": "now"}
    },
    "overwrite": True
}

data = json.dumps(DASHBOARD).encode()
req = urllib.request.Request(
    f"{GRAFANA_URL}/api/dashboards/db",
    data=data,
    headers={"Content-Type": "application/json"},
    method="POST"
)
try:
    with urllib.request.urlopen(req, timeout=10) as resp:
        result = json.loads(resp.read())
        print(f"Dashboard imported: {result.get('url', 'unknown')}")
        print(f"UID: edge-inference-monitoring")
except urllib.error.HTTPError as e:
    print(f"HTTP {e.code}: {e.read().decode()}")
    print("\nRun: kubectl port-forward svc/monitoring-grafana -n monitoring 30080:80")
