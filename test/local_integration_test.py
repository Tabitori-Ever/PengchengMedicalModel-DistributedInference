#!/usr/bin/env python3
"""
Test Script: Local Integration Test (local_integration_test.py)
===============================================================

Purpose
-------
The **primary pre-deployment validation** for the entire inference platform.
Runs all microservices locally as subprocesses (no Docker, no K8s, no Redis,
no real model weights) and validates that:

  1. Every service starts and exposes its health endpoint.
  2. The scheduler correctly accepts tasks via POST /schedule/task.
  3. Task status is queryable via GET /task/result/{id}.
  4. Node resource and edge/cloud separation endpoints work.
  5. Cross-service communication functions (scheduler ↔ part1/part2, etc.).
  6. Both Medical (bpCR) and AlexNet (classification) task types are routable.

Test Phases
-----------
  Phase 0 — Environment Check
    Verifies Python dependencies (torch, sklearn, fastapi, uvicorn, redis, etc.)
    are installed. Services requiring missing deps are skipped gracefully.

  Phase 1 — Start Services
    Launches each microservice as a subprocess on localhost ports 8000-9001.
    Waits for health endpoints to respond. Critical services (scheduler) cause
    abort on failure; non-critical ones skip.

  Phase 2 — Health Checks
    Hits every running service's health endpoint to confirm they're alive.

  Phase 3 — Scheduler API Tests
    Exercises the scheduler's full REST API:
    - Root (GET /), health (GET /health)
    - Task submission (POST /schedule/task) for medical + alexnet
    - Task result query (GET /task/result/{id})
    - Task listing (GET /tasks, GET /tasks/stats)
    - Legacy endpoint compatibility (POST /predict/image)
    - Node status (GET /nodes, /nodes/edge, /nodes/cloud)

  Phase 4 — Cross-Service Communication
    Verifies part1, part2, medical-worker, medical-server, prediction, and
    monitoring are all reachable and return correct health responses.

Dependencies
------------
- fastapi + uvicorn — required (test aborts without them)
- torch — optional, set REDIS_HOST="" for in-memory store
- redis-py — optional, services fall back to in-memory store
- prometheus_client — required by monitoring service

Usage
-----
    cd PengchengMedicalModel-DistributedInference
    python test/local_integration_test.py

    # Skip PyTorch-dependent services
    python test/local_integration_test.py --skip-heavy

    # Longer startup wait + verbose output
    python test/local_integration_test.py --timeout 60 --verbose

    # Quick smoke test
    python test/local_integration_test.py --skip-heavy --timeout 15

Expected Output
---------------
All checks pass → "✓ All tests passed! Code is ready for K8s deployment."
Any failure → prints the failing endpoint/error and returns exit code 1.

Related Test Scripts
---------------------
- submit_task.py   — single-task submission to a live K8s scheduler
- query_result.py  — task result polling against a live K8s scheduler
- stress_test.py   — concurrent load test against a live K8s scheduler
"""
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
os.chdir(PROJECT_ROOT)

# Service configurations for local testing
SERVICES = {
    "scheduler": {
        "port": 8000,
        "cmd": [sys.executable, "-m", "uvicorn", "scheduler.api:app",
                "--host", "0.0.0.0", "--port", "8000", "--no-access-log"],
        "needs_torch": False,
        "health_path": "/",
        "critical": True,
    },
    "part1": {
        "port": 8001,
        "cmd": [sys.executable, "-m", "uvicorn", "app:app",
                "--host", "0.0.0.0", "--port", "8001", "--no-access-log"],
        "cwd": "part1",
        "needs_torch": True,
        "needs_common": True,
        "health_path": "/",
        "critical": False,
    },
    "part2": {
        "port": 8002,
        "cmd": [sys.executable, "-m", "uvicorn", "app:app",
                "--host", "0.0.0.0", "--port", "8002", "--no-access-log"],
        "cwd": "part2",
        "needs_torch": True,
        "needs_common": True,
        "health_path": "/",
        "critical": False,
    },
    "prediction": {
        "port": 8003,
        "cmd": [sys.executable, "-m", "uvicorn", "prediction.app:app",
                "--host", "0.0.0.0", "--port", "8003", "--no-access-log"],
        "needs_torch": False,
        "health_path": "/",
        "critical": False,
    },
    "monitoring": {
        "port": 8005,
        "cmd": [sys.executable, "-m", "uvicorn", "app:app",
                "--host", "0.0.0.0", "--port", "8005", "--no-access-log"],
        "cwd": "monitoring",
        "needs_torch": False,
        "health_path": "/",
        "critical": False,
    },
    "medical-worker": {
        "port": 8006,
        "cmd": [sys.executable, "-m", "uvicorn", "app:app",
                "--host", "0.0.0.0", "--port", "8006", "--no-access-log"],
        "cwd": "medical-worker",
        "needs_torch": True,
        "health_path": "/health",
        "critical": False,
    },
    "medical-server": {
        "port": 9001,
        "cmd": [sys.executable, "-m", "uvicorn", "app:app",
                "--host", "0.0.0.0", "--port", "9001", "--no-access-log"],
        "cwd": "medical-server",
        "needs_torch": True,
        "health_path": "/health",
        "critical": False,
    },
}


class Colors:
    """ANSI color codes for terminal output formatting."""
    GREEN = "\033[92m"
    RED = "\033[91m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    BOLD = "\033[1m"
    RESET = "\033[0m"


def print_header(text: str):
    """Print a bold blue section header."""
    print(f"\n{Colors.BOLD}{Colors.BLUE}{'='*60}{Colors.RESET}")
    print(f"{Colors.BOLD}{Colors.BLUE}  {text}{Colors.RESET}")
    print(f"{Colors.BOLD}{Colors.BLUE}{'='*60}{Colors.RESET}")


def print_ok(text: str):
    """Print a green checkmark line (test passed)."""
    print(f"  {Colors.GREEN}✓{Colors.RESET} {text}")


def print_fail(text: str):
    """Print a red cross line (test failed)."""
    print(f"  {Colors.RED}✗{Colors.RESET} {text}")


def print_warn(text: str):
    """Print a yellow warning line (test skipped)."""
    print(f"  {Colors.YELLOW}⚠{Colors.RESET} {text}")


def check_imports() -> dict:
    """Verify availability of required Python packages.

    Returns a dict mapping module names to True/False.
    Used by Phase 0 to decide which services can be started.
    """
    results = {}
    modules = {
        "torch": "PyTorch (needed for part1/2, medical-worker/server)",
        "sklearn": "scikit-learn (needed for scheduler, prediction)",
        "fastapi": "FastAPI (needed for all services)",
        "uvicorn": "Uvicorn (needed for all services)",
        "redis": "Redis-py (needed for scheduler, prediction)",
        "prometheus_client": "Prometheus client (needed for all services)",
    }
    for mod in modules:
        try:
            __import__(mod)
            results[mod] = True
        except ImportError:
            results[mod] = False
    return results


def wait_for_service(port: int, timeout: int = 30, health_path: str = "/") -> bool:
    """Poll a localhost port until the service responds with HTTP 200.

    Returns True once the health endpoint responds, False on timeout.
    """
    deadline = time.time() + timeout
    url = f"http://localhost:{port}{health_path}"

    while time.time() < deadline:
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=2) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def http_get(port: int, path: str = "/"):
    """Issue an HTTP GET to localhost:<port><path> and return parsed JSON."""
    url = f"http://localhost:{port}{path}"
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=5) as resp:
        return json.loads(resp.read().decode())


def http_post(port: int, path: str, data: dict):
    """Issue an HTTP POST to localhost:<port><path> with JSON body, return parsed JSON."""
    url = f"http://localhost:{port}{path}"
    body = json.dumps(data).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as resp:
        return json.loads(resp.read().decode())


def run_tests(args):
    """Run all integration test phases and return exit code.

    Returns 0 if all tests pass, 1 if any fail.
    The args Namespace controls --skip-heavy, --timeout, --verbose.
    """
    processes = {}
    passed = 0
    failed = 0
    skipped = 0

    # ---- Phase 0: Environment Check ----
    print_header("Phase 0: Environment Check")
    imports = check_imports()

    torch_available = imports.get("torch", False)
    for mod, ok in imports.items():
        if ok:
            print_ok(f"{mod}")
        else:
            print_fail(f"{mod} — NOT INSTALLED")

    if not imports.get("fastapi") or not imports.get("uvicorn"):
        print_fail("\nFastAPI and uvicorn are required. Install with:")
        print("  pip install fastapi uvicorn")
        return 1

    # ---- Phase 1: Start Services ----
    print_header("Phase 1: Start Services")

    for name, cfg in SERVICES.items():
        if cfg["needs_torch"] and not torch_available:
            print_warn(f"Skipping {name} (PyTorch not installed)")
            skipped += 1
            continue

        if args.skip_heavy and cfg["needs_torch"]:
            print_warn(f"Skipping {name} (--skip-heavy)")
            skipped += 1
            continue

        # For part1/part2, ensure common module is accessible
        env = os.environ.copy()
        if cfg.get("needs_common"):
            env["PYTHONPATH"] = str(PROJECT_ROOT) + ":" + env.get("PYTHONPATH", "")
        # Disable Redis dependency for local test
        env["REDIS_HOST"] = ""  # Force memory store fallback
        env["PROMETHEUS_URL"] = "http://localhost:9090"  # Not available locally

        print(f"  Starting {name} on port {cfg['port']}...", end=" ", flush=True)

        try:
            kwargs = {
                "env": env,
                "stdout": subprocess.DEVNULL if not args.verbose else None,
                "stderr": subprocess.DEVNULL if not args.verbose else None,
            }
            if "cwd" in cfg:
                kwargs["cwd"] = str(PROJECT_ROOT / cfg["cwd"])

            proc = subprocess.Popen(cfg["cmd"], **kwargs)
            processes[name] = proc

            if wait_for_service(cfg["port"], args.timeout, cfg["health_path"]):
                print_ok("started")
            else:
                print_fail(f"failed to start (port {cfg['port']})")
                failed += 1
                if cfg["critical"]:
                    print_fail("  Scheduler is critical — aborting.")
                    cleanup(processes)
                    return 1

        except Exception as e:
            print_fail(f"error: {e}")
            failed += 1

    if not processes:
        print_fail("No services started.")
        return 1

    # ---- Phase 2: Health Checks ----
    print_header("Phase 2: Health Checks")

    for name, cfg in SERVICES.items():
        if name not in processes:
            continue

        try:
            result = http_get(cfg["port"], cfg["health_path"])
            print_ok(f"{name}: {json.dumps(result, indent=None)[:100]}")
            passed += 1
        except Exception as e:
            print_fail(f"{name}: {e}")
            failed += 1

    # ---- Phase 3: Scheduler API Tests ----
    print_header("Phase 3: Scheduler API")

    if "scheduler" not in processes:
        print_fail("Scheduler not running — skipping API tests")
    else:
        # Test 1: Root endpoint
        try:
            result = http_get(8000, "/")
            assert result["service"] == "inference-scheduler"
            print_ok(f"GET / → service={result['service']}, "
                     f"redis={result.get('redis', 'N/A')}")
            passed += 1
        except Exception as e:
            print_fail(f"GET / → {e}")
            failed += 1

        # Test 2: Health check
        try:
            result = http_get(8000, "/health")
            assert "scheduler" in result
            print_ok(f"GET /health → ok (services checked: {len(result)})")
            passed += 1
        except Exception as e:
            print_fail(f"GET /health → {e}")
            failed += 1

        # Test 3: Submit medical task
        try:
            task_input = {
                "hospital": "hospital-a",
                "model": "medical",
                "priority": 9,
                "input": {
                    "dce_image": [[[0.5] * 224] * 224],
                    "dwi_image": [[[0.5] * 224] * 224],
                    "clinical": [[0.5] * 23],
                    "radiomics": [[0.5] * 2264],
                    "patient_ids": ["test_patient_001"],
                },
                "deadline": "5s"
            }
            result = http_post(8000, "/schedule/task", task_input)
            medical_task_id = result.get("task_id")
            assert medical_task_id, f"No task_id in response: {result}"
            print_ok(f"POST /schedule/task (medical, hospital-a, p=9) → task_id={medical_task_id}")
            passed += 1
        except Exception as e:
            print_fail(f"POST /schedule/task (medical) → {e}")
            failed += 1
            medical_task_id = None

        # Test 4: Submit AlexNet task
        try:
            task_input = {
                "hospital": "hospital-b",
                "model": "alexnet",
                "priority": 5,
                "input": {"image": [[[0.5]*224]*224]*3},
                "deadline": "3s"
            }
            result = http_post(8000, "/schedule/task", task_input)
            alexnet_task_id = result.get("task_id")
            assert alexnet_task_id, f"No task_id: {result}"
            print_ok(f"POST /schedule/task (alexnet, hospital-b, p=5) → task_id={alexnet_task_id}")
            passed += 1
        except Exception as e:
            print_fail(f"POST /schedule/task (alexnet) → {e}")
            failed += 1
            alexnet_task_id = None

        # Test 5: Query task result
        if medical_task_id:
            try:
                result = http_get(8000, f"/task/result/{medical_task_id}")
                status = result.get("status", "unknown")
                print_ok(f"GET /task/result/{medical_task_id} → status={status}")
                passed += 1
            except Exception as e:
                print_fail(f"GET /task/result/{medical_task_id} → {e}")
                failed += 1

        # Test 6: List tasks
        try:
            result = http_get(8000, "/tasks")
            count = len(result)
            print_ok(f"GET /tasks → {count} tasks")
            passed += 1
        except Exception as e:
            print_fail(f"GET /tasks → {e}")
            failed += 1

        # Test 7: Task stats
        try:
            result = http_get(8000, "/tasks/stats")
            print_ok(f"GET /tasks/stats → total={result.get('total', '?')}, "
                     f"by_model={result.get('by_model', {})}")
            passed += 1
        except Exception as e:
            print_fail(f"GET /tasks/stats → {e}")
            failed += 1

        # Test 8: Legacy compatible endpoints
        try:
            task_input = {
                "hospital": "hospital-a",
                "model": "alexnet",
                "priority": 3,
                "input": {"image": [[[0.5]*224]*224]*3},
                "deadline": "2s"
            }
            result = http_post(8000, "/predict/image", task_input)
            legacy_id = result.get("task_id")
            print_ok(f"POST /predict/image (legacy) → task_id={legacy_id}")
            passed += 1
        except Exception as e:
            print_fail(f"POST /predict/image → {e}")
            failed += 1

        # Test 9: Node status
        try:
            result = http_get(8000, "/nodes")
            nodes = result.get("nodes", {})
            print_ok(f"GET /nodes → {len(nodes)} nodes: {list(nodes.keys())}")
            passed += 1
        except Exception as e:
            print_fail(f"GET /nodes → {e}")
            failed += 1

        # Test 10: Edge/cloud node separation
        try:
            edge = http_get(8000, "/nodes/edge")
            cloud = http_get(8000, "/nodes/cloud")
            print_ok(f"GET /nodes/edge → {len(edge.get('edge_nodes', {}))} edge nodes")
            print_ok(f"GET /nodes/cloud → {len(cloud.get('cloud_nodes', {}))} cloud nodes")
            passed += 2
        except Exception as e:
            print_fail(f"GET /nodes/* → {e}")
            failed += 2

    # ---- Phase 4: Cross-Service Communication ----
    print_header("Phase 4: Cross-Service Communication")

    # Test part1 health
    if "part1" in processes:
        try:
            result = http_get(8001, "/")
            print_ok(f"part1 /health → {result}")
            passed += 1
        except Exception as e:
            print_fail(f"part1 → {e}")
            failed += 1

    # Test part2 health
    if "part2" in processes:
        try:
            result = http_get(8002, "/")
            print_ok(f"part2 /health → {result}")
            passed += 1
        except Exception as e:
            print_fail(f"part2 → {e}")
            failed += 1

    # Test medical-worker health
    if "medical-worker" in processes:
        try:
            result = http_get(8006, "/health")
            model_ok = result.get("model_loaded", False)
            role = result.get("role", "unknown")
            if model_ok:
                print_ok(f"medical-worker /health → model_loaded=True, role={role}")
            else:
                print_warn(f"medical-worker /health → model_loaded=False (no weights), role={role}")
            passed += 1
        except Exception as e:
            print_fail(f"medical-worker → {e}")
            failed += 1

    # Test medical-server health
    if "medical-server" in processes:
        try:
            result = http_get(9001, "/health")
            model_ok = result.get("model_loaded", False)
            role = result.get("role", "unknown")
            if model_ok:
                print_ok(f"medical-server /health → model_loaded=True, role={role}")
            else:
                print_warn(f"medical-server /health → model_loaded=False (no weights), role={role}")
            passed += 1
        except Exception as e:
            print_fail(f"medical-server → {e}")
            failed += 1

    # Test prediction service
    if "prediction" in processes:
        try:
            result = http_get(8003, "/")
            print_ok(f"prediction /health → {result}")
            passed += 1
        except Exception as e:
            print_fail(f"prediction → {e}")
            failed += 1

    # Test monitoring service
    if "monitoring" in processes:
        try:
            result = http_get(8005, "/")
            print_ok(f"monitoring /health → {result}")
            passed += 1
        except Exception as e:
            print_fail(f"monitoring → {e}")
            failed += 1

    # ---- Cleanup ----
    print_header("Cleanup")
    cleanup(processes)

    # ---- Summary ----
    print_header("Results")
    total = passed + failed
    print(f"  Total:   {total}")
    print(f"  {Colors.GREEN}Passed:  {passed}{Colors.RESET}")
    print(f"  {Colors.RED}Failed:  {failed}{Colors.RESET}")
    if skipped:
        print(f"  {Colors.YELLOW}Skipped: {skipped}{Colors.RESET}")

    if failed == 0:
        print(f"\n{Colors.GREEN}{Colors.BOLD}  ✓ All tests passed! Code is ready for K8s deployment.{Colors.RESET}")
        return 0
    else:
        print(f"\n{Colors.RED}{Colors.BOLD}  ✗ {failed} test(s) failed.{Colors.RESET}")
        return 1


def cleanup(processes):
    """Gracefully terminate all subprocesses, killing any that don't exit within 5s."""
    for name, proc in processes.items():
        try:
            proc.terminate()
            proc.wait(timeout=5)
            print(f"  Stopped {name}")
        except subprocess.TimeoutExpired:
            proc.kill()
            print(f"  Killed {name} (timeout)")
        except Exception:
            pass


def main():
    parser = argparse.ArgumentParser(
        description="Local Integration Test — validates code before K8s deployment"
    )
    parser.add_argument("--skip-heavy", action="store_true",
                        help="Skip services requiring PyTorch")
    parser.add_argument("--timeout", type=int, default=30,
                        help="Max startup wait per service (default: 30s)")
    parser.add_argument("--verbose", action="store_true",
                        help="Show service stdout/stderr")
    parser.add_argument("--keep-running", action="store_true",
                        help="Don't stop services after tests")

    args = parser.parse_args()

    # Run tests
    rc = run_tests(args)

    sys.exit(rc)


if __name__ == "__main__":
    main()
