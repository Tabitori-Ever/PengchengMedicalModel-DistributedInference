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
    started_at          TEXT,
    updated_at          TEXT
);

CREATE INDEX IF NOT EXISTS idx_attempts_run      ON attempts(run_id, attempt_index);
CREATE INDEX IF NOT EXISTS idx_attempts_status   ON attempts(status);
CREATE INDEX IF NOT EXISTS idx_attempts_kind     ON attempts(kind, source, mode_used, degraded);
CREATE INDEX IF NOT EXISTS idx_attempts_spec     ON attempts(spec_key);
CREATE INDEX IF NOT EXISTS idx_attempts_created  ON attempts(created_at);

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
    """Additive migrations for databases created by an earlier version."""
    try:
        cols = {r["name"] for r in con.execute("PRAGMA table_info(runs)")}
        if "suite_id" not in cols:
            con.execute("ALTER TABLE runs ADD COLUMN suite_id TEXT")
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
                                 status, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (run["run_id"], run["kind"], run["source"], run["mode"],
             int(run["repeats"]), int(run["concurrency"]), run.get("label"),
             1 if run.get("force_degraded") else 0,
             json.dumps(run.get("params") or {}, ensure_ascii=False),
             run.get("suite_id"), run.get("status", "queued"), now()))
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
# attempts                                                                    #
# --------------------------------------------------------------------------- #
def create_attempts(run_id: str, count: int, base: Dict[str, Any]) -> int:
    ts = now()
    rows = []
    for i in range(int(count)):
        rows.append((run_id, i, base.get("kind"), base.get("source"),
                     base.get("mode_requested"), base.get("spec_key"),
                     json.dumps(base.get("params") or {}, ensure_ascii=False),
                     base.get("bpcr_expected"), ts, ts))
    with connect() as con:
        con.executemany(
            """INSERT INTO attempts (run_id, attempt_index, kind, source,
                                     mode_requested, spec_key, params_json,
                                     bpcr_expected, created_at, updated_at,
                                     status)
               VALUES (?,?,?,?,?,?,?,?,?,?,'pending')""", rows)
    return len(rows)


def create_attempt_plan(run_id: str, items: List[Dict[str, Any]]) -> int:
    """Create attempts that each carry their *own* params (fixed-suite runs).

    `create_attempts` repeats one spec N times; a suite run needs the 20 frozen
    specs side by side in a single run so they can be compared as one block.
    """
    ts = now()
    rows = []
    for i, item in enumerate(items):
        rows.append((run_id, i, item.get("kind"), item.get("source"),
                     item.get("mode_requested"), item.get("spec_key"),
                     json.dumps(item.get("params") or {}, ensure_ascii=False),
                     item.get("bpcr_expected"), ts, ts))
    with connect() as con:
        con.executemany(
            """INSERT INTO attempts (run_id, attempt_index, kind, source,
                                     mode_requested, spec_key, params_json,
                                     bpcr_expected, created_at, updated_at,
                                     status)
               VALUES (?,?,?,?,?,?,?,?,?,?,'pending')""", rows)
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
    """Atomically move the oldest pending attempt of a live run to 'running'."""
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
               ORDER BY attempt_index LIMIT 1""", (run_id,)).fetchone()
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
