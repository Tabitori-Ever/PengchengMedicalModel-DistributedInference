#!/usr/bin/env python3
"""
Test Script: Submit Inference Task (submit_task.py)
====================================================

Purpose
-------
Validates the task submission entry point of the inference scheduling system.
Simulates a hospital submitting Medical (bpCR) or AlexNet (image classification)
inference tasks to the scheduler via HTTP POST.

What This Test Validates
-------------------------
1. **Scheduler reachability** — The scheduler NodePort service is accessible.
2. **Task acceptance** — POST /schedule/task accepts well-formed JSON payloads
   and returns a valid task_id.
3. **Input routing** — Different model types (medical vs alexnet) produce correct
   input payloads with appropriate defaults.
4. **Priority propagation** — Task priority (1-10) is accepted and propagated.
5. **Hospital identity** — hospital-a / hospital-b labels are correctly passed
   for edge-node affinity routing.

Dependencies
------------
- requests (HTTP client)
- Scheduler must be deployed and accessible (K8s NodePort 30080 by default)

Usage
-----
    # Submit a high-priority medical task from hospital-a
    python submit_task.py --hospital hospital-a --model medical --priority 9

    # Submit a routine AlexNet task from hospital-b
    python submit_task.py --hospital hospital-b --model alexnet --priority 5

    # Submit with custom input data from a JSON file
    python submit_task.py --hospital hospital-a --model medical --input my_data.json

    # Target a different scheduler URL (e.g. local dev)
    python submit_task.py --url http://localhost:8000 --hospital hospital-a --model alexnet --priority 1

Expected Output
---------------
On success, prints the assigned task_id and submission status.
On failure, prints the HTTP error or connection error with troubleshooting hints.

Related Test Scripts
---------------------
- query_result.py  — retrieve the result of a submitted task
- stress_test.py   — submit many tasks concurrently under load
- local_integration_test.py — end-to-end local test without K8s
"""
import argparse
import json
import requests
import sys

# Default scheduler URL — points to K8s NodePort service.
# Override with --url for local development against a port-forward or direct service.
SCHEDULER_URL = "http://localhost:30080"


def submit_task(hospital: str, model: str, priority: int, input_file: str = None):
    """Submit an inference task to the scheduler.

    Parameters
    ----------
    hospital : str
        One of 'hospital-a' or 'hospital-b'. Determines edge-node affinity.
    model : str
        'medical' (bpCR prediction, DCE+DWI input) or 'alexnet' (image classification).
    priority : int
        Task priority 1-10, higher = more urgent. Affects scheduling order.
    input_file : str or None
        Path to a JSON file with per-model input data. If None, dummy defaults
        are injected for smoketesting.

    Returns
    -------
    dict
        The parsed JSON response, expected keys: task_id, status.
    """
    url = f"{SCHEDULER_URL}/schedule/task"

    # Build task payload matching the scheduler's TaskRequest schema
    task = {
        "hospital": hospital,
        "model": model,
        "priority": priority,
        "input": {},
        "deadline": "5s"
    }

    # Provide realistic dummy inputs so the task passes validation
    if input_file:
        with open(input_file, "r") as f:
            task["input"] = json.load(f)
    else:
        if model == "medical":
            # Match MedicalWorkerRequest schema:
            #   dce_image: [1, 224, 224] float
            #   dwi_image: [1, 224, 224] float
            #   clinical:  [1, 23] float
            #   radiomics: [1, 2264] float
            H, W = 224, 224
            task["input"] = {
                "dce_image": [[[0.5] * W] * H],
                "dwi_image": [[[0.5] * W] * H],
                "clinical": [[0.5] * 23],
                "radiomics": [[0.5] * 2264],
                "patient_ids": ["test_patient_001"],
            }
        elif model == "alexnet":
            # Match ImageRequest schema: 3-channel 224×224 image
            task["input"] = {
                "image": [[[0.5] * 224] * 224] * 3
            }

    print(f"Submitting task to {url}")
    print(f"  Hospital:  {hospital}")
    print(f"  Model:     {model}")
    print(f"  Priority:  {priority}")
    print()

    try:
        response = requests.post(url, json=task, timeout=10)
        response.raise_for_status()
        result = response.json()
        print(f"Task submitted successfully!")
        print(f"  Task ID:   {result.get('task_id')}")
        print(f"  Status:    {result.get('status')}")
        return result
    except requests.exceptions.ConnectionError:
        print(f"ERROR: Cannot connect to scheduler at {url}", file=sys.stderr)
        print("Make sure the scheduler is deployed and the NodePort is accessible.",
              file=sys.stderr)
        sys.exit(1)
    except requests.exceptions.HTTPError as e:
        print(f"ERROR: HTTP {e.response.status_code}: {e.response.text}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


def main():
    global SCHEDULER_URL

    parser = argparse.ArgumentParser(description="Submit inference task")
    parser.add_argument("--hospital", default="hospital-a",
                        choices=["hospital-a", "hospital-b"],
                        help="Source hospital")
    parser.add_argument("--model", default="medical",
                        choices=["medical", "alexnet"],
                        help="Model to run")
    parser.add_argument("--priority", type=int, default=5,
                        help="Task priority (1-10, higher = more urgent)")
    parser.add_argument("--input", type=str, default=None,
                        help="JSON file with input data")
    parser.add_argument("--url", type=str, default=SCHEDULER_URL,
                        help="Scheduler URL")

    args = parser.parse_args()
    SCHEDULER_URL = args.url

    submit_task(args.hospital, args.model, args.priority, args.input)


if __name__ == "__main__":
    main()
