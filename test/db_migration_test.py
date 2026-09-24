#!/usr/bin/env python3
"""迁移回归测试：用**旧版 schema** 建库，再用当前 db.init() 升级。

为什么需要它：测试方案的改动给 `runs` 加了 `plan_run_id/plan_phase/plan_unit`、
给 `attempts` 加了 `not_before`。如果新加的索引/语句写在 `_migrate()` 之前，
`executescript(SCHEMA)` 会在**已存在的老库**上抛
`sqlite3.OperationalError: no such column: plan_run_id`——而全新库永远测不出来
（线上正是这样启动失败的）。本脚本专门覆盖这条升级路径。

用法：
    python test/db_migration_test.py
"""
import os
import pathlib
import sqlite3
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
BACKEND = HERE.parent / "benchmark" / "backend"
sys.path.insert(0, str(BACKEND))

# 与本改动之前的线上版本一致的 runs / attempts 定义（有意不含新列）
LEGACY_SCHEMA = """
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
    status              TEXT NOT NULL DEFAULT 'pending',
    client_total_ms     REAL,
    spec_key            TEXT,
    params_json         TEXT,
    created_at          TEXT,
    finished_at         TEXT,
    updated_at          TEXT
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS patients (
    patient_id TEXT PRIMARY KEY, bpcr REAL, hospital TEXT,
    input_json TEXT, origin TEXT, updated_at TEXT
);
"""


def main() -> int:
    fails = []
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="dbmig-"))
    db_path = tmp / "legacy.db"

    con = sqlite3.connect(db_path)
    con.executescript(LEGACY_SCHEMA)
    con.execute("INSERT INTO runs (run_id, kind, source, mode, repeats, concurrency,"
                " status, created_at) VALUES ('run-old','compute','clinic-1','local',1,1,"
                " 'completed','2026-01-01T00:00:00.000+00:00')")
    con.execute("INSERT INTO attempts (run_id, attempt_index, kind, status,"
                " client_total_ms, created_at) VALUES ('run-old',0,'compute','completed',"
                " 12.5,'2026-01-01T00:00:00.000+00:00')")
    con.commit()
    con.close()
    print(f"① 建好旧版库：{db_path}")

    # 模拟线上启动：init() 会跑 SCHEMA + _migrate()
    import db
    try:
        db.init(str(db_path))
        print("② db.init() 升级成功")
    except Exception as exc:  # noqa: BLE001
        print(f"② db.init() 失败：{type(exc).__name__}: {exc}")
        return 1

    with db.connect() as c:
        run_cols = {r["name"] for r in c.execute("PRAGMA table_info(runs)")}
        att_cols = {r["name"] for r in c.execute("PRAGMA table_info(attempts)")}
        tables = {r["name"] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        indexes = {r["name"] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='index'")}

    for col in ("plan_run_id", "plan_phase", "plan_unit", "suite_id"):
        if col not in run_cols:
            fails.append(f"runs 缺列 {col}")
    if "not_before" not in att_cols:
        fails.append("attempts 缺列 not_before")
    if "plan_runs" not in tables:
        fails.append("缺 plan_runs 表")
    for idx in ("idx_runs_plan", "idx_plan_runs_created"):
        if idx not in indexes:
            fails.append(f"缺索引 {idx}")
    print(f"③ 新列/新表/新索引齐全：runs+{sorted({'plan_run_id','plan_phase','plan_unit'} & run_cols)} "
          f"attempts+{sorted({'not_before'} & att_cols)} 表+{sorted({'plan_runs'} & tables)}")

    # 老数据必须还在，且能正常读出
    old = db.get_run("run-old")
    if old is None or old.get("status") != "completed":
        fails.append("升级后老 run 数据丢失或状态改变")
    if old and old.get("plan_run_id") is not None:
        fails.append("老 run 的 plan_run_id 应为 NULL")
    rows = db.get_attempts("run-old")
    if not rows or rows[0].get("client_total_ms") != 12.5:
        fails.append("升级后老 attempt 数据丢失")
    print(f"④ 老数据完好：run-old status={old and old.get('status')} · "
          f"attempts={len(rows)} · client_total_ms={rows[0].get('client_total_ms') if rows else None}")

    # 升级后必须能真正用起来：建方案运行 + 带 not_before 的任务
    try:
        pr = db.create_plan_run({
            "plan_run_id": db.new_plan_run_id(), "plan_id": "plan-5.4-collab",
            "plan_name": "迁移后自检", "snapshot": {"generate_window_min": 0.05},
            "strategies": ["local"], "phases": [], "label": "迁移自检"})
        new_run = "run-new"
        db.create_run({"run_id": new_run, "kind": "compute", "source": "clinic-1",
                       "mode": "local", "repeats": 2, "concurrency": 1,
                       "plan_run_id": pr["plan_run_id"], "plan_phase": "local",
                       "plan_unit": "B·compute", "status": "queued"})
        db.create_attempts(new_run, 2, {"kind": "compute"},
                           not_before=["2099-01-01T00:00:00.000+00:00", None])
        kids = db.runs_for_plan(pr["plan_run_id"])
        waiting = db.count_waiting_attempts(new_run)
        due = db.claim_next_attempt(new_run)
        print(f"⑤ 升级后可正常使用：子运行 {len(kids)} 个 · 未到期 {waiting} 个 · "
              f"领取到 attempt_index={due and due.get('attempt_index')}")
        if len(kids) != 1:
            fails.append("plan_run 关联子运行失败")
        if waiting != 1:
            fails.append("not_before 未生效")
        if not due or due.get("attempt_index") != 1:
            fails.append("应领取已到期的那一条（index=1）")
    except Exception as exc:  # noqa: BLE001
        fails.append(f"升级后使用失败：{type(exc).__name__}: {exc}")

    import shutil
    shutil.rmtree(tmp, ignore_errors=True)
    print()
    if fails:
        print("❌ 迁移回归未通过：")
        for f in fails:
            print("  -", f)
        return 1
    print("✅ 迁移回归通过（旧库可平滑升级，老数据完好，新功能可用）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
