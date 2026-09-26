"""v3.2 shared local-execution runtime for edge pods (hospital / clinic).

"Local execution" (本地执行) is **not** "run it on one machine": it means the
*initiating pod itself takes over the scheduler's orchestration role* and runs
the pipeline on itself, falling back to the shortest possible hop only when it
lacks the capability:

  * compute   - the pod runs every partition itself (no peer, no data center)
  * sync      - the pod drives the same P2P pull -> cloud upload -> backup flow
  * routine   - the pod runs the jobs inline (no short-lived Kubernetes Jobs)
  * diagnosis - **no local execution strategy**: the server half (layer4 +
                fusion + classifier) only runs in the data center, so neither a
                hospital (front half only) nor a clinic can complete a diagnosis
                on its own - both must go through the collaborative path

This module gives both edge images the shared pieces:
  * http_json      - stdlib-only HTTP (the images have no `requests` dependency
                     and the build is offline, so no new pip packages allowed)
  * ResultStore    - durable per-task result envelopes (hostPath JSON + memory)
  * LocalQueue     - bounded background worker queue: long local work must never
                     block the request thread or the health probes
  * run_compute / run_sync / run_routine / forward_diagnosis (总是拒绝诊断)
  * envelope helpers so *every* mode (collaborative / local / degraded) reports
    the same result schema and the same decomposed metrics

Nothing here imports torch or numpy: the hospital image keeps only the front
half of the model. `dispatch` still accepts an optional `diagnosis_fn` so
offline tests can inject a stub, but no production path supplies one.
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

# --------------------------------------------------------------------------
# identity / topology
# --------------------------------------------------------------------------
NODE1, NODE2 = "node1", "node2"

# entity -> pod base URL. Env overridable for local single-machine runs
# (start_all.sh points everything at localhost).
ENTITY_URLS: Dict[str, str] = {
    "hospital-a": os.environ.get("HOSPITAL_A_URL", "http://hospital-a-service:8006"),
    "hospital-b": os.environ.get("HOSPITAL_B_URL", "http://hospital-b-service:8006"),
    "clinic-1": os.environ.get("CLINIC_1_URL", "http://clinic-1-service:8007"),
    "clinic-2": os.environ.get("CLINIC_2_URL", "http://clinic-2-service:8007"),
    "clinic-3": os.environ.get("CLINIC_3_URL", "http://clinic-3-service:8007"),
    "clinic-4": os.environ.get("CLINIC_4_URL", "http://clinic-4-service:8007"),
}
HOSPITALS = ("hospital-a", "hospital-b")
# node placement of each entity (mirrors the k8s nodeSelectors + cluster editor)
ENTITY_NODES: Dict[str, str] = {
    "hospital-a": NODE1, "hospital-b": NODE2,
    "clinic-1": NODE1, "clinic-2": NODE2, "clinic-3": NODE1, "clinic-4": NODE2,
}
DC_URL = os.environ.get("DC_SERVICES_URL", "http://dc-services:8010")
SCHEDULER_URL = os.environ.get("SCHEDULER_URL", "http://scheduler-service:8000").rstrip("/")

SCHEDULER_PROBE_TIMEOUT = float(os.environ.get("SCHEDULER_PROBE_TIMEOUT", "1.5"))
DEFAULT_TIMEOUT = float(os.environ.get("LOCAL_HTTP_TIMEOUT", "600"))


def entity_name() -> str:
    """This pod's entity name (hospital-a ... clinic-4)."""
    return (os.environ.get("ENTITY_NAME")
            or os.environ.get("HOSPITAL_NAME")
            or os.environ.get("CLINIC_NAME") or "unknown")


def entity_kind() -> str:
    explicit = os.environ.get("ENTITY_KIND")
    if explicit:
        return explicit
    return "hospital" if os.environ.get("HOSPITAL_NAME") else "clinic"


def node_name() -> str:
    return os.environ.get("NODE_NAME", "unknown")


def pod_name() -> str:
    return os.environ.get("POD_NAME", "") or entity_name()


def capabilities(kind: Optional[str] = None) -> Dict[str, str]:
    """Self-reported capability per task type.

    hospital: only the DoubleTower front half lives in the image; the server
              half (layer4 + fusion + classifier) runs in the data center, so
              diagnosis has no local execution strategy.
    clinic:   no PyTorch / no weights -> diagnosis is likewise unsupported
              (a clinic forwarded to a hospital still cannot finish it).
    """
    kind = kind or entity_kind()
    if kind == "hospital":
        return {"diagnosis": "unsupported", "compute": "local",
                "sync": "local", "routine": "local"}
    return {"diagnosis": "unsupported", "compute": "local",
            "sync": "local", "routine": "local"}


def nearest_hospital(node: Optional[str] = None) -> str:
    """Shortest-hop hospital for a clinic on `node`."""
    node = node or node_name()
    if node == NODE2:
        return "hospital-b"
    if node == NODE1:
        return "hospital-a"
    return "hospital-a"


# --------------------------------------------------------------------------
# stdlib HTTP
# --------------------------------------------------------------------------
def http_json(url: str, payload: Any = None, method: str = "GET",
              timeout: float = DEFAULT_TIMEOUT) -> Any:
    """JSON request that never goes through a corporate proxy.

    A payload implies POST unless the caller says otherwise (urllib would
    otherwise send a bodied GET, which every FastAPI route rejects with 405).
    """
    if payload is not None and method == "GET":
        method = "POST"
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=timeout) as r:
        body = r.read().decode()
    return json.loads(body) if body else {}


def probe_scheduler(force: bool = False) -> dict:
    """Cached (2s) scheduler reachability probe used by /local/health."""
    global _probe_cache
    now = time.time()
    with _probe_lock:
        if not force and _probe_cache and now - _probe_cache[0] < 2.0:
            return dict(_probe_cache[1])
    t0 = time.perf_counter()
    result: Dict[str, Any] = {"reachable": False, "url": SCHEDULER_URL,
                              "checked_ms": 0.0, "error": None}
    try:
        http_json(SCHEDULER_URL + "/", timeout=SCHEDULER_PROBE_TIMEOUT)
        result["reachable"] = True
    except Exception as e:  # noqa: BLE001
        result["error"] = f"{type(e).__name__}: {str(e)[:120]}"
    result["checked_ms"] = round((time.perf_counter() - t0) * 1000, 2)
    with _probe_lock:
        _probe_cache = (now, dict(result))
    return result


_probe_cache = None  # type: Optional[tuple]
_probe_lock = threading.Lock()


# --------------------------------------------------------------------------
# result schema
# --------------------------------------------------------------------------
def now_iso() -> str:
    return datetime.utcnow().isoformat()


def stage(name: str, actor: str, node: str, ms: float, detail: str = "",
          compute_ms: Optional[float] = None,
          network_ms: Optional[float] = None) -> dict:
    out = {"name": name, "actor": actor, "node": node or actor,
           "ms": round(float(ms), 2), "detail": detail}
    if compute_ms is not None:
        out["compute_ms"] = round(float(compute_ms), 2)
    if network_ms is not None:
        out["network_ms"] = round(float(network_ms), 2)
    return out


def envelope(task_id: str, kind: str, *, mode: str, mode_requested: str = "",
             degraded: bool = False, degrade_reason: Optional[str] = None,
             orchestrator: str = "pod", executor: Optional[str] = None,
             initiator: Optional[dict] = None, stages: Optional[List[dict]] = None,
             result_detail: Optional[dict] = None,
             metrics: Optional[dict] = None, status: str = "completed",
             error: Optional[str] = None, stage_name: Optional[str] = None,
             created_at: Optional[str] = None) -> dict:
    """One result shape for collaborative / local / degraded alike (design §3)."""
    ent = executor or entity_name()
    m = dict(metrics or {})
    m.setdefault("queue_wait_ms", 0.0)
    m.setdefault("pipeline_total_ms", 0.0)
    m.setdefault("compute_ms_total", 0.0)
    m.setdefault("network_ms_total", 0.0)
    m.setdefault("degrade_switch_ms", 0.0)
    m.setdefault("e2e_total_ms", m.get("pipeline_total_ms", 0.0))
    m.setdefault("client_total_ms", m.get("e2e_total_ms", 0.0))
    for k in ("queue_wait_ms", "pipeline_total_ms", "e2e_total_ms",
              "client_total_ms", "compute_ms_total", "network_ms_total",
              "degrade_switch_ms"):
        try:
            m[k] = round(float(m[k]), 2)
        except Exception:  # noqa: BLE001
            m[k] = 0.0
    env = {
        "task_id": task_id,
        "kind": kind,
        "mode": mode,
        "mode_requested": mode_requested or mode,
        "degraded": bool(degraded),
        "degrade_reason": degrade_reason,
        "orchestrator": orchestrator,
        "executor": ent,
        "initiator": initiator or {"entity": ent, "role": "pod",
                                   "node": node_name()},
        "stages": stages or [],
        "result_detail": result_detail or {},
        "metrics": m,
        "status": status,
        "error": error,
        "created_at": created_at or now_iso(),
        "finished_at": now_iso(),
        "stage": stage_name or (stages[-1]["name"] if stages else kind),
    }
    return env


# --------------------------------------------------------------------------
# durable result store
# --------------------------------------------------------------------------
class ResultStore:
    """Per-entity JSON result store on a hostPath volume (survives restarts)."""

    def __init__(self, entity: Optional[str] = None,
                 root: Optional[str] = None, max_memory: int = 500,
                 preload: int = 100):
        ent = entity or entity_name()
        self.root = Path(root or os.environ.get("LOCAL_RESULTS_DIR",
                                                "/data/local-results")) / ent
        self.max_memory = max_memory
        self._mem: Dict[str, dict] = {}
        self._lock = threading.Lock()
        self.writable = False
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            probe = self.root / ".write-test"
            probe.write_text("ok")
            probe.unlink()
            self.writable = True
        except Exception:  # noqa: BLE001
            self.writable = False
        self.preload(preload)

    def preload(self, limit: int = 100) -> int:
        if not self.writable:
            return 0
        try:
            files = sorted(self.root.glob("*.json"),
                           key=lambda p: p.stat().st_mtime, reverse=True)[:limit]
        except Exception:  # noqa: BLE001
            return 0
        n = 0
        for f in files:
            try:
                env = json.loads(f.read_text())
                self._mem[env.get("task_id") or f.stem] = env
                n += 1
            except Exception:  # noqa: BLE001
                continue
        return n

    def put(self, task_id: str, env: dict, status: str = "completed") -> None:
        env.setdefault("task_id", task_id)
        env.setdefault("status", status)
        with self._lock:
            self._mem[task_id] = env
            if len(self._mem) > self.max_memory:
                for k in list(self._mem)[:len(self._mem) - self.max_memory]:
                    self._mem.pop(k, None)
        if self.writable:
            try:
                tmp = self.root / f".{task_id}.tmp"
                tmp.write_text(json.dumps(env, ensure_ascii=False))
                os.replace(tmp, self.root / f"{task_id}.json")
            except Exception:  # noqa: BLE001
                pass

    def get(self, task_id: str) -> Optional[dict]:
        with self._lock:
            env = self._mem.get(task_id)
        if env is not None:
            return env
        if self.writable:
            f = self.root / f"{task_id}.json"
            if f.exists():
                try:
                    env = json.loads(f.read_text())
                    with self._lock:
                        self._mem[task_id] = env
                    return env
                except Exception:  # noqa: BLE001
                    return None
        return None

    def recent(self, limit: int = 50) -> List[dict]:
        with self._lock:
            items = list(self._mem.values())
        items.sort(key=lambda e: e.get("created_at") or "", reverse=True)
        return items[:limit]

    def count(self) -> int:
        with self._lock:
            return len(self._mem)

    def info(self) -> dict:
        return {"dir": str(self.root), "writable": self.writable,
                "cached": self.count()}


# --------------------------------------------------------------------------
# bounded background queue
# --------------------------------------------------------------------------
class Job:
    """One queued local task; `wait()` blocks until it finishes."""

    def __init__(self, task_id: str, kind: str):
        self.task_id = task_id
        self.kind = kind
        self.state = "queued"          # queued | running | completed | failed
        self.created_at = now_iso()
        self.started_at: Optional[str] = None
        self.finished_at: Optional[str] = None
        self.result: Optional[dict] = None
        self.error: Optional[str] = None
        self._done = threading.Event()

    def wait(self, timeout: Optional[float] = None) -> bool:
        return self._done.wait(timeout)

    def info(self) -> dict:
        return {"task_id": self.task_id, "kind": self.kind, "state": self.state,
                "created_at": self.created_at, "started_at": self.started_at,
                "finished_at": self.finished_at, "error": self.error}


class QueueFull(RuntimeError):
    pass


class LocalQueue:
    """Serial-ish execution of local tasks in worker threads.

    Local work is CPU heavy and shares the pod with the normal request path, so
    the queue is deliberately small: callers get a 429 instead of silently
    queueing behind a minute of CPU burn.
    """

    def __init__(self, runner: Callable[[Job], Optional[dict]],
                 workers: Optional[int] = None, capacity: Optional[int] = None,
                 store: Optional[ResultStore] = None):
        self.runner = runner
        self.capacity = int(capacity if capacity is not None
                            else os.environ.get("LOCAL_QUEUE_CAPACITY", "8"))
        self.workers = max(1, int(workers if workers is not None
                                  else os.environ.get("LOCAL_WORKERS", "2")))
        self.store = store
        self._jobs: Dict[str, Job] = {}
        self._order: List[str] = []
        self._lock = threading.Lock()
        self._cv = threading.Condition(self._lock)
        self._stop = False
        self._threads = []
        for i in range(self.workers):
            t = threading.Thread(target=self._loop, name=f"local-wkr-{i}",
                                 daemon=True)
            t.start()
            self._threads.append(t)

    # ---------------------------------------------------------------- api --
    def submit(self, task_id: str, kind: str) -> Job:
        with self._lock:
            existing = self._jobs.get(task_id)
            if existing is not None:
                return existing
            pending = sum(1 for j in self._jobs.values()
                          if j.state in ("queued", "running"))
            if pending >= self.capacity:
                raise QueueFull(f"本地执行队列已满({pending}/{self.capacity})，请稍后重试")
            job = Job(task_id, kind)
            self._jobs[task_id] = job
            self._order.append(task_id)
            self._cv.notify()
            return job

    def position(self, task_id: str) -> int:
        with self._lock:
            queued = [j for j in self._jobs.values() if j.state == "queued"]
            for i, j in enumerate(queued):
                if j.task_id == task_id:
                    return i + 1
            return 0

    def state(self, task_id: str) -> Optional[str]:
        with self._lock:
            job = self._jobs.get(task_id)
            return job.state if job else None

    def status(self) -> dict:
        with self._lock:
            items = [j.info() for j in self._jobs.values()]
        pending = [i for i in items if i["state"] == "queued"]
        running = [i for i in items if i["state"] == "running"]
        done = sum(1 for i in items if i["state"] == "completed")
        failed = sum(1 for i in items if i["state"] == "failed")
        return {"pending": len(pending), "running": len(running),
                "completed": done, "failed": failed,
                "capacity": self.capacity, "workers": self.workers,
                "pending_items": pending[:20], "running_items": running[:20]}

    def shutdown(self) -> None:
        with self._lock:
            self._stop = True
            self._cv.notify_all()

    # ------------------------------------------------------------- worker --
    def _next(self) -> Optional[Job]:
        with self._cv:
            while not self._stop:
                for j in self._jobs.values():
                    if j.state == "queued":
                        j.state = "running"
                        j.started_at = now_iso()
                        return j
                self._cv.wait(timeout=0.5)
            return None

    def _loop(self) -> None:
        while True:
            job = self._next()
            if job is None:
                return
            try:
                env = self.runner(job)
                if env is None:
                    raise RuntimeError("本地执行未返回结果")
                job.result = env
                job.state = "completed"
            except Exception as e:  # noqa: BLE001
                job.error = f"{type(e).__name__}: {str(e)[:300]}"
                job.state = "failed"
                job.result = envelope(
                    job.task_id, job.kind, mode="local", orchestrator="pod",
                    status="failed", error=job.error)
            finally:
                job.finished_at = now_iso()
                if self.store is not None and job.result is not None:
                    self.store.put(job.task_id, job.result, status=job.state)
                job._done.set()


# --------------------------------------------------------------------------
# local pipelines
# --------------------------------------------------------------------------
def _det_blob(item_id: str) -> str:
    """Same deterministic payload as scheduler._det_blob / v3.chunk_blob."""
    seed = int(hashlib.sha1(item_id.encode()).hexdigest()[:8], 16)
    rnd = random.Random(seed)
    return "".join(rnd.choice("0123456789abcdef") for _ in range(512))


def run_compute(task_id: str, params: dict, *, degraded: bool = False,
                degrade_reason: Optional[str] = None,
                mode_requested: str = "local") -> dict:
    """Compute locally: this pod runs every partition itself, sequentially."""
    from common import v3_common as v3  # local import: keeps module importable

    ent, node = entity_name(), node_name()
    started = time.perf_counter()
    created = now_iso()
    instruments = int(params.get("instruments", 4))
    rows = int(params.get("rows", 256))
    intensity = int(params.get("intensity", 40))
    partition_count = max(1, min(int(params.get("partition_count", 3)), 6))
    seed = int(params.get("seed", random.randint(0, 2 ** 20)))

    stages, partitions = [], []
    dispatch_t0 = time.perf_counter()
    for i in range(partition_count):
        t0 = time.perf_counter()
        res = v3.compute_partition(rows=rows, instruments=instruments,
                                   intensity=intensity, seed=seed + i,
                                   partition=i, count=partition_count)
        ms = (time.perf_counter() - t0) * 1000
        res.update({"actor": ent, "node": node, "role": entity_kind(),
                    "pod": pod_name(), "ms": round(ms, 2),
                    "local": True, "partition_index": i})
        partitions.append(res)
        stages.append(stage("compute", ent, node, ms,
                            detail=f"本机分区 {i + 1}/{partition_count}(串行)",
                            compute_ms=res.get("cpu_ms"), network_ms=0.0))
    dispatch_wall_ms = (time.perf_counter() - dispatch_t0) * 1000

    pipeline = (time.perf_counter() - started) * 1000
    compute_total = sum(float(p.get("cpu_ms", 0)) for p in partitions)
    # same metric shape as the collaborative path so the two are comparable:
    # local is strictly serial, so its "parallel speedup" stays at ~1.
    speedup = (compute_total / dispatch_wall_ms) if dispatch_wall_ms > 0 else 0.0
    return envelope(task_id, "compute", mode="local",
                    mode_requested=mode_requested, degraded=degraded,
                    degrade_reason=degrade_reason, orchestrator="pod",
                    executor=ent, stages=stages, created_at=created,
                    result_detail={
                        "partitions_ok": len(partitions),
                        "total_bytes": int(sum(p.get("bytes", 0) for p in partitions)),
                        "aggregate_cpu_ms": round(compute_total, 2),
                        "dispatch_wall_ms": round(dispatch_wall_ms, 2),
                        "parallel_speedup": round(speedup, 3),
                        "partners": [ent],
                        "partition_count": partition_count,
                        "checksums": [p.get("checksum", "") for p in partitions],
                        "partitions": partitions,
                        "local_serial": True,
                    },
                    metrics={"pipeline_total_ms": pipeline,
                             "compute_ms_total": compute_total,
                             "dispatch_wall_ms": round(dispatch_wall_ms, 2),
                             "network_ms_total": 0.0})


def run_sync(task_id: str, params: dict, *, degraded: bool = False,
             degrade_reason: Optional[str] = None,
             mode_requested: str = "local") -> dict:
    """Sync locally: this pod itself pulls from peers, uploads to the cloud
    patient DB and triggers the cloud backup (same flow the scheduler drives)."""
    ent, node = entity_name(), node_name()
    started = time.perf_counter()
    created = now_iso()
    base = ENTITY_URLS.get(ent)
    if not base:
        raise RuntimeError(f"未知实体 {ent}: 无法确定自身服务地址")

    bandwidth_mbps = float(params.get("bandwidth_mbps", 20.0))
    concurrency = int(params.get("concurrency", 4))
    chunk_kb = int(params.get("chunk_kb", 4))

    state = http_json(DC_URL + "/db/state", timeout=30)
    local = http_json(base + "/v3/sync/local", timeout=30)
    all_ids = state.get("ids", [])
    held = set(local.get("holds", []) or [])
    missing = [i for i in all_ids if i not in held]
    if not missing:
        missing = all_ids[:24]

    peers = [{"entity": e, "url": u} for e, u in ENTITY_URLS.items() if e != ent]
    peers.append({"entity": "datacenter", "url": DC_URL + "/db/item/"})

    delay_per_kb = (1000.0 / (bandwidth_mbps * 1024.0)) if bandwidth_mbps else 0.0
    chunks, ok_n, pulled_bytes = [], 0, 0
    t0 = time.perf_counter()
    for idx, mid in enumerate(missing):
        peer = peers[idx % len(peers)]
        t_chunk = time.perf_counter()
        item = None
        try:
            url = (peer["url"] + mid if peer["entity"] == "datacenter"
                   else peer["url"] + "/v3/sync/chunk/" + mid)
            item = http_json(url, timeout=30)
        except Exception:  # noqa: BLE001
            try:
                item = http_json(DC_URL + "/db/item/" + mid, timeout=30)
                peer = {"entity": "datacenter", "url": DC_URL + "/db/item/"}
            except Exception as e:  # noqa: BLE001
                chunks.append({"id": mid, "ok": False, "peer": peer["entity"],
                               "error": str(e)[:100]})
                continue
        kb = int(item.get("size", 0)) / 1024.0
        cost_ms = kb * delay_per_kb * 1000
        time.sleep(max(0.0, min(1.5, cost_ms / 1000)))
        chunks.append({"id": mid, "ok": True, "peer": peer["entity"],
                       "size": item.get("size"), "hash": item.get("hash"),
                       "ms": round((time.perf_counter() - t_chunk) * 1000 + cost_ms, 2)})
        ok_n += 1
        pulled_bytes += int(item.get("size", 0))
    pull_ms = (time.perf_counter() - t0) * 1000

    try:
        http_json(base + "/v3/sync/accepted",
                  {"ids": [c["id"] for c in chunks if c.get("ok")]}, timeout=30)
    except Exception:  # noqa: BLE001
        pass

    pushed = [{"id": u["uid"], "blob": _det_blob(u["uid"])}
              for u in local.get("pending_updates", [])]
    t_up = time.perf_counter()
    up = http_json(DC_URL + "/db/upload", {"items": pushed}, timeout=60)
    upload_ms = (time.perf_counter() - t_up) * 1000

    t_bk = time.perf_counter()
    backup = http_json(DC_URL + "/db/backup", {}, timeout=60)
    backup_ms = (time.perf_counter() - t_bk) * 1000

    pipeline = (time.perf_counter() - started) * 1000
    stages = [
        stage("sync", ent, node, pull_ms,
              detail=f"本机 P2P 拉取 {ok_n}/{len(missing)} 分块",
              compute_ms=0.0, network_ms=pull_ms),
        stage("sync", ent, node, upload_ms,
              detail="本机上传云端患者库", compute_ms=0.0, network_ms=upload_ms),
        stage("sync", ent, node, backup_ms,
              detail="本机触发云端备份", compute_ms=0.0, network_ms=backup_ms),
    ]
    return envelope(task_id, "sync", mode="local",
                    mode_requested=mode_requested, degraded=degraded,
                    degrade_reason=degrade_reason, orchestrator="pod",
                    executor=ent, stages=stages, created_at=created,
                    result_detail={
                        "db_version": state.get("db_version"),
                        "cloud_items": state.get("total_items"),
                        "missing": len(missing), "pulled": ok_n,
                        "failed_chunks": len(chunks) - ok_n,
                        "bytes_pulled": pulled_bytes,
                        "pull_ms": round(pull_ms, 2),
                        "concurrency": concurrency,
                        "bandwidth_mbps": bandwidth_mbps,
                        "chunk_kb": chunk_kb,
                        "peers": [p["entity"] for p in peers],
                        "chunks": chunks[:80],
                        "uploaded": up.get("accepted", 0),
                        "cloud_total": up.get("total_uploaded", 0),
                        "upload_ms": round(upload_ms, 2),
                        "backup_ms": round(backup_ms, 2),
                        "backup": backup,
                        "self_orchestrated": True,
                    },
                    metrics={"pipeline_total_ms": pipeline,
                             "compute_ms_total": 0.0,
                             "network_ms_total": pull_ms + upload_ms + backup_ms})


def run_routine(task_id: str, params: dict, *, degraded: bool = False,
                degrade_reason: Optional[str] = None,
                mode_requested: str = "local") -> dict:
    """Routine locally: run the jobs inline in this pod (no Kubernetes Jobs).
    The clinic ServiceAccount has no Job permissions, so creating Jobs from the
    pod is not an option - and inline execution is the point of "local"."""
    from common import v3_common as v3

    ent, node = entity_name(), node_name()
    started = time.perf_counter()
    created = now_iso()
    jobs_n = max(1, int(params.get("jobs", 2)))
    rows = int(params.get("rows", 128))
    intensity = int(params.get("intensity", 30))
    seed_base = int(params.get("seed", 7))

    job_rows, stages = [], []
    for i in range(jobs_n):
        t0 = time.perf_counter()
        res = v3.compute_partition(rows=rows, instruments=3, intensity=intensity,
                                   seed=seed_base + i, partition=i, count=jobs_n)
        ms = (time.perf_counter() - t0) * 1000
        res.update({"job": f"local-{task_id[-12:]}-{i}", "node": node,
                    "state": "succeeded", "wall_ms": round(ms, 2),
                    "pod": pod_name(), "inline": True})
        job_rows.append(res)
        stages.append(stage("routine", ent, node, ms,
                            detail=f"本机内联作业 {i + 1}/{jobs_n}",
                            compute_ms=res.get("cpu_ms"), network_ms=0.0))

    pipeline = (time.perf_counter() - started) * 1000
    compute_total = sum(float(j.get("cpu_ms", 0)) for j in job_rows)
    return envelope(task_id, "routine", mode="local",
                    mode_requested=mode_requested, degraded=degraded,
                    degrade_reason=degrade_reason, orchestrator="pod",
                    executor=ent, stages=stages, created_at=created,
                    result_detail={"jobs": job_rows, "succeeded": len(job_rows),
                                   "inline_jobs": jobs_n,
                                   "no_kubernetes_jobs": True},
                    metrics={"pipeline_total_ms": pipeline,
                             "compute_ms_total": compute_total,
                             "network_ms_total": 0.0})


# 诊断没有本地执行策略：医疗中心（医院）只能跑模型前端，后段（layer4 + 融合 +
# 分类器）只有数据中心的 medical-server 能跑。因此「就地完整诊断」不成立，
# 医院 /local/* 与诊所转诊都必须拒绝，统一报同一条中文原因。
DIAGNOSIS_NO_LOCAL_EXEC = (
    "诊断任务没有本地执行策略：医疗中心无法执行模型的 server 半段"
    "（layer4 + 融合 + 分类器），诊断必须经数据中心完成云边端协同推理")


def forward_diagnosis(task_id: str, params: dict, inp: dict, *,
                      degraded: bool = False,
                      degrade_reason: Optional[str] = None,
                      mode_requested: str = "local") -> dict:
    """诊所侧诊断：不再转诊最近医院就地跑完整模型，直接拒绝。

    v3.14 修正：医院镜像只保留 DoubleTower 前端，转诊到医院也无法完成诊断，
    所谓 capability=forward 的本地路径不成立。诊断只能走数据中心协同。
    """
    raise ValueError(DIAGNOSIS_NO_LOCAL_EXEC)


# --------------------------------------------------------------------------
# dispatch
# --------------------------------------------------------------------------
LOCAL_KINDS = ("diagnosis", "compute", "sync", "routine")


def dispatch(kind: str, task_id: str, params: dict, inp: Optional[dict] = None, *,
             degraded: bool = False, degrade_reason: Optional[str] = None,
             mode_requested: str = "local",
             diagnosis_fn: Optional[Callable[..., dict]] = None) -> dict:
    """Run one task locally. `diagnosis_fn` is only an offline-test injection
    point: no production image supplies one any more, and without it a
    diagnosis is refused (there is no local execution strategy)."""
    params = params or {}
    if kind == "compute":
        return run_compute(task_id, params, degraded=degraded,
                           degrade_reason=degrade_reason,
                           mode_requested=mode_requested)
    if kind == "sync":
        return run_sync(task_id, params, degraded=degraded,
                        degrade_reason=degrade_reason,
                        mode_requested=mode_requested)
    if kind == "routine":
        return run_routine(task_id, params, degraded=degraded,
                           degrade_reason=degrade_reason,
                           mode_requested=mode_requested)
    if kind == "diagnosis":
        if diagnosis_fn is not None:
            return diagnosis_fn(task_id, params, inp or {}, degraded=degraded,
                                degrade_reason=degrade_reason,
                                mode_requested=mode_requested)
        return forward_diagnosis(task_id, params, inp or {}, degraded=degraded,
                                 degrade_reason=degrade_reason,
                                 mode_requested=mode_requested)
    raise RuntimeError(f"未知任务类型: {kind}")


# --------------------------------------------------------------------------
# request/response plumbing shared by both apps
# --------------------------------------------------------------------------
def health_payload(store: ResultStore, queue: Optional[LocalQueue],
                   version: str = "3.2") -> dict:
    kind = entity_kind()
    return {
        "entity": entity_name(), "kind": kind, "node": node_name(),
        "pod": pod_name(), "capabilities": capabilities(kind),
        "queue": (queue.status() if queue else {"pending": 0, "running": 0}),
        "scheduler": probe_scheduler(),
        "local_mode": {"supported": True, "version": version,
                       "results_store": store.info(),
                       "workers": (queue.workers if queue else 0),
                       "capacity": (queue.capacity if queue else 0)},
        "ts": now_iso(),
    }


def run_local_request(kind: str, task_id: str, params: dict, inp: Optional[dict],
                      *, source: Optional[str] = None,
                      degraded: bool = False,
                      degrade_reason: Optional[str] = None,
                      mode_requested: str = "local",
                      is_async: bool = False,
                      store: ResultStore,
                      queue: LocalQueue,
                      runner: Any = None,
                      diagnosis_fn: Optional[Callable[..., dict]] = None,
                      initiator: Optional[dict] = None) -> dict:
    """Common /local/execute handling: dedupe, queue, wait, store.

    `runner` is the callable returned by `queue_runner_factory` - the shared
    worker pool executes whatever each request registered under its task_id.
    """
    if kind not in LOCAL_KINDS:
        raise ValueError(f"未知任务类型: {kind}（可选 {', '.join(LOCAL_KINDS)}）")

    # 诊断没有本地执行策略：没有显式注入 diagnosis_fn（仅离线测试会注入）时，
    # 立刻拒绝，让 /local/execute 返回 400 而不是排队去跑一个不可能的完整模型。
    if kind == "diagnosis" and diagnosis_fn is None:
        raise ValueError(DIAGNOSIS_NO_LOCAL_EXEC)

    cached = store.get(task_id)
    if cached is not None and cached.get("status") in ("completed", "failed"):
        return cached  # idempotent: same task_id never runs twice

    init = initiator or ({"entity": source, "role": entity_kind(),
                          "node": node_name()} if source else None)
    if runner is not None:
        runner.register(task_id, {
            "kind": kind, "params": params or {}, "input": inp or {},
            "degraded": degraded, "degrade_reason": degrade_reason,
            "mode_requested": mode_requested, "initiator": init,
        })
    job = queue.submit(task_id, kind)
    if is_async:
        return {"task_id": task_id, "status": job.state,
                "queue_position": queue.position(task_id),
                "poll": f"/local/result/{task_id}"}
    return _wait_for(job, store, timeout=DEFAULT_TIMEOUT)


def _wait_for(job: Job, store: ResultStore, timeout: float = DEFAULT_TIMEOUT) -> dict:
    if not job.wait(timeout=timeout):
        return envelope(job.task_id, job.kind, mode="local", orchestrator="pod",
                        status="running", error=None,
                        stage_name="queued")
    if job.result is not None:
        return job.result
    return envelope(job.task_id, job.kind, mode="local", orchestrator="pod",
                    status="failed", error=job.error or "本地执行失败")


def queue_runner_factory(store: ResultStore,
                         diagnosis_fn: Optional[Callable[..., dict]] = None
                         ) -> Callable[[Job], Optional[dict]]:
    """Build the queue's runner from the request that created each job.

    Each /local/execute call registers its own payload here, so one shared
    worker pool can serve concurrent requests with different parameters.
    """
    payloads: Dict[str, dict] = {}
    lock = threading.Lock()

    def register(task_id: str, payload: dict) -> None:
        with lock:
            payloads[task_id] = payload

    def runner(job: Job) -> Optional[dict]:
        with lock:
            payload = payloads.pop(job.task_id, None)
        if payload is None:
            raise RuntimeError("本地任务载荷丢失")
        env = dispatch(payload["kind"], job.task_id, payload.get("params") or {},
                       payload.get("input"), degraded=payload.get("degraded", False),
                       degrade_reason=payload.get("degrade_reason"),
                       mode_requested=payload.get("mode_requested", "local"),
                       diagnosis_fn=diagnosis_fn)
        if payload.get("initiator"):
            env["initiator"] = payload["initiator"]
        return env

    runner.register = register  # type: ignore[attr-defined]
    return runner
