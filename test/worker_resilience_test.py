#!/usr/bin/env python3
"""worker 韧性回归测试：领取任务失败 ≠ 放弃任务。

真实踩到的坑：测试方案运行里有一条子 run 卡在 `running`、4/5 完成、剩 1 条
永远 `pending`。原因是 worker 的等待条件写成"还有**未到期**的任务才继续等"，
于是当 `claim_next_attempt` 因为 SQLite 锁冲突短暂返回 None（任务其实已到期）时，
worker 直接跳出循环收尾，那条 pending 就被永久遗弃了。

正确行为：只要还有 pending 就必须继续等（等到期 / 等锁释放），
只有在"到期却长时间领不到"时才如实标记失败，让 run 能收尾且问题可见。

用法：
    python test/worker_resilience_test.py
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
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="worker-"))
    os.environ["DB_PATH"] = str(tmp / "w.db")

    import config
    import db
    import engine

    db.init(str(tmp / "w.db"))
    config.update({"pod_urls": {"clinic-1": "http://clinic-1:8000"}})

    executed = []

    def fake_execute(attempt, run, cfg):
        executed.append(int(attempt["attempt_index"]))
        time.sleep(0.05)
        return {"status": "completed", "client_total_ms": 10.0,
                "finished_at": db.now(), "error": None}

    engine._execute_attempt = fake_execute

    fails = []

    # ---------------------------------------------------------------- 场景 1
    # 前 6 次领取直接"失败"（模拟锁冲突），任务本身早已到期。
    real_claim = db.claim_next_attempt
    state = {"fails": 6, "calls": 0}

    def flaky_claim(run_id):
        state["calls"] += 1
        if state["fails"] > 0:
            state["fails"] -= 1
            return None
        return real_claim(run_id)

    db.claim_next_attempt = flaky_claim
    run_id = "run-flaky"
    db.create_run({"run_id": run_id, "kind": "compute", "source": "clinic-1",
                   "mode": "local", "repeats": 3, "concurrency": 1,
                   "status": "queued"})
    db.create_attempts(run_id, 3, {"kind": "compute", "source": "clinic-1",
                                   "mode_requested": "local"})
    engine._spawn_workers(run_id, 1)
    for _ in range(600):   # 容器内 CPU 受限时留足余量
        c = db.count_attempts(run_id)
        if not c["pending"] and not c["running"]:
            break
        time.sleep(0.1)

    c = db.count_attempts(run_id)
    run = db.get_run(run_id, parse=False)
    print(f"① 领取失败 6 次后：完成 {c['completed']}/{c['total']} · "
          f"pending={c['pending']} running={c['running']} · run={run['status']} · "
          f"实际执行 {sorted(executed)}")
    if c["completed"] != 3 or c["pending"] or c["running"]:
        fails.append(f"领取短暂失败导致任务被遗弃（completed={c['completed']}, "
                     f"pending={c['pending']}）")
    if run["status"] != "completed":
        fails.append(f"run 未能收尾（status={run['status']}）")

    # ---------------------------------------------------------------- 场景 2
    # 任务还没到"产生"时刻：worker 必须等，不能提前收尾。
    db.claim_next_attempt = real_claim
    executed2 = []
    engine._execute_attempt = lambda a, r, c: (
        executed2.append(int(a["attempt_index"])),
        {"status": "completed", "client_total_ms": 10.0,
         "finished_at": db.now(), "error": None})[1]

    run2 = "run-window"
    db.create_run({"run_id": run2, "kind": "compute", "source": "clinic-1",
                   "mode": "local", "repeats": 2, "concurrency": 1,
                   "status": "queued"})
    future = [(time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() + 2))
               + ".000+00:00")]
    db.create_attempts(run2, 2, {"kind": "compute", "source": "clinic-1",
                                 "mode_requested": "local"},
                       not_before=[future[0], None])
    engine._spawn_workers(run2, 1)
    time.sleep(0.8)
    mid = db.count_attempts(run2)
    mid_run = db.get_run(run2, parse=False)
    print(f"② 窗口未到 0.8s 时：完成 {mid['completed']}/{mid['total']} · "
          f"pending={mid['pending']} · run={mid_run['status']}（应为 running，不能提前 completed）")
    if mid_run["status"] != "running":
        fails.append(f"窗口未到时 run 被提前收尾（status={mid_run['status']}）")

    for _ in range(600):   # 容器内 CPU 受限时留足余量
        c = db.count_attempts(run2)
        if not c["pending"] and not c["running"]:
            break
        time.sleep(0.1)
    c2 = db.count_attempts(run2)
    print(f"   等到期后：完成 {c2['completed']}/{c2['total']} · "
          f"执行顺序 {sorted(executed2)}")
    if c2["completed"] != 2:
        fails.append(f"窗口到期后任务未全部执行（completed={c2['completed']}）")

    # ---------------------------------------------------------------- 场景 3
    # 一直领不到（永久故障）：必须如实标记失败并收尾，不能挂着。
    state2 = {"n": 0}

    def dead_claim(run_id):
        state2["n"] += 1
        return None

    db.claim_next_attempt = dead_claim
    run3 = "run-dead"
    db.create_run({"run_id": run3, "kind": "compute", "source": "clinic-1",
                   "mode": "local", "repeats": 1, "concurrency": 1,
                   "status": "queued"})
    db.create_attempts(run3, 1, {"kind": "compute", "source": "clinic-1",
                                 "mode_requested": "local"})
    engine._spawn_workers(run3, 1)
    t0 = time.time()
    for _ in range(600):
        c = db.count_attempts(run3)
        if not c["pending"] and not c["running"]:
            break
        time.sleep(0.1)
    c3 = db.count_attempts(run3)
    run3row = db.get_run(run3, parse=False)
    dt = time.time() - t0
    print(f"③ 永久领不到：{dt:.1f}s 后 completed={c3['completed']} "
          f"failed={c3['failed']} pending={c3['pending']} · run={run3row['status']}")
    if c3["pending"]:
        fails.append("永久领不到时仍留下 pending（应标记失败）")
    if run3row["status"] != "failed":
        fails.append(f"永久领不到时 run 应 failed，实为 {run3row['status']}")

    db.claim_next_attempt = real_claim
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)

    print()
    if fails:
        print("❌ worker 韧性回归未通过：")
        for f in fails:
            print("  -", f)
        return 1
    print("✅ worker 韧性回归通过（短暂失败不遗弃、窗口内耐心等、永久故障如实收尾）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
