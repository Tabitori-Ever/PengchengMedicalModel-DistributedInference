"""
Core scheduler: orchestrates multi-model inference across edge and cloud nodes.
Implements FromGPT.txt's intelligent scheduling architecture.
"""
import json
import os
import time
import requests
from datetime import datetime, timezone
from typing import Dict, Any, Optional

from .task_manager import create_task, update_task, complete_task, fail_task
from .redis_client import (
    save_task, get_task, enqueue_task,
    redis_set, redis_get, REDIS_AVAILABLE, get_queue_length as redis_queue_len
)
from .queue import PriorityTaskQueue
from .resource_monitor import ResourceMonitor
from .predictor import LatencyPredictor
from .policy import SchedulingPolicy
from .metrics import inference_latency, stage_latency
from .service_registry import (
    PART2_URL,
    MEDICAL_SERVER_URL,
    hospital_medical_infer_url,
    hospital_alexnet_infer_url,
    clinic_mem_query_url,
    pick_hospital,
    pick_clinic,
)


# ---- Shared requests Session with connection pooling and retry ----

from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

_SESSION = None


def _get_session() -> requests.Session:
    """Lazy-init a shared requests.Session with retry + connection pooling.

    Connection pool size: 20 connections, enough for 4 workers + API handlers.
    Retries: 3 attempts with exponential backoff for transient server errors.
    """
    global _SESSION
    if _SESSION is None:
        _SESSION = requests.Session()
        retry_strategy = Retry(
            total=3,
            backoff_factor=0.5,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["POST", "GET"],
        )
        adapter = HTTPAdapter(
            max_retries=retry_strategy,
            pool_connections=10,
            pool_maxsize=20,
            pool_block=False,
        )
        _SESSION.mount("http://", adapter)
        _SESSION.mount("https://", adapter)
    return _SESSION


class InferenceScheduler:
    """Unified scheduler for AlexNet and Medical model tasks."""

    def __init__(self, max_concurrent: int = 4, max_queue: int = 200):
        self.max_concurrent = max_concurrent
        self.task_queue = PriorityTaskQueue(maxsize=max_queue)
        self.resource_monitor = ResourceMonitor()
        self.predictor = LatencyPredictor()
        self.policy = SchedulingPolicy()
        self.active_tasks: Dict[str, Dict[str, Any]] = {}

    def submit_task(self, hospital: str, model: str, priority: int,
                    input_data: dict, deadline: str = "5s") -> dict:
        """Submit a new inference task. Called by POST /schedule/task.

        Task input data is stored in Redis. Only a lightweight task_ref
        (task_id + metadata) is placed in queue, keeping memory minimal.
        """
        # Check queue capacity
        if redis_queue_len() >= self.task_queue._maxsize:
            raise RuntimeError("Task queue is full, please try again later")

        # Create full task and persist to Redis (input data stays in Redis)
        task = create_task(
            model=model,
            source=hospital,
            priority=priority,
            input_data=input_data,
            deadline=deadline
        )
        task_id = task["id"]

        # Queue only the task_id in Redis (shared across threads)
        enqueue_task(task_id, priority)

        print(f"[Scheduler] Task {task_id} queued: model={model}, "
              f"hospital={hospital}, priority={priority}")

        return {"task_id": task_id, "status": "queued"}

    def dispatch_task(self, task_ref: dict) -> bool:
        """Dispatch a task to the appropriate worker/server.

        Accepts a lightweight task_ref with task_id. Fetches the full
        task data (including input) from Redis on demand.
        """
        task_id = task_ref["id"]

        # Fetch full task data from Redis (input stays in Redis, not in queue)
        task_raw = get_task(task_id)
        if not task_raw:
            print(f"[Scheduler] Task {task_id} not found in Redis, skipping")
            return False
        task = json.loads(task_raw)

        model = task.get("model", "alexnet")

        if task_id in self.active_tasks:
            return False

        self.active_tasks[task_id] = task

        try:
            if model == "alexnet":
                self._run_alexnet_pipeline(task)
            elif model == "medical":
                self._run_medical_pipeline(task)
            elif model == "clinic":
                self._run_clinic_pipeline(task)
            else:
                fail_task(task_id, f"Unknown model: {model}")
        except Exception as e:
            fail_task(task_id, str(e))
        finally:
            self.active_tasks.pop(task_id, None)

        return True

    def _compute_queue_wait_ms(self, task: dict) -> float:
        """Compute time spent waiting in queue before dispatch.

        Uses the task's queued_at (or start_time) timestamp and the current
        perf_counter to estimate queue wait time. Returns 0 if timestamps
        are unavailable.
        """
        queued_str = task.get("queued_at") or task.get("start_time")
        if not queued_str:
            return 0.0
        try:
            queued_dt = datetime.fromisoformat(queued_str)
            now_dt = datetime.now(timezone.utc)
            # queued_at is naive (no tz), so make now naive for comparison
            now_naive = now_dt.replace(tzinfo=None)
            wait_s = (now_naive - queued_dt).total_seconds()
            return max(0.0, wait_s * 1000)
        except (ValueError, TypeError):
            return 0.0

    def _run_alexnet_pipeline(self, task: dict):
        """Execute AlexNet distributed inference pipeline."""
        task_id = task["id"]
        dispatch_start = time.perf_counter()

        # Compute queue wait time from submission to dispatch
        queue_wait_ms = self._compute_queue_wait_ms(task)

        # Stage 1: AlexNet part1 (Conv layers) runs inside a hospital pod
        hospital = pick_hospital(task.get("source", ""))
        if not hospital:
            fail_task(task_id, "No hospital pod available for AlexNet part1")
            return
        part1_url = hospital_alexnet_infer_url(hospital)

        update_task(task_id, {"stage": "part1", "node": hospital})

        image_data = task.get("input", {}).get("image")
        if not image_data:
            fail_task(task_id, "No image data provided for AlexNet task")
            return

        session = _get_session()
        part1_start = time.perf_counter()
        try:
            response = session.post(
                part1_url,
                json={"image": image_data},
                timeout=120
            )
            response.raise_for_status()
            part1_result = response.json()
        except Exception as e:
            fail_task(task_id, f"Part1 inference failed: {e}")
            return
        part1_roundtrip_ms = (time.perf_counter() - part1_start) * 1000
        part1_compute_ms = part1_result.get("latency_ms", part1_roundtrip_ms)
        part1_network_ms = max(0, part1_roundtrip_ms - part1_compute_ms)

        # Stage 2: Part2 (FC layers) - cloud/edge part2 pods
        nodes = self.resource_monitor.get_all_nodes()
        part2_node = self.policy.select_target_node(
            "alexnet", "part2", task.get("source", ""),
            nodes, self.resource_monitor
        )
        if not part2_node:
            part2_node = "node2"

        update_task(task_id, {"stage": "part2", "node": part2_node})

        part2_start = time.perf_counter()
        try:
            response = session.post(
                PART2_URL,
                json={"feature": part1_result.get("feature")},
                timeout=120
            )
            response.raise_for_status()
            part2_result = response.json()
        except Exception as e:
            fail_task(task_id, f"Part2 inference failed: {e}")
            return
        part2_roundtrip_ms = (time.perf_counter() - part2_start) * 1000
        part2_compute_ms = part2_result.get("latency_ms", part2_roundtrip_ms)
        part2_network_ms = max(0, part2_roundtrip_ms - part2_compute_ms)

        pipeline_total_ms = (time.perf_counter() - dispatch_start) * 1000
        e2e_total_ms = pipeline_total_ms + queue_wait_ms

        # Inter-stage transfer time (gap between part1 end and part2 start)
        inter_stage_ms = max(0, pipeline_total_ms - part1_roundtrip_ms - part2_roundtrip_ms)

        # Record Prometheus metrics
        inference_latency.labels(model="alexnet").observe(pipeline_total_ms / 1000)
        stage_latency.labels(model="alexnet", stage="part1").observe(part1_roundtrip_ms / 1000)
        stage_latency.labels(model="alexnet", stage="part2").observe(part2_roundtrip_ms / 1000)

        result = {
            "class_id": part2_result.get("class_id"),
            "class_name": part2_result.get("class_name"),
            "score": part2_result.get("score"),
            "metrics": {
                "queue_wait_ms": queue_wait_ms,
                "part1_compute_ms": part1_compute_ms,
                "part1_network_ms": part1_network_ms,
                "part1_total_ms": part1_roundtrip_ms,
                "inter_stage_ms": inter_stage_ms,
                "part2_compute_ms": part2_compute_ms,
                "part2_network_ms": part2_network_ms,
                "part2_total_ms": part2_roundtrip_ms,
                "pipeline_total_ms": pipeline_total_ms,
                "e2e_total_ms": e2e_total_ms,
            }
        }

        complete_task(task_id, pipeline_total_ms, result)
        redis_set(f"inference:result:{task_id}", json.dumps({
            "status": "completed",
            "result": result
        }))
        print(f"[Scheduler] AlexNet task {task_id} completed in {pipeline_total_ms:.2f}ms "
              f"(e2e: {e2e_total_ms:.2f}ms, queue_wait: {queue_wait_ms:.2f}ms)")

    def _run_medical_pipeline(self, task: dict):
        """Execute Medical model distributed inference pipeline."""
        task_id = task["id"]
        source = task.get("source", "hospital-a")
        dispatch_start = time.perf_counter()

        # Compute queue wait time from submission to dispatch
        queue_wait_ms = self._compute_queue_wait_ms(task)

        nodes = self.resource_monitor.get_all_nodes()

        # Stage 1: Medical Worker (front-end) runs inside the SOURCE hospital pod.
        # Patient data never leaves its own hospital pod/node.
        hospital = pick_hospital(source)
        if not hospital:
            fail_task(task_id, "No hospital pod available for medical worker")
            return
        worker_url = hospital_medical_infer_url(hospital)

        update_task(task_id, {"stage": "worker", "node": hospital})

        session = _get_session()
        worker_start = time.perf_counter()
        try:
            response = session.post(
                worker_url,
                json=task.get("input", {}),
                timeout=300
            )
            response.raise_for_status()
            worker_result = response.json()
        except Exception as e:
            fail_task(task_id, f"Medical worker inference failed: {e}")
            return
        worker_roundtrip_ms = (time.perf_counter() - worker_start) * 1000
        worker_compute_ms = worker_result.get("latency_ms", worker_roundtrip_ms)
        worker_network_ms = max(0, worker_roundtrip_ms - worker_compute_ms)

        # Stage 2: Medical Server (cloud only) - must run on cloud node
        server_node = self.policy.select_target_node(
            "medical", "server", source,
            nodes, self.resource_monitor
        )
        if not server_node:
            server_node = "node3"

        update_task(task_id, {"stage": "server", "node": server_node})

        server_start = time.perf_counter()
        try:
            response = session.post(
                MEDICAL_SERVER_URL,
                json={
                    "dce_features": worker_result.get("dce_features"),
                    "dwi_features": worker_result.get("dwi_features"),
                    "clinical_features": worker_result.get("clinical_features"),
                    "radiomics_features": worker_result.get("radiomics_features"),
                    "patient_ids": worker_result.get("patient_ids"),
                },
                timeout=300
            )
            response.raise_for_status()
            server_result = response.json()
        except Exception as e:
            fail_task(task_id, f"Medical server inference failed: {e}")
            return
        server_roundtrip_ms = (time.perf_counter() - server_start) * 1000
        server_compute_ms = server_result.get("latency_ms", server_roundtrip_ms)
        server_network_ms = max(0, server_roundtrip_ms - server_compute_ms)

        pipeline_total_ms = (time.perf_counter() - dispatch_start) * 1000
        e2e_total_ms = pipeline_total_ms + queue_wait_ms

        # Inter-stage transfer time (scheduler overhead between worker response and server request)
        inter_stage_ms = max(0, pipeline_total_ms - worker_roundtrip_ms - server_roundtrip_ms)

        # Record Prometheus metrics
        inference_latency.labels(model="medical").observe(pipeline_total_ms / 1000)
        stage_latency.labels(model="medical", stage="worker").observe(worker_roundtrip_ms / 1000)
        stage_latency.labels(model="medical", stage="server").observe(server_roundtrip_ms / 1000)

        result = {
            "predictions": server_result.get("predictions", []),
            "bpCR_probability": server_result.get("bpCR_probability"),
            "metrics": {
                "queue_wait_ms": queue_wait_ms,
                "worker_compute_ms": worker_compute_ms,
                "worker_network_ms": worker_network_ms,
                "worker_total_ms": worker_roundtrip_ms,
                "inter_stage_ms": inter_stage_ms,
                "server_compute_ms": server_compute_ms,
                "server_network_ms": server_network_ms,
                "server_total_ms": server_roundtrip_ms,
                "pipeline_total_ms": pipeline_total_ms,
                "e2e_total_ms": e2e_total_ms,
            }
        }

        complete_task(task_id, pipeline_total_ms, result)
        redis_set(f"inference:result:{task_id}", json.dumps({
            "status": "completed",
            "result": result
        }))
        print(f"[Scheduler] Medical task {task_id} completed in {pipeline_total_ms:.2f}ms "
              f"(e2e: {e2e_total_ms:.2f}ms, queue_wait: {queue_wait_ms:.2f}ms)")

    def _run_clinic_pipeline(self, task: dict):
        """Execute a clinic task: query a pod's memory usage.

        The selected clinic pod answers; by default it reports its own memory
        usage, otherwise the explicitly requested target pod.
        """
        task_id = task["id"]
        source = task.get("source", "clinic-1")
        dispatch_start = time.perf_counter()

        queue_wait_ms = self._compute_queue_wait_ms(task)

        clinic = pick_clinic(source)
        if not clinic:
            fail_task(task_id, "No clinic pod available for memory query")
            return
        mem_url = clinic_mem_query_url(clinic)

        update_task(task_id, {"stage": "mem", "node": clinic})

        inp = task.get("input", {}) or {}
        body = {
            "target_pod": inp.get("target_pod") or None,
            "namespace": inp.get("namespace", "default"),
        }

        session = _get_session()
        mem_start = time.perf_counter()
        try:
            response = session.post(mem_url, json=body, timeout=30)
            response.raise_for_status()
            mem_result = response.json()
        except Exception as e:
            fail_task(task_id, f"Clinic memory query failed: {e}")
            return
        clinic_roundtrip_ms = (time.perf_counter() - mem_start) * 1000

        pipeline_total_ms = (time.perf_counter() - dispatch_start) * 1000
        e2e_total_ms = pipeline_total_ms + queue_wait_ms

        # Record Prometheus metrics
        inference_latency.labels(model="clinic").observe(pipeline_total_ms / 1000)
        stage_latency.labels(model="clinic", stage="mem").observe(clinic_roundtrip_ms / 1000)

        result = {
            "pod": mem_result.get("pod"),
            "namespace": mem_result.get("namespace"),
            "usage_bytes": mem_result.get("usage_bytes"),
            "limit_bytes": mem_result.get("limit_bytes"),
            "usage_percent": mem_result.get("usage_percent"),
            "containers": mem_result.get("containers", []),
            "measured_at": mem_result.get("measured_at"),
            "source": mem_result.get("source"),
            "clinic": clinic,
            "metrics": {
                "queue_wait_ms": queue_wait_ms,
                "clinic_total_ms": clinic_roundtrip_ms,
                "pipeline_total_ms": pipeline_total_ms,
                "e2e_total_ms": e2e_total_ms,
            },
        }

        complete_task(task_id, pipeline_total_ms, result)
        redis_set(f"inference:result:{task_id}", json.dumps({
            "status": "completed",
            "result": result
        }))
        print(f"[Scheduler] Clinic task {task_id} completed in {pipeline_total_ms:.2f}ms "
              f"(pod={mem_result.get('pod')}, "
              f"usage_percent={mem_result.get('usage_percent')})")

    def get_task_result(self, task_id: str) -> dict:
        """Get task result. Called by GET /task/result/{id}."""
        result_key = f"inference:result:{task_id}"
        result_data = redis_get(result_key)

        if result_data:
            return json.loads(result_data)

        task_data = get_task(task_id)
        if task_data:
            task = json.loads(task_data)
            status = task.get("status", "unknown")
            if status == "running":
                return {
                    "task_id": task_id,
                    "status": "running",
                    "stage": task.get("stage"),
                    "progress": task.get("progress"),
                    "node": task.get("node")
                }
            elif status == "failed":
                return {
                    "task_id": task_id,
                    "status": "failed",
                    "error": task.get("error")
                }
            elif status == "finished":
                return {
                    "task_id": task_id,
                    "status": "finished",
                    "result": task.get("result"),
                    "duration_ms": task.get("duration_ms")
                }

        return {"task_id": task_id, "status": "not_found"}

    def get_queue_status(self) -> dict:
        return {
            "queue_length": redis_queue_len(),
            "active_tasks": len(self.active_tasks),
            "max_concurrent": self.max_concurrent,
            "redis_available": REDIS_AVAILABLE
        }
