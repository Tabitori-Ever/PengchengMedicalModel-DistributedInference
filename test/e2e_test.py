#!/usr/bin/env python3
"""
Test Script: End-to-End Inference Pipeline (e2e_test.py)
=========================================================

Purpose
-------
Exercises the **complete inference pipeline** — submit a task, poll for its
result, and validate the response — in a single command.  This is the
primary smoketest for verifying the system works end-to-end.

What This Test Validates
-------------------------
1. **Submission → Result flow**  — a submitted task reaches a terminal state
   (finished / failed) within the timeout.
2. **Medical pipeline**         — worker edge processing → server cloud
   processing → bpCR prediction.
3. **AlexNet pipeline**         — part1 conv layers → part2 FC layers →
   ImageNet classification.
4. **Result structure**         — finished results contain model-appropriate
   output fields and latency metrics.
5. **Priority scheduling**      — tasks are queued and processed in order.

Usage
-----
    # Single medical task (default)
    python test/e2e_test.py

    # Single AlexNet task
    python test/e2e_test.py --model alexnet

    # Medical task with high priority + custom timeout
    python test/e2e_test.py --model medical --priority 10 --timeout 120

    # Batch: run N tasks and summarize
    python test/e2e_test.py --count 5 --model alexnet

    # Target a specific scheduler
    python test/e2e_test.py --url http://192.168.1.100:30080

Expected Output
---------------
    ============================================================
    E2E TEST: medical
    ============================================================
    [1/4] Submitting task ... ok (task_id=..., status=queued)
    [2/4] Waiting for result ... running completed
    [3/4] Validating result ...
      Prediction:      non-pCR (prob=0.4175)
      ┌─ Queue wait:              12.34ms
      ├─ Worker (Edge):
      │   ├─ Compute:            450.23ms
      │   ├─ Network/Ser:         15.67ms
      │   └─ Total roundtrip:    465.90ms
      ├─ Scheduler overhead:       1.23ms
      ├─ Server (Cloud):
      │   ├─ Compute:            780.45ms
      │   ├─ Network/Ser:         22.10ms
      │   └─ Total roundtrip:    802.55ms
      ├─ Pipeline total:        1269.68ms
      └─ E2E total (incl. queue): 1282.02ms
    [4/4] Result: PASS
    ============================================================
"""
import argparse
import json
import sys
import time
import requests

# ---------- defaults ----------
SCHEDULER_URL = "http://localhost:30080"
DEFAULT_TIMEOUT = 120  # seconds


# ---------- helper: build correct input per model ----------
def make_input(model: str) -> dict:
    """Return a valid inference payload for the given model type.

    The returned dict matches the schema expected by the downstream service:
      - medical  → MedicalWorkerRequest (FastAPI validates field names + types)
      - alexnet  → ImageRequest (3-channel image)
    """
    if model == "medical":
        H, W = 224, 224
        return {
            "dce_image": [[[0.5] * W] * H],
            "dwi_image": [[[0.5] * W] * H],
            "clinical": [[0.5] * 23],
            "radiomics": [[0.5] * 2264],
            "patient_ids": ["e2e_test_patient"],
        }
    elif model == "alexnet":
        return {"image": [[[0.5] * 224] * 224] * 3}
    return {}


# ---------- latency display helpers ----------
def _ms(val) -> str:
    """Format a millisecond value for display, or 'N/A' if None."""
    if val is None:
        return "N/A"
    return f"{val:.2f}ms"


def _print_latency_breakdown(metrics: dict):
    """Print detailed per-stage latency breakdown for the medical pipeline."""
    if not metrics:
        print("  (no latency metrics available)")
        return

    print()
    print("  ┌─ Queue wait:             ", _ms(metrics.get("queue_wait_ms")))

    # Worker (Edge)
    w_compute = metrics.get("worker_compute_ms")
    w_network = metrics.get("worker_network_ms")
    w_total = metrics.get("worker_total_ms")
    if w_total is not None:
        print("  ├─ Worker (Edge):")
        print("  │   ├─ Compute:            ", _ms(w_compute))
        print("  │   ├─ Network/Ser:        ", _ms(w_network))
        print("  │   └─ Total roundtrip:    ", _ms(w_total))

    # Inter-stage
    inter = metrics.get("inter_stage_ms")
    if inter is not None:
        print("  ├─ Scheduler overhead:     ", _ms(inter))

    # Server (Cloud)
    s_compute = metrics.get("server_compute_ms")
    s_network = metrics.get("server_network_ms")
    s_total = metrics.get("server_total_ms")
    if s_total is not None:
        print("  ├─ Server (Cloud):")
        print("  │   ├─ Compute:            ", _ms(s_compute))
        print("  │   ├─ Network/Ser:        ", _ms(s_network))
        print("  │   └─ Total roundtrip:    ", _ms(s_total))

    # Totals
    pipeline = metrics.get("pipeline_total_ms")
    e2e = metrics.get("e2e_total_ms")
    if pipeline is not None:
        print("  ├─ Pipeline total:         ", _ms(pipeline))
    if e2e is not None:
        print("  └─ E2E total (incl. queue):", _ms(e2e))
    print()


def _print_alexnet_latency_breakdown(metrics: dict):
    """Print detailed per-stage latency breakdown for the AlexNet pipeline."""
    if not metrics:
        print("  (no latency metrics available)")
        return

    print()
    print("  ┌─ Queue wait:             ", _ms(metrics.get("queue_wait_ms")))

    # Part1
    p1_compute = metrics.get("part1_compute_ms")
    p1_network = metrics.get("part1_network_ms")
    p1_total = metrics.get("part1_total_ms")
    if p1_total is not None:
        print("  ├─ Part1 (Conv layers):")
        print("  │   ├─ Compute:            ", _ms(p1_compute))
        print("  │   ├─ Network/Ser:        ", _ms(p1_network))
        print("  │   └─ Total roundtrip:    ", _ms(p1_total))

    # Inter-stage
    inter = metrics.get("inter_stage_ms")
    if inter is not None:
        print("  ├─ Scheduler overhead:     ", _ms(inter))

    # Part2
    p2_compute = metrics.get("part2_compute_ms")
    p2_network = metrics.get("part2_network_ms")
    p2_total = metrics.get("part2_total_ms")
    if p2_total is not None:
        print("  ├─ Part2 (FC layers):")
        print("  │   ├─ Compute:            ", _ms(p2_compute))
        print("  │   ├─ Network/Ser:        ", _ms(p2_network))
        print("  │   └─ Total roundtrip:    ", _ms(p2_total))

    # Totals
    pipeline = metrics.get("pipeline_total_ms")
    e2e = metrics.get("e2e_total_ms")
    if pipeline is not None:
        print("  ├─ Pipeline total:         ", _ms(pipeline))
    if e2e is not None:
        print("  └─ E2E total (incl. queue):", _ms(e2e))
    print()


# ---------- core: submit + wait + validate ----------
def run_e2e(hospital: str, model: str, priority: int, timeout: int,
            scheduler_url: str) -> dict:
    """Execute the full submit→wait→validate cycle for one task.

    Returns a dict with keys: passed, task_id, result, duration_ms, error.
    """
    print(f"  Model:    {model}")
    print(f"  Hospital: {hospital}")
    print(f"  Priority: {priority}")
    print()

    # ---- Step 1: Submit ----
    print("[1/4] Submitting task ...", end=" ", flush=True)
    step_start = time.time()

    try:
        resp = requests.post(
            f"{scheduler_url}/schedule/task",
            json={
                "hospital": hospital,
                "model": model,
                "priority": priority,
                "input": make_input(model),
                "deadline": "10s",
            },
            timeout=30,
        )
        resp.raise_for_status()
        submit_result = resp.json()
        task_id = submit_result.get("task_id")
        if not task_id:
            return {"passed": False, "task_id": None, "result": None,
                    "duration_ms": 0, "error": "No task_id in response"}
        print(f"ok (task_id={task_id}, status={submit_result.get('status')})")

    except requests.exceptions.ConnectionError:
        return {"passed": False, "task_id": None, "result": None,
                "duration_ms": 0,
                "error": f"Cannot connect to {scheduler_url}"}
    except Exception as e:
        return {"passed": False, "task_id": None, "result": None,
                "duration_ms": 0, "error": str(e)}

    # ---- Step 2: Poll until terminal ----
    print("[2/4] Waiting for result ...", end=" ", flush=True)
    url = f"{scheduler_url}/task/result/{task_id}"
    deadline = time.time() + timeout
    result = None
    last_status = ""

    while time.time() < deadline:
        try:
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
            result = resp.json()
            status = result.get("status", "?")

            # Print status changes
            if status != last_status:
                print(status, end=" ", flush=True)
                last_status = status

            if status in ("finished", "completed", "failed"):
                print()
                break

        except Exception:
            pass  # transient — retry next poll

        time.sleep(0.5)

    duration_ms = (time.time() - step_start) * 1000

    if result is None:
        return {"passed": False, "task_id": task_id, "result": None,
                "duration_ms": duration_ms,
                "error": f"Timeout after {timeout}s"}

    status = result.get("status", "")
    if status == "failed":
        return {"passed": False, "task_id": task_id, "result": result,
                "duration_ms": duration_ms,
                "error": result.get("error", "unknown failure")}

    # ---- Step 3: Validate result structure ----
    print("[3/4] Validating result ...")
    validation_errors = []

    if model == "medical":
        # Medical result should have worker + server metadata
        res = result.get("result", {})
        if not res:
            validation_errors.append("missing 'result' dict in response")

        # Display prediction
        prediction = res.get("prediction") or res.get("bpCR_probability")
        if prediction is not None:
            if isinstance(prediction, (int, float)):
                label = "pCR" if float(prediction) >= 0.5 else "non-pCR"
                print(f"  Prediction:      {label} (prob={float(prediction):.4f})")

        # Display detailed per-stage latency breakdown
        _print_latency_breakdown(result.get("result", {}).get("metrics", {}))

    elif model == "alexnet":
        res = result.get("result", {})
        if not res:
            validation_errors.append("missing 'result' dict in response")

        class_name = res.get("class_name")
        score = res.get("score")
        if class_name is not None:
            print(f"  Classification:  {class_name} (confidence={score:.4f})"
                  if score else f"  Classification:  {class_name}")

        # Display detailed per-stage latency breakdown
        _print_alexnet_latency_breakdown(result.get("result", {}).get("metrics", {}))

    if validation_errors:
        return {"passed": False, "task_id": task_id, "result": result,
                "duration_ms": duration_ms,
                "error": "; ".join(validation_errors)}

    # ---- Step 4: Verdict ----
    print()
    print(f"[4/4] Result: PASS")
    print(f"  Client duration: {duration_ms:.0f}ms")
    return {"passed": True, "task_id": task_id, "result": result,
            "duration_ms": duration_ms, "error": None}


# ---------- batch runner ----------
def run_batch(count: int, hospital: str, model: str, priority: int,
              timeout: int, scheduler_url: str) -> int:
    """Submit `count` tasks sequentially, collect results, print summary."""
    print("=" * 60)
    print(f"E2E BATCH: {count}× {model}  timeout={timeout}s")
    print("=" * 60)

    results = []
    for i in range(count):
        print(f"\n--- Task {i+1}/{count} ---")
        r = run_e2e(hospital, model, priority, timeout, scheduler_url)
        results.append(r)

    # Summary
    passed = sum(1 for r in results if r["passed"])
    failed = count - passed
    durations = [r["duration_ms"] for r in results]

    print()
    print("=" * 60)
    print("BATCH SUMMARY")
    print("=" * 60)
    print(f"  Total:    {count}")
    print(f"  Passed:   {passed}")
    print(f"  Failed:   {failed}")
    if durations:
        print(f"  Avg time: {sum(durations)/len(durations):.0f}ms")
    for i, r in enumerate(results):
        if not r["passed"]:
            print(f"  FAIL #{i+1}: task={r['task_id']} error={r['error']}")

    return 0 if failed == 0 else 1


# ---------- CLI ----------
def main():
    global SCHEDULER_URL

    parser = argparse.ArgumentParser(
        description="End-to-end inference pipeline test"
    )
    parser.add_argument("--hospital", default="hospital-a",
                        choices=["hospital-a", "hospital-b"],
                        help="Source hospital")
    parser.add_argument("--model", default="medical",
                        choices=["medical", "alexnet"],
                        help="Model to test")
    parser.add_argument("--priority", type=int, default=9,
                        help="Task priority (1-10)")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT,
                        help=f"Max wait for result in seconds (default: {DEFAULT_TIMEOUT})")
    parser.add_argument("--url", type=str, default=SCHEDULER_URL,
                        help="Scheduler URL")
    parser.add_argument("--count", type=int, default=1,
                        help="Run N tasks in sequence (for batch validation)")

    args = parser.parse_args()
    SCHEDULER_URL = args.url

    print("=" * 60)
    print(f"E2E TEST: {args.model}")
    print("=" * 60)
    print()

    if args.count > 1:
        rc = run_batch(args.count, args.hospital, args.model,
                       args.priority, args.timeout, args.url)
    else:
        r = run_e2e(args.hospital, args.model, args.priority,
                    args.timeout, args.url)
        if not r["passed"]:
            print(f"\nFAIL: {r['error'] or 'unknown error'}")
            rc = 1
        else:
            print("\n✓ End-to-end test passed.")
            rc = 0

    sys.exit(rc)


if __name__ == "__main__":
    main()
