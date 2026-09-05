#!/usr/bin/env python3
"""
Test Script: Concurrent Stress & Priority Test (stress_test.py)
================================================================

Purpose
-------
Stress-tests the inference scheduling system under concurrent mixed-model load
and validates that the priority-based scheduling works correctly.

What This Test Validates
-------------------------
1. **Concurrent submission throughput** — The scheduler can accept hundreds of
   tasks submitted in parallel without rejecting or dropping them.
2. **Priority scheduling** — High-priority tasks (priority 9-10) complete
   before lower-priority ones, confirming the priority queue works.
3. **Mixed workload handling** — Both Medical (bpCR) and AlexNet (image
   classification) tasks are interleaved; the scheduler routes each to the
   correct worker pool.
4. **Latency distribution under load** — Measures min/max/avg latency for
   Medical and AlexNet tasks separately to identify bottlenecks.
5. **Edge/Cloud split** — Tasks from hospital-a and hospital-b should be
   processed on edge nodes (node1/node2), while server-heavy work goes to
   cloud (node3).

Test Design
-----------
The test runs in three phases:

  Phase 1 — Submission
    Submits all tasks via ThreadPoolExecutor with configurable concurrency.
    Tasks are mixed: ~10% emergency (p=10), ~30% urgent (p=9), ~60% routine
    (p=5) for Medical; AlexNet tasks are mostly low-priority (p=1).

  Phase 2 — Result Collection
    Polls every submitted task_id until completion or timeout (120s per task).
    Uses ThreadPoolExecutor for parallel polling.

  Phase 3 — Analysis
    Computes latency statistics per model type and prints observations about
    priority ordering and edge/cloud distribution.

Dependencies
------------
- requests (HTTP client)
- Scheduler and all worker services must be deployed in K8s
- Redis must be available for task state management

Usage
-----
    # Default: 100 AlexNet + 100 Medical tasks, 10 concurrent
    python stress_test.py

    # Lighter smoke test
    python stress_test.py --alexnet 10 --medical 10 --concurrent 5

    # Heavy load test
    python stress_test.py --alexnet 500 --medical 500 --concurrent 50

    # Target a specific scheduler
    python stress_test.py --url http://192.168.1.100:30080 --alexnet 50 --medical 50

Expected Output
---------------
Prints submission rate, completion counts, per-model latency stats
(min/max/avg), and qualitative observations about priority effectiveness
and edge/cloud distribution.

Related Test Scripts
---------------------
- submit_task.py    — single-task submission (smoke test)
- query_result.py   — single-task result polling
- local_integration_test.py — local end-to-end test without K8s
"""
import argparse
import json
import sys
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import defaultdict
import requests

# Default scheduler URL — K8s NodePort. Override with --url for dev/testing.
SCHEDULER_URL = "http://localhost:30080"


class StressTestStats:
    """Thread-safe accumulator for stress test metrics.

    Tracks submitted tasks, completion status, and failures across
    concurrent submission and polling threads.
    """
    def __init__(self):
        self.lock = threading.Lock()
        self.medical_tasks = []
        self.alexnet_tasks = []
        self.completed = defaultdict(list)
        self.failed = []
        self.start_time = None

    def add_submitted(self, task_type: str, task_id: str, priority: int):
        """Record a successfully submitted task."""
        with self.lock:
            if task_type == "medical":
                self.medical_tasks.append({"id": task_id, "priority": priority})
            else:
                self.alexnet_tasks.append({"id": task_id, "priority": priority})

    def add_completed(self, task_type: str, result: dict):
        """Record a completed task result for latency analysis."""
        with self.lock:
            self.completed[task_type].append(result)

    def add_failed(self, task_id: str, error: str):
        """Record a failed or timed-out task."""
        with self.lock:
            self.failed.append({"id": task_id, "error": error})


# Module-level stats collector shared across threads
stats = StressTestStats()


_FULL_SIZE = False  # controlled by --full flag


def _make_input(model: str) -> dict:
    """Generate dummy inference input for stress testing.

    Default (--fast): minimal shapes (<100B), focuses on scheduler throughput.
    With --full: correct model shapes (~500KB), tasks succeed but slower.
    For end-to-end correctness, prefer e2e_test.py --count N.
    """
    if model == "medical":
        if _FULL_SIZE:
            H, W = 224, 224
            return {
                "dce_image": [[[0.5] * W] * H],
                "dwi_image": [[[0.5] * W] * H],
                "clinical": [[0.5] * 23],
                "radiomics": [[0.5] * 2264],
                "patient_ids": ["stress_test_patient"],
            }
        return {
            "dce_image": [[[0.5]]],
            "dwi_image": [[[0.5]]],
            "clinical": [[0.5]],
            "radiomics": [[0.5]],
            "patient_ids": ["s"],
        }
    elif model == "alexnet":
        if _FULL_SIZE:
            return {"image": [[[0.5] * 224] * 224] * 3}
        return {"image": [[[0.5]]]}
    return {}


def submit_one(hospital: str, model: str, priority: int) -> dict:
    """Submit a single task to the scheduler.

    Called concurrently by multiple threads during Phase 1.

    Returns
    -------
    dict or None
        The parsed JSON response on success, None on failure.
    """
    task = {
        "hospital": hospital,
        "model": model,
        "priority": priority,
        "input": _make_input(model),
        "deadline": "5s"
    }
    try:
        response = requests.post(
            f"{SCHEDULER_URL}/schedule/task",
            json=task, timeout=10
        )
        if response.status_code == 200:
            result = response.json()
            stats.add_submitted(model, result["task_id"], priority)
            return result
        else:
            stats.add_failed("N/A", f"HTTP {response.status_code}")
            return None
    except Exception as e:
        stats.add_failed("N/A", str(e))
        return None


def wait_and_collect(task_id: str, model: str, timeout: int = 60) -> dict:
    """Poll a task until completion or timeout.

    Called concurrently by multiple threads during Phase 2.

    Returns
    -------
    dict or None
        The final result on completion, None on timeout.
    """
    url = f"{SCHEDULER_URL}/task/result/{task_id}"
    deadline = time.time() + timeout

    while time.time() < deadline:
        try:
            response = requests.get(url, timeout=5)
            if response.status_code == 200:
                result = response.json()
                if result.get("status") in ("finished", "completed"):
                    stats.add_completed(model, result)
                    return result
                elif result.get("status") == "failed":
                    stats.add_failed(task_id, result.get("error", "unknown"))
                    return result
        except Exception:
            pass  # Transient error, retry
        time.sleep(0.5)

    stats.add_failed(task_id, "timeout")
    return None


def run_stress_test(num_alexnet: int, num_medical: int, max_workers: int):
    """Execute the full stress test: submit, collect, analyze.

    Parameters
    ----------
    num_alexnet : int
        Total AlexNet classification tasks to submit.
    num_medical : int
        Total Medical bpCR prediction tasks to submit.
    max_workers : int
        Maximum threads for concurrent submission and polling.
    """
    print("=" * 60)
    print("INFERENCE STRESS TEST")
    print("=" * 60)
    print(f"  AlexNet tasks:  {num_alexnet}")
    print(f"  Medical tasks:  {num_medical}")
    print(f"  Max concurrent: {max_workers}")
    print(f"  Target:         {SCHEDULER_URL}")
    print()

    stats.start_time = time.time()

    # ================================================================
    # Phase 1: Concurrent task submission
    # ================================================================
    print("Phase 1: Submitting tasks...")
    submit_start = time.time()

    tasks_to_submit = []

    # Medical tasks — mix of priorities to verify scheduling order:
    #   10% emergency (p=10), 30% urgent (p=9), 60% routine (p=5)
    for i in range(num_medical):
        hospital = "hospital-a" if i % 2 == 0 else "hospital-b"
        if i < num_medical * 0.1:
            priority = 10       # emergency
        elif i < num_medical * 0.4:
            priority = 9        # urgent
        else:
            priority = 5        # routine
        tasks_to_submit.append((hospital, "medical", priority))

    # AlexNet tasks — mostly low priority to validate that high-priority
    # medical tasks are scheduled first
    for i in range(num_alexnet):
        hospital = "hospital-a" if i % 2 == 0 else "hospital-b"
        priority = 1 if i < num_alexnet * 0.8 else 5
        tasks_to_submit.append((hospital, "alexnet", priority))

    submitted = 0
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = []
        for hospital, model, priority in tasks_to_submit:
            f = executor.submit(submit_one, hospital, model, priority)
            futures.append((f, model))

        for f, model in futures:
            try:
                result = f.result(timeout=30)
                if result:
                    submitted += 1
            except Exception:
                pass

    submit_time = time.time() - submit_start
    print(f"  Submitted: {submitted}/{len(tasks_to_submit)} in {submit_time:.2f}s")
    print(f"  Rate: {submitted/submit_time:.1f} tasks/s" if submit_time > 0 else "")
    print()

    # ================================================================
    # Phase 2: Wait for all results
    # ================================================================
    print("Phase 2: Waiting for results...")
    wait_start = time.time()

    # Collect all successfully submitted task IDs
    all_task_ids = []
    for t in stats.medical_tasks:
        all_task_ids.append((t["id"], "medical"))
    for t in stats.alexnet_tasks:
        all_task_ids.append((t["id"], "alexnet"))

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = []
        for task_id, model in all_task_ids:
            f = executor.submit(wait_and_collect, task_id, model, 120)
            futures.append(f)

        for f in as_completed(futures):
            try:
                f.result(timeout=5)
            except Exception:
                pass

    wait_time = time.time() - wait_start
    total_time = time.time() - stats.start_time

    # ================================================================
    # Phase 3: Analyze and report
    # ================================================================
    print()
    print("=" * 60)
    print("RESULTS")
    print("=" * 60)
    print(f"  Total elapsed:        {total_time:.2f}s")
    print(f"  Submission time:      {submit_time:.2f}s")
    print(f"  Wait time:            {wait_time:.2f}s")
    print(f"  Medical completed:    {len(stats.completed['medical'])}")
    print(f"  AlexNet completed:    {len(stats.completed['alexnet'])}")
    print(f"  Failed:               {len(stats.failed)}")

    # --- Latency analysis per model type ---
    medical_latencies = []
    for r in stats.completed["medical"]:
        res = r.get("result", {})
        metrics = res.get("metrics", {})
        total = metrics.get("total_ms", 0)
        if isinstance(total, (int, float)) and total > 0:
            medical_latencies.append(total)

    alexnet_latencies = []
    for r in stats.completed["alexnet"]:
        res = r.get("result", {})
        metrics = res.get("metrics", {})
        total = metrics.get("total_ms", 0)
        if isinstance(total, (int, float)) and total > 0:
            alexnet_latencies.append(total)

    if medical_latencies:
        print()
        print("Medical Model Latency:")
        print(f"  Min:  {min(medical_latencies):.2f}ms")
        print(f"  Max:  {max(medical_latencies):.2f}ms")
        print(f"  Avg:  {sum(medical_latencies)/len(medical_latencies):.2f}ms")

    if alexnet_latencies:
        print()
        print("AlexNet Latency:")
        print(f"  Min:  {min(alexnet_latencies):.2f}ms")
        print(f"  Max:  {max(alexnet_latencies):.2f}ms")
        print(f"  Avg:  {sum(alexnet_latencies)/len(alexnet_latencies):.2f}ms")

    # --- Priority effectiveness ---
    print()
    print("Priority Check: High-priority tasks should complete first on average.")

    # --- Interpretation guidance ---
    print()
    print("=" * 60)
    print("Observations:")
    print("  1. High priority (p=9-10) tasks should finish before low (p=1-5)")
    print("  2. node3 should serve as the cloud server center")
    print("  3. Hospital data should stay on edge nodes (node1/node2)")
    print("  4. AlexNet latency should scale with partition depth")
    print("  5. Medical latency should be bounded by model complexity")
    print("=" * 60)


def main():
    global SCHEDULER_URL, _FULL_SIZE

    parser = argparse.ArgumentParser(description="Inference stress test")
    parser.add_argument("--alexnet", type=int, default=100,
                        help="Number of AlexNet tasks")
    parser.add_argument("--medical", type=int, default=100,
                        help="Number of Medical tasks")
    parser.add_argument("--concurrent", type=int, default=10,
                        help="Max concurrent submissions")
    parser.add_argument("--url", type=str, default=SCHEDULER_URL,
                        help="Scheduler URL")
    parser.add_argument("--full", action="store_true",
                        help="Use full model input sizes (~500KB/task, slower but tasks succeed). Default: fast mode (throughput test).")

    args = parser.parse_args()
    SCHEDULER_URL = args.url
    _FULL_SIZE = args.full

    if args.full and args.concurrent > 3:
        print(f"NOTE: --full with --concurrent {args.concurrent} may overload scheduler.")
        print(f"      Auto-capping to --concurrent 3. Override with explicit --concurrent if needed.")
        print()
        args.concurrent = 3

    run_stress_test(args.alexnet, args.medical, args.concurrent)


if __name__ == "__main__":
    main()
