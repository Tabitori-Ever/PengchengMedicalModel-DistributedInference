"""Benchmark / performance-comparison site backend (benchmark-site v1.0).

Implements the `/api/*` contract of 设计方案 §6.1 for the cloud-edge-end
collaborative inference platform:

    GET    /api/health                 site + scheduler + pod reachability
    GET    /api/config                 current runtime config
    PUT    /api/config                 update config (force_degraded switch, urls, ...)
    GET    /api/patients               bundled patients incl. bpCR ground truth
    POST   /api/runs                   create experiment -> background execution
    GET    /api/runs?limit=50          experiment list with aggregate summary
    GET    /api/runs/{run_id}          experiment detail + per-attempt records
    POST   /api/runs/{run_id}/cancel   cancel not-yet-started attempts
    DELETE /api/runs/{run_id}          delete an experiment
    GET    /api/results?limit=100      recent attempt-level results (cross run)
    GET    /api/compare?kind=&source=&mode=
    GET    /api/compare/matrix         full (kind x source x mode) aggregate table
    GET    /api/export.csv             attempt-level export (UTF-8 BOM, Excel safe)
    GET    /api/export.json            attempt-level export (JSON)

Run:  uvicorn app:app --host 0.0.0.0 --port 8090
"""
import csv
import io
import json
import os
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

# allow `python -c "import app"` (and any cwd) to resolve the sibling modules
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from fastapi import FastAPI, HTTPException, Query, Request  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import JSONResponse, Response  # noqa: E402
from pydantic import BaseModel, ConfigDict  # noqa: E402

import config  # noqa: E402
import db  # noqa: E402
import engine  # noqa: E402
import stats  # noqa: E402
import suites as suites_mod  # noqa: E402

SERVICE = "benchmark-site-backend"
VERSION = "1.0"
DB_PATH = os.getenv("DB_PATH", "/data/bench/bench.db")


# --------------------------------------------------------------------------- #
# lifecycle                                                                   #
# --------------------------------------------------------------------------- #
@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init(DB_PATH)
    recovered = db.recover_interrupted()
    config.init()
    info = engine.load_patients()
    print(f"[benchmark-site] db={db.db_path()} recovered={recovered} "
          f"patients={info.get('count')} source={info.get('source')} "
          f"error={info.get('error')}", flush=True)
    yield


app = FastAPI(title="Benchmark Site - Kubernetes 云边端协同推理性能对比",
              version=VERSION, lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=False,
                   allow_methods=["*"], allow_headers=["*"])


# --------------------------------------------------------------------------- #
# request models                                                              #
# --------------------------------------------------------------------------- #
class RunRequest(BaseModel):
    """POST /api/runs body (§6.1 + §2 `mode`)."""
    model_config = ConfigDict(extra="allow")

    # 固定套件模式：给 suite_id 即可（20 个固定任务），其余字段作为可选覆盖
    suite_id: Optional[str] = None
    kind: Optional[str] = None
    source: Optional[str] = None
    params: Dict[str, Any] = {}
    mode: Optional[str] = None
    repeats: Optional[int] = None
    concurrency: Optional[int] = None
    label: Optional[str] = None
    force_degraded: Optional[bool] = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _limit(value: int, default: int, hi: int) -> int:
    if value is None:
        return default
    return max(1, min(int(value), hi))


# --------------------------------------------------------------------------- #
# health / config / patients                                                  #
# --------------------------------------------------------------------------- #
@app.get("/api/health")
def api_health(pods: int = Query(1, description="1=probe every pod, 0=skip pod probing")):
    return engine.health(probe_pods=bool(pods))


def _config_payload() -> Dict[str, Any]:
    cfg = config.current()
    cfg["patients_path"] = os.getenv("PATIENTS_PATH", "/app/patients.json")
    cfg["db_path"] = db.db_path()
    cfg["entities"] = list(config.ENTITIES)
    return cfg


@app.get("/api/config")
def api_get_config():
    return _config_payload()


@app.put("/api/config")
async def api_put_config(request: Request):
    try:
        body = await request.json()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"invalid JSON body: {exc}")
    try:
        config.update(body)
    except config.ConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return _config_payload()


@app.get("/api/patients")
def api_patients(refresh: int = Query(0, description="1=re-read PATIENTS_PATH / refetch"),
                 format: str = Query("full", description="full|list"),
                 limit: int = Query(500, ge=1, le=5000)):
    info = engine.patients_info(force=bool(refresh))
    patients = db.list_patients(limit=limit)
    if format == "list":
        return patients
    return {"count": info.get("count"), "source": info.get("source"),
            "path": info.get("path"), "origins": info.get("origins"),
            "error": info.get("error"), "patients": patients}


# --------------------------------------------------------------------------- #
# runs                                                                        #
# --------------------------------------------------------------------------- #
@app.post("/api/runs", status_code=201)
def api_create_run(body: RunRequest):
    payload = body.model_dump()
    try:
        if payload.get("suite_id"):
            return engine.create_suite_run(payload)
        return engine.create_run(payload)
    except engine.RunError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/suites")
def api_list_suites():
    """固定测试套件（同一套 20 个任务，便于反复对比）。"""
    items = suites_mod.list_suites()
    return {"count": len(items), "suites": items}


@app.get("/api/compare/suite")
def api_compare_suite(suite_id: str = Query(...), source: Optional[str] = None,
                      run_ids: Optional[str] = None):
    """按档位对比同一套件在两种调度策略下的表现。

    `run_ids`（逗号分隔）用于只统计指定运行——报告需要「本次采集」的干净数据。
    """
    ids = [x.strip() for x in (run_ids or "").split(",") if x.strip()]
    try:
        return engine.suite_compare(suite_id, source=source, run_ids=ids or None)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.get("/api/runs")
def api_list_runs(limit: int = Query(50, ge=1, le=1000),
                  kind: Optional[str] = None,
                  source: Optional[str] = None,
                  status: Optional[str] = None):
    runs = db.list_runs(limit=_limit(limit, 50, 1000))
    out: List[Dict[str, Any]] = []
    for run in runs:
        if kind and run.get("kind") != kind:
            continue
        if source and run.get("source") != source:
            continue
        if status and run.get("status") != status:
            continue
        attempts = db.query_attempts(run_id=run["run_id"], include_pending=True,
                                     limit=None, order="ASC")
        out.append({**run, "progress": db.count_attempts(run["run_id"]),
                    "attempts": len(attempts),
                    "summary": stats.summarize(attempts, run.get("kind"))})
    return {"count": len(out), "runs": out}


@app.get("/api/runs/{run_id}")
def api_get_run(run_id: str, attempt_limit: int = Query(2000, ge=1, le=20000)):
    detail = engine.get_run_detail(run_id, attempt_limit=attempt_limit)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")
    attempts = detail["attempts"]
    return {"run": detail["run"], "progress": detail["progress"],
            "summary": stats.summarize(attempts, detail["run"].get("kind")),
            "attempts": attempts}


@app.post("/api/runs/{run_id}/cancel")
def api_cancel_run(run_id: str):
    result = engine.cancel_run(run_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")
    return result


@app.delete("/api/runs/{run_id}")
def api_delete_run(run_id: str):
    result = engine.delete_run(run_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")
    return result


# --------------------------------------------------------------------------- #
# results / compare                                                           #
# --------------------------------------------------------------------------- #
@app.get("/api/results")
def api_results(limit: int = Query(100, ge=1, le=2000),
                kind: Optional[str] = None,
                source: Optional[str] = None,
                mode: Optional[str] = None,
                run_id: Optional[str] = None,
                status: Optional[str] = None,
                degraded: Optional[bool] = None,
                include_pending: int = Query(0)):
    rows = db.query_attempts(kind=kind, source=source, mode=mode, run_id=run_id,
                             status=status, degraded=degraded,
                             limit=_limit(limit, 100, 2000),
                             order="DESC",
                             include_pending=bool(include_pending))
    runs = {r["run_id"]: r for r in db.list_runs(limit=1000)}
    for row in rows:
        run = runs.get(row["run_id"])
        row["run_label"] = (run or {}).get("label")
        row["run_source"] = (run or {}).get("source")
    return {"count": len(rows), "results": rows}


def _compare_rows(kind: Optional[str], source: Optional[str],
                  mode: Optional[str], run_id: Optional[str],
                  degraded: Optional[bool], limit: int) -> List[Dict[str, Any]]:
    return db.query_attempts(kind=kind, source=source, mode=mode, run_id=run_id,
                             degraded=degraded, limit=_limit(limit, 5000, 20000),
                             order="DESC")


@app.get("/api/compare")
def api_compare(kind: Optional[str] = None,
                source: Optional[str] = None,
                mode: Optional[str] = None,
                run_id: Optional[str] = None,
                degraded: Optional[bool] = None,
                group_by: str = Query("mode", description="mode|spec"),
                limit: int = Query(5000, ge=1, le=20000)):
    rows = _compare_rows(kind, source, mode, run_id, degraded, limit)
    refs = stats.build_checksum_refs(db.checksum_reference_rows())
    fields = stats.GROUP_FIELDS
    if group_by == "spec":
        fields = ("kind", "source", "mode_used", "degraded", "spec_key")
    groups = stats.group_rows(rows, fields, refs=refs)
    return {"filters": {"kind": kind, "source": source, "mode": mode,
                        "run_id": run_id, "degraded": degraded,
                        "group_by": group_by, "limit": limit},
            "count": len(rows), "groups": groups}


@app.get("/api/compare/matrix")
def api_compare_matrix(kind: Optional[str] = None,
                       source: Optional[str] = None,
                       mode: Optional[str] = None,
                       run_id: Optional[str] = None,
                       degraded: Optional[bool] = None,
                       limit: int = Query(20000, ge=1, le=50000)):
    rows = _compare_rows(kind, source, mode, run_id, degraded, limit)
    refs = stats.build_checksum_refs(db.checksum_reference_rows())
    table = stats.matrix(rows, refs=refs)
    return {"filters": {"kind": kind, "source": source, "mode": mode,
                        "run_id": run_id, "degraded": degraded},
            "count": len(rows), "rows": table}


# --------------------------------------------------------------------------- #
# exports                                                                     #
# --------------------------------------------------------------------------- #
# §6.2 fields with Chinese-friendly headers (Excel/UTF-8 BOM)
CSV_COLUMNS = [
    ("id", "记录ID"),
    ("run_id", "实验ID"),
    ("attempt_index", "序号"),
    ("kind", "任务类型"),
    ("source", "发起实体"),
    ("mode_requested", "请求模式"),
    ("mode_used", "实际模式"),
    ("degraded", "是否降级"),
    ("degrade_reason", "降级原因"),
    ("orchestrator", "编排者"),
    ("executor", "执行者"),
    ("task_id", "任务ID"),
    ("client_total_ms", "端到端时延ms"),
    ("queue_wait_ms", "排队等待ms"),
    ("pipeline_total_ms", "流水线耗时ms"),
    ("compute_ms_total", "计算耗时ms"),
    ("network_ms_total", "网络耗时ms"),
    ("degrade_switch_ms", "降级切换ms"),
    ("status", "状态"),
    ("error", "错误"),
    ("created_at", "创建时间"),
    ("finished_at", "完成时间"),
    ("bpcr_expected", "bpCR真值"),
    ("bpcr_actual", "bpCR预测值"),
    ("stages_json", "阶段明细JSON"),
    ("metric_source", "指标来源"),
    ("run_label", "实验标签"),
    ("params_json", "参数JSON"),
    ("result_detail_json", "结果明细JSON"),
]


def _export_rows(kind: Optional[str], source: Optional[str], mode: Optional[str],
                 run_id: Optional[str], degraded: Optional[bool],
                 limit: Optional[int]) -> List[Dict[str, Any]]:
    rows = db.query_attempts(kind=kind, source=source, mode=mode, run_id=run_id,
                             degraded=degraded, limit=limit, order="DESC")
    runs = {r["run_id"]: r for r in db.list_runs(limit=5000)}
    for row in rows:
        row["run_label"] = (runs.get(row["run_id"]) or {}).get("label")
        row["params_json"] = json.dumps(row.get("params") or {}, ensure_ascii=False)
        row["result_detail_json"] = json.dumps(row.get("result_detail") or {},
                                               ensure_ascii=False)
        row["stages_json"] = json.dumps(row.get("stages") or [], ensure_ascii=False)
        row["metric_source"] = row.get("metrics_source")
    return rows


@app.get("/api/export.csv")
def api_export_csv(kind: Optional[str] = None,
                   source: Optional[str] = None,
                   mode: Optional[str] = None,
                   run_id: Optional[str] = None,
                   degraded: Optional[bool] = None,
                   limit: Optional[int] = Query(None, ge=1, le=100000)):
    rows = _export_rows(kind, source, mode, run_id, degraded, limit)
    buf = io.StringIO()
    writer = csv.writer(buf, dialect="excel", lineterminator="\r\n")
    writer.writerow([label for _, label in CSV_COLUMNS])
    for row in rows:
        line = []
        for key, _label in CSV_COLUMNS:
            value = row.get(key)
            if isinstance(value, bool):
                value = "true" if value else "false"
            elif value is None:
                value = ""
            line.append(value)
        writer.writerow(line)
    # utf-8-sig: BOM so Excel opens the Chinese headers correctly
    payload = buf.getvalue().encode("utf-8-sig")
    filename = f"bench_attempts_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}.csv"
    return Response(content=payload, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@app.get("/api/export.json")
def api_export_json(kind: Optional[str] = None,
                    source: Optional[str] = None,
                    mode: Optional[str] = None,
                    run_id: Optional[str] = None,
                    degraded: Optional[bool] = None,
                    limit: Optional[int] = Query(None, ge=1, le=100000),
                    compact: int = Query(0, description="1=omit raw envelope payload")):
    rows = _export_rows(kind, source, mode, run_id, degraded, limit)
    if compact:
        for row in rows:
            row.pop("envelope", None)
    return {"service": SERVICE, "version": VERSION, "generated_at": _now(),
            "filters": {"kind": kind, "source": source, "mode": mode,
                        "run_id": run_id, "degraded": degraded, "limit": limit},
            "count": len(rows), "attempts": rows}


# --------------------------------------------------------------------------- #
# error hardening: always JSON, never an HTML traceback                        #
# --------------------------------------------------------------------------- #
@app.exception_handler(Exception)
async def unhandled_exception_handler(_request: Request, exc: Exception):
    return JSONResponse(status_code=500,
                        content={"detail": f"{type(exc).__name__}: {exc}"[:500]})


# --------------------------------------------------------------------------- #
# static frontend: the site serves its own UI so that it keeps working (and can
# keep driving degraded local execution) while the scheduler pod is down.       #
# --------------------------------------------------------------------------- #
_STATIC_DIR = os.getenv("STATIC_DIR",
                        os.path.join(os.path.dirname(_HERE), "frontend", "dist"))
if os.path.isdir(_STATIC_DIR):
    from fastapi.staticfiles import StaticFiles  # noqa: E402

    # mounted last: the /api/* routes declared above win, everything else
    # (index.html + hashed assets) falls through to the built React app
    app.mount("/", StaticFiles(directory=_STATIC_DIR, html=True), name="site")
else:
    @app.get("/")
    def root_no_ui():
        return {"service": SERVICE, "version": VERSION, "status": "running",
                "ui": False, "api": "/api/health",
                "hint": "前端未打包：构建 benchmark/frontend 并设置 STATIC_DIR"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8090")))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8090")))
