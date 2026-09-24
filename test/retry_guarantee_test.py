#!/usr/bin/env python3
"""任务保障回归测试：失败自动重传，成功率要求 100%。

覆盖：
1. **瞬时失败被重传后成功**：按概率/计数器制造失败，最终 100% 完成；
2. **重传代价被如实记账**（`retry_count` / `retry_ms_total` / `retry_errors`），
   不能把失败尝试的时间悄悄抹掉；
3. **重传额度用尽后如实判失败**（不假装成功）；
4. **方案声明的重传额度优先于全局默认**；
5. **判定用 `complete_gte 100%`**：全部完成才 pass。

用法：
    python test/retry_guarantee_test.py
"""
import os
import pathlib
import sys
import tempfile
import time

HERE = pathlib.Path(__file__).resolve().parent
BACKEND = HERE.parent / "benchmark" / "backend"
sys.path.insert(0, str(BACKEND))


def main() -> int:
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="retry-"))
    os.environ["DB_PATH"] = str(tmp / "r.db")

    import config
    import db
    import engine

    db.init(str(tmp / "r.db"))
    config.update({"pod_urls": {"clinic-1": "http://clinic-1:8000"},
                   "attempt_retries": 3, "retry_backoff_ms": 50})

    fails = []

    def wait(run_id, limit=600):   # 容器内 CPU 受限时留足余量
        for _ in range(limit):
            c = db.count_attempts(run_id)
            if not c["pending"] and not c["running"]:
                return c
            time.sleep(0.1)
        return db.count_attempts(run_id)

    # ---------------------------------------------------------------- 场景 1+2
    # 每个任务的第 1 次执行必失败，第 2 次成功 → 重传后应 100% 完成。
    tries = {}

    def flaky(attempt, run, cfg):
        idx = int(attempt["attempt_index"])
        tries[idx] = tries.get(idx, 0) + 1
        if tries[idx] == 1:
            time.sleep(0.02)
            return {"status": "failed", "client_total_ms": 20.0,
                    "error": "transient DNS failure", "finished_at": db.now()}
        return {"status": "completed", "client_total_ms": 50.0,
                "finished_at": db.now(), "error": None}

    engine._execute_attempt = flaky
    run_id = "run-flaky"
    db.create_run({"run_id": run_id, "kind": "sync", "source": "clinic-1",
                   "mode": "local", "repeats": 5, "concurrency": 2,
                   "status": "queued"})
    db.create_attempts(run_id, 5, {"kind": "sync", "source": "clinic-1",
                                   "mode_requested": "local"})
    engine._spawn_workers(run_id, 2)
    c = wait(run_id)
    run = db.get_run(run_id, parse=False)
    rows = db.get_attempts(run_id)
    print(f"① 首次必失败、重传后成功：完成 {c['completed']}/{c['total']} · "
          f"失败 {c['failed']} · run={run['status']}")
    print(f"   重传次数 {sum(int(r['retry_count'] or 0) for r in rows)} · "
          f"失败耗时合计 {sum(float(r['retry_ms_total'] or 0) for r in rows):.0f}ms · "
          f"执行次数 {sorted(tries.values())}")
    if c["completed"] != 5 or c["failed"]:
        fails.append(f"重传后仍未全部成功（completed={c['completed']}, failed={c['failed']}）")
    if run["status"] != "completed":
        fails.append(f"重传后 run 状态应为 completed，实为 {run['status']}")
    if sum(int(r["retry_count"] or 0) for r in rows) != 5:
        fails.append("retry_count 记账不对（应为 5 次重传）")
    if sum(float(r["retry_ms_total"] or 0) for r in rows) < 100:
        fails.append("retry_ms_total 未记录失败尝试的耗时（重传代价被抹掉）")
    hist = [r for r in rows if r.get("retry_errors")]
    if not hist or "transient DNS failure" not in str(hist[0].get("retry_errors")):
        fails.append("retry_errors 未保留失败原因")
    # 成功那次时延是 50ms，重传代价单独记 → 两者不能混
    if any(abs(float(r["client_total_ms"] or 0) - 50.0) > 0.01 for r in rows):
        fails.append("client_total_ms 应为成功那次的时延（50ms）")

    # ---------------------------------------------------------------- 场景 3
    # 永远失败 + 额度 2 → 用尽后必须如实判失败。
    config.update({"attempt_retries": 2})
    always = {"n": 0}

    def dead(attempt, run, cfg):
        always["n"] += 1
        return {"status": "failed", "client_total_ms": 5.0,
                "error": "permanent failure", "finished_at": db.now()}

    engine._execute_attempt = dead
    run2 = "run-dead"
    db.create_run({"run_id": run2, "kind": "sync", "source": "clinic-1",
                   "mode": "local", "repeats": 1, "concurrency": 1,
                   "status": "queued"})
    db.create_attempts(run2, 1, {"kind": "sync", "source": "clinic-1",
                                 "mode_requested": "local"})
    engine._spawn_workers(run2, 1)
    c2 = wait(run2)
    r2 = db.get_attempts(run2)[0]
    attempts_made = always["n"]
    print(f"② 永久失败 + 额度 2：执行 {attempts_made} 次 · "
          f"retry_count={r2['retry_count']} · 最终 {r2['status']} · run={db.get_run(run2, parse=False)['status']}")
    if attempts_made != 3:
        fails.append(f"额度 2 应共执行 3 次（首次+2 重传），实际 {attempts_made}")
    if r2["status"] != "failed":
        fails.append("额度用尽后应如实判失败，不能假装成功")
    if c2["failed"] != 1:
        fails.append("额度用尽后 failed 计数应为 1")

    # ---------------------------------------------------------------- 场景 4
    # 全局默认 0，但方案（run 级 _retries）声明 2 → 应以 run 级为准。
    config.update({"attempt_retries": 0})
    tries2 = {}

    def flaky2(attempt, run, cfg):
        idx = int(attempt["attempt_index"])
        tries2[idx] = tries2.get(idx, 0) + 1
        if tries2[idx] == 1:
            return {"status": "failed", "client_total_ms": 5.0,
                    "error": "first try fails", "finished_at": db.now()}
        return {"status": "completed", "client_total_ms": 7.0,
                "finished_at": db.now(), "error": None}

    engine._execute_attempt = flaky2
    run3 = "run-runlevel"
    db.create_run({"run_id": run3, "kind": "sync", "source": "clinic-1",
                   "mode": "local", "repeats": 2, "concurrency": 1,
                   "params": {"_retries": 2}, "status": "queued"})
    db.create_attempts(run3, 2, {"kind": "sync", "source": "clinic-1",
                                 "mode_requested": "local"})
    engine._spawn_workers(run3, 1)
    c3 = wait(run3)
    print(f"③ 全局额度 0 但 run 级声明 2：完成 {c3['completed']}/{c3['total']} · "
          f"执行次数 {sorted(tries2.values())}")
    if c3["completed"] != 2:
        fails.append("run 级重传额度未生效（应覆盖全局默认）")

    # ---------------------------------------------------------------- 场景 5
    # 判定：complete_gte 100% 必须全部完成才 pass。
    import plans
    spec = [c for c in plans.PLAN_COLLAB["criteria"] if c["id"] == "completion"][0]
    print(f"④ 判定规则：type={spec['type']} value={spec['value']}{spec['unit']} label={spec['label']}")
    if spec["type"] != "complete_gte" or float(spec["value"]) != 100.0:
        fails.append("完成率判定应为 complete_gte 100%")
    both = plans.PLAN_NETWORK["criteria"]
    if not any(c["type"] == "complete_gte" and float(c["value"]) == 100.0 for c in both):
        fails.append("5.5 的完成率判定也应为 100%")

    import shutil
    shutil.rmtree(tmp, ignore_errors=True)
    print()
    if fails:
        print("❌ 任务保障回归未通过：")
        for f in fails:
            print("  -", f)
        return 1
    print("✅ 任务保障回归通过（瞬时失败重传后 100%、代价如实记账、额度用尽如实判失败、"
          "run 级额度优先、判定为 100%）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
