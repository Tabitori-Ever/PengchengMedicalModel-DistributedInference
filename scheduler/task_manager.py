"""
Task manager for multi-model inference tasks with enhanced schema.
"""
import json
import time
import uuid
from datetime import datetime
from .redis_client import save_task, get_task, get_tasks, redis_delete, REDIS_AVAILABLE


def create_task(model: str, source: str, priority: int = 5, input_data: dict = None,
                resource_requirement: dict = None, deadline: str = "5s") -> dict:
    """Create a new task with enhanced multi-model schema."""
    task_id = datetime.now().strftime("%Y%m%d%H%M%S") + str(uuid.uuid4())[:4]

    now_iso = datetime.now().isoformat()
    task = {
        "id": task_id,
        "model": model,
        "source": source,
        "priority": priority,
        "stage": "scheduler",
        "node": "pending",
        "progress": 0,
        "status": "running",
        "resource": resource_requirement or get_default_resources(model),
        "deadline": deadline,
        "input": input_data or {},
        "start_time": now_iso,
        "queued_at": now_iso,
        "metrics": {}
    }
    save_task(task)
    return task


def update_task(task_id: str, updates: dict):
    data = get_task(task_id)
    if data:
        task = json.loads(data)
        task.update(updates)
        if "stage" in updates:
            stage_progress = {
                "scheduler": 0,
                "preprocess": 5,
                "worker": 30,
                "part1": 30,
                "conv": 30,
                "server": 60,
                "part2": 60,
                "fc": 60,
                "mem": 60,
                "completed": 100,
                "finished": 100
            }
            task["progress"] = stage_progress.get(updates["stage"], task.get("progress", 0))
        save_task(task)
        return task
    return None


def complete_task(task_id: str, latency_ms: float, result: dict = None):
    updates = {
        "stage": "finished",
        "status": "finished",
        "progress": 100,
        "latency": f"{latency_ms:.2f}ms",
        "end_time": datetime.now().isoformat(),
        "duration_ms": latency_ms
    }
    if result:
        updates["result"] = result
    task = update_task(task_id, updates)
    return task


def fail_task(task_id: str, error: str):
    updates = {
        "status": "failed",
        "error": error,
        "end_time": datetime.now().isoformat()
    }
    task = update_task(task_id, updates)
    return task


def get_default_resources(model: str) -> dict:
    profiles = {
        "alexnet": {"cpu": 2, "memory": "4G"},
        "medical": {"cpu": 4, "memory": "8G", "gpu": 1},
        "clinic": {"cpu": 1, "memory": "512M"}
    }
    return profiles.get(model, {"cpu": 1, "memory": "2G"})


def get_running_tasks():
    all_tasks = get_tasks()
    return [t for t in all_tasks if t.get("status") == "running"]


def get_tasks_by_model(model: str):
    all_tasks = get_tasks()
    return [t for t in all_tasks if t.get("model") == model]


def get_task_stats():
    tasks = get_tasks(limit=1000)
    model_stats = {}
    node_stats = {}
    stage_stats = {}
    running_count = 0

    for t in tasks:
        model = t.get("model", "unknown")
        node = t.get("node", "unknown")
        stage = t.get("stage", "unknown")

        model_stats[model] = model_stats.get(model, 0) + 1
        node_stats[node] = node_stats.get(node, 0) + 1
        stage_stats[stage] = stage_stats.get(stage, 0) + 1

        if t.get("status") == "running":
            running_count += 1

    return {
        "total": len(tasks),
        "running": running_count,
        "by_model": model_stats,
        "by_node": node_stats,
        "by_stage": stage_stats
    }
