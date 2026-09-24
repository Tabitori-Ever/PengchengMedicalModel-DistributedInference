"""Runtime configuration for the benchmark-site backend (benchmark-site v1.0).

Resolution order (highest priority first):

1. rows in the SQLite ``settings`` table  (runtime overrides via ``PUT /api/config``)
2. environment variables                 (image / Deployment defaults)
3. built-in defaults                      (in-cluster DNS names, port 8090 layout)

Contract keys required by 设计方案 §6.1:
    scheduler_url, pod_urls, force_degraded, auto_degrade, probe_timeout_ms,
    concurrency, default_repeats
Extra keys below exist only to give *every* HTTP call an explicit timeout and to
control pod-side polling; they are documented in README.md.
"""
import json
import os
import threading
from typing import Any, Dict, List, Optional

# --------------------------------------------------------------------------- #
# entities                                                                     #
# --------------------------------------------------------------------------- #
ENTITIES: List[str] = ["hospital-a", "hospital-b", "clinic-1", "clinic-2",
                       "clinic-3", "clinic-4"]

# per-entity env var holding the pod base url
ENV_POD_KEYS: Dict[str, str] = {
    "hospital-a": "HOSPITAL_A_URL",
    "hospital-b": "HOSPITAL_B_URL",
    "clinic-1": "CLINIC_1_URL",
    "clinic-2": "CLINIC_2_URL",
    "clinic-3": "CLINIC_3_URL",
    "clinic-4": "CLINIC_4_URL",
}

# in-cluster DNS defaults (the deployed image); on the WSL host override with
# ClusterIPs, e.g. HOSPITAL_A_URL=http://10.50.126.210:8006
DEFAULT_POD_URLS: Dict[str, str] = {
    "hospital-a": "http://hospital-a-service:8006",
    "hospital-b": "http://hospital-b-service:8006",
    "clinic-1": "http://clinic-1-service:8007",
    "clinic-2": "http://clinic-2-service:8007",
    "clinic-3": "http://clinic-3-service:8007",
    "clinic-4": "http://clinic-4-service:8007",
}

# --------------------------------------------------------------------------- #
# helpers                                                                      #
# --------------------------------------------------------------------------- #
def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on", "y", "t")


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(float(raw))
    except ValueError:
        return default


def _env_str(name: str, default: str) -> str:
    raw = os.getenv(name)
    return default if raw is None or raw.strip() == "" else raw.strip()


def _env_pod_urls() -> Dict[str, str]:
    """POD_URLS (JSON object) overrides the whole map; per-entity vars override one."""
    urls = dict(DEFAULT_POD_URLS)
    raw = os.getenv("POD_URLS")
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                for k, v in parsed.items():
                    if isinstance(v, str) and v.strip():
                        urls[str(k)] = v.strip()
        except ValueError:
            pass
    for entity, env_name in ENV_POD_KEYS.items():
        value = os.getenv(env_name)
        if value and value.strip():
            urls[entity] = value.strip()
    return urls


# --------------------------------------------------------------------------- #
# defaults                                                                     #
# --------------------------------------------------------------------------- #
# type: int | bool | str | "pod_urls"
SCHEMA: Dict[str, Any] = {
    "scheduler_url": "str",
    "pod_urls": "pod_urls",
    "force_degraded": "bool",
    "auto_degrade": "bool",
    # v3.5 批量诊断：控制面下计划、数据面直投执行者（绕开调度器中转）
    "direct_delivery": "bool",
    "probe_timeout_ms": "int",
    "concurrency": "int",
    "default_repeats": "int",
    # --- extras: explicit HTTP timeouts / polling (see README) ---
    "call_timeout_ms": "int",       # submit POST (scheduler /schedule/*, pod /local/execute)
    "poll_http_timeout_ms": "int",  # poll GET (/task/result/*, /local/result/*)
    "task_timeout_ms": "int",       # overall wait budget for one attempt
    "poll_interval_ms": "int",      # poll period
    "local_async": "bool",          # submit pod tasks with async=true + poll
    # --- 任务保障：失败自动重传（要求成功率 100%）---
    "attempt_retries": "int",       # 单个任务最多重传几次（不含首次）
    "retry_backoff_ms": "int",      # 每次重传前的退避
    # --- 测试方案运行 ---
    "plan_unit_concurrency": "int", # 每个「设备 × 任务类型」执行单元的并发
}

INT_BOUNDS: Dict[str, tuple] = {
    "probe_timeout_ms": (50, 60000),
    "call_timeout_ms": (100, 3600000),
    "poll_http_timeout_ms": (100, 600000),
    "task_timeout_ms": (1000, 7200000),
    "poll_interval_ms": (50, 10000),
    "concurrency": (1, 32),
    "default_repeats": (1, 1000),
    "attempt_retries": (0, 10),
    "retry_backoff_ms": (0, 60000),
    "plan_unit_concurrency": (1, 32),
}


def env_defaults() -> Dict[str, Any]:
    return {
        "scheduler_url": _env_str("SCHEDULER_URL", "http://scheduler-service:8000"),
        "pod_urls": _env_pod_urls(),
        "force_degraded": _env_bool("FORCE_DEGRADED", False),
        "auto_degrade": _env_bool("AUTO_DEGRADE", True),
        "direct_delivery": _env_bool("DIRECT_DELIVERY", True),
        "probe_timeout_ms": _env_int("PROBE_TIMEOUT_MS", 1500),
        "concurrency": _env_int("CONCURRENCY", 2),
        "default_repeats": _env_int("DEFAULT_REPEATS", 3),
        "call_timeout_ms": _env_int("CALL_TIMEOUT_MS", 300000),
        "poll_http_timeout_ms": _env_int("POLL_HTTP_TIMEOUT_MS", 30000),
        "task_timeout_ms": _env_int("TASK_TIMEOUT_MS", 600000),
        "poll_interval_ms": _env_int("POLL_INTERVAL_MS", 250),
        "local_async": _env_bool("LOCAL_ASYNC", False),
        # 失败任务自动重传：默认重传 3 次（首次 + 3 = 最多 4 次尝试）
        "attempt_retries": _env_int("ATTEMPT_RETRIES", 3),
        "retry_backoff_ms": _env_int("RETRY_BACKOFF_MS", 500),
        "plan_unit_concurrency": _env_int("PLAN_UNIT_CONCURRENCY", 1),
    }


class ConfigError(ValueError):
    """Invalid configuration update (mapped to HTTP 400 by app.py)."""


def _coerce(key: str, value: Any) -> Any:
    kind = SCHEMA.get(key)
    if kind is None:
        raise ConfigError(f"unknown config key: {key}")
    if kind == "bool":
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "on")
        if isinstance(value, (int, float)):
            return bool(value)
        raise ConfigError(f"{key} must be a boolean")
    if kind == "int":
        try:
            ival = int(value)
        except (TypeError, ValueError):
            raise ConfigError(f"{key} must be an integer")
        lo, hi = INT_BOUNDS.get(key, (None, None))
        if lo is not None and not (lo <= ival <= hi):
            raise ConfigError(f"{key} must be within [{lo}, {hi}]")
        return ival
    if kind == "str":
        if not isinstance(value, str) or not value.strip():
            raise ConfigError(f"{key} must be a non-empty string")
        return value.strip()
    if kind == "pod_urls":
        if not isinstance(value, dict):
            raise ConfigError("pod_urls must be an object {entity: url}")
        out: Dict[str, str] = {}
        for ent, url in value.items():
            if not isinstance(ent, str) or not isinstance(url, str):
                raise ConfigError("pod_urls must map entity -> url string")
            if not url.strip():
                raise ConfigError(f"pod_urls[{ent}] must be a non-empty url")
            out[ent] = url.strip()
        return out
    raise ConfigError(f"unsupported config type for {key}")


class ConfigStore:
    """Holds env defaults plus DB overrides; thread-safe."""

    def __init__(self) -> None:
        self._defaults: Dict[str, Any] = env_defaults()
        self._overrides: Dict[str, Any] = {}
        self._lock = threading.Lock()

    # ---------------------------------------------------------------- load --
    def load(self) -> Dict[str, Any]:
        """Read persisted overrides from the settings table (best effort)."""
        import db
        try:
            stored = db.get_settings()
        except Exception:
            stored = {}
        with self._lock:
            self._overrides = {}
            for key, value in stored.items():
                if key not in SCHEMA:
                    continue
                try:
                    parsed = json.loads(value) if isinstance(value, str) else value
                    self._overrides[key] = _coerce(key, parsed)
                except Exception:
                    continue
        return self.current()

    # -------------------------------------------------------------- read ----
    def current(self) -> Dict[str, Any]:
        with self._lock:
            merged = dict(self._defaults)
            merged.update(self._overrides)
            # always return the full entity set so the frontend can render it
            urls = dict(merged.get("pod_urls") or {})
            for ent in ENTITIES:
                urls.setdefault(ent, DEFAULT_POD_URLS.get(ent, ""))
            merged["pod_urls"] = urls
            return merged

    def entity_url(self, entity: str) -> Optional[str]:
        return (self.current().get("pod_urls") or {}).get(entity)

    def timeout(self, key: str) -> float:
        """Seconds, used for explicit requests timeouts."""
        return float(self.current().get(key) or 0) / 1000.0

    # ------------------------------------------------------------- update ---
    def update(self, patch: Dict[str, Any]) -> Dict[str, Any]:
        """Validate + persist a partial update; returns the new full config."""
        if not isinstance(patch, dict) or not patch:
            raise ConfigError("request body must be a non-empty JSON object")
        updates: Dict[str, Any] = {}
        for key, value in patch.items():
            if key == "pod_urls":
                if not isinstance(value, dict):
                    raise ConfigError("pod_urls must be an object {entity: url}")
                merged = dict(self.current().get("pod_urls") or {})
                merged.update(_coerce("pod_urls", value))
                updates["pod_urls"] = merged
            else:
                updates[key] = _coerce(key, value)
        with self._lock:
            self._overrides.update(updates)
            snapshot = dict(self._overrides)
        import db
        db.set_settings({k: json.dumps(v, ensure_ascii=False) for k, v in snapshot.items()})
        return self.current()

    def reset(self) -> Dict[str, Any]:
        """Drop runtime overrides (fall back to env defaults)."""
        import db
        with self._lock:
            self._overrides = {}
        db.clear_settings()
        return self.current()


CONFIG = ConfigStore()


def init() -> Dict[str, Any]:
    """Called once at application startup (after db.init())."""
    return CONFIG.load()


def current() -> Dict[str, Any]:
    return CONFIG.current()


def entity_url(entity: str) -> Optional[str]:
    return CONFIG.entity_url(entity)


def update(patch: Dict[str, Any]) -> Dict[str, Any]:
    return CONFIG.update(patch)


def reset() -> Dict[str, Any]:
    return CONFIG.reset()
