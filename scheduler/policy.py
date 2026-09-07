"""
Scheduling policy: model-aware, resource-aware, priority-based.
Implements FromGPT.txt's three-level scheduling strategy.
"""
import os
from typing import Dict, Any, Optional

# Lazy import yaml — falls back to defaults if not installed
try:
    import yaml
    _HAS_YAML = True
except ImportError:
    yaml = None
    _HAS_YAML = False

# Try to load model profile from YAML, fall back to defaults
MODEL_PROFILE = {
    "diagnosis": {
        "worker": {"cpu": 4, "memory": "8G", "gpu": 0},
        "server": {"cpu": 4, "memory": "8G", "gpu": 1},
        "constraints": {
            "worker": {"allowed_roles": ["edge"]},
            "server": {"allowed_roles": ["cloud"]},
        }
    },
    "compute": {
        "comp": {"cpu": 2, "memory": "2G", "gpu": 0},
        "constraints": {"comp": {"allowed_roles": ["edge", "cloud"]}},
    },
    "sync": {
        "sync": {"cpu": 1, "memory": "1G", "gpu": 0},
        "constraints": {"sync": {"allowed_roles": ["edge", "cloud"]}},
    },
    "routine": {
        "job": {"cpu": 1, "memory": "512M", "gpu": 0},
        "constraints": {"job": {"allowed_roles": ["edge", "cloud"]}},
    },
}

# Try loading from config file
CONFIG_PATH = os.environ.get("MODEL_PROFILE_PATH", "/app/model_profile.yaml")
try:
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "r") as f:
            loaded = yaml.safe_load(f)
            if loaded and "models" in loaded:
                MODEL_PROFILE.update(loaded["models"])
except Exception:
    pass


class SchedulingPolicy:
    """Implements FromGPT.txt's three-level scheduling strategy."""

    @staticmethod
    def get_model_stages(model: str) -> list:
        """Get the processing stages for a given model."""
        if model == "diagnosis":
            return ["worker", "server"]
        elif model == "compute":
            return ["compute"]
        elif model == "sync":
            return ["sync"]
        elif model == "routine":
            return ["routine"]
        return []

    @staticmethod
    def get_stage_constraints(model: str, stage: str) -> dict:
        """Get deployment constraints for a model stage."""
        model_config = MODEL_PROFILE.get(model, {})
        return model_config.get("constraints", {}).get(stage, {})

    @staticmethod
    def get_stage_resources(model: str, stage: str) -> dict:
        """Get resource requirements for a model stage."""
        model_config = MODEL_PROFILE.get(model, {})
        return model_config.get(stage, {"cpu": 1, "memory": "2G"})

    @staticmethod
    def select_target_node(
        model: str,
        stage: str,
        source_hospital: str,
        nodes: Dict[str, Dict[str, Any]],
        resource_monitor
    ) -> Optional[str]:
        """
        Select the best target node for a given model stage.

        Implements the FromGPT.txt three-level strategy:
        1. Priority queue
        2. Resource awareness
        3. Model constraints
        """
        constraints = SchedulingPolicy.get_stage_constraints(model, stage)
        allowed_roles = constraints.get("allowed_roles", ["edge", "cloud", "control-plane"])

        candidate_nodes = {
            name: info for name, info in nodes.items()
            if info.get("role") in allowed_roles
        }

        if not candidate_nodes:
            return None

        # For medical worker, prefer same hospital edge node
        if model == "medical" and stage == "worker":
            hospital_nodes = {
                name: info for name, info in candidate_nodes.items()
                if info.get("hospital") == source_hospital
            }
            if hospital_nodes:
                candidate_nodes = hospital_nodes

        # Resource-aware scoring
        def score_node(item):
            name, info = item
            cpu_free = info.get("cpu_free", 0)
            gpu_free = info.get("gpu_free", 0)
            mem_free = info.get("memory_free", 0)
            return 0.4 * cpu_free + 0.35 * gpu_free + 0.25 * mem_free

        best_node = max(candidate_nodes.items(), key=score_node)
        return best_node[0]

    @staticmethod
    def should_schedule(task_priority: int, queue_length: int, max_concurrent: int) -> bool:
        """Decide whether to schedule a task now."""
        if task_priority >= 9:  # Emergency: always schedule
            return True
        if queue_length < max_concurrent:
            return True
        return False
