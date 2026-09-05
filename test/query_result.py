#!/usr/bin/env python3
"""
Test Script: Query Inference Result (query_result.py)
======================================================

Purpose
-------
Validates the task result retrieval flow of the inference scheduling system.
Queries a previously submitted task by ID and optionally polls until completion.

What This Test Validates
-------------------------
1. **Result endpoint** — GET /task/result/{task_id} returns correct status
   and structured result data.
2. **Task lifecycle** — Status transitions through pending → running → finished
   (or failed) are observable.
3. **Polling mechanism** — When --wait is used, the script correctly polls
   at 1-second intervals until the task reaches a terminal state.
4. **Result structure** — Finished tasks return model-specific outputs:
   - Medical: bpCR_probability, prediction label
   - AlexNet: class_name, confidence score
5. **Error handling** — Failed tasks report an error message; not-found tasks
   return the appropriate status.

Dependencies
------------
- requests (HTTP client)
- Scheduler must be deployed and accessible (K8s NodePort 30080 by default)
- A task_id from submit_task.py (or any other submission mechanism)

Usage
-----
    # One-shot query (returns immediately with current status)
    python query_result.py 202607220001

    # Poll until completion (max 120s by default)
    python query_result.py 202607220001 --wait

    # Poll with a longer timeout
    python query_result.py 202607220001 --wait --timeout 300

    # Alternative flag syntax
    python query_result.py --task-id 202607220001 --wait

    # Target a different scheduler URL
    python query_result.py 202607220001 --wait --url http://localhost:8000

Expected Output
---------------
- One-shot: prints current status (running/progress, finished, failed, not_found).
- With --wait: polls every second. On completion prints the full result JSON
  including model-specific predictions and latency metrics.

Related Test Scripts
---------------------
- submit_task.py  — submit a task to get a task_id for this script
- stress_test.py  — submit many tasks and collect all results
- local_integration_test.py — end-to-end test including result query
"""
import argparse
import json
import sys
import time
import requests

# Default scheduler URL — points to K8s NodePort service.
# Override with --url for local development.
SCHEDULER_URL = "http://localhost:30080"


def query_result(task_id: str, wait: bool = False, timeout: int = 60):
    """Query a task result, optionally polling until it finishes.

    Parameters
    ----------
    task_id : str
        The task ID returned by POST /schedule/task.
    wait : bool
        If True, poll until the task reaches a terminal state
        (finished / completed / failed / not_found).
    timeout : int
        Maximum seconds to wait when polling. Ignored when wait=False.

    Returns
    -------
    dict
        Parsed JSON response. Key fields vary by status:
        - running: progress, stage, node
        - finished/completed: result with model-specific predictions + metrics
        - failed: error message
    """
    url = f"{SCHEDULER_URL}/task/result/{task_id}"
    start_time = time.time()

    while True:
        try:
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            result = response.json()

            status = result.get("status", "unknown")
            print(f"Task {task_id}: {status}")

            if status == "finished" or status == "completed":
                print()
                print("=" * 50)
                print("RESULT:")
                print("=" * 50)
                print(json.dumps(result, indent=2, ensure_ascii=False))

                # Extract model-specific predictions for readability
                if "result" in result:
                    res = result["result"]
                    if "bpCR_probability" in res:
                        print(f"\n  bpCR Probability: {res['bpCR_probability']}")
                        print(f"  Prediction: {'pCR' if res.get('bpCR_probability', 0) >= 0.5 else 'non-pCR'}")
                    elif "class_name" in res:
                        print(f"\n  Classification: {res['class_name']}")
                        print(f"  Confidence: {res['score']:.4f}")

                    if "metrics" in res.get("result", {}):
                        metrics = res["result"]["metrics"]
                        print(f"\n  Total: {metrics.get('total_ms', 'N/A'):.2f}ms" if isinstance(metrics.get('total_ms'), (int, float)) else f"\n  Total: {metrics.get('total_ms', 'N/A')}")
                elif "duration_ms" in result:
                    print(f"\n  Duration: {result['duration_ms']:.2f}ms")

                return result

            elif status == "failed":
                print(f"\n  ERROR: {result.get('error', 'Unknown error')}")
                return result

            elif status == "not_found":
                print(f"\n  Task {task_id} not found")
                return result

            else:
                # Task is still running — show progress details
                progress = result.get("progress", 0)
                stage = result.get("stage", "unknown")
                node = result.get("node", "unknown")
                print(f"  Progress: {progress}% | Stage: {stage} | Node: {node}")

                if not wait:
                    return result

                elapsed = time.time() - start_time
                if elapsed > timeout:
                    print(f"\n  Timeout after {timeout}s - task still running")
                    return result

                time.sleep(1)  # Poll every 1s to avoid hammering the API

        except requests.exceptions.ConnectionError:
            print(f"ERROR: Cannot connect to scheduler at {url}", file=sys.stderr)
            sys.exit(1)
        except Exception as e:
            print(f"Error: {e}")
            if not wait:
                sys.exit(1)
            time.sleep(1)


def main():
    global SCHEDULER_URL

    parser = argparse.ArgumentParser(description="Query inference result")
    parser.add_argument("task_id", nargs="?", default=None,
                        help="Task ID to query")
    parser.add_argument("--task-id", dest="task_id_alt", default=None,
                        help="Task ID (alternative syntax)")
    parser.add_argument("--wait", action="store_true",
                        help="Wait until task completes")
    parser.add_argument("--timeout", type=int, default=120,
                        help="Max wait time in seconds")
    parser.add_argument("--url", type=str, default=SCHEDULER_URL,
                        help="Scheduler URL")

    args = parser.parse_args()
    task_id = args.task_id or args.task_id_alt

    if not task_id:
        parser.error("Task ID is required")

    SCHEDULER_URL = args.url

    query_result(task_id, args.wait, args.timeout)


if __name__ == "__main__":
    main()
