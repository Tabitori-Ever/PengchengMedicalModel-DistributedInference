"""Shared Prometheus metric definitions for the scheduler.

Import-safe for both api.py and scheduler.py (no circular dependency).
"""
from prometheus_client import Counter, Histogram, Gauge

# ---- Queue & Task Counts ----
inference_queue_length = Gauge(
    "inference_queue_length",
    "Current inference task queue length"
)
task_running = Gauge(
    "task_running",
    "Currently running tasks per model",
    ["model"]
)

# ---- Node Resources ----
node_available_cpu = Gauge(
    "node_available_cpu",
    "Available CPU per node",
    ["node"]
)
node_available_gpu = Gauge(
    "node_available_gpu",
    "Available GPU per node",
    ["node"]
)

# ---- Request Counters (per model) ----
requests_total = Counter(
    "inference_requests_total",
    "Total inference requests",
    ["model"]
)
failed_requests = Counter(
    "inference_failed_requests_total",
    "Total failed requests",
    ["model"]
)

# ---- Inference Latency Histogram ----
inference_latency = Histogram(
    "inference_latency_seconds",
    "End-to-end inference latency",
    ["model"],
    buckets=[0.5, 1.0, 2.0, 3.0, 5.0, 10.0, 20.0, 30.0, 60.0, 120.0]
)

# ---- Per-Stage Latency ----
stage_latency = Histogram(
    "inference_stage_latency_seconds",
    "Per-stage inference latency (worker/server)",
    ["model", "stage"],
    buckets=[0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 20.0, 30.0, 60.0, 120.0]
)
