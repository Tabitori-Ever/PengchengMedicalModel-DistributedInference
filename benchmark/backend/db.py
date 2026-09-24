"""SQLite storage for the benchmark site (stdlib sqlite3 only).

Tables
------
runs      one benchmark experiment (kind/source/params/mode/repeats/concurrency)
attempts  one row per single execution, fields per 设计方案 §6.2 (+ raw payload columns)
settings  runtime config overrides (PUT /api/config), key -> JSON text
patients  cached patient inputs so diagnosis works while the scheduler is down

Robustness notes
----------------
* WAL journal mode + ``busy_timeout`` so uvicorn's threadpool workers can write
  concurrently; connections are opened per call (isolation_level=None, explicit
  BEGIN IMMEDIATE for the claim transaction).
* Attempt claiming is atomic, which is what makes ``POST /api/runs/{id}/cancel``
  ("cancel not-yet-started attempts") race-free.
"""
import contextlib
import json
import os
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

DEFAULT_DB_PATH = "/data/bench/bench.db"

_DB_PATH: str = os.getenv("DB_PATH", DEFAULT_DB_PATH)
_init_lock = threading.Lock()
_initialized = False

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id          TEXT PRIMARY KEY,
    kind            TEXT NOT NULL,
    source          TEXT NOT NULL,
    mode            TEXT NOT NULL,
    repeats         INTEGER NOT NULL DEFAULT 1,
    concurrency     INTEGER NOT NULL DEFAULT 1,
    label           TEXT,
    force_degraded  INTEGER NOT NULL DEFAULT 0,
    suite_id        TEXT,
    plan_run_id     TEXT,
    plan_phase      TEXT,
    plan_unit       TEXT,
    params_json     TEXT,
    status          TEXT NOT NULL DEFAULT 'queued',
    error           TEXT,
    created_at      TEXT,
    started_at      TEXT,
    finished_at     TEXT,
    cancelled_at    TEXT
);

CREATE TABLE IF NOT EXISTS attempts (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id              TEXT NOT NULL,
    attempt_index       INTEGER NOT NULL,
    kind                TEXT,
    source              TEXT,
    mode_requested      TEXT,
    mode_used           TEXT,
    degraded            INTEGER NOT NULL DEFAULT 0,
    degrade_reason      TEXT,
    orchestrator        TEXT,
    executor            TEXT,
    task_id             TEXT,
    client_total_ms     REAL,
    queue_wait_ms       REAL,
    pipeline_total_ms   REAL,
    compute_ms_total    REAL,
    network_ms_total    REAL,
    degrade_switch_ms   REAL,
    stages_json         TEXT,
    status              TEXT NOT NULL DEFAULT 'pending',
    error               TEXT,
    created_at          TEXT,
    finished_at         TEXT,
    bpcr_expected       REAL,
    bpcr_actual         REAL,
    -- raw / audit columns (not part of §6.2, kept so nothing is lost) --------
    spec_key            TEXT,
    params_json         TEXT,
    input_keys          TEXT,
    result_detail_json  TEXT,
    metrics_json        TEXT,
    envelope_json       TEXT,
    metrics_source      TEXT,
    detail_json         TEXT,
    not_before          TEXT,
    -- 任务保障：失败自动重传的记账（§ 成功率 100% 要求）------------------
    retry_count         INTEGER NOT NULL DEFAULT 0,  -- 该任务已重传几次
    retry_ms_total      REAL,                        -- 失败尝试累计浪费的墙钟
    retry_errors_json   TEXT,                        -- 每次失败的原因与耗时
    started_at          TEXT,
    updated_at          TEXT
);

CREATE INDEX IF NOT EXISTS idx_attempts_run      ON attempts(run_id, attempt_index);
CREATE INDEX IF NOT EXISTS idx_attempts_status   ON attempts(status);
CREATE INDEX IF NOT EXISTS idx_attempts_kind     ON attempts(kind, source, mode_used, degraded);
CREATE INDEX IF NOT EXISTS idx_attempts_spec     ON attempts(spec_key);
CREATE INDEX IF NOT EXISTS idx_attempts_created  ON attempts(created_at);

-- 注意：idx_runs_plan 建在 runs.plan_run_id 上，而老库的 runs 表还没有这一列。
-- 它必须放在 _migrate() 里、ALTER TABLE 之后建，否则 executescript(SCHEMA)
-- 会在升级既有数据库时抛 "no such column: plan_run_id"。

-- 测试方案运行（测试大纲 §5.4 / §5.5）：一次方案运行 = 若干子 run，
-- 按调度策略分阶段顺序执行（先本地、后协同），阶段内按时间窗随机产生任务。
CREATE TABLE IF NOT EXISTS plan_runs (
    plan_run_id     TEXT PRIMARY KEY,
    plan_id         TEXT NOT NULL,
    plan_name       TEXT,
    snapshot_json   TEXT,
    strategies_json TEXT,
    status          TEXT NOT NULL DEFAULT 'queued',
    current_phase   TEXT,
    phases_json     TEXT,
    error           TEXT,
    label           TEXT,
    created_at      TEXT,
    started_at      TEXT,
    finished_at     TEXT
);

CREATE INDEX IF NOT EXISTS idx_plan_runs_created ON plan_runs(created_at);

CREATE TABLE IF NOT EXISTS settings (
    key         TEXT PRIMARY KEY,
    value       TEXT,
    updated_at  TEXT
);

CREATE TABLE IF NOT EXISTS patients (
    patient_id  TEXT PRIMARY KEY,
    bpcr        REAL,
    hospital    TEXT,
    input_json  TEXT,
    origin      TEXT,
    updated_at  TEXT
);
"""

# columns of the attempts table in §6.2 order (used by exports)
ATTEMPT_FIELDS: List[str] = [
    "id", "run_id", "attempt_index", "kind", "source", "mode_requested",
    "mode_used", "degraded", "degrade_reason", "orchestrator", "executor",
    "task_id", "client_total_ms", "queue_wait_ms", "pipeline_total_ms",
    "compute_ms_total", "network_ms_total", "degrade_switch_ms", "stages_json",
    "status", "error", "created_at", "finished_at", "bpcr_expected",
    "bpcr_actual",
]


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def new_run_id() -> str:
    return "run-" + datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S") + "-" + uuid.uuid4().hex[:6]


def db_path() -> str:
    return _DB_PATH


@contextlib.contextmanager
def connect(path: Optional[str] = None):
    """Per-call connection: autocommit (isolation_level=None) + WAL + row dicts."""
    target = path or _DB_PATH
    directory = os.path.dirname(os.path.abspath(target))
    if directory:
        os.makedirs(directory, exist_ok=True)
    con = sqlite3.connect(target, timeout=20.0, isolation_level=None)
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA busy_timeout=20000")
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=NORMAL")
    except sqlite3.Error:
        pass
    try:
        yield con
    finally:
        con.close()


def init(path: Optional[str] = None) -> str:
    """Create the schema (idempotent) and remember the db path."""
    global _DB_PATH, _initialized
    with _init_lock:
        if path:
            _DB_PATH = path
        with connect(_DB_PATH) as con:
            con.executescript(SCHEMA)
            _migrate(con)
        _initialized = True
    return _DB_PATH


def _migrate(con: sqlite3.Connection) -> None:
    """把既有数据库补到当前 SCHEMA（幂等、附列式升级）。

    做法：先在内存库里按当前 SCHEMA 建一份"应有结构"，再比出真实库缺哪些列。
    这样迁移列表**不可能与 SCHEMA 漂移**——手写列清单一旦漏一列，老库升级后
    就会在 INSERT/UPDATE 时抛 `table attempts has no column named X`
    （或索引建在还不存在的列上，启动直接失败）。
    """
    try:
        ref = sqlite3.connect(":memory:")
        ref.row_factory = sqlite3.Row
        ref.executescript(SCHEMA)
        for table in ("runs", "attempts"):
            have = {r["name"] for r in con.execute(f"PRAGMA table_info({table})")}
            if not have:
                continue                      # 表本身由 SCHEMA 新建，无需补列
            for row in ref.execute(f"PRAGMA table_info({table})"):
                name, decl = row["name"], (row["type"] or "TEXT")
                if name in have or name == "id":
                    continue                  # id 是主键，ALTER 不能补
                ddl = decl
                if row["notnull"] and row["dflt_value"] is None:
                    ddl += " DEFAULT ''" if "INT" not in decl.upper() else " DEFAULT 0"
                try:
                    con.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")
                except sqlite3.Error:
                    pass
        ref.close()
    except sqlite3.Error:
        pass

    # 索引一律在补列之后建：老库的 runs 原本没有 plan_run_id，
    # 建在 SCHEMA 里的索引会让 executescript 在升级既有库时直接失败。
    for stmt in ("CREATE INDEX IF NOT EXISTS idx_runs_plan ON runs(plan_run_id)",
                 "CREATE INDEX IF NOT EXISTS idx_plan_runs_created ON plan_runs(created_at)",
                 "CREATE INDEX IF NOT EXISTS idx_attempts_created ON attempts(created_at)"):
        try:
            con.execute(stmt)
        except sqlite3.Error:
            pass


def _json_loads(raw: Any, default: Any = None) -> Any:
    if raw is None:
        return default
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return default


def row_attempt(row: sqlite3.Row, parse: bool = True) -> Dict[str, Any]:
    d = dict(row)
    d["degraded"] = bool(d.get("degraded"))
    if parse:
        d["stages"] = _json_loads(d.pop("stages_json", None), [])
        d["metrics"] = _json_loads(d.get("metrics_json"), {}) or {}
        d["result_detail"] = _json_loads(d.get("result_detail_json"), {}) or {}
        d["envelope"] = _json_loads(d.get("envelope_json"), None)
        d["params"] = _json_loads(d.get("params_json"), {}) or {}
        d["detail"] = _json_loads(d.get("detail_json"), {}) or {}
        d["retry_errors"] = _json_loads(d.get("retry_errors_json"), []) or []
    return d


def row_run(row: sqlite3.Row, parse: bool = True) -> Dict[str, Any]:
    d = dict(row)
    d["force_degraded"] = bool(d.get("force_degraded"))
    if parse:
        d["params"] = _json_loads(d.get("params_json"), {}) or {}
    return d


# --------------------------------------------------------------------------- #
# runs                                                                        #
# --------------------------------------------------------------------------- #
def create_run(run: Dict[str, Any]) -> Dict[str, Any]:
    with connect() as con:
        con.execute(
            """INSERT INTO runs (run_id, kind, source, mode, repeats, concurrency,
                                 label, force_degraded, params_json, suite_id,
                                 plan_run_id, plan_phase, plan_unit,
                                 status, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (run["run_id"], run["kind"], run["source"], run["mode"],
             int(run["repeats"]), int(run["concurrency"]), run.get("label"),
             1 if run.get("force_degraded") else 0,
             json.dumps(run.get("params") or {}, ensure_ascii=False),
             run.get("suite_id"), run.get("plan_run_id"), run.get("plan_phase"),
             run.get("plan_unit"), run.get("status", "queued"), now()))
    return get_run(run["run_id"], parse=False)  # type: ignore[return-value]


def get_run(run_id: str, parse: bool = True) -> Optional[Dict[str, Any]]:
    with connect() as con:
        row = con.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
    return row_run(row, parse) if row else None


def update_run(run_id: str, fields: Dict[str, Any]) -> None:
    if not fields:
        return
    cols = ", ".join(f"{k}=?" for k in fields)
    with connect() as con:
        con.execute(f"UPDATE runs SET {cols} WHERE run_id=?",
                    list(fields.values()) + [run_id])


def list_runs(limit: int = 50) -> List[Dict[str, Any]]:
    with connect() as con:
        rows = con.execute(
            "SELECT * FROM runs ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (max(1, int(limit)),)).fetchall()
    return [row_run(r) for r in rows]


def delete_run(run_id: str) -> int:
    with connect() as con:
        con.execute("DELETE FROM attempts WHERE run_id=?", (run_id,))
        cur = con.execute("DELETE FROM runs WHERE run_id=?", (run_id,))
        return cur.rowcount or 0


# --------------------------------------------------------------------------- #
# plan runs (测试方案 · 测试大纲 §5.4 / §5.5)                                   #
# --------------------------------------------------------------------------- #
def new_plan_run_id() -> str:
    return ("plan-" + datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
            + "-" + uuid.uuid4().hex[:6])


def row_plan_run(row: sqlite3.Row, parse: bool = True) -> Dict[str, Any]:
    d = dict(row)
    if parse:
        d["snapshot"] = _json_loads(d.pop("snapshot_json", None), {}) or {}
        d["strategies"] = _json_loads(d.pop("strategies_json", None), []) or []
        d["phases"] = _json_loads(d.pop("phases_json", None), []) or []
    return d


def create_plan_run(plan: Dict[str, Any]) -> Dict[str, Any]:
    with connect() as con:
        con.execute(
            """INSERT INTO plan_runs (plan_run_id, plan_id, plan_name,
                                      snapshot_json, strategies_json, status,
                                      current_phase, phases_json, label,
                                      created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (plan["plan_run_id"], plan["plan_id"], plan.get("plan_name"),
             json.dumps(plan.get("snapshot") or {}, ensure_ascii=False),
             json.dumps(plan.get("strategies") or [], ensure_ascii=False),
             plan.get("status", "queued"), plan.get("current_phase"),
             json.dumps(plan.get("phases") or [], ensure_ascii=False),
             plan.get("label"), now()))
    return get_plan_run(plan["plan_run_id"])  # type: ignore[return-value]


def get_plan_run(plan_run_id: str, parse: bool = True) -> Optional[Dict[str, Any]]:
    with connect() as con:
        row = con.execute("SELECT * FROM plan_runs WHERE plan_run_id=?",
                          (plan_run_id,)).fetchone()
    return row_plan_run(row, parse) if row else None


def update_plan_run(plan_run_id: str, fields: Dict[str, Any]) -> None:
    if not fields:
        return
    fields = dict(fields)
    for key in ("snapshot", "strategies", "phases"):
        if key in fields:
            fields[f"{key}_json"] = json.dumps(fields.pop(key), ensure_ascii=False)
    cols = ", ".join(f"{k}=?" for k in fields)
    with connect() as con:
        con.execute(f"UPDATE plan_runs SET {cols} WHERE plan_run_id=?",
                    list(fields.values()) + [plan_run_id])


def list_plan_runs(limit: int = 50) -> List[Dict[str, Any]]:
    with connect() as con:
        rows = con.execute(
            "SELECT * FROM plan_runs ORDER BY created_at DESC LIMIT ?",
            (max(1, int(limit)),)).fetchall()
    return [row_plan_run(r) for r in rows]


def runs_for_plan(plan_run_id: str) -> List[Dict[str, Any]]:
    with connect() as con:
        rows = con.execute(
            """SELECT * FROM runs WHERE plan_run_id=?
               ORDER BY plan_phase, kind, source""", (plan_run_id,)).fetchall()
    return [row_run(r) for r in rows]


# --------------------------------------------------------------------------- #
# attempts                                                                    #
# --------------------------------------------------------------------------- #
def create_attempts(run_id: str, count: int, base: Dict[str, Any],
                    not_before: Optional[List[str]] = None) -> int:
    """Create `count` pending attempts; `not_before[i]` delays the i-th one.

    Test-plan runs (§5.4/§5.5) require tasks to be *produced randomly inside a
    time window* rather than all at once, so each attempt carries the earliest
    wall-clock instant at which a worker may claim it.
    """
    ts = now()
    rows = []
    for i in range(int(count)):
        due = None
        if not_before is not None and i < len(not_before):
            due = not_before[i]
        rows.append((run_id, i, base.get("kind"), base.get("source"),
                     base.get("mode_requested"), base.get("spec_key"),
                     json.dumps(base.get("params") or {}, ensure_ascii=False),
                     base.get("bpcr_expected"), ts, ts, due))
    with connect() as con:
        con.executemany(
            """INSERT INTO attempts (run_id, attempt_index, kind, source,
                                     mode_requested, spec_key, params_json,
                                     bpcr_expected, created_at, updated_at,
                                     not_before, status)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,'pending')""", rows)
    return len(rows)


def create_attempt_plan(run_id: str, items: List[Dict[str, Any]],
                        not_before: Optional[List[str]] = None) -> int:
    """Create attempts that each carry their *own* params (fixed-suite runs).

    `create_attempts` repeats one spec N times; a suite run needs the 20 frozen
    specs side by side in a single run so they can be compared as one block.
    """
    ts = now()
    rows = []
    for i, item in enumerate(items):
        due = None
        if not_before is not None and i < len(not_before):
            due = not_before[i]
        rows.append((run_id, i, item.get("kind"), item.get("source"),
                     item.get("mode_requested"), item.get("spec_key"),
                     json.dumps(item.get("params") or {}, ensure_ascii=False),
                     item.get("bpcr_expected"), ts, ts, due))
    with connect() as con:
        con.executemany(
            """INSERT INTO attempts (run_id, attempt_index, kind, source,
                                     mode_requested, spec_key, params_json,
                                     bpcr_expected, created_at, updated_at,
                                     not_before, status)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,'pending')""", rows)
    return len(rows)


def run_ids_for_suite(suite_id: str, limit: int = 200) -> List[str]:
    """Runs created from a fixed suite (newest first)."""
    with connect() as con:
        rows = con.execute(
            """SELECT run_id FROM runs WHERE suite_id=?
               ORDER BY created_at DESC LIMIT ?""", (suite_id, int(limit))).fetchall()
    return [r["run_id"] for r in rows]


def attempts_for_runs(run_ids: List[str], limit: int = 4000) -> List[Dict[str, Any]]:
    """Finished/pending attempts of the given runs (used by suite comparison)."""
    if not run_ids:
        return []
    marks = ",".join("?" for _ in run_ids)
    with connect() as con:
        rows = con.execute(
            f"""SELECT * FROM attempts WHERE run_id IN ({marks})
                ORDER BY id DESC LIMIT ?""",
            list(run_ids) + [int(limit)]).fetchall()
    return [row_attempt(r) for r in rows]


def claim_next_attempt(run_id: str) -> Optional[Dict[str, Any]]:
    """Atomically move the oldest *due* pending attempt of a live run to 'running'.

    Attempts created by a test-plan run carry `not_before` (the instant at which
    the task is "produced"); ones still in the future are skipped so the worker
    keeps waiting instead of draining the run.
    """
    con = sqlite3.connect(_DB_PATH, timeout=20.0, isolation_level=None)
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA busy_timeout=20000")
        con.execute("BEGIN IMMEDIATE")
        run = con.execute("SELECT status FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if run is None or run["status"] not in ("queued", "running"):
            con.execute("COMMIT")
            return None
        row = con.execute(
            """SELECT * FROM attempts WHERE run_id=? AND status='pending'
                 AND (not_before IS NULL OR not_before<=?)
               ORDER BY attempt_index LIMIT 1""", (run_id, now())).fetchone()
        if row is None:
            con.execute("COMMIT")
            return None
        cur = con.execute(
            """UPDATE attempts SET status='running', started_at=?, updated_at=?
               WHERE id=? AND status='pending'""", (now(), now(), row["id"]))
        if not cur.rowcount:
            con.execute("COMMIT")
            return None
        con.execute("COMMIT")
        fresh = con.execute("SELECT * FROM attempts WHERE id=?", (row["id"],)).fetchone()
        return row_attempt(fresh)
    except Exception:
        try:
            con.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise
    finally:
        con.close()


def requeue_attempt(attempt_id: int, retry_count: int, retry_ms_total: float,
                    retry_errors_json: str, not_before: Optional[str] = None) -> int:
    """把一次失败的任务放回 pending 以便重传。

    保留同一行（同一个 attempt_index = 同一个逻辑任务），只累加重传计数与
    失败耗时，并把 `not_before` 推到退避之后；这样：
      * 任务成功率按"任务"而不是"尝试"统计，重传后成功仍算成功；
      * 失败尝试浪费的时间记在 `retry_ms_total` 里，不会被悄悄抹掉。
    """
    fields = ["status='pending'", "retry_count=?", "retry_ms_total=?",
              "retry_errors_json=?", "error=NULL", "started_at=NULL",
              "finished_at=NULL", "updated_at=?"]
    values: List[Any] = [int(retry_count), float(retry_ms_total or 0.0),
                         retry_errors_json, now()]
    if not_before:
        fields.append("not_before=?")
        values.append(not_before)
    values.append(int(attempt_id))
    with connect() as con:
        cur = con.execute(
            f"UPDATE attempts SET {', '.join(fields)} WHERE id=?", values)
        return cur.rowcount or 0


def count_waiting_attempts(run_id: str) -> int:
    """Pending attempts whose `not_before` is still in the future."""
    with connect() as con:
        row = con.execute(
            """SELECT COUNT(*) AS c FROM attempts
               WHERE run_id=? AND status='pending'
                 AND not_before IS NOT NULL AND not_before>?""",
            (run_id, now())).fetchone()
    return int(row["c"] or 0)


def finish_attempt(attempt_id: int, fields: Dict[str, Any]) -> int:
    fields = dict(fields)
    fields["updated_at"] = now()
    fields.setdefault("finished_at", now())
    cols = ", ".join(f"{k}=?" for k in fields)
    with connect() as con:
        cur = con.execute(f"UPDATE attempts SET {cols} WHERE id=?",
                          list(fields.values()) + [int(attempt_id)])
        return cur.rowcount or 0


def cancel_pending_attempts(run_id: str, reason: str = "cancelled before start") -> int:
    ts = now()
    with connect() as con:
        cur = con.execute(
            """UPDATE attempts SET status='cancelled', error=?, finished_at=?,
                                   updated_at=?
               WHERE run_id=? AND status='pending'""",
            (reason[:300], ts, ts, run_id))
        return cur.rowcount or 0


def get_attempts(run_id: str) -> List[Dict[str, Any]]:
    with connect() as con:
        rows = con.execute(
            "SELECT * FROM attempts WHERE run_id=? ORDER BY attempt_index",
            (run_id,)).fetchall()
    return [row_attempt(r) for r in rows]


def count_attempts(run_id: str) -> Dict[str, int]:
    with connect() as con:
        rows = con.execute(
            "SELECT status, COUNT(*) AS n FROM attempts WHERE run_id=? GROUP BY status",
            (run_id,)).fetchall()
    out: Dict[str, int] = {"total": 0, "pending": 0, "running": 0,
                           "completed": 0, "failed": 0, "cancelled": 0}
    for r in rows:
        out[str(r["status"])] = int(r["n"])
        out["total"] += int(r["n"])
    return out


def query_attempts(kind: Optional[str] = None, source: Optional[str] = None,
                   mode: Optional[str] = None, run_id: Optional[str] = None,
                   status: Optional[str] = None,
                   degraded: Optional[bool] = None,
                   spec_key: Optional[str] = None,
                   limit: Optional[int] = 100,
                   order: str = "DESC",
                   include_pending: bool = False) -> List[Dict[str, Any]]:
    """Filtered attempt rows (newest first by default)."""
    where: List[str] = []
    args: List[Any] = []
    if kind:
        where.append("kind=?"); args.append(kind)
    if source:
        where.append("source=?"); args.append(source)
    if run_id:
        where.append("run_id=?"); args.append(run_id)
    if status:
        where.append("status=?"); args.append(status)
    if spec_key:
        where.append("spec_key=?"); args.append(spec_key)
    if degraded is not None:
        where.append("degraded=?"); args.append(1 if degraded else 0)
    if mode:
        if mode == "degraded":
            where.append("degraded=1")
        elif mode in ("collaborative", "local"):
            where.append("mode_used=?")
            args.append(mode)
        else:  # requested mode filter (also matches pending rows)
            where.append("(mode_requested=? OR mode_used=?)")
            args.extend([mode, mode])
    if not include_pending:
        where.append("status IN ('completed','failed','cancelled')")
    sql = "SELECT * FROM attempts"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id " + ("DESC" if order.upper() == "DESC" else "ASC")
    if limit:
        sql += " LIMIT ?"
        args.append(int(limit))
    with connect() as con:
        rows = con.execute(sql, args).fetchall()
    return [row_attempt(r) for r in rows]


def checksum_reference_rows(run_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """Collaborative compute attempts that produced checksums (correctness refs)."""
    sql = ("SELECT id, run_id, spec_key, mode_used, degraded, result_detail_json, "
           "status FROM attempts WHERE kind='compute' AND result_detail_json IS NOT NULL")
    args: List[Any] = []
    if run_id:
        sql += " AND run_id=?"
        args.append(run_id)
    with connect() as con:
        rows = con.execute(sql, args).fetchall()
    out = []
    for r in rows:
        out.append({"id": r["id"], "run_id": r["run_id"], "spec_key": r["spec_key"],
                    "mode_used": r["mode_used"], "degraded": bool(r["degraded"]),
                    "status": r["status"],
                    "result_detail": _json_loads(r["result_detail_json"], {}) or {}})
    return out


# --------------------------------------------------------------------------- #
# settings                                                                    #
# --------------------------------------------------------------------------- #
def get_settings() -> Dict[str, str]:
    with connect() as con:
        rows = con.execute("SELECT key, value FROM settings").fetchall()
    return {r["key"]: r["value"] for r in rows}


def set_settings(values: Dict[str, str]) -> None:
    ts = now()
    with connect() as con:
        con.executemany(
            """INSERT INTO settings (key, value, updated_at) VALUES (?,?,?)
               ON CONFLICT(key) DO UPDATE SET value=excluded.value,
                                              updated_at=excluded.updated_at""",
            [(k, v, ts) for k, v in values.items()])


def clear_settings() -> None:
    with connect() as con:
        con.execute("DELETE FROM settings")


# --------------------------------------------------------------------------- #
# patients                                                                    #
# --------------------------------------------------------------------------- #
def upsert_patients(patients: Iterable[Dict[str, Any]], origin: str = "file") -> int:
    ts = now()
    rows = []
    for p in patients:
        pid = str(p.get("patient_id") or p.get("id") or "").strip()
        if not pid:
            continue
        rows.append((pid, p.get("bpCR", p.get("bpcr")), p.get("hospital"),
                     json.dumps(p.get("input") or {}, ensure_ascii=False),
                     origin, ts))
    if not rows:
        return 0
    with connect() as con:
        con.executemany(
            """INSERT INTO patients (patient_id, bpcr, hospital, input_json,
                                     origin, updated_at)
               VALUES (?,?,?,?,?,?)
               ON CONFLICT(patient_id) DO UPDATE SET bpcr=excluded.bpcr,
                   hospital=excluded.hospital, input_json=excluded.input_json,
                   origin=excluded.origin, updated_at=excluded.updated_at""", rows)
    return len(rows)


def list_patients(limit: int = 500, with_input: bool = False) -> List[Dict[str, Any]]:
    with connect() as con:
        rows = con.execute(
            "SELECT * FROM patients ORDER BY patient_id LIMIT ?",
            (max(1, int(limit)),)).fetchall()
    out = []
    for r in rows:
        inp = _json_loads(r["input_json"], {}) or {}
        item = {"patient_id": r["patient_id"], "bpCR": r["bpcr"],
                "hospital": r["hospital"], "origin": r["origin"],
                "has_input": bool(inp), "input_keys": sorted(inp.keys())}
        if with_input:
            item["input"] = inp
        out.append(item)
    return out


def get_patient(patient_id: str) -> Optional[Dict[str, Any]]:
    with connect() as con:
        r = con.execute("SELECT * FROM patients WHERE patient_id=?",
                        (str(patient_id),)).fetchone()
    if not r:
        return None
    return {"patient_id": r["patient_id"], "bpCR": r["bpcr"],
            "hospital": r["hospital"], "origin": r["origin"],
            "input": _json_loads(r["input_json"], {}) or {}}


def count_patients() -> int:
    with connect() as con:
        r = con.execute("SELECT COUNT(*) AS n FROM patients").fetchone()
    return int(r["n"]) if r else 0


def patient_origins() -> Dict[str, int]:
    with connect() as con:
        rows = con.execute(
            "SELECT origin, COUNT(*) AS n FROM patients GROUP BY origin").fetchall()
    return {str(r["origin"]): int(r["n"]) for r in rows}


# --------------------------------------------------------------------------- #
# startup housekeeping                                                        #
# --------------------------------------------------------------------------- #
def recover_interrupted() -> Dict[str, int]:
    """Fail attempts/runs left non-terminal by a previous process (restart)."""
    ts = now()
    msg = "interrupted by backend restart"
    with connect() as con:
        att = con.execute(
            """UPDATE attempts SET status='failed', error=?, finished_at=?,
                                   updated_at=?
               WHERE status IN ('pending','running')""", (msg, ts, ts,))
        run = con.execute(
            """UPDATE runs SET status='failed', error=?, finished_at=?
               WHERE status IN ('queued','running')""", (msg, ts))
    return {"attempts": att.rowcount or 0, "runs": run.rowcount or 0}
