"""Execution engine for the benchmark site.

Responsibilities
----------------
* create a run and its attempts, then execute them in background worker threads
  with the run's concurrency (never blocking the uvicorn event loop);
* route every attempt per 设计方案 §6.3 (force_degraded / local / collaborative /
  auto) including the site-side fallback when the scheduler is unreachable;
* measure ``client_total_ms`` with a wall clock in this process around the real
  submit -> result exchange (scheduler path includes its queue wait, pod path is
  the pod call).  Server-reported numbers are only *stored*, never used to
  re-derive the site's latency (§3 "时延权威口径");
* keep patient inputs available offline (§8.5) from ``PATIENTS_PATH``, with a
  scheduler fetch + SQLite cache as fallback.
"""
import hashlib
import array
import base64
import json
import os
import random
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import requests
from requests.adapters import HTTPAdapter

import config
import db
import plans as plans_mod
import suites as suites_mod

KINDS: Tuple[str, ...] = ("diagnosis", "compute", "sync", "routine")
MODES: Tuple[str, ...] = ("collaborative", "local", "auto")

# 站点版本单一来源：app.py 也引用它，避免 /api/health 与 /openapi 各报一个版本
SITE_VERSION = "1.1"
SITE_SERVICE = "benchmark-site"

DEFAULT_MODE = "auto"
MAX_REPEATS = 500
MAX_CONCURRENCY = 32
MAX_ERROR_LEN = 300

# --------------------------------------------------------------------------- #
# HTTP session: explicit timeouts everywhere, NO hidden retries (a retry would
# silently inflate the measured latency and could double-execute a task).
# --------------------------------------------------------------------------- #
_SESSION = requests.Session()
_ADAPTER = HTTPAdapter(pool_connections=32, pool_maxsize=64, max_retries=0)
_SESSION.mount("http://", _ADAPTER)
_SESSION.mount("https://", _ADAPTER)

_PATIENTS_LOCK = threading.Lock()
_PATIENTS_INFO: Dict[str, Any] = {"loaded": False, "source": None, "path": None,
                                  "count": 0, "error": None, "loaded_at": None}

# inline imaging inputs (params.input) live in memory only — never in SQLite
_RUN_INPUTS: Dict[str, Dict[str, Any]] = {}
_RUN_INPUTS_LOCK = threading.Lock()


class RunError(ValueError):
    """Invalid run specification (mapped to HTTP 400 by app.py)."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _ms(t0: float) -> float:
    """Wall-clock milliseconds since t0 (monotonic)."""
    return round((time.perf_counter() - t0) * 1000.0, 3)


def _trunc(text: Any, limit: int = MAX_ERROR_LEN) -> str:
    s = "" if text is None else str(text)
    return s if len(s) <= limit else s[:limit] + "..."


def _num(value: Any, digits: Optional[int] = 3) -> Optional[float]:
    """Float coercion; `digits=None` keeps full precision (probabilities)."""
    try:
        if value is None or isinstance(value, bool):
            return None
        out = float(value)
        return out if digits is None else round(out, digits)
    except (TypeError, ValueError):
        return None


def _f(value: Any) -> Optional[float]:
    """Millisecond-style metric: 3 decimals is well below measurement noise."""
    return _num(value, 3)


def _timeouts(cfg: Dict[str, Any], key: str = "call_timeout_ms") -> Tuple[float, float]:
    """(connect, read) seconds — always explicit."""
    read = max(0.1, float(cfg.get(key, 300000)) / 1000.0)
    connect = min(5.0, read)
    return (connect, read)


# --------------------------------------------------------------------------- #
# patient inputs (offline dataset, §8.5)                                      #
# --------------------------------------------------------------------------- #
def load_patients(force: bool = False) -> Dict[str, Any]:
    """Load PATIENTS_PATH into the SQLite cache; fall back to scheduler fetch."""
    global _PATIENTS_INFO
    cfg = config.current()
    path = os.getenv("PATIENTS_PATH", "/app/patients.json")
    with _PATIENTS_LOCK:
        if _PATIENTS_INFO.get("loaded") and not force and _PATIENTS_INFO.get("path") == path:
            _PATIENTS_INFO["count"] = db.count_patients()
            _PATIENTS_INFO["origins"] = db.patient_origins()
            return dict(_PATIENTS_INFO)

        info: Dict[str, Any] = {"loaded": False, "path": path, "source": None,
                                "count": db.count_patients(), "error": None,
                                "loaded_at": _now()}
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    raw = json.load(fh)
                patients = raw if isinstance(raw, list) else raw.get("patients", [])
                n = db.upsert_patients(patients, origin="file")
                info.update({"loaded": True, "source": "file", "count": n})
            except Exception as exc:  # noqa: BLE001
                info["error"] = f"failed to read {path}: {_trunc(exc, 200)}"
        else:
            info["error"] = f"patients file not found: {path}"
            fetched = _fetch_patients_from_scheduler(cfg)
            if fetched:
                n = db.upsert_patients(fetched, origin="scheduler")
                info.update({"loaded": True, "source": "scheduler", "count": n,
                             "error": None})
        info["origins"] = db.patient_origins()
        _PATIENTS_INFO = info
        return dict(info)


def _fetch_patients_from_scheduler(cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Fallback only: /test/patients + /test/patient/{id} (carries the inputs)."""
    base = str(cfg.get("scheduler_url", "")).rstrip("/")
    if not base:
        return []
    timeout = _timeouts(cfg, "probe_timeout_ms")
    out: List[Dict[str, Any]] = []
    try:
        r = _SESSION.get(base + "/test/patients", timeout=(timeout[0], 5.0))
        r.raise_for_status()
        listing = r.json()
    except Exception:
        return []
    if not isinstance(listing, list):
        return []
    for item in listing[:100]:
        pid = str(item.get("patient_id", "")).strip()
        if not pid:
            continue
        try:
            r = _SESSION.get(base + "/test/patient/" + pid, timeout=(timeout[0], 10.0))
            r.raise_for_status()
            full = r.json()
        except Exception:
            continue
        out.append({"patient_id": pid, "bpCR": item.get("bpCR"),
                    "hospital": item.get("hospital"),
                    "input": (full or {}).get("input", {})})
    return out


def patients_info(force: bool = False) -> Dict[str, Any]:
    info = load_patients(force=force)
    info["count"] = db.count_patients()
    return info


def get_patient(patient_id: str) -> Optional[Dict[str, Any]]:
    if db.count_patients() == 0:
        load_patients()
    return db.get_patient(patient_id)


# --------------------------------------------------------------------------- #
# run specification                                                           #
# --------------------------------------------------------------------------- #
def _spec_key(kind: str, params: Dict[str, Any]) -> str:
    stable = {k: params[k] for k in sorted(params.keys()) if k != "input"}
    blob = json.dumps({"kind": kind, "params": stable}, sort_keys=True,
                      ensure_ascii=False)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:16]


def _coerce_int(value: Any, field: str, default: int, lo: int, hi: int) -> int:
    if value is None:
        return default
    try:
        ival = int(value)
    except (TypeError, ValueError):
        raise RunError(f"{field} must be an integer")
    if not lo <= ival <= hi:
        raise RunError(f"{field} must be within [{lo}, {hi}]")
    return ival


def validate_spec(body: Dict[str, Any]) -> Dict[str, Any]:
    """Normalise + validate a POST /api/runs body."""
    if not isinstance(body, dict):
        raise RunError("request body must be a JSON object")
    cfg = config.current()

    kind = str(body.get("kind") or "").strip()
    if kind not in KINDS:
        raise RunError(f"kind must be one of {list(KINDS)}")

    source = str(body.get("source") or "").strip()
    pod_urls = cfg.get("pod_urls") or {}
    if source not in pod_urls:
        raise RunError(f"unknown source '{source}'; known entities: {sorted(pod_urls)}")

    mode = str(body.get("mode") or DEFAULT_MODE).strip().lower()
    if mode not in MODES:
        raise RunError(f"mode must be one of {list(MODES)}")

    params = body.get("params") or {}
    if not isinstance(params, dict):
        raise RunError("params must be a JSON object")

    repeats = _coerce_int(body.get("repeats"), "repeats", int(cfg["default_repeats"]),
                          1, MAX_REPEATS)
    concurrency = _coerce_int(body.get("concurrency"), "concurrency",
                              int(cfg["concurrency"]), 1, MAX_CONCURRENCY)

    force_raw = body.get("force_degraded")
    force_degraded = bool(force_raw) if force_raw is not None else False

    label = body.get("label")
    label = None if label is None else str(label)[:200]

    bpcr_expected = None
    if kind == "diagnosis":
        patient_id = params.get("patient_id")
        patient_ids = params.get("patient_ids")
        input_data = params.get("input")
        if isinstance(patient_ids, list) and patient_ids:
            params["patient_ids"] = [str(x) for x in patient_ids]
        elif not patient_id and not isinstance(input_data, dict):
            raise RunError("diagnosis requires params.patient_id, "
                           "params.patient_ids or params.input")
        if patient_id:
            patient = get_patient(str(patient_id))
            if patient is None:
                if db.count_patients() > 0:
                    raise RunError(
                        f"patient {patient_id} is not in the local dataset "
                        f"(see GET /api/patients)")
            else:
                bpcr_expected = _f(patient.get("bpCR"))
                if patient.get("hospital") is not None:
                    params.setdefault("hospital", patient.get("hospital"))

    return {
        "run_id": db.new_run_id(),
        "kind": kind,
        "source": source,
        "mode": mode,
        "mode_requested": mode,
        "repeats": repeats,
        "concurrency": concurrency,
        "label": label,
        "force_degraded": force_degraded,
        "params": params,
        "spec_key": _spec_key(kind, params),
        "bpcr_expected": bpcr_expected,
    }


def create_run(body: Dict[str, Any]) -> Dict[str, Any]:
    """Validate + persist a run, pre-create its attempts and start the workers."""
    spec = validate_spec(body)
    params = dict(spec["params"])
    # the imaging payload (~2 MB/patient) is NOT duplicated into every attempt
    # row: inline inputs live in memory, patient_id inputs are re-read from the
    # patients table at execution time (§8.5).
    inline = params.pop("input", None)
    if isinstance(inline, dict) and inline:
        with _RUN_INPUTS_LOCK:
            _RUN_INPUTS[spec["run_id"]] = inline
    spec["params"] = params
    db_params = dict(params)
    if isinstance(inline, dict) and inline:
        db_params["input_inline"] = True
        db_params["input_keys"] = sorted(inline.keys())
    spec["db_params"] = db_params

    db.create_run({**spec, "params": db_params, "status": "queued"})
    db.create_attempts(spec["run_id"], spec["repeats"],
                       {"kind": spec["kind"], "source": spec["source"],
                        "mode_requested": spec["mode_requested"],
                        "spec_key": spec["spec_key"],
                        "params": db_params,
                        "bpcr_expected": spec["bpcr_expected"]})
    _spawn_workers(spec["run_id"], spec["concurrency"])
    public_params = {k: v for k, v in db_params.items() if k != "input"}
    return {
        "run_id": spec["run_id"],
        "spec": {"kind": spec["kind"], "source": spec["source"],
                 "mode": spec["mode"], "repeats": spec["repeats"],
                 "concurrency": spec["concurrency"],
                 "force_degraded": spec["force_degraded"],
                 "label": spec["label"], "params": public_params,
                 "spec_key": spec["spec_key"]},
        "status": "queued",
        "attempts": spec["repeats"],
    }


# --------------------------------------------------------------------------- #
# routing helpers (§6.3)                                                      #
# --------------------------------------------------------------------------- #
def _probe_scheduler(cfg: Dict[str, Any]) -> Tuple[float, bool, Optional[str], Optional[int]]:
    """Short pre-flight probe (mode=auto). Returns (ms, reachable, error, code)."""
    base = str(cfg.get("scheduler_url", "")).rstrip("/")
    t0 = time.perf_counter()
    if not base:
        return (_ms(t0), False, "scheduler_url is empty", None)
    try:
        r = _SESSION.get(base + "/", timeout=_timeouts(cfg, "probe_timeout_ms"))
        code = r.status_code
        elapsed = _ms(t0)
        if code < 500:
            return (elapsed, True, None, code)
        return (elapsed, False, f"scheduler returned HTTP {code}", code)
    except Exception as exc:  # noqa: BLE001
        return (_ms(t0), False, _trunc(f"{type(exc).__name__}: {exc}", 200), None)


_IMG_KEYS = ("dce_image", "dwi_image", "clinical", "radiomics")


def _shape_of(nested) -> List[int]:
    shape, cur = [], nested
    while isinstance(cur, (list, tuple)):
        shape.append(len(cur))
        cur = cur[0] if len(cur) else None
    return shape


def _flatten(nested):
    if isinstance(nested, (list, tuple)):
        for item in nested:
            yield from _flatten(item)
    else:
        yield float(nested)


def encode_f32b64(nested) -> Dict[str, Any]:
    """Nested float lists -> {"__f32b64__", "shape"} (stdlib array/base64).

    The imaging input is ~2MB of float text per patient; sending it as float32
    base64 is ~4x smaller and skips per-float JSON work on both ends. The pod
    decodes it back to the exact same float32 values.
    """
    flat = array.array("f", _flatten(nested))
    if sys.byteorder == "big":
        flat.byteswap()
    return {"__f32b64__": base64.b64encode(flat.tobytes()).decode(),
            "shape": _shape_of(nested), "dtype": "float32"}


def encode_input(inp: Dict[str, Any]) -> Dict[str, Any]:
    """Compact-encode imaging fields (top level and inside a diagnosis batch)."""
    out = dict(inp or {})
    for key in _IMG_KEYS:
        if isinstance(out.get(key), list):
            out[key] = encode_f32b64(out[key])
    batch = out.get("batch")
    if isinstance(batch, list):
        out["batch"] = [encode_input(item) if isinstance(item, dict) else item
                        for item in batch]
    return out


def _schedule_body(kind: str, source: str, params: Dict[str, Any],
                   input_data: Dict[str, Any]) -> Dict[str, Any]:
    body: Dict[str, Any] = {
        "source": source,
        "priority": int(params.get("priority", 5) or 5),
        "deadline": str(params.get("deadline") or "120s"),
    }
    if kind == "diagnosis":
        if params.get("patient_id") and not params.get("patient_ids"):
            body["patient_id"] = str(params["patient_id"])
        if params.get("target_hospital"):
            body["target_hospital"] = params["target_hospital"]
        # the batch payload is materialised on this side so that the
        # collaborative and the local path receive byte-identical inputs
        if input_data:
            batch = input_data.get("batch") or []
            if batch and config.current().get("direct_delivery", True):
                # 批量诊断走「控制面下计划 + 数据面直投」，避免输入经控制面中转两趟
                body["deliver"] = "direct"
                body["patient_ids"] = [str(it.get("patient_id")) for it in batch]
            else:
                body["input"] = encode_input(input_data)
    elif kind == "compute":
        body.update({
            "instruments": int(params.get("instruments", 4)),
            "rows": int(params.get("rows", 256)),
            "intensity": int(params.get("intensity", 40)),
            "partition_count": int(params.get("partition_count", 3)),
        })
        # a fixed seed keeps collaborative and local checksums comparable
        if params.get("seed") is not None:
            body["seed"] = int(params["seed"])
    elif kind == "sync":
        body.update({
            "bandwidth_mbps": float(params.get("bandwidth_mbps", 20.0)),
            "concurrency": int(params.get("concurrency", 4)),
            "chunk_kb": int(params.get("chunk_kb", 4)),
        })
    elif kind == "routine":
        body.update({
            "jobs": int(params.get("jobs", 2)),
            "rows": int(params.get("rows", 128)),
            "intensity": int(params.get("intensity", 30)),
        })
        # same fixed-seed convention as compute (Job path vs inlined local jobs)
        if params.get("seed") is not None:
            body["seed"] = int(params["seed"])
    return body


def _pod_body(kind: str, source: str, params: Dict[str, Any],
              input_data: Dict[str, Any], task_id: str,
              degraded: bool, degrade_reason: Optional[str],
              cfg: Dict[str, Any]) -> Dict[str, Any]:
    pod_params = {k: v for k, v in params.items() if k != "input"}
    if kind == "diagnosis" and params.get("patient_id"):
        pod_params["patient_id"] = str(params["patient_id"])
    return {
        "task_id": task_id,
        "kind": kind,
        "source": source,
        "params": pod_params,
        "input": encode_input(input_data) if kind == "diagnosis" else {},
        # (batch payloads are materialised by _execute_attempt)
        "degraded": bool(degraded),
        "degrade_reason": degrade_reason,
        "async": bool(cfg.get("local_async")),
    }


# --------------------------------------------------------------------------- #
# HTTP execution paths                                                        #
# --------------------------------------------------------------------------- #
# --------------------------------------------------------------------------- #
# direct data-plane delivery (v3.5)                                           #
# --------------------------------------------------------------------------- #
def _call_scheduler_direct(source: str, plan: Dict[str, Any], input_data: Dict[str, Any],
                           cfg: Dict[str, Any], task_id: str, t_control: float
                           ) -> Dict[str, Any]:
    """把输入**直投执行者**（医院 / 数据中心），绕开控制面的数据中转。

    控制面（调度器）只给了「谁执行哪几个患者」的计划；数据面由本站直接发给执行者，
    因此输入只走一趟（和本地执行一样），不再走「站点→调度器→执行者」两趟。
    """
    out: Dict[str, Any] = {"path": "scheduler-direct", "ok": False, "error": None,
                           "envelope": None, "task_id": task_id, "unreachable": False}
    by_pid = {str(it.get("patient_id")): it for it in (input_data.get("batch") or [])}
    # 站点的一次性时间预算：不把控制面往返计入「协同开销」之外
    t0 = t_control

    def _one(exec_spec: Dict[str, Any]):
        url = str(exec_spec.get("url") or "")
        pids = [str(x) for x in (exec_spec.get("patient_ids") or [])]
        rec: Dict[str, Any] = {"executor": exec_spec.get("entity"), "state": "failed",
                               "patient_ids": pids}
        t = time.perf_counter()

        def _patient(pid: str) -> Dict[str, Any]:
            # 执行者端点是**单例**接口（与已验证逐位一致的路径一致），
            # 因此这里按患者各发一次、同一执行者内部并发。
            item = by_pid.get(pid) or {}
            body = encode_input({k: v for k, v in item.items() if k in _IMG_KEYS})
            body["patient_ids"] = item.get("patient_ids") or [str(item.get("patient_id") or pid)]
            r = _SESSION.post(url, json=body, timeout=_timeouts(cfg, "call_timeout_ms"))
            r.raise_for_status()
            return r.json()

        try:
            with ThreadPoolExecutor(max_workers=max(1, len(pids))) as pool:
                outs = list(pool.map(_patient, pids))
            rec.update({
                "state": "succeeded",
                "predictions": [p for o in outs for p in (o.get("predictions") or [])],
                "compute_ms": sum(float(o.get("latency_ms") or 0) for o in outs),
            })
        except Exception as e:  # noqa: BLE001
            rec["error"] = f"{type(e).__name__}: {str(e)[:150]}"
        rec["ms"] = round((time.perf_counter() - t) * 1000, 2)
        return rec

    with ThreadPoolExecutor(max_workers=max(1, len(plan.get("executors") or []))) as pool:
        results = list(pool.map(_one, plan.get("executors") or []))
    wall_ms = round((time.perf_counter() - t0) * 1000, 2)

    try:  # 回传记账（失败不影响本次结果，只影响调度器归档）
        _SESSION.post(str(cfg.get("scheduler_url", "")).rstrip("/")
                      + f"/task/{task_id}/report",
                      json={"results": results, "client_total_ms": wall_ms},
                      timeout=_timeouts(cfg, "call_timeout_ms"))
    except Exception:  # noqa: BLE001
        pass

    ok = [r for r in results if r.get("state") == "succeeded"]
    envelope = {
        "task_id": task_id, "kind": "diagnosis", "mode": "collaborative",
        "mode_requested": "collaborative", "degraded": False,
        "orchestrator": "pod+datacenter",
        "executor": ",".join(sorted(str(r.get("executor")) for r in results)),
        "stages": [{"name": "data-parallel", "actor": r.get("executor"),
                    "node": r.get("executor"), "ms": r.get("ms"),
                    "compute_ms": r.get("compute_ms")} for r in results],
        "result_detail": {
            "strategy": "data", "deliver": "direct",
            "batch_size": len(plan.get("executors") or []),
            "predictions": [p for r in ok for p in (r.get("predictions") or [])],
            "success": len(ok), "failed": [r for r in results if r not in ok],
            "per_executor": {str(r.get("executor")): r.get("ms") for r in results},
        },
        "metrics": {"pipeline_total_ms": wall_ms, "e2e_total_ms": wall_ms,
                    "compute_ms_total": round(sum(float(r.get("compute_ms") or 0)
                                                  for r in results), 2)},
        "status": "completed" if ok else "failed",
        "error": None if ok else "所有执行者均失败",
    }
    out.update({"ok": bool(ok), "envelope": envelope,
                "error": None if ok else envelope["error"],
                "elapsed_ms": _ms(t0)})
    return out


def _call_scheduler(kind: str, source: str, params: Dict[str, Any],
                    input_data: Dict[str, Any],
                    cfg: Dict[str, Any]) -> Dict[str, Any]:
    """POST /schedule/{kind} then poll /task/result/{id}. Wall-clock measured."""
    base = str(cfg.get("scheduler_url", "")).rstrip("/")
    url = f"{base}/schedule/{kind}"
    body = _schedule_body(kind, source, params, input_data)
    out: Dict[str, Any] = {"path": "scheduler", "unreachable": False,
                           "ok": False, "error": None, "envelope": None,
                           "task_id": None, "elapsed_ms": None}
    t0 = time.perf_counter()
    try:
        r = _SESSION.post(url, json=body, timeout=_timeouts(cfg, "call_timeout_ms"))
        r.raise_for_status()
        payload = r.json()
    except requests.exceptions.HTTPError as exc:
        code = getattr(getattr(exc, "response", None), "status_code", None)
        detail = ""
        try:
            detail = _trunc(getattr(exc.response, "text", ""), 150)
        except Exception:  # noqa: BLE001
            detail = ""
        out.update({"error": _trunc(f"scheduler HTTP {code} on /schedule/{kind}: {detail}"),
                    "elapsed_ms": _ms(t0)})
        return out
    except (requests.exceptions.ConnectionError,
            requests.exceptions.ConnectTimeout) as exc:
        # "scheduler not reachable" (refused / DNS / connect timeout) is the ONLY
        # case that may trigger the site-side pod fallback. An HTTP error status
        # or a read timeout means the scheduler is alive and may already own the
        # task, so falling back could double-execute it (§8.11).
        out.update({"unreachable": True, "elapsed_ms": _ms(t0),
                    "error": _trunc(f"scheduler unreachable: {type(exc).__name__}: {exc}")})
        return out
    except requests.exceptions.Timeout as exc:
        out.update({"elapsed_ms": _ms(t0),
                    "error": _trunc(f"scheduler submit read timeout: {exc}")})
        return out
    except ValueError as exc:
        out.update({"elapsed_ms": _ms(t0),
                    "error": _trunc(f"scheduler returned non-JSON: {exc}")})
        return out
    except requests.exceptions.RequestException as exc:
        out.update({"elapsed_ms": _ms(t0),
                    "error": _trunc(f"scheduler submit failed: {type(exc).__name__}: {exc}")})
        return out

    task_id = None
    if isinstance(payload, dict):
        task_id = payload.get("task_id") or payload.get("id")
    if isinstance(payload, dict) and payload.get("executors") and task_id:
        # v3.5: 调度器返回了执行计划 → 数据面直投执行者
        return _call_scheduler_direct(source, payload, input_data, cfg,
                                      str(task_id), t0)
    if not task_id:
        out.update({"elapsed_ms": _ms(t0),
                    "error": _trunc(f"scheduler did not return a task_id: {payload}")})
        return out
    out["task_id"] = str(task_id)

    deadline = time.perf_counter() + float(cfg.get("task_timeout_ms", 600000)) / 1000.0
    interval = max(0.05, float(cfg.get("poll_interval_ms", 250)) / 1000.0)
    # Poll tightly for the first few seconds: the scheduler path is asynchronous
    # (submit -> queue -> poll) while local execution answers in one call, so a
    # coarse poll interval would charge the collaborative mode a measurement
    # penalty that has nothing to do with the architecture.
    fast_poll_until = time.perf_counter() + float(
        cfg.get("poll_fast_window_ms", 3000)) / 1000.0
    delay = 0.02
    last: Any = None
    while True:
        try:
            r = _SESSION.get(f"{base}/task/result/{task_id}",
                             timeout=_timeouts(cfg, "poll_http_timeout_ms"))
            r.raise_for_status()
            last = r.json()
        except requests.exceptions.HTTPError as exc:
            code = getattr(getattr(exc, "response", None), "status_code", None)
            out.update({"elapsed_ms": _ms(t0),
                        "error": _trunc(f"scheduler /task/result/{task_id} HTTP {code}")})
            return out
        except (requests.exceptions.ConnectionError,
                requests.exceptions.ConnectTimeout) as exc:
            # the task was already accepted: do NOT re-dispatch on the pod
            out.update({"elapsed_ms": _ms(t0),
                        "error": _trunc(f"lost contact with scheduler while polling "
                                        f"{task_id}: {type(exc).__name__}")})
            return out
        except requests.exceptions.Timeout:
            last = None  # keep polling until the task budget is exhausted
        except ValueError:
            last = None

        if isinstance(last, dict):
            st = str(last.get("status") or "").lower()
            if st in ("completed", "finished", "failed", "error", "timeout",
                      "cancelled"):
                envelope, status, error = _normalize_scheduler_result(last)
                out.update({"ok": status == "completed", "envelope": envelope,
                            "error": error, "elapsed_ms": _ms(t0),
                            "task_status": st})
                return out
        if time.perf_counter() >= deadline:
            out.update({"elapsed_ms": _ms(t0),
                        "error": _trunc(f"scheduler task {task_id} did not finish within "
                                        f"{int(cfg.get('task_timeout_ms', 600000))}ms")})
            return out
        time.sleep(delay)
        delay = (0.02 if time.perf_counter() < fast_poll_until else interval)


def _normalize_scheduler_result(payload: Dict[str, Any]
                                ) -> Tuple[Optional[Dict[str, Any]], str, Optional[str]]:
    """`{'status':'completed','result':{...}}` -> (envelope, status, error)."""
    st = str(payload.get("status") or "").lower()
    inner = payload.get("result")
    envelope = inner if isinstance(inner, dict) else payload
    status = "completed" if st in ("completed", "finished") else "failed"
    error = payload.get("error")
    inner_status = str(envelope.get("status") or "").lower()
    if inner_status in ("failed", "error"):
        status = "failed"
        error = error or envelope.get("error")
    return (envelope, status, _trunc(error) if error else None)


def _call_pod(entity: str, kind: str, source: str, params: Dict[str, Any],
              input_data: Dict[str, Any], degraded: bool,
              degrade_reason: Optional[str], cfg: Dict[str, Any]
              ) -> Dict[str, Any]:
    """POST {pod}/local/execute (设计方案 §4). Wall-clock measured."""
    base = str(config.entity_url(entity) or "").rstrip("/")
    out: Dict[str, Any] = {"path": "pod", "ok": False, "error": None,
                           "envelope": None, "task_id": None, "elapsed_ms": None,
                           "unreachable": False}
    if not base:
        out["error"] = f"no pod_url configured for {entity}"
        return out
    task_id = "ext-" + uuid.uuid4().hex[:16]
    out["task_id"] = task_id
    body = _pod_body(kind, source, params, input_data, task_id, degraded,
                     degrade_reason, cfg)
    url = base + "/local/execute"
    t0 = time.perf_counter()
    try:
        r = _SESSION.post(url, json=body, timeout=_timeouts(cfg, "call_timeout_ms"))
    except requests.exceptions.RequestException as exc:
        out.update({"elapsed_ms": _ms(t0),
                    "error": _trunc(f"pod {entity} {url} failed: "
                                    f"{type(exc).__name__}: {exc}")})
        return out

    if r.status_code == 404:
        out.update({"elapsed_ms": _ms(t0),
                    "error": _trunc(f"local endpoint unavailable (404) at {url}")})
        return out
    if r.status_code >= 400:
        out.update({"elapsed_ms": _ms(t0),
                    "error": _trunc(f"local endpoint error (HTTP {r.status_code}) at "
                                    f"{url}: {_trunc(r.text, 150)}")})
        return out
    try:
        payload = r.json()
    except ValueError as exc:
        out.update({"elapsed_ms": _ms(t0),
                    "error": _trunc(f"local endpoint returned non-JSON: {exc}")})
        return out

    envelope, status, error = _normalize_pod_result(payload)
    if status in ("queued", "running"):
        envelope, status, error = _poll_pod_result(base, task_id, cfg)
    out.update({"ok": status == "completed", "envelope": envelope, "error": error,
                "elapsed_ms": _ms(t0)})
    return out


def _normalize_pod_result(payload: Any) -> Tuple[Optional[Dict[str, Any]], str, Optional[str]]:
    if not isinstance(payload, dict):
        return (None, "failed", _trunc(f"unexpected local response: {payload}"))
    inner = payload.get("result")
    envelope = inner if isinstance(inner, dict) else payload
    st = str(payload.get("status") or envelope.get("status") or "").lower()
    if st in ("completed", "finished", "success", "ok"):
        status = "completed"
    elif st in ("queued", "pending", "running"):
        status = st
    elif st in ("failed", "error", "timeout"):
        status = "failed"
    else:
        status = "completed" if envelope.get("result_detail") is not None else "failed"
    error = payload.get("error") or envelope.get("error")
    return (envelope, status, _trunc(error) if error else None)


def _poll_pod_result(base: str, task_id: str, cfg: Dict[str, Any]
                     ) -> Tuple[Optional[Dict[str, Any]], str, Optional[str]]:
    url = f"{base}/local/result/{task_id}"
    deadline = time.perf_counter() + float(cfg.get("task_timeout_ms", 600000)) / 1000.0
    interval = max(0.05, float(cfg.get("poll_interval_ms", 250)) / 1000.0)
    delay = min(0.15, interval)
    while True:
        try:
            r = _SESSION.get(url, timeout=_timeouts(cfg, "poll_http_timeout_ms"))
        except requests.exceptions.RequestException as exc:
            return (None, "failed", _trunc(f"pod poll failed: {type(exc).__name__}: {exc}"))
        if r.status_code == 404:
            return (None, "failed", _trunc(f"local endpoint unavailable (404) at {url}"))
        if r.status_code >= 400:
            return (None, "failed",
                    _trunc(f"local endpoint error (HTTP {r.status_code}) at {url}"))
        try:
            envelope, status, error = _normalize_pod_result(r.json())
        except ValueError:
            envelope, status, error = (None, "running", None)
        if status not in ("queued", "running"):
            return (envelope, status, error)
        if time.perf_counter() >= deadline:
            return (None, "failed",
                    _trunc(f"pod task {task_id} did not finish within "
                           f"{int(cfg.get('task_timeout_ms', 600000))}ms"))
        time.sleep(delay)
        delay = min(interval, delay * 2)


# --------------------------------------------------------------------------- #
# metric decomposition (stored only — never replaces client_total_ms)         #
# --------------------------------------------------------------------------- #
def _extract_metrics(kind: str, envelope: Optional[Dict[str, Any]],
                     mode_used: str) -> Dict[str, Any]:
    env = envelope or {}
    metrics = env.get("metrics") if isinstance(env.get("metrics"), dict) else {}
    stages = env.get("stages") if isinstance(env.get("stages"), list) else []
    detail = env.get("result_detail") if isinstance(env.get("result_detail"), dict) else {}
    notes: List[str] = []

    def metric(*names: str) -> Optional[float]:
        for name in names:
            if name in metrics:
                val = _f(metrics.get(name))
                if val is not None:
                    return val
        return None

    queue_wait = metric("queue_wait_ms")
    pipeline = metric("pipeline_total_ms")
    if pipeline is None:
        pipeline = metric("e2e_total_ms", "total_ms")

    compute = metric("compute_ms_total")
    if compute is None:
        stage_sum = [ _f(s.get("compute_ms")) for s in stages
                      if isinstance(s, dict) and s.get("compute_ms") is not None ]
        if stage_sum:
            compute = round(sum(v for v in stage_sum if v is not None), 3)
            notes.append("compute_ms_total=sum(stage.compute_ms)")
        else:
            flat = [v for k, v in metrics.items()
                    if k.endswith("_compute_ms") and _f(v) is not None]
            if flat:
                compute = round(sum(_f(v) for v in flat), 3)
                notes.append("compute_ms_total=sum(*_compute_ms in metrics)")
            elif kind == "compute" and _f(detail.get("aggregate_cpu_ms")) is not None:
                compute = _f(detail.get("aggregate_cpu_ms"))
                notes.append("compute_ms_total=result_detail.aggregate_cpu_ms")

    network = metric("network_ms_total")
    if network is None:
        stage_net = [ _f(s.get("network_ms")) for s in stages
                      if isinstance(s, dict) and s.get("network_ms") is not None ]
        if stage_net:
            network = round(sum(v for v in stage_net if v is not None), 3)
            notes.append("network_ms_total=sum(stage.network_ms)")
        else:
            flat = [v for k, v in metrics.items()
                    if k.endswith("_network_ms") and _f(v) is not None]
            if flat:
                network = round(sum(_f(v) for v in flat), 3)
                notes.append("network_ms_total=sum(*_network_ms in metrics)")
            elif pipeline is not None and compute is not None:
                network = round(max(0.0, pipeline - compute), 3)
                notes.append("network_ms_total=pipeline_total_ms-compute_ms_total")

    if mode_used == "local" and queue_wait is None:
        # §3: local execution never queues (the pod owns the task)
        queue_wait = 0.0
        notes.append("queue_wait_ms=0 (local execution)")

    return {
        "queue_wait_ms": queue_wait,
        "pipeline_total_ms": pipeline,
        "compute_ms_total": compute,
        "network_ms_total": network,
        "envelope_degrade_switch_ms": metric("degrade_switch_ms"),
        "envelope_e2e_total_ms": metric("e2e_total_ms"),
        "source": "envelope" if not notes else "; ".join(notes),
        "raw": metrics,
    }


def _extract_result(kind: str, envelope: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    env = envelope or {}
    detail = env.get("result_detail")
    if not isinstance(detail, dict):
        detail = {}
    stages = env.get("stages")
    if not isinstance(stages, list):
        stages = []
    return {"result_detail": detail, "stages": stages,
            "orchestrator": env.get("orchestrator"),
            "executor": env.get("executor"),
            "initiator": env.get("initiator"),
            "mode": env.get("mode")}


def _bpcr_actual(kind: str, detail: Dict[str, Any]) -> Optional[float]:
    """Full-precision probability — §3.1 requires bit-identical predictions."""
    if kind != "diagnosis":
        return None
    for key in ("bpCR_probability", "bpcr_probability", "prediction",
                "bpCR_prediction"):
        if key in detail:
            val = _num(detail.get(key), None)
            if val is not None:
                return val
    return None


# --------------------------------------------------------------------------- #
# attempt execution                                                           #
# --------------------------------------------------------------------------- #
def _execute_attempt(attempt: Dict[str, Any], run: Dict[str, Any],
                     cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Run one attempt and return the column values to persist. Never raises."""
    kind = run["kind"]
    source = run["source"]
    # a fixed-suite run carries different params per attempt; a normal run
    # repeats one spec, so both shapes end up here.
    run_params = (attempt.get("params") if isinstance(attempt.get("params"), dict)
                  and attempt.get("params") else None) or (run.get("params") or {})
    input_data: Dict[str, Any] = {}
    if kind == "diagnosis":
        with _RUN_INPUTS_LOCK:
            input_data = _RUN_INPUTS.get(run["run_id"]) or {}
        pids = run_params.get("patient_ids")
        if not input_data and isinstance(pids, list) and pids:
            # materialise the batch here so the collaborative and the local
            # path both send the same payload (the scheduler path could resolve
            # ids itself, but then the two paths would not be comparable)
            batch = []
            for pid in pids:
                patient = db.get_patient(str(pid))
                if not patient:
                    return {"status": "failed",
                            "error": _trunc(f"patient {pid} not in the bundled "
                                            f"dataset (see GET /api/patients)"),
                            "finished_at": _now()}
                item = dict(patient.get("input") or {})
                item["patient_id"] = str(pid)
                item.setdefault("patient_ids", [str(pid)])
                batch.append(item)
            input_data = {"batch": batch, "patient_ids": [str(x) for x in pids]}
        if not input_data and run_params.get("patient_id"):
            patient = db.get_patient(str(run_params["patient_id"]))
            input_data = (patient or {}).get("input") or {}
        if not input_data:
            return {"status": "failed",
                    "error": _trunc(f"no input available for diagnosis patient "
                                    f"{run_params.get('patient_id')} (missing "
                                    f"patients dataset / params.input)"),
                    "finished_at": _now()}

    mode_requested = run["mode"]
    forced = bool(run["force_degraded"]) or bool(cfg.get("force_degraded"))
    switch_ms = 0.0
    switch_notes: List[str] = []
    degrade_reason: Optional[str] = None
    degraded = False
    route = "scheduler"

    if forced:
        route, degraded, degrade_reason = "pod", True, "forced"
        switch_notes.append("force_degraded -> skip scheduler")
    elif mode_requested == "local":
        route, degraded = "pod", False
        switch_notes.append("mode=local -> direct pod")
    elif mode_requested == "auto":
        probe_ms, reachable, probe_err, code = _probe_scheduler(cfg)
        switch_ms += probe_ms
        if reachable:
            switch_notes.append(f"auto probe ok in {probe_ms}ms (HTTP {code})")
        else:
            route, degraded, degrade_reason = "pod", True, "scheduler_unreachable"
            switch_notes.append(f"auto probe failed in {probe_ms}ms: {probe_err}")

    scheduler_attempt: Optional[Dict[str, Any]] = None
    call: Dict[str, Any]
    if route == "scheduler":
        call = _call_scheduler(kind, source, run_params, input_data, cfg)
        scheduler_attempt = {"ok": call.get("ok"), "error": call.get("error"),
                             "elapsed_ms": call.get("elapsed_ms"),
                             "task_id": call.get("task_id")}
        if not call.get("ok") and call.get("unreachable") and cfg.get("auto_degrade"):
            decision_t0 = time.perf_counter()
            switch_ms += float(call.get("elapsed_ms") or 0.0)
            switch_ms += _ms(decision_t0)
            switch_notes.append(
                f"scheduler submit failed ({call.get('error')}) -> pod fallback")
            route, degraded, degrade_reason = "pod", True, "scheduler_unreachable"
            call = _call_pod(source, kind, source, run_params, input_data or {},
                             degraded, degrade_reason, cfg)
    else:
        call = _call_pod(source, kind, source, run_params, input_data or {},
                         degraded, degrade_reason, cfg)

    envelope = call.get("envelope")
    metrics = _extract_metrics(kind, envelope, "local" if route == "pod" else "collaborative")
    parts = _extract_result(kind, envelope)

    envelope_switch = metrics.get("envelope_degrade_switch_ms")
    site_switch = round(switch_ms, 3)
    if site_switch <= 0 and envelope_switch:
        site_switch = envelope_switch
        switch_notes.append("degrade_switch_ms taken from envelope")

    status = "completed" if call.get("ok") else "failed"
    error = call.get("error")
    if not call.get("ok") and not error:
        error = "attempt failed without an error message"
    if call.get("ok") and envelope and str(envelope.get("status", "")).lower() == "failed":
        status, error = "failed", _trunc(error or envelope.get("error") or "task reported failed")

    detail = {
        "route": route,
        "attempt_index": attempt.get("attempt_index"),
        "switch_notes": switch_notes,
        "scheduler_attempt": scheduler_attempt,
        "call_elapsed_ms": call.get("elapsed_ms"),
        "http_path": call.get("path"),
        "auto_degrade": bool(cfg.get("auto_degrade")),
        "force_degraded_run": bool(run.get("force_degraded")),
        "force_degraded_global": bool(cfg.get("force_degraded")),
    }

    return {
        "mode_used": "local" if route == "pod" else "collaborative",
        "degraded": 1 if degraded else 0,
        "degrade_reason": degrade_reason,
        "orchestrator": (parts.get("orchestrator")
                         or ("pod" if route == "pod" else "scheduler")),
        "executor": parts.get("executor") or source,
        "task_id": call.get("task_id"),
        "client_total_ms": _f(call.get("elapsed_ms")),
        "queue_wait_ms": metrics.get("queue_wait_ms"),
        "pipeline_total_ms": metrics.get("pipeline_total_ms"),
        "compute_ms_total": metrics.get("compute_ms_total"),
        "network_ms_total": metrics.get("network_ms_total"),
        "degrade_switch_ms": site_switch,
        "stages_json": json.dumps(parts.get("stages") or [], ensure_ascii=False),
        "result_detail_json": json.dumps(parts.get("result_detail") or {},
                                         ensure_ascii=False),
        "metrics_json": json.dumps(metrics.get("raw") or {}, ensure_ascii=False),
        "envelope_json": json.dumps(envelope, ensure_ascii=False) if envelope else None,
        "metrics_source": metrics.get("source"),
        "detail_json": json.dumps(detail, ensure_ascii=False),
        "input_keys": json.dumps(sorted(input_data.keys()), ensure_ascii=False) if input_data else None,
        "bpcr_actual": _bpcr_actual(kind, parts.get("result_detail") or {}),
        "status": status,
        "error": _trunc(error) if error else None,
        "finished_at": _now(),
    }


def create_suite_run(body: Dict[str, Any]) -> Dict[str, Any]:
    """Create one run that executes a *fixed* suite (e.g. 20 compute tasks).

    The suite's spec list is frozen in suites.py, so 协同 vs 本地 runs measure
    byte-identical work; only the execution mode (and optionally the initiating
    entity) changes.
    """
    if not isinstance(body, dict):
        raise RunError("request body must be a JSON object")
    suite_id = str(body.get("suite_id") or "").strip()
    try:
        suite = suites_mod.get_suite(suite_id)
    except KeyError as exc:
        raise RunError(str(exc))

    cfg = config.current()
    pod_urls = cfg.get("pod_urls") or {}
    source = str(body.get("source") or suite["default_source"]).strip()
    if source not in pod_urls:
        raise RunError(f"unknown source '{source}'; known entities: {sorted(pod_urls)}")

    mode = str(body.get("mode") or DEFAULT_MODE).strip().lower()
    if mode not in MODES:
        raise RunError(f"mode must be one of {list(MODES)}")

    concurrency = _coerce_int(body.get("concurrency"), "concurrency",
                              int(suite.get("default_concurrency") or 1), 1,
                              MAX_CONCURRENCY)
    label = body.get("label")
    label = None if label is None else str(label)[:200]

    run_id = db.new_run_id()
    items: List[Dict[str, Any]] = []
    for task in suites_mod.expand(suite):
        params = dict(task["params"])
        items.append({
            "kind": suite["kind"],
            "source": source,
            "mode_requested": mode,
            "params": params,
            "spec_key": _spec_key(suite["kind"], params),
            "bpcr_expected": None,
        })

    db.create_run({"run_id": run_id, "kind": suite["kind"], "source": source,
                   "mode": mode, "repeats": len(items), "concurrency": concurrency,
                   "label": label or suite["name"], "force_degraded": False,
                   "params": {}, "suite_id": suite_id, "status": "queued"})
    db.create_attempt_plan(run_id, items)
    _spawn_workers(run_id, concurrency)
    return {
        "run_id": run_id,
        "suite_id": suite_id,
        "spec": {"kind": suite["kind"], "source": source, "mode": mode,
                 "repeats": len(items), "concurrency": concurrency,
                 "label": label or suite["name"], "suite": suite["name"]},
        "status": "queued",
        "attempts": len(items),
    }


def _percentile(sorted_values: List[float], pct: float) -> float:
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return sorted_values[0]
    k = (len(sorted_values) - 1) * (pct / 100.0)
    lo = int(k)
    hi = min(lo + 1, len(sorted_values) - 1)
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (k - lo)


# --------------------------------------------------------------------------- #
# 测试方案运行（测试大纲 §5.4 / §5.5）                                          #
#                                                                             #
# 一次方案运行 = 若干子 run，按调度策略分阶段**顺序**执行：                     #
#   阶段 1「本地执行」(未调度) → 全部子 run 跑完 → 记录阶段总用时；             #
#   阶段 2「云边端协同」→ 同样的任务需求再产生一遍 → 记录阶段总用时。           #
# 阶段内每个任务的产生时刻在 `generate_window_s` 内按种子随机分布，             #
# 对应大纲「在 5 分钟内由不同端侧医疗设备随机产生」。                           #
# --------------------------------------------------------------------------- #
_PLAN_LOCK = threading.Lock()
_PLAN_WORKERS: Dict[str, threading.Thread] = {}
_PLAN_POLL_S = 1.0


def _iso_after(seconds: float) -> str:
    from datetime import timedelta
    return (datetime.now(timezone.utc) + timedelta(seconds=max(0.0, float(seconds)))
            ).isoformat(timespec="milliseconds")


def _plan_offsets(count: int, window_s: float, rng) -> List[float]:
    """在 [0, window_s] 内随机产生 count 个任务时刻（升序，便于按序领取）。"""
    count = max(0, int(count))
    if count == 0:
        return []
    if window_s <= 0:
        return [0.0] * count
    return sorted(rng.uniform(0.0, float(window_s)) for _ in range(count))


def _deal_offsets(units: List[Dict[str, Any]], window_s: float,
                  rng) -> List[List[float]]:
    """把「整个阶段的任务产生时刻」摊到各执行单元上。

    大纲的窗口是**阶段级**的（B、C 两台设备的全部任务在 5 分钟内随机产生），
    所以先在整个阶段的任务总数上抽样，再轮流发给各单元；单元内部仍按时刻升序
    领取。若按单元各自抽样，"每单元只有 1 个任务"时窗口会退化成 0。
    """
    counts = [max(0, int(u.get("repeats") or 0)) for u in units]
    total = sum(counts)
    if total == 0 or not units:
        return [[] for _ in units]
    pool = _plan_offsets(total, window_s, rng)
    buckets: List[List[float]] = [[] for _ in units]
    for idx, when in enumerate(pool):
        buckets[idx % len(units)].append(when)   # 轮流发放，避免同一时刻挤在一台设备
    for bucket, want in zip(buckets, counts):
        if len(bucket) != want:                  # 轮询分配必定等长，防御性兜底
            bucket[:] = (bucket + [0.0] * want)[:want]
        bucket.sort()
    return buckets


def _plan_patient_batches(kind: str, params: Dict[str, Any], count: int,
                          offset: int = 0) -> Optional[List[List[str]]]:
    """诊断类任务：给每个任务分配 `batch` 位患者（按数据集轮换）。

    返回长度为 count 的列表，每项是该任务的患者 id 列表。
    `offset` 让同一阶段内多个诊断单元**接着往下轮换**，避免两台设备都从
    第一位患者开始、同一患者被反复推理。
    """
    if kind != "diagnosis":
        return None
    if not params.get("patient_cycle"):
        return None
    batch = max(1, int(params.get("batch") or 1))
    try:
        rows = db.list_patients(limit=500)
    except Exception:  # noqa: BLE001
        rows = []
    ids = [str(r["patient_id"]) for r in rows if r.get("patient_id")]
    if not ids:
        return None
    flat = [ids[(offset + i) % len(ids)] for i in range(count * batch)]
    return [flat[i * batch:(i + 1) * batch] for i in range(count)]


def _create_plan_unit_run(plan_run_id: str, phase: str, unit: Dict[str, Any],
                          offsets: List[float], cfg: Dict[str, Any],
                          concurrency: int, patient_offset: int = 0,
                          retries_override: Optional[int] = None) -> Dict[str, Any]:
    """为一个执行单元（设备 × 任务类）建一个子 run，任务按 offsets 产生。"""
    kind = str(unit["kind"])
    source = str(unit["source"])
    pod_urls = cfg.get("pod_urls") or {}
    if source not in pod_urls:
        raise RunError(f"unknown source '{source}'; known entities: {sorted(pod_urls)}")
    if kind not in KINDS:
        raise RunError(f"kind must be one of {list(KINDS)}")
    mode = "local" if phase == "local" else "collaborative"

    params = dict(unit.get("params") or {})
    repeats = int(unit.get("repeats") or 0)
    run_id = db.new_run_id()
    batches = _plan_patient_batches(kind, params, repeats, patient_offset)

    # 诊断类：每个任务带自己的患者 → 每 attempt 自己的参数
    items: List[Dict[str, Any]] = []
    if batches is not None:
        db_params = {k: v for k, v in params.items() if k != "patient_cycle"}
        db_params["task_patient_batches"] = batches
        for pids in batches:
            ip = ({"patient_id": pids[0]} if len(pids) == 1
                  else {"patient_ids": list(pids)})
            items.append({
                "kind": kind, "source": source, "mode_requested": mode,
                "params": ip, "spec_key": _spec_key(kind, ip), "bpcr_expected": None,
            })
    else:
        db_params = {k: v for k, v in params.items() if k != "patient_cycle"}
    # 方案声明的重传额度随子 run 一起下发，_requeue_for_retry 优先采用它
    if retries_override is not None:
        db_params["_retries"] = int(retries_override)

    db.create_run({"run_id": run_id, "kind": kind, "source": source, "mode": mode,
                   "repeats": repeats, "concurrency": concurrency,
                   "label": f"{phase} · {unit.get('unit_id')}",
                   "force_degraded": False, "params": db_params,
                   "plan_run_id": plan_run_id, "plan_phase": phase,
                   "plan_unit": unit.get("unit_id"), "status": "queued"})

    not_before = [_iso_after(s) for s in offsets]
    if items:
        db.create_attempt_plan(run_id, items, not_before=not_before)
    else:
        db.create_attempts(run_id, repeats, {
            "kind": kind, "source": source, "mode_requested": mode,
            "spec_key": _spec_key(kind, db_params), "params": db_params,
            "bpcr_expected": None,
        }, not_before=not_before)
    _spawn_workers(run_id, concurrency)
    return {"run_id": run_id, "unit_id": unit.get("unit_id"), "phase": phase,
            "kind": kind, "source": source, "device": unit.get("device_name"),
            "repeats": repeats, "concurrency": concurrency,
            "first_task_in_s": round(min(offsets), 3) if offsets else 0.0,
            "last_task_in_s": round(max(offsets), 3) if offsets else 0.0}


def _plan_phase_stats(plan_run_id: str, phase: str) -> Dict[str, Any]:
    """一个阶段的实测汇总：总用时、完成率、逐次时延统计。"""
    rows = db.attempts_for_runs(
        [r["run_id"] for r in db.runs_for_plan(plan_run_id)
         if r.get("plan_phase") == phase], limit=20000)
    done = [r for r in rows if r.get("status") not in ("pending", "running")]
    ok = [r for r in done if r.get("status") == "completed"]
    lat = sorted(float(r["client_total_ms"]) for r in done
                 if isinstance(r.get("client_total_ms"), (int, float)))
    starts = [r["created_at"] for r in rows if r.get("created_at")]
    finishes = [r["finished_at"] for r in done if r.get("finished_at")]
    span = None
    if starts and finishes:
        try:
            t0 = min(datetime.fromisoformat(s.replace("Z", "+00:00")) for s in starts)
            t1 = max(datetime.fromisoformat(s.replace("Z", "+00:00")) for s in finishes)
            span = round((t1 - t0).total_seconds() * 1000.0, 2)
        except ValueError:
            span = None

    def stat(vals: List[float]) -> Dict[str, Any]:
        if not vals:
            return {"n": 0, "mean": None, "p50": None, "p95": None,
                    "min": None, "max": None}
        return {"n": len(vals), "mean": round(sum(vals) / len(vals), 2),
                "p50": round(_percentile(vals, 50), 2),
                "p95": round(_percentile(vals, 95), 2),
                "min": round(vals[0], 2), "max": round(vals[-1], 2)}

    total = len(rows)
    # 任务保障记账：重传次数 + 失败尝试浪费的墙钟。
    # 判定的"任务处理总用时"用 exec + retry（含重传代价），不把失败时间藏起来。
    retries = sum(int(r.get("retry_count") or 0) for r in rows)
    retry_ms = sum(float(r.get("retry_ms_total") or 0.0) for r in rows
                   if isinstance(r.get("retry_ms_total"), (int, float)))
    retried = len([r for r in rows if int(r.get("retry_count") or 0) > 0])
    exec_ms = round(sum(lat), 2) if lat else 0.0
    return {
        "phase": phase,
        "tasks_total": total,
        "completed": len(ok),
        "failed": len([r for r in done if r.get("status") != "completed"]),
        "pending": len([r for r in rows if r.get("status") in ("pending", "running")]),
        "completion_pct": round(100.0 * len(ok) / total, 2) if total else None,
        "total_wall_ms": span,
        "latency": stat(lat),
        "retries": retries,
        "retried_tasks": retried,
        "retry_ms_total": round(retry_ms, 2),
        "exec_ms": exec_ms,
        "processing_ms": round(exec_ms + retry_ms, 2),
    }


def _plan_by_kind(plan_run_id: str) -> List[Dict[str, Any]]:
    child = db.runs_for_plan(plan_run_id)
    phase_of = {r["run_id"]: r.get("plan_phase") for r in child}
    rows = db.attempts_for_runs([r["run_id"] for r in child], limit=20000)
    buckets: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for r in rows:
        phase = phase_of.get(r.get("run_id"))
        key = (str(r.get("kind")), str(phase))
        b = buckets.setdefault(key, {"kind": r.get("kind"), "phase": phase,
                                     "lat": [], "ok": 0, "fail": 0, "total": 0})
        b["total"] += 1
        if r.get("status") == "completed":
            b["ok"] += 1
        elif r.get("status") not in ("pending", "running"):
            b["fail"] += 1
        if isinstance(r.get("client_total_ms"), (int, float)):
            b["lat"].append(float(r["client_total_ms"]))
    out = []
    for (kind, phase), b in buckets.items():
        lat = sorted(b.pop("lat"))
        b["unit_id"] = f"{kind}/{phase}"
        b["latency"] = ({"n": len(lat), "mean": round(sum(lat) / len(lat), 2),
                         "p50": round(_percentile(lat, 50), 2),
                         "p95": round(_percentile(lat, 95), 2)}
                        if lat else {"n": 0, "mean": None, "p50": None, "p95": None})
        out.append(b)
    order = {k: i for i, k in enumerate(KINDS)}
    out.sort(key=lambda x: (order.get(str(x["kind"]), 9), str(x["phase"])))
    return out


def _plan_coordinator(plan_run_id: str) -> None:
    """按策略分阶段顺序执行整个方案，并记录每阶段的实测汇总。"""
    try:
        plan_run = db.get_plan_run(plan_run_id)
        if plan_run is None:
            return
        snapshot = plan_run.get("snapshot") or {}
        strategies = plan_run.get("strategies") or ["local", "collaborative"]
        window_s = float(snapshot.get("generate_window_min") or 0) * 60.0
        units = plans_mod.expand_units(snapshot)
        cfg = config.current()
        concurrency = max(1, min(int(cfg.get("plan_unit_concurrency") or 1),
                                 MAX_CONCURRENCY))
        plan_retries = snapshot.get("retries")
        if plan_retries is None:
            plan_retries = cfg.get("attempt_retries")

        db.update_plan_run(plan_run_id, {"status": "running",
                                         "started_at": _now()})
        phases: List[Dict[str, Any]] = []
        for phase in strategies:
            # 每开一个新阶段前确认方案运行仍处于活动状态：否则"取消"之后
            # 协调器还会继续把后面几个阶段跑完（实测踩过）。
            current = db.get_plan_run(plan_run_id, parse=False)
            if current is None or current["status"] not in ("queued", "running"):
                _finalize_cancelled_plan(plan_run_id, strategy)
                return
            db.update_plan_run(plan_run_id, {"current_phase": phase})
            rng = random.Random(f"{plan_run_id}:{phase}")
            buckets = _deal_offsets(units, window_s, rng)
            child: List[Dict[str, Any]] = []
            patient_cursor = 0
            for unit, offsets in zip(units, buckets):
                child.append(_create_plan_unit_run(
                    plan_run_id, phase, unit, offsets, cfg, concurrency,
                    patient_offset=patient_cursor,
                    retries_override=plan_retries))
                if str(unit.get("kind")) == "diagnosis":
                    batch = max(1, int((unit.get("params") or {}).get("batch") or 1))
                    patient_cursor += int(unit.get("repeats") or 0) * batch
            # 等本阶段所有子 run 结束（方案要求"跑完本地、再切换到协同"）
            deadline = time.perf_counter() + max(
                600.0, float(snapshot.get("duration_min") or 1) * 60.0 * 3)
            while time.perf_counter() < deadline:
                live = 0
                for c in child:
                    run = db.get_run(c["run_id"], parse=False)
                    counts = db.count_attempts(c["run_id"])
                    if run and run["status"] in ("queued", "running") and (
                            counts.get("pending") or counts.get("running")
                            or run["status"] == "queued"):
                        live += 1
                if live == 0:
                    break
                time.sleep(_PLAN_POLL_S)
            stats = _plan_phase_stats(plan_run_id, phase)
            stats["units"] = child
            phases.append(stats)
            db.update_plan_run(plan_run_id, {"phases": phases})

        final = "completed"
        if all((p.get("completed") or 0) == 0 for p in phases):
            final = "failed"
        current = db.get_plan_run(plan_run_id, parse=False)
        if current is not None and current["status"] == "cancelled":
            db.update_plan_run(plan_run_id, {"phases": phases})
            return
        db.update_plan_run(plan_run_id, {"status": final, "current_phase": None,
                                         "phases": phases, "finished_at": _now()})
    except Exception as exc:  # noqa: BLE001 - a coordinator must not die silently
        db.update_plan_run(plan_run_id, {
            "status": "failed", "error": _trunc(f"{type(exc).__name__}: {exc}"),
            "finished_at": _now()})


def cancel_plan_run(plan_run_id: str) -> Optional[Dict[str, Any]]:
    """取消方案运行：停掉后续阶段，并取消尚未执行的任务。"""
    plan_run = db.get_plan_run(plan_run_id)
    if plan_run is None:
        return None
    if plan_run.get("status") in ("completed", "failed", "cancelled"):
        return plan_run_public(plan_run_id)
    _finalize_cancelled_plan(plan_run_id)
    return plan_run_public(plan_run_id)


def _finalize_cancelled_plan(plan_run_id: str,
                             strategy: Optional[str] = None) -> None:
    """把方案运行及其所有未完成子 run 标记为已取消。"""
    try:
        for run in db.runs_for_plan(plan_run_id):
            if run.get("status") in ("queued", "running"):
                db.cancel_pending_attempts(run["run_id"], "plan run cancelled")
                db.update_run(run["run_id"], {"status": "cancelled",
                                              "finished_at": _now()})
        fields: Dict[str, Any] = {"status": "cancelled", "current_phase": None,
                                  "finished_at": _now()}
        if strategy:
            fields["error"] = f"cancelled before phase '{strategy}'"
        db.update_plan_run(plan_run_id, fields)
    except Exception:  # noqa: BLE001
        pass


def create_plan_run(body: Dict[str, Any]) -> Dict[str, Any]:
    """POST /api/plans/runs：按测试方案下发一次运行（含自定义覆盖）。"""
    if not isinstance(body, dict):
        raise RunError("request body must be a JSON object")
    plan_id = str(body.get("plan_id") or "").strip()
    try:
        base = plans_mod.get_plan(plan_id)
    except KeyError as exc:
        raise RunError(str(exc))

    overrides = body.get("overrides") if isinstance(body.get("overrides"), dict) else {}
    # 允许把覆盖项直接放在顶层（前端表单更直观）
    for key in ("devices", "kinds", "generate_window_min", "duration_min",
                "strategies", "name"):
        if body.get(key) is not None and overrides.get(key) is None:
            overrides[key] = body[key]

    snapshot = plans_mod.apply_overrides(base, overrides)
    problems = plans_mod.validate_plan(snapshot)
    if problems:
        raise RunError("；".join(problems))

    # 标签只反映"用户给的名字"，不要因为用户写了名字就标成"自定义配置"；
    # 是否自定义由 overrides 是否真的改动了方案默认值决定。
    given = body.get("label")
    if given:
        label = str(given)[:160]
    elif snapshot.get("customized"):
        label = "自定义配置"
    else:
        label = base["name"]
    snapshot["label"] = label
    plan_run_id = db.new_plan_run_id()
    db.create_plan_run({"plan_run_id": plan_run_id, "plan_id": plan_id,
                        "plan_name": snapshot.get("name") or base["name"],
                        "snapshot": snapshot, "label": label,
                        "strategies": snapshot["strategies"],
                        "status": "queued"})
    with _PLAN_LOCK:
        thread = threading.Thread(target=_plan_coordinator,
                                  args=(plan_run_id,),
                                  name=f"bench-plan-{plan_run_id}", daemon=True)
        _PLAN_WORKERS[plan_run_id] = thread
        thread.start()
    return plan_run_public(plan_run_id) or {}


def plan_run_public(plan_run_id: str) -> Optional[Dict[str, Any]]:
    """方案运行的对外视图：配置快照 + 各阶段实测 + 逐类对比 + 判定。"""
    plan_run = db.get_plan_run(plan_run_id)
    if plan_run is None:
        return None
    snapshot = plan_run.get("snapshot") or {}
    child = db.runs_for_plan(plan_run_id)
    by_phase: Dict[str, List[Dict[str, Any]]] = {}
    for run in child:
        by_phase.setdefault(str(run.get("plan_phase")), []).append(run)

    progress: Dict[str, Any] = {}
    for phase, runs in by_phase.items():
        counts = {"total": 0, "pending": 0, "running": 0, "completed": 0, "failed": 0}
        for run in runs:
            c = db.count_attempts(run["run_id"])
            for k in counts:
                counts[k] += int(c.get(k) or 0)
        progress[phase] = counts

    live = plan_run.get("status") in ("queued", "running")
    result = {
        "plan_run_id": plan_run_id,
        "plan_id": plan_run.get("plan_id"),
        "plan_name": plan_run.get("plan_name"),
        "label": plan_run.get("label"),
        "status": plan_run.get("status"),
        "current_phase": plan_run.get("current_phase"),
        "error": plan_run.get("error"),
        "created_at": plan_run.get("created_at"),
        "started_at": plan_run.get("started_at"),
        "finished_at": plan_run.get("finished_at"),
        "strategies": plan_run.get("strategies") or [],
        "config": snapshot,
        "total_tasks_per_phase": plans_mod.plan_total_tasks(snapshot),
        "phases": plan_run.get("phases") or [],
        "progress": progress,
        "runs": [{"run_id": r["run_id"], "phase": r.get("plan_phase"),
                  "unit": r.get("plan_unit"), "kind": r.get("kind"),
                  "source": r.get("source"), "status": r.get("status"),
                  "repeats": r.get("repeats")} for r in child],
        "live": live,
        "by_kind": _plan_by_kind(plan_run_id),
        "comparison": _plan_comparison(plan_run_id, snapshot),
    }
    return result


def _plan_comparison(plan_run_id: str, snapshot: Dict[str, Any]) -> Dict[str, Any]:
    """逐类 + 总体：本地（未调度） vs 云边端协同。"""
    per_kind: List[Dict[str, Any]] = []
    kinds = sorted({str(r.get("kind")) for r in db.runs_for_plan(plan_run_id)})
    order = {k: i for i, k in enumerate(KINDS)}
    kinds.sort(key=lambda k: order.get(k, 9))
    for kind in kinds:
        entry: Dict[str, Any] = {"kind": kind, "modes": {}}
        for phase in ("local", "collaborative"):
            rows = db.attempts_for_runs(
                [r["run_id"] for r in db.runs_for_plan(plan_run_id)
                 if r.get("plan_phase") == phase and r.get("kind") == kind],
                limit=20000)
            lat = sorted(float(r["client_total_ms"]) for r in rows
                         if isinstance(r.get("client_total_ms"), (int, float)))
            r_ms = sum(float(r.get("retry_ms_total") or 0.0) for r in rows
                       if isinstance(r.get("retry_ms_total"), (int, float)))
            entry["modes"][phase] = {
                "n": len(rows),
                "completed": len([r for r in rows if r.get("status") == "completed"]),
                "total_client_ms": round(sum(lat), 2) if lat else None,
                "mean": round(sum(lat) / len(lat), 2) if lat else None,
                "p50": round(_percentile(lat, 50), 2) if lat else None,
                "p95": round(_percentile(lat, 95), 2) if lat else None,
                "min": round(lat[0], 2) if lat else None,
                "max": round(lat[-1], 2) if lat else None,
                "retries": sum(int(r.get("retry_count") or 0) for r in rows),
                "retried_tasks": len([r for r in rows
                                      if int(r.get("retry_count") or 0) > 0]),
                "retry_ms_total": round(r_ms, 2),
                # 含重传代价的任务处理总用时（判定口径）
                "processing_ms": round((sum(lat) if lat else 0.0) + r_ms, 2),
            }
        c, l = entry["modes"]["collaborative"], entry["modes"]["local"]
        entry["gain_pct"] = (round(100.0 * (l["total_client_ms"] - c["total_client_ms"])
                                   / l["total_client_ms"], 2)
                             if l.get("total_client_ms") and c.get("total_client_ms")
                             else None)
        per_kind.append(entry)

    totals = {ph: _plan_phase_stats(plan_run_id, ph) for ph in ("local", "collaborative")}
    l_ms = (totals["local"] or {}).get("total_wall_ms")
    c_ms = (totals["collaborative"] or {}).get("total_wall_ms")
    exec_l = sum((e["modes"]["local"].get("total_client_ms") or 0) for e in per_kind)
    exec_c = sum((e["modes"]["collaborative"].get("total_client_ms") or 0) for e in per_kind)
    proc_l = sum((e["modes"]["local"].get("processing_ms") or 0) for e in per_kind)
    proc_c = sum((e["modes"]["collaborative"].get("processing_ms") or 0) for e in per_kind)
    return {
        "per_kind": per_kind,
        "totals": totals,
        "overall": {
            "local_wall_ms": l_ms,
            "collaborative_wall_ms": c_ms,
            "wall_gain_pct": (round(100.0 * (l_ms - c_ms) / l_ms, 2)
                              if l_ms and c_ms else None),
            # exec = 只算成功那次执行；processing = exec + 重传浪费（判定用）
            "local_exec_ms": round(exec_l, 2) if exec_l else None,
            "collaborative_exec_ms": round(exec_c, 2) if exec_c else None,
            "local_processing_ms": round(proc_l, 2) if proc_l else None,
            "collaborative_processing_ms": round(proc_c, 2) if proc_c else None,
            "processing_gain_pct": (round(100.0 * (proc_l - proc_c) / proc_l, 2)
                                    if proc_l and proc_c else None),
            "retries_local": (totals.get("local") or {}).get("retries"),
            "retries_collaborative": (totals.get("collaborative") or {}).get("retries"),
        },
        "criteria": _plan_criteria(plan_run_id, snapshot, per_kind,
                                   {"local": l_ms, "collaborative": c_ms}),
    }


def _plan_criteria(plan_run_id: str, snapshot: Dict[str, Any],
                   per_kind: List[Dict[str, Any]],
                   wall: Dict[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    l_ms, c_ms = wall.get("local"), wall.get("collaborative")
    sum_l = sum((e["modes"]["local"].get("processing_ms") or 0) for e in per_kind)
    sum_c = sum((e["modes"]["collaborative"].get("processing_ms") or 0) for e in per_kind)
    # 完成率以"最差的那个阶段"为准：两遍都必须达到要求。
    # 只要还有任务没跑完，就不下结论（否则运行中会显示"不通过"，误导判读）。
    stats_ph = {ph: (_plan_phase_stats(plan_run_id, ph) or {})
                for ph in ("local", "collaborative")}
    rates = [v.get("completion_pct") for v in stats_ph.values()
             if v.get("completion_pct") is not None]
    unfinished = any((v.get("pending") or 0) > 0 for v in stats_ph.values())
    done = {"completion_pct": None if unfinished else (min(rates) if rates else None)}
    for spec in snapshot.get("criteria") or []:
        item = dict(spec)
        ctype = spec.get("type")
        if ctype == "external":
            item.update({"verdict": "pending", "measured": None,
                         "note": f"由 {spec.get('source')} 实测填写，平台不自动判定"})
        elif ctype in ("complete_gt", "complete_gte"):
            val = done.get("completion_pct")
            target = float(spec.get("value") or 0)
            # val 可能为 None（任务还没跑完就查结果）——必须先判空再比较，
            # 否则 `None >= 100.0` 会直接抛 TypeError，连下发都失败。
            if val is None:
                verdict = "pending"
            else:
                ok = (val > target) if ctype == "complete_gt" else (val >= target)
                verdict = "pass" if ok else "fail"
            item.update({"verdict": verdict,
                         "measured": val,
                         "measured_retries": sum(
                             int((_plan_phase_stats(plan_run_id, ph) or {})
                                 .get("retries") or 0)
                             for ph in ("local", "collaborative"))})
        elif ctype == "gain_gt":
            # 大纲的「任务处理总用时」= 各任务耗时之和（Σ client_total_ms）。
            # 阶段墙钟（makespan）会包含固定的"任务产生窗口"，那部分两遍相同，
            # 计进去只会稀释策略差异，所以判定用处理总用时，墙钟另列参考。
            gain = (round(100.0 * (sum_l - sum_c) / sum_l, 2)
                    if sum_l and sum_c else None)   # 含重传代价的处理总用时
            wall = (round(100.0 * (l_ms - c_ms) / l_ms, 2) if l_ms and c_ms else None)
            item.update({"verdict": ("pending" if gain is None
                                     else ("pass" if gain > float(spec.get("value") or 0)
                                           else "fail")),
                         "measured": gain,
                         "measured_wall_gain_pct": wall,
                         "note": "判定口径 = 任务处理总用时（Σ 单任务 client_total_ms）；"
                                 "阶段墙钟含固定产生窗口，仅作参考"})
        else:
            item.update({"verdict": "pending", "measured": None})
        out.append(item)
    return out


def _label_for(suite: Dict[str, Any], params: Dict[str, Any]) -> str:
    for sp in suite["specs"]:
        if all(params.get(k) == v for k, v in sp["params"].items()):
            return sp["label"]
    return "?"


def suite_compare(suite_id: str, source: Optional[str] = None,
                  run_ids: Optional[List[str]] = None) -> Dict[str, Any]:
    """Compare the same fixed suite across execution modes.

    For every config of the suite: n / success / latency stats per mode, plus
    the per-mode overall aggregate and the collaborative-vs-local speedup.
    """
    suite = suites_mod.get_suite(suite_id)  # KeyError -> 404 upstream

    # only attempts that belong to runs created from this suite, so unrelated
    # runs with identical params cannot leak into the comparison
    # 默认聚合该套件的全部历史运行；给了 run_ids 就只统计这些运行
    # （报告需要「本次采集」的干净数据，否则会把旧构建的历史结果混进来）
    scope = [r for r in (run_ids or []) if r] or db.run_ids_for_suite(suite_id)
    rows = [r for r in db.attempts_for_runs(scope)
            if (r.get("params") or {})
            and (not source or r.get("source") == source)]

    def stats(values: List[float]) -> Dict[str, Any]:
        clean = sorted(float(v) for v in values if isinstance(v, (int, float)))
        if not clean:
            return {"n": 0, "mean": None, "p50": None, "p95": None,
                    "min": None, "max": None}
        return {"n": len(clean),
                "mean": round(sum(clean) / len(clean), 2),
                "p50": round(_percentile(clean, 50), 2),
                "p95": round(_percentile(clean, 95), 2),
                "min": round(clean[0], 2),
                "max": round(clean[-1], 2)}

    by_mode: Dict[str, Dict[str, Any]] = {}
    configs: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        mode = str(r.get("mode_used") or r.get("mode_requested") or "?")
        params = r.get("params") or {}
        key = _spec_key(suite["kind"], params)
        lat = r.get("client_total_ms")
        finished = str(r.get("status"))
        for target in (by_mode.setdefault(mode, {"lat": [], "ok": 0, "fail": 0}),
                       configs.setdefault(key, {
                           "spec_key": key, "label": _label_for(suite, params),
                           "params": params, "modes": {},
                       })["modes"].setdefault(mode, {"lat": [], "ok": 0, "fail": 0})):
            if lat is not None:
                target["lat"].append(float(lat))
            if finished == "completed":
                target["ok"] += 1
            elif finished in ("failed", "cancelled", "timeout"):
                target["fail"] += 1

    overall = {}
    for mode, bucket in by_mode.items():
        s = stats(bucket["lat"])
        s["success"] = bucket["ok"]
        s["fail"] = bucket["fail"]
        overall[mode] = s

    # attribution: what the two strategies actually did differently (parallelism,
    # orchestration wall clock, compute vs transfer) - averaged per mode
    def _mean(vals: List[float]):
        clean = [float(v) for v in vals if isinstance(v, (int, float))]
        return round(sum(clean) / len(clean), 3) if clean else None

    attribution: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        mode = str(r.get("mode_used") or r.get("mode_requested") or "?")
        m = r.get("metrics") or {}
        rd = r.get("result_detail") or {}
        bucket = attribution.setdefault(mode, {
            "parallel_speedup": [], "dispatch_wall_ms": [],
            "compute_ms_total": [], "network_ms_total": [],
            "pipeline_speedup": [], "streams": []})
        ps = rd.get("parallel_speedup", m.get("parallel_speedup"))
        if isinstance(ps, (int, float)):
            bucket["parallel_speedup"].append(ps)
        pw = rd.get("pipeline_speedup", m.get("pipeline_speedup"))
        if isinstance(pw, (int, float)):
            bucket["pipeline_speedup"].append(pw)
        for key in ("dispatch_wall_ms", "compute_ms_total", "network_ms_total"):
            val = m.get(key, rd.get(key))
            if isinstance(val, (int, float)):
                bucket[key].append(val)
        if isinstance(rd.get("streams"), (int, float)):
            bucket["streams"].append(rd["streams"])
        if isinstance(rd.get("batch_size"), (int, float)):
            bucket.setdefault("batch_size", []).append(rd["batch_size"])

    extra: Dict[str, Any] = {}
    for mode, bucket in attribution.items():
        for key, vals in bucket.items():
            extra[f"{mode}_{key}_mean"] = _mean(vals)

    order = [sp["params"] for sp in suite["specs"]]
    config_rows = []
    for cfg in configs.values():
        entry = {"spec_key": cfg["spec_key"], "label": cfg["label"],
                 "params": cfg["params"], "modes": {}}
        for mode, cm in cfg["modes"].items():
            s = stats(cm["lat"])
            s["success"] = cm["ok"]
            s["fail"] = cm["fail"]
            entry["modes"][mode] = s
        config_rows.append(entry)
    config_rows.sort(key=lambda e: _level_index(order, e["params"]))

    speedup = None
    c, l = overall.get("collaborative"), overall.get("local")
    if c and l and c.get("mean") and l.get("mean"):
        speedup = {"collaborative_mean_ms": c["mean"], "local_mean_ms": l["mean"],
                   "collaborative_faster_pct": round(
                       (l["mean"] - c["mean"]) / l["mean"] * 100, 2),
                   "ratio": round(l["mean"] / c["mean"], 3)}
    return {"suite_id": suite_id, "suite_name": suite["name"],
            "kind": suite["kind"], "source": source,
            "run_ids": scope, "scope": "runs" if run_ids else "all-runs",
            "description": suite.get("description"),
            "overall": overall, "configs": config_rows, "speedup": speedup,
            "extra": extra}


def _level_index(level_params: List[Dict[str, Any]], params: Dict[str, Any]) -> int:
    for i, lv in enumerate(level_params):
        if all(params.get(k) == v for k, v in lv.items()):
            return i
    return len(level_params)


def _run_one(run_id: str) -> None:
    """Worker body: claim attempts until none are left, then finalise the run.

    Test-plan runs (§5.4/§5.5) spread their tasks over a generation window, so a
    worker that finds no *due* attempt must keep waiting while pending attempts
    remain in the future instead of draining the run early.
    """
    stall = 0
    while True:
        try:
            attempt = db.claim_next_attempt(run_id)
        except Exception:  # noqa: BLE001 - never let a worker die
            time.sleep(0.2)
            attempt = None
        if attempt is None:
            # 领取不到任务分两种情况：① 还没到"产生"时刻（方案的时间窗）→ 等；
            # ② 到期了却领不到（例如 SQLite 短暂锁冲突）→ 也**不能**直接收尾，
            #    否则那条 pending 会被永久遗弃、run 卡在 running（实测踩过）。
            try:
                counts = db.count_attempts(run_id)
                waiting = db.count_waiting_attempts(run_id)
            except Exception:  # noqa: BLE001
                counts, waiting = {"pending": 0}, 0
            run = db.get_run(run_id, parse=False)
            if run is None:
                return
            if run["status"] not in ("queued", "running"):
                break                       # 被取消或已结束
            if counts.get("pending", 0) <= 0:
                break                       # 真的没有待执行了 → 收尾
            if waiting > 0:
                stall = 0                   # 有任务还没产生，耐心等
            else:
                stall += 1
                if stall > 20:              # 到期却始终领不到：如实报错而不是挂着
                    _fail_orphan_attempts(
                        run_id, "worker could not claim a due pending attempt")
                    break
            time.sleep(0.35)
            continue
        stall = 0
        run = db.get_run(run_id)
        if run is None:
            return
        if run["status"] == "queued":
            db.update_run(run_id, {"status": "running", "started_at": _now()})
            run["status"] = "running"
        cfg = config.current()
        try:
            fields = _execute_attempt(attempt, run, cfg)
        except Exception as exc:  # noqa: BLE001 - one bad attempt must not kill the run
            fields = {"status": "failed",
                      "error": _trunc(f"{type(exc).__name__}: {exc}"),
                      "finished_at": _now()}
        # ---------------------------------------------------------------- 失败重传
        # 任务保障：只要本次没成功、还有重传额度、且 run 没被取消，就把这条任务
        # 放回 pending 重传，而不是直接判失败。失败尝试的耗时单独记账，
        # 不被悄悄抹掉（否则重传就成了"把失败时间藏起来"）。
        if (str(fields.get("status")) != "completed"
                and _requeue_for_retry(int(attempt["id"]), attempt, fields, cfg, run_id)):
            continue
        try:
            db.finish_attempt(int(attempt["id"]), fields)
        except Exception:  # noqa: BLE001
            pass
    _finalize_run(run_id)


def _requeue_for_retry(attempt_id: int, attempt: Dict[str, Any],
                       fields: Dict[str, Any], cfg: Dict[str, Any],
                       run_id: str) -> bool:
    """失败任务是否要重传；要则放回 pending 并返回 True。"""
    try:
        run = db.get_run(run_id)            # 解析 params：方案可声明自己的额度
        if run is None or run["status"] not in ("queued", "running"):
            return False                    # 已取消：不再重传
        run_limit = (run.get("params") or {}).get("_retries")
        limit = max(0, int(run_limit if run_limit is not None
                           else (cfg.get("attempt_retries") or 0)))
        done = int(attempt.get("retry_count") or 0)
        if limit <= 0 or done >= limit:
            return False
        try:
            wasted = float(attempt.get("retry_ms_total") or 0.0)
        except (TypeError, ValueError):
            wasted = 0.0
        try:
            spent = float(fields.get("client_total_ms") or 0.0)
        except (TypeError, ValueError):
            spent = 0.0
        history = attempt.get("retry_errors") or []
        if not isinstance(history, list):
            history = []
        history = history + [{"retry": done + 1, "ms": round(spent, 2),
                              "error": _trunc(str(fields.get("error") or "failed"))}]
        backoff = max(0, int(cfg.get("retry_backoff_ms") or 0)) / 1000.0
        db.requeue_attempt(attempt_id, done + 1, wasted + spent,
                           json.dumps(history, ensure_ascii=False),
                           not_before=_iso_after(backoff) if backoff else None)
        return True
    except Exception:  # noqa: BLE001 - 重传失败就退回"按失败处理"
        return False


def _fail_orphan_attempts(run_id: str, reason: str) -> int:
    """把「已到期但始终领不到」的 pending attempt 标记为失败。

    宁可让 run 以 failed 收尾并把原因写清楚，也不要留着一条永不执行的
    pending 让整个方案运行卡在 running。
    """
    try:
        with db.connect() as con:
            rows = con.execute(
                """SELECT id FROM attempts WHERE run_id=? AND status='pending'""",
                (run_id,)).fetchall()
            for row in rows:
                con.execute(
                    """UPDATE attempts SET status='failed', error=?,
                              finished_at=?, updated_at=?
                       WHERE id=?""",
                    (_trunc(reason), _now(), _now(), row["id"]))
        return len(rows)
    except Exception:  # noqa: BLE001
        return 0


def _finalize_run(run_id: str) -> Dict[str, int]:
    counts = db.count_attempts(run_id)
    if counts["pending"] or counts["running"]:
        return counts
    run = db.get_run(run_id)
    if run is None or run["status"] in ("completed", "failed", "cancelled"):
        return counts
    if counts["cancelled"]:
        status, error = "cancelled", run.get("error")
    elif counts["completed"] == 0 and counts["total"]:
        status, error = "failed", run.get("error") or "all attempts failed"
    else:
        status, error = "completed", run.get("error")
    fields: Dict[str, Any] = {"status": status, "finished_at": _now()}
    if error:
        fields["error"] = error
    db.update_run(run_id, fields)
    with _RUN_INPUTS_LOCK:
        _RUN_INPUTS.pop(run_id, None)
    return counts


_WORKERS_LOCK = threading.Lock()
_WORKERS: Dict[str, int] = {}


def _spawn_workers(run_id: str, concurrency: int) -> None:
    def runner() -> None:
        try:
            _run_one(run_id)
        finally:
            with _WORKERS_LOCK:
                _WORKERS[run_id] = max(0, _WORKERS.get(run_id, 1) - 1)

    for _ in range(max(1, int(concurrency))):
        with _WORKERS_LOCK:
            _WORKERS[run_id] = _WORKERS.get(run_id, 0) + 1
        threading.Thread(target=runner, name=f"bench-run-{run_id}", daemon=True).start()


# --------------------------------------------------------------------------- #
# run lifecycle API (used by app.py)                                          #
# --------------------------------------------------------------------------- #
def run_progress(run_id: str) -> Dict[str, int]:
    return db.count_attempts(run_id)


def get_run_detail(run_id: str, attempt_limit: int = 2000) -> Optional[Dict[str, Any]]:
    run = db.get_run(run_id)
    if run is None:
        return None
    attempts = db.get_attempts(run_id)[: max(1, attempt_limit)]
    return {"run": run, "attempts": attempts, "progress": run_progress(run_id)}


def cancel_run(run_id: str) -> Optional[Dict[str, Any]]:
    run = db.get_run(run_id)
    if run is None:
        return None
    cancelled = db.cancel_pending_attempts(run_id)
    # the run itself only flips to 'cancelled' once nothing is in flight any more
    # (_finalize_run below): a running attempt is never interrupted mid-HTTP.
    db.update_run(run_id, {"cancelled_at": _now()})
    counts = _finalize_run(run_id)
    return {"run_id": run_id, "cancelled_attempts": cancelled,
            "progress": counts, "status": (db.get_run(run_id) or {}).get("status")}


def delete_run(run_id: str) -> Optional[Dict[str, Any]]:
    run = db.get_run(run_id)
    if run is None:
        return None
    db.cancel_pending_attempts(run_id, reason="run deleted")
    db.update_run(run_id, {"status": "cancelled", "cancelled_at": _now()})
    with _WORKERS_LOCK:
        workers = _WORKERS.get(run_id, 0)
        _WORKERS.pop(run_id, None)
    with _RUN_INPUTS_LOCK:
        _RUN_INPUTS.pop(run_id, None)
    deleted = db.delete_run(run_id)
    return {"run_id": run_id, "deleted": bool(deleted), "workers_still_running": workers}


# --------------------------------------------------------------------------- #
# health                                                                      #
# --------------------------------------------------------------------------- #
def _probe_pod(entity: str, url: str, cfg: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {"entity": entity, "url": url, "reachable": False,
                           "local_mode_available": False, "latency_ms": None,
                           "local_health": None, "error": None}
    timeout = _timeouts(cfg, "probe_timeout_ms")
    t0 = time.perf_counter()
    try:
        r = _SESSION.get(url.rstrip("/") + "/local/health", timeout=timeout)
        out["latency_ms"] = _ms(t0)
        if r.status_code == 404:
            out["reachable"] = True
            out["error"] = "local endpoint unavailable (404)"
            out["local_health"] = None
        elif r.status_code >= 400:
            out["reachable"] = True
            out["error"] = f"local/health HTTP {r.status_code}"
        else:
            out["reachable"] = True
            out["local_mode_available"] = True
            body = r.json()
            out["local_health"] = body
            if isinstance(body, dict):
                out["capabilities"] = body.get("capabilities")
                out["entity_reported"] = body.get("entity")
    except Exception as exc:  # noqa: BLE001
        out["latency_ms"] = _ms(t0)
        out["error"] = _trunc(f"{type(exc).__name__}: {exc}", 200)
        # distinguish "pod down" from "pod up, /local/* missing"
        try:
            r = _SESSION.get(url.rstrip("/") + "/health", timeout=timeout)
            out["reachable"] = r.status_code < 500
            if out["reachable"]:
                out["error"] = ("pod up but /local/health unavailable "
                                f"(HTTP {r.status_code})")
        except Exception as exc2:  # noqa: BLE001
            out["error"] = _trunc(f"{type(exc2).__name__}: {exc2}", 200)
    return out


def health(probe_pods: bool = True) -> Dict[str, Any]:
    cfg = config.current()
    probe_ms, reachable, error, code = _probe_scheduler(cfg)
    scheduler = {"url": cfg.get("scheduler_url"), "reachable": reachable,
                 "status_code": code, "latency_ms": probe_ms, "error": error}
    if reachable:
        try:
            r = _SESSION.get(str(cfg["scheduler_url"]).rstrip("/") + "/",
                             timeout=_timeouts(cfg, "probe_timeout_ms"))
            scheduler["info"] = r.json()
        except Exception:  # noqa: BLE001
            scheduler["info"] = None

    pods: Dict[str, Any] = {}
    if probe_pods:
        urls = cfg.get("pod_urls") or {}
        with ThreadPoolExecutor(max_workers=min(8, max(1, len(urls)))) as pool:
            futures = {ent: pool.submit(_probe_pod, ent, url, cfg)
                       for ent, url in urls.items()}
            for ent, fut in futures.items():
                try:
                    pods[ent] = fut.result(timeout=20)
                except Exception as exc:  # noqa: BLE001
                    pods[ent] = {"entity": ent, "url": urls.get(ent),
                                 "reachable": False,
                                 "error": _trunc(f"{type(exc).__name__}: {exc}")}

    info = patients_info()
    return {
        "site": {"service": SITE_SERVICE, "version": SITE_VERSION, "status": "ok",
                 "time": _now(), "db_path": db.db_path(),
                 "patients": {"path": info.get("path"), "source": info.get("source"),
                              "count": info.get("count"),
                              "origins": info.get("origins"),
                              "error": info.get("error")}},
        "scheduler": scheduler,
        "pods": pods,
        "config": {"force_degraded": cfg.get("force_degraded"),
                   "auto_degrade": cfg.get("auto_degrade"),
                   "probe_timeout_ms": cfg.get("probe_timeout_ms"),
                   "concurrency": cfg.get("concurrency"),
                   "default_repeats": cfg.get("default_repeats")},
    }
