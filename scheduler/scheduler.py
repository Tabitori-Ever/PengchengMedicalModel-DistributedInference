"""
v3.0 data-center scheduler.

Four task types, all initiated by hospital (edge) or clinic (terminal) pods
and executed cooperatively with the data center (cloud node3):
  diagnosis - medical DoubleTower: hospital worker -> dc medical-server
  compute   - instrument-stream simulation, multi-pod + dc collaborative math
  sync      - patient-db P2P cloud sync + backup
  routine   - scheduler spawns short-lived Kubernetes Jobs on free nodes,
              captures their JSON output and deletes them afterwards.
"""
import hashlib
import json
import os
import time
import uuid
from datetime import datetime
from typing import Any, Dict, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .task_manager import create_task, update_task, complete_task, fail_task
from .redis_client import (
    get_task, enqueue_task, redis_set, redis_get, REDIS_AVAILABLE,
    get_queue_length as redis_queue_len,
)
from .metrics import inference_latency
from .service_registry import (
    clinic_base, hospital_base, is_clinic, is_source,
    medical_server_url, pick_hospital, dc,
)
from . import node_usage as usage
from . import kubeops

_SESSION: Optional[requests.Session] = None


def _session() -> requests.Session:
    global _SESSION
    if _SESSION is None:
        _SESSION = requests.Session()
        retry = Retry(total=2, backoff_factor=0.3,
                      status_forcelist=[429, 500, 502, 503, 504],
                      allowed_methods=["POST", "GET"])
        ad = HTTPAdapter(max_retries=retry, pool_connections=10, pool_maxsize=40)
        _SESSION.mount("http://", ad)
        _SESSION.mount("https://", ad)
    return _SESSION


ROLE_BY_ENTITY = {
    "hospital-a": ("edge", "node1"), "hospital-b": ("edge", "node2"),
    "clinic-1": ("terminal", "node1"), "clinic-2": ("terminal", "node2"),
}


def _queue_wait_ms(task: dict) -> float:
    queued = task.get("queued_at") or task.get("start_time")
    if not queued:
        return 0.0
    try:
        return max(0.0, (datetime.utcnow() - datetime.fromisoformat(queued)).total_seconds() * 1000)
    except Exception:
        return 0.0


def _stage(task_id: str, name: str, actor: str, ms: float,
           detail: str = "", node: str = ""):
    update_task(task_id, {"stage": name, "node": node or actor})
    return {"name": name, "actor": actor, "node": node or actor,
            "ms": round(float(ms), 2), "detail": detail}


class InferenceScheduler:
    """v3.0 data-center scheduler."""

    def __init__(self, max_concurrent: int = 4, max_queue: int = 200):
        self.max_concurrent = max_concurrent
        self.active_tasks: Dict[str, Dict[str, Any]] = {}

    # ------------------------------------------------------------ submit ----
    def submit_task(self, model: str, source: str, priority: int = 5,
                    input_data: dict = None, deadline: str = "30s") -> dict:
        if not is_source(source):
            raise RuntimeError(f"unknown initiator: {source}")
        if redis_queue_len() >= int(os.getenv("MAX_QUEUE_SIZE", "200")):
            raise RuntimeError("Task queue is full, please try again later")
        task = create_task(model=model, source=source, priority=priority,
                           input_data=input_data or {}, deadline=deadline)
        enqueue_task(task["id"], priority)
        return {"task_id": task["id"], "status": "queued"}

    # ---------------------------------------------------------- dispatch ----
    def dispatch_task(self, task_ref: dict) -> bool:
        task_id = task_ref["id"]
        raw = get_task(task_id)
        if not raw:
            return False
        task = json.loads(raw)
        model = task.get("model", "diagnosis")
        if task_id in self.active_tasks:
            return False
        self.active_tasks[task_id] = task
        try:
            pipe = {"diagnosis": self._run_diagnosis,
                    "compute": self._run_compute,
                    "sync": self._run_sync,
                    "routine": self._run_routine}.get(model)
            if not pipe:
                fail_task(task_id, f"未知任务类型: {model}")
            else:
                pipe(task)
        except Exception as e:  # noqa: BLE001
            fail_task(task_id, str(e))
        finally:
            self.active_tasks.pop(task_id, None)
        return True

    # ---------------------------------------------------------- helpers -----
    def _initiator(self, task: dict) -> dict:
        source = task.get("source", "hospital-a")
        role, node = ROLE_BY_ENTITY.get(source, ("?", "?"))
        return {"entity": source, "role": role, "node": node,
                "priority": task.get("priority"),
                "deadline": task.get("deadline"),
                "queued_at": task.get("queued_at")}

    def _finish(self, task_id: str, start: float, result: dict, model: str):
        pipeline = (time.perf_counter() - start) * 1000
        result.setdefault("metrics", {})
        result["metrics"].update({"pipeline_total_ms": pipeline,
                                  "e2e_total_ms": pipeline})
        inference_latency.labels(model=model).observe(pipeline / 1000)
        complete_task(task_id, pipeline, result)
        redis_set(f"inference:result:{task_id}", json.dumps(
            {"status": "completed", "result": result}))

    def _entity_url(self, entity: str) -> Optional[str]:
        if entity in ("hospital-a", "hospital-b"):
            return hospital_base(entity)
        if entity in ("clinic-1", "clinic-2"):
            return clinic_base(entity)
        return None

    # ------------------------------------------------------- diagnosis -------
    def _run_diagnosis(self, task: dict):
        task_id = task["id"]
        source = task.get("source", "hospital-a")
        inp = task.get("input", {}) or {}
        start = time.perf_counter()
        hospital = pick_hospital(source, inp.get("target_hospital"))
        forwarded = hospital if is_clinic(source) else None
        update_task(task_id, {"stage": "worker", "node": hospital})

        stages = []
        wstart = time.perf_counter()
        try:
            r = _session().post(hospital_base(hospital) + "/medical/infer",
                                json=inp, timeout=300)
            r.raise_for_status()
            worker = r.json()
        except Exception as e:
            fail_task(task_id, f"医院 worker 推理失败({hospital}): {e}")
            return
        stages.append(_stage(task_id, "worker", hospital,
                             (time.perf_counter() - wstart) * 1000,
                             "医院 Pod 医疗前端", hospital))

        sstart = time.perf_counter()
        try:
            r = _session().post(
                medical_server_url(),
                json={"dce_features": worker.get("dce_features"),
                      "dwi_features": worker.get("dwi_features"),
                      "clinical_features": worker.get("clinical_features"),
                      "radiomics_features": worker.get("radiomics_features"),
                      "patient_ids": worker.get("patient_ids")},
                timeout=300)
            r.raise_for_status()
            server = r.json()
        except Exception as e:
            fail_task(task_id, f"数据中心 medical-server 推理失败: {e}")
            return
        stages.append(_stage(task_id, "server", "datacenter",
                             (time.perf_counter() - sstart) * 1000,
                             "DC medical-server", "node3"))

        self._finish(task_id, start, {
            "initiator": self._initiator(task),
            "forwarded_to": forwarded,
            "stages": stages,
            "result_detail": {
                "predictions": server.get("predictions", []),
                "bpCR_probability": server.get("bpCR_probability"),
                "worker_latency_ms": worker.get("latency_ms"),
                "server_latency_ms": server.get("latency_ms"),
            },
        }, "diagnosis")

    # ---------------------------------------------------------- compute ------
    def _run_compute(self, task: dict):
        task_id = task["id"]
        source = task.get("source", "hospital-a")
        inp = task.get("input", {}) or {}
        start = time.perf_counter()

        instruments = int(inp.get("instruments", 4))
        rows = int(inp.get("rows", 256))
        intensity = int(inp.get("intensity", 40))
        partition_count = max(2, min(int(inp.get("partition_count", 3)), 6))
        seed = int(inp.get("seed", uuid.uuid4().int % (2 ** 20)))

        partners = [source, "datacenter"]
        other = "hospital-a" if source != "hospital-a" else "hospital-b"
        if source not in ("hospital-a", "hospital-b"):
            other = pick_hospital(source)
        partners.append(other)
        partners = partners[:partition_count]
        update_task(task_id, {"stage": "compute"})

        stages = []
        partitions = []
        for i, actor in enumerate(partners):
            pstart = time.perf_counter()
            try:
                if actor == "datacenter":
                    url = dc("/v3/compute")
                else:
                    url = self._entity_url(actor) + "/v3/compute"
                r = _session().post(url, json={
                    "rows": rows, "instruments": instruments,
                    "intensity": intensity, "seed": seed + i,
                    "partition": i, "count": len(partners)}, timeout=120)
                r.raise_for_status()
                res = r.json()
            except Exception as e:
                res = {"failed": True, "error": str(e)[:140],
                       "partition": i, "actor": actor}
            ms = (time.perf_counter() - pstart) * 1000
            res["ms"] = round(ms, 2)
            res.setdefault("actor", actor)
            partitions.append(res)
            stages.append(_stage(task_id, "compute", actor, ms,
                                 detail=f"分区 {i+1}/{len(partners)}",
                                 node=res.get("node", actor)))

        ok = [p for p in partitions if not p.get("failed")]
        self._finish(task_id, start, {
            "initiator": self._initiator(task),
            "stages": stages,
            "produced": {"instruments": instruments, "rows": rows,
                         "samples": instruments * rows},
            "partitions": partitions,
            "result_detail": {
                "partitions_ok": len(ok),
                "total_bytes": int(sum(p.get("bytes", 0) for p in ok)),
                "aggregate_cpu_ms": round(
                    sum(float(p.get("cpu_ms", 0)) for p in ok), 2),
                "checksums": [p.get("checksum", "") for p in ok],
            },
        }, "compute")

    # ------------------------------------------------------------- sync ------
    def _run_sync(self, task: dict):
        task_id = task["id"]
        source = task.get("source", "clinic-1")
        inp = task.get("input", {}) or {}
        start = time.perf_counter()

        if not is_source(source):
            fail_task(task_id, f"非法的发起端 {source}")
            return
        base = self._entity_url(source)
        bandwidth_mbps = float(inp.get("bandwidth_mbps", 20.0))
        concurrency = int(inp.get("concurrency", 4))
        chunk_kb = int(inp.get("chunk_kb", 4))

        s = _session()
        try:
            state = s.get(dc("/db/state"), timeout=20).json()
            local = s.get(base + "/v3/sync/local", timeout=20).json()
        except Exception as e:
            fail_task(task_id, f"患者库/本地副本不可达: {e}")
            return

        all_ids = state.get("ids", [])
        held = set(local.get("holds", []) or [])
        missing = [i for i in all_ids if i not in held]
        if not missing:
            missing = all_ids[:24]

        peers = []
        for ent in ("hospital-a", "hospital-b", "clinic-1", "clinic-2"):
            if ent != source:
                u = self._entity_url(ent)
                if u:
                    peers.append({"entity": ent, "url": u})
        peers.append({"entity": "datacenter", "url": dc("/db/item/")})

        update_task(task_id, {"stage": "sync"})
        delay_per_kb = (1000.0 / (bandwidth_mbps * 1024.0)) if bandwidth_mbps else 0.0
        chunks = []
        ok_n = 0
        pulled_bytes = 0
        t0 = time.perf_counter()
        for idx, mid in enumerate(missing):
            peer = peers[idx % len(peers)]
            started = time.perf_counter()
            item = None
            try:
                if peer["entity"] == "datacenter":
                    r = s.get(peer["url"] + mid, timeout=30)
                else:
                    r = s.get(peer["url"] + "/v3/sync/chunk/" + mid, timeout=30)
                r.raise_for_status()
                item = r.json()
            except Exception:
                try:  # fall back to cloud master
                    r = s.get(dc("/db/item/") + mid, timeout=30)
                    r.raise_for_status()
                    item = r.json()
                except Exception as e:
                    chunks.append({"id": mid, "ok": False, "peer": peer["entity"],
                                   "error": str(e)[:100]})
                    continue
            kb = int(item.get("size", 0)) / 1024.0
            cost_ms = kb * delay_per_kb * 1000
            time.sleep(max(0.0, min(1.5, cost_ms / 1000)))
            chunks.append({"id": mid, "ok": True, "peer": peer["entity"],
                           "size": item.get("size"), "hash": item.get("hash"),
                           "ms": round((time.perf_counter() - started) * 1000
                                       + cost_ms, 2)})
            ok_n += 1
            pulled_bytes += int(item.get("size", 0))
        pull_ms = (time.perf_counter() - t0) * 1000

        try:
            s.post(base + "/v3/sync/accepted",
                   json={"ids": [c["id"] for c in chunks if c.get("ok")]}, timeout=20)
        except Exception:
            pass

        # push local pending updates -> cloud -> backup
        pushed = []
        for u in local.get("pending_updates", []):
            blob = _det_blob(u["uid"])
            pushed.append({"id": u["uid"], "blob": blob})
        try:
            up = s.post(dc("/db/upload"), json={"items": pushed}, timeout=30).json()
            backup = s.post(dc("/db/backup"), json={}, timeout=30).json()
        except Exception as e:
            fail_task(task_id, f"云端上传/备份失败: {e}")
            return

        stages = [_stage(task_id, "sync", "datacenter", 1.0,
                         "cloud manifest + 备份", "node3")]
        self._finish(task_id, start, {
            "initiator": self._initiator(task),
            "stages": stages,
            "result_detail": {
                "db_version": state.get("db_version"),
                "cloud_items": state.get("total_items"),
                "missing": len(missing),
                "pulled": ok_n,
                "failed_chunks": len(chunks) - ok_n,
                "bytes_pulled": pulled_bytes,
                "pull_ms": round(pull_ms, 2),
                "concurrency": concurrency,
                "bandwidth_mbps": bandwidth_mbps,
                "peers": [p["entity"] for p in peers],
                "chunks": chunks[:80],
                "uploaded": up.get("accepted", 0),
                "cloud_total": up.get("total_uploaded", 0),
                "backup": backup,
            },
        }, "sync")

    # ---------------------------------------------------------- routine ------
    def _run_routine(self, task: dict):
        task_id = task["id"]
        source = task.get("source", "clinic-1")
        inp = task.get("input", {}) or {}
        start = time.perf_counter()

        if not kubeops.available():
            fail_task(task_id, "Kubernetes Job 能力不可用")
            return
        jobs_n = max(1, int(inp.get("jobs", 2)))
        rows = int(inp.get("rows", 128))
        intensity = int(inp.get("intensity", 30))
        nodes = usage.free_edge_nodes()
        while len(nodes) < jobs_n:
            nodes = nodes + ["node1", "node2"]
        nodes = nodes[:jobs_n]

        update_task(task_id, {"stage": "routine"})
        job_rows = []
        for i in range(jobs_n):
            job_name = f"routine-{task_id[-12:]}-{i}"
            args = {"rows": rows, "instruments": 3, "intensity": intensity,
                    "seed": 7 + i, "partition": i, "count": jobs_n}
            jr = {"job": job_name, "node": nodes[i], "state": "failed"}
            if kubeops.create_routine_job(job_name, nodes[i], args):
                deadline = time.time() + 180
                while time.time() < deadline:
                    st = kubeops.job_status(job_name)
                    if st["state"] == "succeeded":
                        parsed = _last_json(kubeops.read_pod_log(
                            st.get("pod_name", "")))
                        jr.update({"state": "succeeded", "output": parsed,
                                   "pod": st.get("pod_name")})
                        break
                    if st["state"] == "failed":
                        break
                    time.sleep(3)
                else:
                    jr["state"] = "timeout"
                kubeops.delete_job(job_name)
                jr["deleted"] = True
            job_rows.append(jr)

        self._finish(task_id, start, {
            "initiator": self._initiator(task),
            "stages": [_stage(task_id, "routine", "scheduler", 1.0,
                              f"生成 {jobs_n} 个一次性 Job pod", "node3")],
            "result_detail": {
                "jobs": job_rows,
                "succeeded": sum(1 for j in job_rows if j.get("state") == "succeeded"),
            },
        }, "routine")

    def get_task_result(self, task_id: str) -> dict:
        result_key = f"inference:result:{task_id}"
        data = redis_get(result_key)
        if data:
            return json.loads(data)
        raw = get_task(task_id)
        if raw:
            t = json.loads(raw)
            if t.get("status") == "failed":
                return {"task_id": task_id, "status": "failed",
                        "error": t.get("error")}
            if t.get("status") == "finished":
                return {"task_id": task_id, "status": "finished",
                        "result": t.get("result"),
                        "duration_ms": t.get("duration_ms")}
            if t.get("status") == "running":
                return {"task_id": task_id, "status": "running",
                        "stage": t.get("stage"), "progress": t.get("progress"),
                        "node": t.get("node")}
        return {"task_id": task_id, "status": "not_found"}

    def get_queue_status(self) -> dict:
        return {"queue_length": redis_queue_len(),
                "active_tasks": len(self.active_tasks),
                "redis_available": REDIS_AVAILABLE}


def _det_blob(item_id: str) -> str:
    """Deterministic payload identical to common.v3_common.chunk_blob."""
    seed = int(hashlib.sha1(item_id.encode()).hexdigest()[:8], 16)
    rnd = __import__("random").Random(seed)
    return "".join(rnd.choice("0123456789abcdef") for _ in range(512))


def _last_json(log: str) -> Optional[dict]:
    for line in reversed((log or "").splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except Exception:
                continue
    return None
