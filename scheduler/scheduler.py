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
import array
import base64
import hashlib
import json
import os
import queue
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
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
    HOSPITALS, CLINICS,
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


ENTITY_NODES = {
    "hospital-a": "node1", "hospital-b": "node2",
    "clinic-1": "node1", "clinic-2": "node2",
    "clinic-3": "node1", "clinic-4": "node2",
}

ROLE_BY_ENTITY = {
    "hospital-a": ("edge", "node1"), "hospital-b": ("edge", "node2"),
    "clinic-1": ("terminal", "node1"), "clinic-2": ("terminal", "node2"),
}

# v3.14: 诊断没有本地执行策略。医疗中心（医院）只能执行 DoubleTower 前端，
# server 半段（layer4 + 融合 + 分类器）只有数据中心 medical-server 能执行，
# 因此 mode=local / force_degraded / auto 降级对 diagnosis 一律拒绝，绝不就地执行。
NO_LOCAL_DIAGNOSIS = (
    "诊断任务没有本地执行策略：医疗中心无法执行模型的 server 半段"
    "（layer4 + 融合 + 分类器），诊断必须经数据中心完成云边端协同推理")


class UnsupportedLocalMode(RuntimeError):
    """请求的诊断本地/降级执行已不再支持（医疗中心无 server 半段）。

    继承 RuntimeError 以便既有的调度器调用方仍能捕获；API 层单独映射为 400，
    避免与「队列已满」的 429 混在一起。
    """


# ---------------------------------------------------------- compact enc ---
_IMG_KEYS = ("dce_image", "dwi_image", "clinical", "radiomics")


def _shape_of(nested) -> list:
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


def encode_f32b64(nested) -> dict:
    """Nested float lists -> {"__f32b64__", "shape"} using stdlib only.

    v3.3: the imaging input (~2MB of float text per patient) used to be parsed
    three times per collaborative diagnosis (client -> scheduler -> worker) and
    dominated the measured latency. float32 base64 is ~4x smaller and converts
    at C speed; the worker decodes it back to exactly the same float32 values.
    """
    flat = array.array("f", _flatten(nested))
    if sys.byteorder == "big":
        flat.byteswap()
    return {"__f32b64__": base64.b64encode(flat.tobytes()).decode(),
            "shape": _shape_of(nested), "dtype": "float32"}


def encode_input(inp: dict) -> dict:
    """Encode whichever imaging fields are still plain lists (batch included)."""
    out = dict(inp)
    for key in _IMG_KEYS:
        if isinstance(out.get(key), list):
            out[key] = encode_f32b64(out[key])
    batch = out.get("batch")
    if isinstance(batch, list):
        out["batch"] = [encode_input(item) if isinstance(item, dict) else item
                        for item in batch]
    return out


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
    """v3.0 data-center scheduler (+ v3.2 execution-mode selection / degradation).

    Execution modes (see 调度模式与降级-设计方案.md):
      collaborative - the data center orchestrates every actor (default)
      local         - the *initiating pod* runs the whole pipeline itself; the
                      scheduler only forwards and accounts for the task
                      (diagnosis has **no** local strategy: the server half only
                      runs in the data center, so local/degraded is refused)
      auto          - collaborative when the cloud dependencies are healthy,
                      otherwise degrade to local and flag the result
                      (diagnosis never degrades: it fails instead)
    """

    def __init__(self, max_concurrent: int = 4, max_queue: int = 200):
        self.max_concurrent = max_concurrent
        self.active_tasks: Dict[str, Dict[str, Any]] = {}
        # per-task runtime decisions (mode actually used / degradation reason),
        # stamped onto the result by _finish and read by the slim task feed.
        self._runtime: Dict[str, Dict[str, Any]] = {}
        self._direct_tasks: Dict[str, Dict[str, Any]] = {}
        # 执行者速度画像：每例完整模型的毫秒数（滚动平均），用于按算力加权分片
        self._exec_cost: Dict[str, float] = {"hospital-a": 130.0,
                                             "hospital-b": 130.0,
                                             "datacenter": 230.0}

    # ------------------------------------------------------------ submit ----
    def submit_task(self, model: str, source: str, priority: int = 5,
                    input_data: dict = None, deadline: str = "30s",
                    mode: str = "collaborative",
                    force_degraded: bool = False) -> dict:
        if not is_source(source):
            raise RuntimeError(f"unknown initiator: {source}")
        if redis_queue_len() >= int(os.getenv("MAX_QUEUE_SIZE", "200")):
            raise RuntimeError("Task queue is full, please try again later")
        mode = (mode or "collaborative").strip().lower()
        if mode not in ("collaborative", "local", "auto"):
            raise RuntimeError(f"未知执行模式: {mode}（可选 collaborative/local/auto）")
        # 诊断只能协同：本地 / 显式降级都直接拒绝（客户端会拿到 400）
        if model == "diagnosis" and (mode == "local" or force_degraded):
            raise UnsupportedLocalMode(NO_LOCAL_DIAGNOSIS)
        task = create_task(model=model, source=source, priority=priority,
                           input_data=input_data or {}, deadline=deadline,
                           extra={"mode": mode,
                                  "force_degraded": bool(force_degraded)})
        enqueue_task(task["id"], priority)
        return {"task_id": task["id"], "status": "queued", "mode": mode}

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
        mode = (task.get("mode") or "collaborative").lower()
        force = bool(task.get("force_degraded"))
        try:
            if model == "diagnosis" and (force or mode == "local"):
                # 防御：历史遗留任务（升级前入队）或直接 create_task 的任务
                # 也不能在医院就地跑完整模型。
                fail_task(task_id, NO_LOCAL_DIAGNOSIS)
            elif force:
                self._run_local(task, degrade_reason="forced")
            elif mode == "local":
                self._run_local(task)
            elif mode == "auto":
                healthy, reason = self._dependencies_healthy(model)
                if healthy:
                    self._runtime[task_id] = {"mode_used": "collaborative",
                                              "mode_requested": "auto",
                                              "degraded": False,
                                              "degrade_reason": None}
                    self._pipeline(task, model)
                elif model == "diagnosis":
                    # 诊断没有本地降级路径：协同依赖不可用时直接失败，
                    # 绝不降级到医院本地执行。
                    fail_task(task_id, NO_LOCAL_DIAGNOSIS)
                else:
                    self._run_local(task, degrade_reason=reason)
            else:
                self._runtime[task_id] = {"mode_used": "collaborative",
                                          "mode_requested": "collaborative",
                                          "degraded": False,
                                          "degrade_reason": None}
                self._pipeline(task, model)
        except Exception as e:  # noqa: BLE001
            fail_task(task_id, str(e))
        finally:
            self.active_tasks.pop(task_id, None)
            self._runtime.pop(task_id, None)
        return True

    def _pipeline(self, task: dict, model: str):
        pipe = {"diagnosis": self._run_diagnosis,
                "compute": self._run_compute,
                "sync": self._run_sync,
                "routine": self._run_routine}.get(model)
        if not pipe:
            fail_task(task["id"], f"未知任务类型: {model}")
        else:
            pipe(task)

    # ------------------------------------------------- degradation policy ---
    @staticmethod
    def _reachable(url: str, timeout: float = 1.5) -> bool:
        try:
            return _session().get(url, timeout=timeout).status_code == 200
        except Exception:  # noqa: BLE001
            return False

    def _dependencies_healthy(self, model: str):
        """Cloud-dependency probe used by mode=auto. Returns (healthy, reason)."""
        if model == "routine":
            if kubeops.available():
                return True, None
            return False, "kube_job_api_unavailable"
        if model == "diagnosis":
            if not self._reachable(medical_server_url().rstrip("/").replace("/infer", "") + "/health"):
                return False, "medical_server_unreachable"
            if not (self._reachable(hospital_base("hospital-a") + "/health")
                    or self._reachable(hospital_base("hospital-b") + "/health")):
                return False, "hospital_unreachable"
            return True, None
        if model in ("compute", "sync"):
            if not self._reachable(dc("/health")):
                return False, "dc_services_unreachable"
            return True, None
        return True, None

    # -------------------------------------------------- local delegation ----
    @staticmethod
    def _split_payload(model: str, inp: dict) -> tuple:
        """Split a task input into pod `params` + heavy `input` payload."""
        inp = dict(inp or {})
        if model == "diagnosis":
            batch = inp.get("batch")
            if isinstance(batch, list) and batch:
                params = {k: v for k, v in inp.items() if k != "batch"}
                return params, encode_input({"batch": batch})
            heavy = ("dce_image", "dwi_image", "clinical", "radiomics",
                     "patient_ids")
            payload = {k: inp.pop(k) for k in list(inp) if k in heavy}
            if payload:
                payload = encode_input(payload)
            return inp, payload
        return inp, {}

    def _run_local(self, task: dict, degrade_reason: Optional[str] = None):
        """Hand the whole task to the initiating pod (local / degraded mode)."""
        task_id = task["id"]
        model = task.get("model", "compute")
        # 诊断没有本地执行策略：兜底拒绝，绝不把完整模型派给医院 Pod。
        if model == "diagnosis":
            fail_task(task_id, NO_LOCAL_DIAGNOSIS)
            return
        source = task.get("source", "hospital-a")
        inp = task.get("input", {}) or {}
        start = time.perf_counter()
        queue_wait = _queue_wait_ms(task)

        base = self._entity_url(source)
        if not base:
            fail_task(task_id, f"发起实体 {source} 无法定位服务地址")
            return
        url = base + "/local/execute"
        params, payload = self._split_payload(model, inp)
        mode_requested = (task.get("mode") or "collaborative").lower()
        if degrade_reason == "forced":
            degraded = True
        else:
            degraded = degrade_reason is not None
        req = {"task_id": task_id, "kind": model, "source": source,
               "params": params, "input": payload,
               "degraded": degraded, "degrade_reason": degrade_reason,
               "mode_requested": mode_requested, "async": False}
        update_task(task_id, {"stage": "local", "node": source})
        self._runtime[task_id] = {"mode_used": "local",
                                  "mode_requested": mode_requested,
                                  "degraded": degraded,
                                  "degrade_reason": degrade_reason}
        t0 = time.perf_counter()
        try:
            r = _session().post(url, json=req, timeout=900)
            if r.status_code == 404:
                fail_task(task_id, f"{source} 未部署本地执行端点 /local/execute（需 v3.2 镜像）")
                return
            if r.status_code == 429:
                fail_task(task_id, f"{source} 本地执行队列已满，请稍后重试")
                return
            r.raise_for_status()
            env = r.json()
        except Exception as e:  # noqa: BLE001
            fail_task(task_id, f"本地执行调用失败({source}): {str(e)[:200]}")
            return
        delegation_ms = (time.perf_counter() - t0) * 1000

        pod_metrics = dict(env.get("metrics") or {})
        stages = [s for s in (env.get("stages") or [])]
        stages.insert(0, _stage(task_id, "delegate", "scheduler", delegation_ms,
                                f"调度器委派 {source} 就地执行"
                                + ("（降级）" if degraded else ""), "node3"))
        pod_pipeline = float(pod_metrics.get("pipeline_total_ms") or 0.0)
        result = {
            "initiator": self._initiator(task),
            "mode": "local",
            "mode_requested": mode_requested,
            "degraded": degraded,
            "degrade_reason": degrade_reason,
            "orchestrator": "pod",
            "delegated_by": "scheduler",
            "executor": env.get("executor") or source,
            "stage": "local",
            "stages": stages,
            "result_detail": env.get("result_detail") or {},
            "produced": env.get("produced"),
        }
        if env.get("status") == "failed":
            fail_task(task_id, env.get("error") or "本地执行失败")
            return
        self._finish(task_id, start, result, model, queue_wait_ms=queue_wait,
                     extra_metrics={
                         "delegation_ms": round(delegation_ms, 2),
                         "pod_pipeline_ms": round(pod_pipeline, 2),
                         "compute_ms_total": pod_metrics.get("compute_ms_total", 0.0),
                         "network_ms_total": pod_metrics.get("network_ms_total", 0.0),
                         "degrade_switch_ms": round(delegation_ms, 2) if degraded else 0.0,
                     })

    # ---------------------------------------------------------- helpers -----
    def _initiator(self, task: dict) -> dict:
        source = task.get("source", "hospital-a")
        role, node = ROLE_BY_ENTITY.get(source, ("?", "?"))
        return {"entity": source, "role": role, "node": node,
                "priority": task.get("priority"),
                "deadline": task.get("deadline"),
                "queued_at": task.get("queued_at")}

    def _finish(self, task_id: str, start: float, result: dict, model: str,
                queue_wait_ms: float = 0.0, extra_metrics: Optional[dict] = None):
        pipeline = (time.perf_counter() - start) * 1000
        metrics = dict(extra_metrics or {})
        metrics.update({"queue_wait_ms": round(queue_wait_ms, 2),
                        "pipeline_total_ms": round(pipeline, 2),
                        "e2e_total_ms": round(pipeline + queue_wait_ms, 2)})
        # v3.2: every result carries the execution mode it actually used, so the
        # benchmark site can group collaborative / local / degraded runs alike.
        rt = self._runtime.get(task_id, {})
        result.setdefault("kind", model)
        result["mode"] = rt.get("mode_used", result.get("mode", "collaborative"))
        result.setdefault("mode_requested", rt.get("mode_requested", result["mode"]))
        result["degraded"] = bool(rt.get("degraded", result.get("degraded", False)))
        result["degrade_reason"] = rt.get("degrade_reason",
                                          result.get("degrade_reason"))
        result.setdefault("orchestrator", "scheduler")
        if result["mode"] == "collaborative":
            result.setdefault("executor", result.get("initiator", {}).get("entity"))
        result.setdefault("status", "completed")
        result.setdefault("finished_at", datetime.utcnow().isoformat())
        result.setdefault("created_at", result.get("initiator", {}).get("queued_at"))
        result["metrics"] = metrics
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

    def _run_diagnosis_data_parallel(self, task: dict, batch: list, hospital: str,
                                     start: float, queue_wait: float,
                                     forwarded: bool):
        """数据并行分片：把患者分成互不依赖的两份，医院与数据中心同时开工。

        **v3.14 口径修正**：医疗中心不能执行模型的 server 半段（layer4 + 融合 +
        分类器），所以医院这一份不再是"本地跑完整模型"，而是走
        `/medical/infer_forward`——前端（conv1..layer3）在边缘就地算，后段直投
        云端后端；数据中心那一份仍由 `/infer_full` 整段执行。
        并行收益因此来自"把前端算力卸到边缘"，墙钟 ≈ max(两侧份额) + 输入搬运。
        """
        task_id = task["id"]
        # 分片：奇数位给数据中心，偶数位留在医院（两边份额尽量均衡）
        assign = []
        for i, item in enumerate(batch):
            assign.append(("datacenter" if i % 2 else hospital, i, item))
        update_task(task_id, {"stage": "worker", "node": hospital})

        def _run_one(executor: str, index: int, item: dict):
            payload = {k: item.get(k) for k in _IMG_KEYS if item.get(k) is not None}
            payload["patient_ids"] = item.get("patient_ids") or [item.get("patient_id")]
            if executor == "datacenter":
                url = medical_server_url().rstrip("/").replace("/infer", "") + "/infer_full"
            else:
                # 医疗中心只能做前端：走 forward（前端就地 → 云端后端），
                # 旧的 /medical/infer_full（医院整段执行）已不再支持。
                url = hospital_base(executor) + "/medical/infer_forward"
            t0 = time.perf_counter()
            try:
                r = _session().post(url, json=encode_input(payload), timeout=300)
                r.raise_for_status()
                data = r.json()
                err = None
            except Exception as e:  # noqa: BLE001
                data, err = None, str(e)[:140]
            return {"index": index, "executor": executor,
                    "patient_id": item.get("patient_id"),
                    "ms": round((time.perf_counter() - t0) * 1000, 2),
                    "compute_ms": (data or {}).get("latency_ms"),
                    "state": "succeeded" if data else "failed",
                    "predictions": (data or {}).get("predictions"), "error": err}

        t_dispatch = time.perf_counter()
        with ThreadPoolExecutor(max_workers=len(assign),
                                thread_name_prefix="diag-data") as pool:
            recs = sorted(pool.map(lambda a: _run_one(*a), assign),
                          key=lambda r: r["index"])
        dispatch_wall_ms = (time.perf_counter() - t_dispatch) * 1000

        preds, failed = [], []
        for r in recs:
            preds.extend(r.get("predictions") or [])
            if r["state"] != "succeeded":
                failed.append({"index": r["index"], "patient_id": r["patient_id"],
                               "error": r.get("error")})
        ok = [r for r in recs if r["state"] == "succeeded"]
        by_exec: Dict[str, list] = {}
        for r in recs:
            by_exec.setdefault(r["executor"], []).append(r)
        stages = [_stage(task_id, "worker-data", ex, max(x["ms"] for x in rs),
                         (f"{len(rs)} 例整段执行（数据中心）"
                          if ex == "datacenter"
                          else f"{len(rs)} 例：边缘前端 + 云端后端"),
                         (rs[0].get("node") or ex))
                  for ex, rs in by_exec.items()]
        compute_total = sum(float(r.get("compute_ms") or 0) for r in recs)
        self._finish(task_id, start, {
            "initiator": self._initiator(task),
            "forwarded_to": hospital if forwarded else None,
            "stages": stages,
            "result_detail": {
                "strategy": "data",
                "batch_size": len(batch),
                "predictions": preds,
                "bpCR_probability": (preds[0]["bpCR_probability"] if preds else None),
                "success": len(ok),
                "failed": failed,
                "per_patient": [{k: r.get(k) for k in (
                    "index", "patient_id", "executor", "state", "ms", "compute_ms")}
                    for r in recs],
                "executors": sorted(by_exec),
                "orchestration_wall_ms": round(dispatch_wall_ms, 2),
                "aggregate_cpu_ms": round(compute_total, 2),
                "data_parallel": True,
            },
        }, "diagnosis", queue_wait_ms=queue_wait, extra_metrics={
            "compute_ms_total": round(compute_total, 2),
            "dispatch_wall_ms": round(dispatch_wall_ms, 2),
            "network_ms_total": round(max(0.0, dispatch_wall_ms - compute_total), 2),
            "batch_size": len(batch),
        })

    # ------------------------------------------------ diagnosis (direct) -------
    # 控制面与数据面分离：调度器只登记任务并给出「谁执行哪几个患者」的计划，
    # 输入数据由客户端**直投执行者**，不再经调度器中转两趟。
    # 实测中转成本（站点→调度器→执行者）在批量下是秒级，是端侧空闲时诊断不达标的主因。
    # 医疗中心只能执行前端：直投给医院的计划必须用 forward 端点
    # （前端就地 → 云端后端），/medical/infer_full 已不再支持。
    _DIAG_DATA_ENDPOINTS = {"hospital-a": "/medical/infer_forward",
                            "hospital-b": "/medical/infer_forward",
                            "datacenter": "/infer_full"}

    def _diag_executor_url(self, entity: str) -> Optional[str]:
        if entity == "datacenter":
            base = medical_server_url().rstrip("/")
            if base.endswith("/infer"):
                base = base[: -len("/infer")]
            return base + "/infer_full"
        base = self._entity_url(entity)
        return (base + "/medical/infer_forward") if base else None

    def plan_diagnosis(self, source: str, patient_ids: list, priority: int = 5,
                       deadline: str = "300s", mode: str = "collaborative") -> dict:
        """登记任务 + 返回数据面执行计划（不入队，由客户端驱动）。"""
        if not is_source(source):
            raise RuntimeError(f"unknown initiator: {source}")
        if (mode or "").strip().lower() == "local":
            # 直投计划本身是协同路径；显式 mode=local 的诊断必须拒绝
            raise UnsupportedLocalMode(NO_LOCAL_DIAGNOSIS)
        ids = [str(x) for x in patient_ids]
        if len(ids) < 1:
            raise RuntimeError("直投模式需要至少 1 例患者")
        hospital = pick_hospital(source, None)
        # v3.5c 按算力加权分片：份额按「每例成本」的倒数分配，成本取各次实测的滚动
        # 平均。v3.14 起医院的份额是"边缘前端 + 云端后端"，成本随之变化，滚动平均
        # 会自动跟上，不需要改权重公式。
        partners = [hospital] if len(ids) == 1 else [hospital, "datacenter"]
        costs = [max(30.0, self._exec_cost.get(e, 200.0)) for e in partners]
        inv = [1.0 / c for c in costs]
        total = sum(inv)
        # 先按比例取整，再把余数给最快的那台
        shares = [int(len(ids) * x / total) for x in inv]
        while sum(shares) < len(ids):
            shares[inv.index(max(inv))] += 1
        for i, e in enumerate(partners):
            pass
        executors: Dict[str, list] = {}
        order: list = []
        idx = 0
        for ent, share in zip(partners, shares):
            if share <= 0:
                continue
            executors[ent] = ids[idx:idx + share]
            order.append(ent)
            idx += share
        for pid in ids[idx:]:                     # 兜底：余下的给第一台
            executors[order[0]].append(pid)
        task = create_task(model="diagnosis", source=source, priority=priority,
                           input_data={"patient_ids": ids}, deadline=deadline,
                           extra={"mode": mode, "force_degraded": False,
                                  "deliver": "direct"})
        task_id = task["id"]
        self._runtime[task_id] = {"mode_used": "collaborative",
                                  "mode_requested": mode, "degraded": False,
                                  "degrade_reason": None}
        update_task(task_id, {"stage": "dispatched", "node": ",".join(order)})
        self._direct_tasks[task_id] = {"source": source, "patient_ids": ids,
                                       "created": time.perf_counter()}
        plan = []
        for ent in order:
            url = self._diag_executor_url(ent)
            if not url:
                fail_task(task_id, f"无法定位执行者 {ent} 的服务地址")
                return {"task_id": task_id, "status": "failed"}
            plan.append({"entity": ent, "url": url, "patient_ids": executors[ent]})
        return {"task_id": task_id, "status": "dispatched", "strategy": "data",
                "deliver": "direct", "executors": plan,
                "shares": {e: len(v) for e, v in executors.items()},
                "report_url": f"/task/{task_id}/report"}

    def report_diagnosis(self, task_id: str, payload: dict) -> dict:
        """客户端回传直投执行结果；调度器统一记账（结果 + 指标 + Prometheus）。

        数据面已不在调度器上，因此 pipeline 时延取客户端回传的端到端测量值
        （记为 client-reported），调度器只负责归档与统计，不重复计时。
        """
        raw = get_task(task_id)
        if not raw:
            return {"status": "not_found"}
        task = json.loads(raw)
        results = payload.get("results") or []
        per_exec: Dict[str, list] = {}
        preds, failed = [], []
        for r in results:
            ent = str(r.get("executor") or "?")
            per_exec.setdefault(ent, []).append(r)
            if r.get("state") == "succeeded":
                for p in (r.get("predictions") or []):
                    preds.append(p)
            else:
                failed.append({"patient_id": r.get("patient_id"),
                               "error": str(r.get("error"))[:140]})
        for ent, rs in per_exec.items():
            ms_vals = [float(x.get("ms") or 0) for x in rs]
            n_pat = sum(len(x.get("patient_ids") or []) or 1 for x in rs)
            if ms_vals and n_pat:
                sample = max(ms_vals) / max(1, max(
                    len(x.get("patient_ids") or []) or 1 for x in rs))
                prev = self._exec_cost.get(ent, sample)
                self._exec_cost[ent] = round(0.7 * prev + 0.3 * sample, 1)
        client_ms = float(payload.get("client_total_ms") or 0.0)
        compute_total = sum(float(r.get("compute_ms") or 0) for r in results)
        stages = [_stage(task_id, "worker-data", ent,
                         max(float(x.get("ms") or 0) for x in rs),
                         f"{len(rs)} 例完整模型（客户端直投）", ent)
                  for ent, rs in per_exec.items()]
        result = {
            "initiator": self._initiator(task),
            "mode": "collaborative", "mode_requested": task.get("mode", "collaborative"),
            "degraded": False, "degrade_reason": None,
            "orchestrator": "pod+datacenter", "executor": ",".join(sorted(per_exec)),
            "stages": stages,
            "result_detail": {
                "strategy": "data", "deliver": "direct",
                "batch_size": len(task.get("input", {}).get("patient_ids") or []),
                "predictions": preds,
                "bpCR_probability": (preds[0]["bpCR_probability"] if preds else None),
                "success": len(preds), "failed": failed,
                "per_executor": {ent: len(rs) for ent, rs in per_exec.items()},
                "measurement": "client-reported (数据面直投，调度器不在链路上)",
            },
            "metrics": {
                "pipeline_total_ms": round(client_ms, 2),
                "e2e_total_ms": round(client_ms, 2),
                "queue_wait_ms": 0.0,
                "compute_ms_total": round(compute_total, 2),
                "network_ms_total": round(max(0.0, client_ms - compute_total), 2),
                "client_reported": True,
            },
            "status": "completed", "error": None,
            "kind": "diagnosis",
            "created_at": task.get("start_time"), "finished_at": datetime.utcnow().isoformat(),
        }
        complete_task(task_id, client_ms, result)
        redis_set(f"inference:result:{task_id}", json.dumps(
            {"status": "completed", "result": result}))
        inference_latency.labels(model="diagnosis").observe(client_ms / 1000)
        self._direct_tasks.pop(task_id, None)
        self._runtime.pop(task_id, None)
        return {"status": "completed", "task_id": task_id}

    # ------------------------------------------- diagnosis (batch, pipelined) --
    # A single diagnosis is a *sequential* model split (edge front-end -> cloud
    # back-end), so one pod doing everything locally is inherently cheaper.
    # Batching changes the picture: while the hospital computes the front-end of
    # patient i+1, the cloud is already computing the back-end of patient i, so
    # the batch costs ~max(N*T_edge, N*T_cloud) instead of N*(T_edge+T_cloud).
    # That pipeline is the collaborative advantage for diagnosis (v3.3).
    def _run_diagnosis_batch(self, task: dict, batch: list):
        task_id = task["id"]
        source = task.get("source", "hospital-a")
        inp = task.get("input", {}) or {}
        start = time.perf_counter()
        queue_wait = _queue_wait_ms(task)
        hospital = pick_hospital(source, inp.get("target_hospital"))
        update_task(task_id, {"stage": "worker", "node": hospital})
        n = len(batch)

        q: "queue.Queue" = queue.Queue(maxsize=2)
        lock = threading.Lock()
        records: Dict[int, Dict[str, Any]] = {}
        stop = object()

        # v3.4: 批量前端**并发派发**。原先逐个串行调用医院，医院要等云端返回才处理
        # 下一个患者，两段永远无法重叠、相当于只用了一台机器的算力。并发之后
        # 「医院 4 核跑前端」与「云端 4 核跑后端」同时工作，流水线才真正建立。
        parallel = max(1, min(int(inp.get("parallel") or 4), n))
        # v3.4: 批量诊断有两条协同路线，按批大小自动选择：
        #   data  数据并行——把患者分片给医院与数据中心**各自跑完整模型**，聚合两端算力；
        #                     无中间特征搬运，是批量场景的默认路线；
        #   split 模型拆分——边端前端 + 云端后端的两段流水线（单例场景开销更小）。
        strategy = str(inp.get("strategy") or ("data" if n >= 2 else "split")).lower()
        if strategy == "data" and n >= 2:
            self._run_diagnosis_data_parallel(task, batch, hospital, start,
                                              queue_wait, is_clinic(source))
            return


        def _front_one(i: int, item: dict):
                t0 = time.perf_counter()
                payload = {k: item.get(k) for k in _IMG_KEYS
                           if item.get(k) is not None}
                payload["patient_ids"] = item.get("patient_ids") or [
                    item.get("patient_id")]
                payload["features_encoding"] = "f32b64"
                url = hospital_base(hospital) + "/medical/infer_forward"
                try:
                    r = _session().post(url, json=payload, timeout=300)
                    if r.status_code == 404:      # worker older than v3.3
                        url = hospital_base(hospital) + "/medical/infer"
                        r = _session().post(url, json=payload, timeout=300)
                    if r.status_code == 422:
                        legacy = {k: item.get(k) for k in _IMG_KEYS
                                  if item.get(k) is not None}
                        legacy["patient_ids"] = payload["patient_ids"]
                        r = _session().post(
                            hospital_base(hospital) + "/medical/infer",
                            json=legacy, timeout=300)
                    r.raise_for_status()
                    data = r.json()
                    err = None
                except Exception as e:  # noqa: BLE001
                    data, err = None, str(e)[:140]
                q.put((i, data, (time.perf_counter() - t0) * 1000, err))

        def _front():
            with ThreadPoolExecutor(max_workers=parallel,
                                    thread_name_prefix="diag-front") as pool:
                list(pool.map(lambda pair: _front_one(*pair), list(enumerate(batch))))
            q.put(stop)

        def _back():
            while True:
                got = q.get()
                if got is stop:
                    break
                i, data, edge_ms, err = got
                rec = {"index": i, "edge_ms": round(edge_ms, 2),
                       "patient_id": (batch[i].get("patient_id")
                                      if i < len(batch) else None)}
                if data is None:
                    rec.update({"state": "failed", "error": err})
                    with lock:
                        records[i] = rec
                    continue
                if data.get("handoff") == "direct":
                    # edge -> cloud happened inside the worker call
                    rec.update({"state": "succeeded",
                                "edge_compute_ms": float(data.get("front_latency_ms") or 0.0),
                                "cloud_ms": float(data.get("cloud_rtt_ms") or 0.0),
                                "cloud_compute_ms": float(data.get("server_latency_ms") or 0.0),
                                "server": {"predictions": data.get("predictions"),
                                           "latency_ms": data.get("server_latency_ms")}})
                    with lock:
                        records[i] = rec
                    continue
                rec["edge_compute_ms"] = float(data.get("latency_ms") or 0.0)
                t0 = time.perf_counter()
                try:
                    r = _session().post(medical_server_url(), json={
                        "dce_features": data.get("dce_features"),
                        "dwi_features": data.get("dwi_features"),
                        "clinical_features": data.get("clinical_features"),
                        "radiomics_features": data.get("radiomics_features"),
                        "patient_ids": data.get("patient_ids"),
                        "encoding": data.get("encoding")}, timeout=300)
                    r.raise_for_status()
                    srv = r.json()
                    rec.update({"state": "succeeded", "server": srv})
                except Exception as e:  # noqa: BLE001
                    rec.update({"state": "failed", "error": str(e)[:140]})
                rec["cloud_ms"] = round((time.perf_counter() - t0) * 1000, 2)
                rec["cloud_compute_ms"] = float(
                    (rec.get("server") or {}).get("latency_ms") or 0.0)
                with lock:
                    records[i] = rec

        front = threading.Thread(target=_front, name="diag-front", daemon=True)
        back = threading.Thread(target=_back, name="diag-back", daemon=True)
        front.start()
        back.start()
        front.join()
        back.join()
        pipeline = (time.perf_counter() - start) * 1000

        ordered = [records.get(i, {"index": i, "state": "failed",
                                   "error": "no result"}) for i in range(n)]
        ok = [r for r in ordered if r.get("state") == "succeeded"]
        preds, failed = [], []
        for r in ordered:
            srv = r.get("server") or {}
            for p in (srv.get("predictions") or []):
                preds.append(p)
            if r.get("state") != "succeeded":
                failed.append({"index": r.get("index"),
                               "patient_id": r.get("patient_id"),
                               "error": r.get("error")})
        sum_edge = sum(float(r.get("edge_ms") or 0) for r in ordered)
        sum_cloud = sum(float(r.get("cloud_ms") or 0) for r in ordered)
        compute_total = sum(float(r.get("edge_compute_ms") or 0) +
                            float(r.get("cloud_compute_ms") or 0) for r in ordered)
        net_total = max(0.0, sum_edge + sum_cloud - compute_total)
        serial_ms = sum_edge + sum_cloud
        stages = [
            _stage(task_id, "worker", hospital, sum_edge,
                   f"批量前端 {n} 例（与云侧重叠执行）", hospital),
            _stage(task_id, "server", "datacenter", sum_cloud,
                   f"批量后端 {n} 例（与边端重叠执行）", "node3"),
        ]
        self._finish(task_id, start, {
            "initiator": self._initiator(task),
            "forwarded_to": hospital if is_clinic(source) else None,
            "stages": stages,
            "result_detail": {
                "batch_size": n,
                "predictions": preds,
                "bpCR_probability": (preds[0]["bpCR_probability"] if preds else None),
                "success": len(ok),
                "failed": failed,
                "per_patient": [{k: r.get(k) for k in (
                    "index", "patient_id", "state", "edge_ms", "cloud_ms",
                    "edge_compute_ms", "cloud_compute_ms", "error")}
                    for r in ordered],
                "edge_total_ms": round(sum_edge, 2),
                "cloud_total_ms": round(sum_cloud, 2),
                "serial_sum_ms": round(serial_ms, 2),
                "pipeline_speedup": round(serial_ms / pipeline, 3) if pipeline else 0.0,
                "pipelined": True,
                "parallel": parallel,
                "edge_share": round(sum(float(r.get("edge_compute_ms") or 0)
                                        for r in ordered)
                                    / max(1e-6, compute_total), 3),
            },
        }, "diagnosis", queue_wait_ms=queue_wait, extra_metrics={
            "worker_total_ms": round(sum_edge, 2),
            "server_total_ms": round(sum_cloud, 2),
            "compute_ms_total": round(compute_total, 2),
            "network_ms_total": round(net_total, 2),
            "pipeline_speedup": round(serial_ms / pipeline, 3) if pipeline else 0.0,
            "batch_size": n,
        })

    # ------------------------------------------------------- diagnosis -------
    def _run_diagnosis(self, task: dict):
        task_id = task["id"]
        source = task.get("source", "hospital-a")
        inp = task.get("input", {}) or {}
        if isinstance(inp.get("batch"), list) and inp["batch"]:
            self._run_diagnosis_batch(task, inp["batch"])
            return
        start = time.perf_counter()
        queue_wait = _queue_wait_ms(task)
        hospital = pick_hospital(source, inp.get("target_hospital"))
        forwarded = hospital if is_clinic(source) else None
        update_task(task_id, {"stage": "worker", "node": hospital})

        stages = []
        wstart = time.perf_counter()
        try:
            # ask the hospital for compact float32 features (v3.3): the legacy
            # nested-list payload made JSON encode/decode dominate the stage
            worker_payload = encode_input(inp)
            worker_payload.setdefault("features_encoding", "f32b64")
            # v3.3: the edge hands the intermediate features straight to the
            # cloud back-end (data plane = 1 hop); the scheduler stays in the
            # control plane instead of relaying megabytes twice.
            url = hospital_base(hospital) + "/medical/infer_forward"
            r = _session().post(url, json=worker_payload, timeout=300)
            if r.status_code == 404:
                url = hospital_base(hospital) + "/medical/infer"
                r = _session().post(url, json=worker_payload, timeout=300)
            if r.status_code == 422 and worker_payload is not inp:
                # A worker image older than v3.3 only accepts the legacy nested
                # float lists. Rolling updates mix versions, so degrade to the
                # legacy payload instead of failing the task.
                r = _session().post(hospital_base(hospital) + "/medical/infer",
                                    json=inp, timeout=300)
            r.raise_for_status()
            worker = r.json()
            if worker.get("handoff") == "direct":
                # predictions already came back from the cloud back-end
                server = {"predictions": worker.get("predictions"),
                          "bpCR_probability": worker.get("bpCR_probability"),
                          "latency_ms": worker.get("server_latency_ms")}
                worker_rtt = (time.perf_counter() - wstart) * 1000
                worker_compute = float(worker.get("front_latency_ms") or 0.0)
                stages.append(_stage(task_id, "worker", hospital, worker_rtt,
                                     "医院 Pod 医疗前端（直交云端后端）", hospital))
                pipeline = (time.perf_counter() - start) * 1000
                self._finish(task_id, start, {
                    "initiator": self._initiator(task),
                    "forwarded_to": forwarded,
                    "stages": stages + [_stage(task_id, "server", "datacenter",
                                               float(worker.get("cloud_rtt_ms") or 0.0),
                                               "DC medical-server（边端直连）", "node3")],
                    "result_detail": {
                        "predictions": server.get("predictions", []),
                        "bpCR_probability": server.get("bpCR_probability"),
                        "worker_latency_ms": worker.get("front_latency_ms"),
                        "server_latency_ms": worker.get("server_latency_ms"),
                        "handoff": "direct",
                    },
                }, "diagnosis", queue_wait_ms=queue_wait, extra_metrics={
                    "worker_total_ms": round(worker_rtt, 2),
                    "worker_compute_ms": round(worker_compute, 2),
                    "worker_network_ms": round(max(0.0, worker_rtt - worker_compute), 2),
                    "server_total_ms": round(float(worker.get("cloud_rtt_ms") or 0.0), 2),
                    "server_compute_ms": round(float(worker.get("server_latency_ms") or 0.0), 2),
                    "inter_stage_ms": 0.0,
                })
                return
        except Exception as e:
            fail_task(task_id, f"医院 worker 推理失败({hospital}): {e}")
            return
        worker_rtt = (time.perf_counter() - wstart) * 1000
        worker_compute = float(worker.get("latency_ms") or 0.0)
        worker_network = max(0.0, worker_rtt - worker_compute)
        stages.append(_stage(task_id, "worker", hospital, worker_rtt,
                             "医院 Pod 医疗前端", hospital))

        sstart = time.perf_counter()
        try:
            r = _session().post(
                medical_server_url(),
                json={"dce_features": worker.get("dce_features"),
                      "dwi_features": worker.get("dwi_features"),
                      "clinical_features": worker.get("clinical_features"),
                      "radiomics_features": worker.get("radiomics_features"),
                      "patient_ids": worker.get("patient_ids"),
                      # forwarded verbatim: list (legacy) or f32b64 dict
                      "encoding": worker.get("encoding")},
                timeout=300)
            r.raise_for_status()
            server = r.json()
        except Exception as e:
            fail_task(task_id, f"数据中心 medical-server 推理失败: {e}")
            return
        server_rtt = (time.perf_counter() - sstart) * 1000
        server_compute = float(server.get("latency_ms") or 0.0)
        server_network = max(0.0, server_rtt - server_compute)
        stages.append(_stage(task_id, "server", "datacenter", server_rtt,
                             "DC medical-server", "node3"))
        pipeline = (time.perf_counter() - start) * 1000
        inter = max(0.0, pipeline - worker_rtt - server_rtt)

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
        }, "diagnosis", queue_wait_ms=queue_wait, extra_metrics={
            "worker_total_ms": round(worker_rtt, 2),
            "worker_compute_ms": round(worker_compute, 2),
            "worker_network_ms": round(worker_network, 2),
            "inter_stage_ms": round(inter, 2),
            "server_total_ms": round(server_rtt, 2),
            "server_compute_ms": round(server_compute, 2),
            "server_network_ms": round(server_network, 2),
        })

    # ---------------------------------------------------------- compute ------
    def _run_compute(self, task: dict):
        """协同计算：把 N 个分区**同时**派发给 N 个协作方（发起端 + 数据中心 + 伙伴边缘端）。

        v3.3 修复：v3.2 及以前是 `for` 循环里逐个阻塞 POST，分区实际是串行执行的
        ——"协同"退化成了"排队 + 多次传输"，既拿不到并行收益又白付通信成本。
        现在用线程池并发派发，协同侧墙钟 ≈ 最慢分区（而不是分区之和），
        于是「本地单端串行 vs 多端并行」才是真正被测量的架构差异。
        """
        task_id = task["id"]
        source = task.get("source", "hospital-a")
        inp = task.get("input", {}) or {}
        start = time.perf_counter()
        queue_wait = _queue_wait_ms(task)

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

        def _dispatch(index: int, actor: str):
            pstart = time.perf_counter()
            try:
                if actor == "datacenter":
                    url = dc("/v3/compute")
                else:
                    url = self._entity_url(actor) + "/v3/compute"
                r = _session().post(url, json={
                    "rows": rows, "instruments": instruments,
                    "intensity": intensity, "seed": seed + index,
                    "partition": index, "count": len(partners)}, timeout=600)
                r.raise_for_status()
                res = r.json()
            except Exception as e:  # noqa: BLE001
                res = {"failed": True, "error": str(e)[:140],
                       "partition": index, "actor": actor}
            ms = (time.perf_counter() - pstart) * 1000
            res["ms"] = round(ms, 2)
            res.setdefault("actor", actor)
            return index, actor, res, ms

        dispatch_t0 = time.perf_counter()
        with ThreadPoolExecutor(max_workers=len(partners),
                                thread_name_prefix="partition") as pool:
            futures = [pool.submit(_dispatch, i, a)
                       for i, a in enumerate(partners)]
            dispatched = sorted((f.result() for f in futures), key=lambda x: x[0])
        dispatch_wall_ms = (time.perf_counter() - dispatch_t0) * 1000

        stages, partitions = [], []
        for index, actor, res, ms in dispatched:
            partitions.append(res)
            stages.append(_stage(task_id, "compute", actor, ms,
                                 detail=f"分区 {index + 1}/{len(partners)}（并行派发）",
                                 node=res.get("node", actor)))

        ok = [p for p in partitions if not p.get("failed")]
        cpu_ms = sum(float(p.get("cpu_ms", 0)) for p in ok)
        speedup = (cpu_ms / dispatch_wall_ms) if dispatch_wall_ms > 0 else 0.0
        self._finish(task_id, start, {
            "initiator": self._initiator(task),
            "stages": stages,
            "produced": {"instruments": instruments, "rows": rows,
                         "samples": instruments * rows},
            "partitions": partitions,
            "result_detail": {
                "partitions_ok": len(ok),
                "total_bytes": int(sum(p.get("bytes", 0) for p in ok)),
                "aggregate_cpu_ms": round(cpu_ms, 2),
                "dispatch_wall_ms": round(dispatch_wall_ms, 2),
                "parallel_speedup": round(speedup, 3),
                "partners": partners,
                "partition_count": len(partners),
                "checksums": [p.get("checksum", "") for p in ok],
            },
        }, "compute", queue_wait_ms=queue_wait, extra_metrics={
            "compute_ms_total": round(cpu_ms, 2),
            "dispatch_wall_ms": round(dispatch_wall_ms, 2),
            "parallel_speedup": round(speedup, 3),
            "network_ms_total": round(max(
                0.0, dispatch_wall_ms - max((float(p.get("cpu_ms", 0))
                                             for p in ok), default=0.0)), 2),
        })

    # ------------------------------------------------------------- sync ------
    def _run_sync(self, task: dict):
        task_id = task["id"]
        source = task.get("source", "clinic-1")
        inp = task.get("input", {}) or {}
        start = time.perf_counter()
        queue_wait = _queue_wait_ms(task)

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
        # v3.3: the data center keeps the chunk->holder map, so it can drive
        # `concurrency` parallel streams over *different* sources (cloud master
        # plus several edge peers), each with its own link. `bandwidth_mbps` is
        # charged per source link - the point of coordinating the fetch is to
        # aggregate several uplinks instead of draining one. Local execution has
        # no such map and fetches sequentially over a single link (see 设计方案 §12).
        streams = max(1, int(concurrency or 1))
        chunks: list = []
        ok_n = 0
        pulled_bytes = 0
        pull_lock = threading.Lock()

        def _pull_one(idx: int, mid: str):
            nonlocal ok_n, pulled_bytes
            # each stream rotates over the peer list, so parallel streams use
            # different holders instead of queueing behind one of them
            peer = peers[idx % len(peers)]
            started = time.perf_counter()
            try:
                if peer["entity"] == "datacenter":
                    r = s.get(peer["url"] + mid, timeout=30)
                else:
                    r = s.get(peer["url"] + "/v3/sync/chunk/" + mid, timeout=30)
                r.raise_for_status()
                item = r.json()
            except Exception:  # noqa: BLE001
                try:  # fall back to cloud master
                    r = s.get(dc("/db/item/") + mid, timeout=30)
                    r.raise_for_status()
                    item = r.json()
                    peer = {"entity": "datacenter", "url": dc("/db/item/")}
                except Exception as e:  # noqa: BLE001
                    with pull_lock:
                        chunks.append({"id": mid, "ok": False,
                                       "peer": peer["entity"],
                                       "error": str(e)[:100]})
                    return
            kb = int(item.get("size", 0)) / 1024.0
            cost_ms = kb * delay_per_kb * 1000
            time.sleep(max(0.0, min(1.5, cost_ms / 1000)))
            with pull_lock:
                chunks.append({"id": mid, "ok": True, "peer": peer["entity"],
                               "size": item.get("size"), "hash": item.get("hash"),
                               "ms": round((time.perf_counter() - started) * 1000
                                           + cost_ms, 2)})
                ok_n += 1
                pulled_bytes += int(item.get("size", 0))

        t0 = time.perf_counter()
        if streams > 1 and len(missing) > 1:
            with ThreadPoolExecutor(max_workers=min(streams, len(missing)),
                                    thread_name_prefix="sync") as pool:
                list(pool.map(lambda pair: _pull_one(*pair),
                              list(enumerate(missing))))
        else:
            for idx, mid in enumerate(missing):
                _pull_one(idx, mid)
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
        tu = time.perf_counter()
        try:
            up = s.post(dc("/db/upload"), json={"items": pushed}, timeout=30).json()
            upload_ms = (time.perf_counter() - tu) * 1000
        except Exception as e:
            fail_task(task_id, f"云端上传失败: {e}")
            return
        tb = time.perf_counter()
        try:
            backup = s.post(dc("/db/backup"), json={}, timeout=30).json()
            backup_ms = (time.perf_counter() - tb) * 1000
        except Exception as e:
            fail_task(task_id, f"云端备份失败: {e}")
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
                "streams": streams,
                "chunks": sorted(chunks, key=lambda c: str(c.get("id")))[:80],
                "uploaded": up.get("accepted", 0),
                "cloud_total": up.get("total_uploaded", 0),
                "upload_ms": round(upload_ms, 2),
                "backup_ms": round(backup_ms, 2),
                "backup": backup,
            },
        }, "sync", queue_wait_ms=queue_wait, extra_metrics={
            "pull_ms": round(pull_ms, 2),
            "upload_ms": round(upload_ms, 2),
            "backup_ms": round(backup_ms, 2),
            "streams": streams,
        })

    # ---------------------------------------------------------- routine ------
    # v3.3: this pipeline used to create, poll (3s granularity) and delete each
    # Job strictly one after another, so N jobs cost N x (create + wait +
    # delete) - ~13s of pure orchestration for two near-instant jobs, which made
    # the collaborative mode look absurd next to inlined local execution. The
    # Jobs are now created, polled and deleted concurrently and spread over the
    # free edge nodes plus the data center, i.e. "按节点空闲调度" finally does
    # what it says: the queue is distributed across machines instead of being
    # serialised behind one orchestrator.
    @staticmethod
    def _routine_nodes(jobs_n: int):
        """Round-robin the jobs over free edge nodes and the data center."""
        targets = list(usage.free_edge_nodes())      # node1/node2 by free score
        targets.append("node3")                      # 数据中心也参与（云边端）
        return [targets[i % len(targets)] for i in range(jobs_n)]

    def _one_routine_job(self, task_id: str, index: int, node: str, args: dict) -> dict:
        job_name = f"routine-{task_id[-12:]}-{index}"
        jr = {"index": index, "job": job_name, "node": node, "state": "failed"}
        jwall = time.perf_counter()
        if not kubeops.create_routine_job(job_name, node, args):
            jr["error"] = "Job 创建失败"
            jr["wall_ms"] = round((time.perf_counter() - jwall) * 1000, 2)
            return jr
        created_ms = round((time.perf_counter() - jwall) * 1000, 2)
        deadline = time.time() + 240
        state = "timeout"
        while time.time() < deadline:
            st = kubeops.job_status(job_name)
            if st["state"] == "succeeded":
                state = "succeeded"
                jr.update({"output": _last_json(kubeops.read_pod_log(
                    st.get("pod_name", ""))), "pod": st.get("pod_name")})
                break
            if st["state"] == "failed":
                state = "failed"
                break
            time.sleep(0.2)          # was 3s: polling granularity was visible
        jr["state"] = state
        kubeops.delete_job(job_name)
        jr["deleted"] = True
        jr["created_ms"] = created_ms
        jr["wall_ms"] = round((time.perf_counter() - jwall) * 1000, 2)
        return jr

    @staticmethod
    def _routine_targets(jobs_n: int):
        """Idle-node capacity for 日常 jobs: one worker per node (云 + 边 + 端).

        v3.3: "按节点空闲调度" is realised by dispatching the jobs to the pods
        already running on the freest nodes, in parallel - the ephemeral-Job
        executor below pays a ~4-6s pod cold start per job, which only pays off
        for very large batches. Both executors stay available (`executor`),
        because the Job path is what makes 日常 serverless-style.
        """
        by_node = {}
        for ent in tuple(HOSPITALS) + tuple(CLINICS):
            by_node.setdefault(ENTITY_NODES.get(ent, "?"), []).append(ent)
        ordered_nodes = list(usage.free_edge_nodes()) + ["node3"]
        targets = []
        for node in ordered_nodes:
            for ent in by_node.get(node, []):
                if ent not in targets:
                    targets.append(ent)
        for node, ents in by_node.items():          # anything left over
            for ent in ents:
                if ent not in targets:
                    targets.append(ent)
        targets.append("datacenter")                # 数据中心也作为空闲算力
        return targets

    def _one_warm_job(self, index: int, actor: str, args: dict) -> dict:
        url = (dc("/v3/compute") if actor == "datacenter"
               else self._entity_url(actor) + "/v3/compute")
        t0 = time.perf_counter()
        rec = {"index": index, "job": f"routine-{index}", "actor": actor,
               "state": "failed"}
        try:
            r = _session().post(url, json=args, timeout=600)
            r.raise_for_status()
            out = r.json()
            rec.update({"state": "succeeded", "output": out,
                        "node": out.get("node", actor)})
        except Exception as e:  # noqa: BLE001
            rec["error"] = str(e)[:140]
        rec["wall_ms"] = round((time.perf_counter() - t0) * 1000, 2)
        return rec

    def _run_routine_warm(self, task: dict, jobs_n: int, rows: int,
                          intensity: int, seed_base: int, start: float,
                          queue_wait: float):
        task_id = task["id"]
        targets = self._routine_targets(jobs_n)
        assign = [targets[i % len(targets)] for i in range(jobs_n)]
        update_task(task_id, {"stage": "routine"})
        dispatch_t0 = time.perf_counter()
        with ThreadPoolExecutor(max_workers=jobs_n,
                                thread_name_prefix="routine") as pool:
            futures = [
                pool.submit(self._one_warm_job, i, assign[i],
                            {"rows": rows, "instruments": 3,
                             "intensity": intensity, "seed": seed_base + i,
                             "partition": i, "count": jobs_n})
                for i in range(jobs_n)
            ]
            job_rows = sorted((f.result() for f in futures),
                              key=lambda r: r["index"])
        dispatch_wall_ms = (time.perf_counter() - dispatch_t0) * 1000
        ok = [j for j in job_rows if j.get("state") == "succeeded"]
        cpu_ms = sum(float(((j.get("output") or {}).get("cpu_ms") or 0)) for j in ok)
        by_actor: Dict[str, list] = {}
        for j in job_rows:
            by_actor.setdefault(j["actor"], []).append(j)
        stages = [_stage(task_id, "routine", actor,
                         max((j.get("wall_ms") or 0) for j in rows_same),
                         f"{len(rows_same)} 个日常作业（并行）",
                         (rows_same[0].get("node") or actor))
                  for actor, rows_same in by_actor.items()]
        stages.insert(0, _stage(task_id, "routine", "scheduler", dispatch_wall_ms,
                                f"并发派发 {jobs_n} 个日常作业到 "
                                f"{len(by_actor)} 个空闲节点", "node3"))
        self._finish(task_id, start, {
            "initiator": self._initiator(task),
            "stages": stages,
            "result_detail": {
                "executor": "warm",
                "jobs": job_rows,
                "succeeded": len(ok),
                "nodes_used": sorted({j["actor"] for j in job_rows}),
                "orchestration_wall_ms": round(dispatch_wall_ms, 2),
                "aggregate_cpu_ms": round(cpu_ms, 2),
                "parallel_dispatch": True,
                "no_kubernetes_jobs": True,
            },
        }, "routine", queue_wait_ms=queue_wait, extra_metrics={
            "compute_ms_total": round(cpu_ms, 2),
            "dispatch_wall_ms": round(dispatch_wall_ms, 2),
            "network_ms_total": round(max(0.0, dispatch_wall_ms - cpu_ms), 2),
        })

    def _run_routine(self, task: dict):
        task_id = task["id"]
        inp = task.get("input", {}) or {}
        start = time.perf_counter()
        queue_wait = _queue_wait_ms(task)

        if not kubeops.available():
            fail_task(task_id, "Kubernetes Job 能力不可用")
            return
        jobs_n = max(1, int(inp.get("jobs", 2)))
        rows = int(inp.get("rows", 128))
        intensity = int(inp.get("intensity", 30))
        # v3.2: an explicit seed makes routine output comparable across modes
        # (collaborative Jobs vs inlined local jobs).
        seed_base = int(inp.get("seed", 7))
        if (inp.get("executor") or "warm").lower() != "job":
            self._run_routine_warm(task, jobs_n, rows, intensity, seed_base,
                                   start, queue_wait)
            return
        nodes = self._routine_nodes(jobs_n)

        update_task(task_id, {"stage": "routine"})
        dispatch_t0 = time.perf_counter()
        with ThreadPoolExecutor(max_workers=jobs_n,
                                thread_name_prefix="routine") as pool:
            futures = [
                pool.submit(self._one_routine_job, task_id, i, nodes[i],
                            {"rows": rows, "instruments": 3, "intensity": intensity,
                             "seed": seed_base + i, "partition": i, "count": jobs_n})
                for i in range(jobs_n)
            ]
            job_rows = sorted((f.result() for f in futures),
                              key=lambda r: r["index"])
        dispatch_wall_ms = (time.perf_counter() - dispatch_t0) * 1000

        ok = [j for j in job_rows if j.get("state") == "succeeded"]
        job_cpu_ms = sum(float(((j.get("output") or {}).get("cpu_ms") or 0))
                         for j in ok)
        nodes_used = sorted({j["node"] for j in job_rows})
        stages = [_stage(task_id, "routine", f"job@{n}", 0.0, "", n)
                  for n in nodes_used]
        for st, n in zip(stages, nodes_used):
            st["ms"] = round(max((j.get("wall_ms") or 0) for j in job_rows
                                 if j["node"] == n), 2)
            st["detail"] = (f"{sum(1 for j in job_rows if j['node'] == n)} 个一次性 Job"
                            f"（并行）")
        stages.insert(0, _stage(task_id, "routine", "scheduler", dispatch_wall_ms,
                                f"并发派发 {jobs_n} 个一次性 Job 到 "
                                f"{len(nodes_used)} 个节点", "node3"))
        self._finish(task_id, start, {
            "initiator": self._initiator(task),
            "stages": stages,
            "result_detail": {
                "jobs": job_rows,
                "succeeded": len(ok),
                "nodes_used": nodes_used,
                "executor": "job",
                "orchestration_wall_ms": round(dispatch_wall_ms, 2),
                "aggregate_cpu_ms": round(job_cpu_ms, 2),
                "parallel_dispatch": True,
            },
        }, "routine", queue_wait_ms=queue_wait, extra_metrics={
            "compute_ms_total": round(job_cpu_ms, 2),
            "dispatch_wall_ms": round(dispatch_wall_ms, 2),
            "network_ms_total": round(max(0.0, dispatch_wall_ms - job_cpu_ms), 2),
        })

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
