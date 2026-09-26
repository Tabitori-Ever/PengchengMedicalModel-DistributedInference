#!/usr/bin/env python3
"""长时间运行的断点续跑回归测试（站点重启不能毁掉整场测试）。

背景：测试方案的运行可以持续十几小时（大纲 5.5 = 10 小时产生窗口 × 两阶段顺序执行）。
旧实现里 `db.recover_interrupted()` 会在站点启动时把**所有 pending 任务判失败**——
只要站点 Pod 重启一次，整场测试就废了；而 20 小时的窗口期内重启是很有可能的。

要求：
1. 重启只收尾**真正执行到一半**（running）的任务，且是**放回待执行**而不是判失败
   （否则"成功率 100%"会因为一次重启破功）；
2. **等待中的任务（not_before 在未来）必须原样保留**；
3. 有剩余任务的 run 保持非终态，恢复后重新拉起 worker；
4. 方案运行能续跑：**复用已有子 run，不重复创建**，已完成的阶段直接跳过；
5. worker 在长窗口下不能空转狂查库（按到期时间自适应睡眠）。

用法：
    python test/restart_resume_test.py
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
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="resume-"))
    os.environ["DB_PATH"] = str(tmp / "r.db")

    import config
    import db
    import engine
    import plans

    db.init(str(tmp / "r.db"))
    config.update({"pod_urls": {"clinic-1": "http://clinic-1:8000"},
                   "attempt_retries": 1, "retry_backoff_ms": 10})

    fails = []
    done = []

    def fake(attempt, run, cfg):
        done.append((run["run_id"], int(attempt["attempt_index"])))
        return {"status": "completed", "client_total_ms": 5.0,
                "finished_at": db.now(), "error": None}

    engine._execute_attempt = fake

    # ------------------------------------------------- 1) 等待中的任务不能被杀
    run_id = "run-waiting"
    db.create_run({"run_id": run_id, "kind": "routine", "source": "clinic-1",
                   "mode": "local", "repeats": 2, "concurrency": 1,
                   "status": "queued"})
    future = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() + 3600)) + ".000+00:00"
    db.create_attempts(run_id, 2, {"kind": "routine", "source": "clinic-1"},
                       not_before=[future, None])
    # 模拟"一个任务执行到一半时进程被杀"
    db.claim_next_attempt(run_id)          # 领取 idx=1（已到期那条）→ running

    before = db.count_attempts(run_id)
    created = db.create_attempts.__name__  # noqa: F841  (保持导入被使用)
    rec = db.recover_interrupted()
    after = db.count_attempts(run_id)
    print(f"① 重启恢复：before={dict(before)} → after={dict(after)}  recovered={rec}")
    if after["failed"] != 0:
        fails.append("重启把 pending/running 任务判失败了（应放回待执行）")
    if after["pending"] != 2:
        fails.append(f"恢复后应有 2 条待执行，实为 {after['pending']}")
    run = db.get_run(run_id, parse=False)
    if run["status"] not in ("queued", "running"):
        fails.append(f"有剩余任务的 run 不该被收尾，实为 {run['status']}")

    # ------------------------------------------------- 2) 恢复后能继续跑完
    engine._spawn_workers(run_id, 1)       # 模拟 resume_after_restart 重新拉起
    time.sleep(0.5)
    c = db.count_attempts(run_id)
    print(f"   续跑后：{dict(c)}（未来那条到点前不会执行）")
    if c["completed"] != 1:
        fails.append(f"已到期的那条应被重跑完成，实为 {c['completed']}")

    # ------------------------------------------------- 3) 方案运行续跑不重复建子 run
    snap = plans.apply_overrides(plans.PLAN_COLLAB, {
        "generate_window_min": 0, "duration_min": 1,
        "strategies": ["collaborative"],
        "kinds": {k: {"per_device": 1} for k in
                  ("diagnosis", "compute", "sync", "routine")},
    })
    pr = db.create_plan_run({"plan_run_id": db.new_plan_run_id(),
                             "plan_id": "plan-5.4-collab", "plan_name": "续跑验证",
                             "snapshot": snap, "strategies": ["collaborative"],
                             "status": "running"})
    pid = pr["plan_run_id"]
    # 先让协调器建好子 run 并跑完
    thread = engine._PLAN_WORKERS  # noqa: F841
    engine._plan_coordinator(pid)
    kids1 = db.runs_for_plan(pid)
    st1 = engine.plan_run_public(pid)
    # 方案默认 2 台设备 × 4 类（协同阶段含诊断）= 8 个执行单元
    want_units = len(plans.expand_units(snap, phase="collaborative"))
    print(f"② 首次执行：子 run {len(kids1)} 个（应 {want_units}）· 状态 {st1['status']} · "
          f"完成 {st1['phases'][0]['completed']}/{st1['phases'][0]['tasks_total']}")
    if len(kids1) != want_units:
        fails.append(f"首次应建 {want_units} 个子 run，实为 {len(kids1)}")
    if st1["status"] != "completed":
        fails.append(f"首次执行应完成，实为 {st1['status']}")

    # 模拟重启后再次拉起协调器：必须复用子 run、不重复创建
    engine._plan_coordinator(pid)
    kids2 = db.runs_for_plan(pid)
    st2 = engine.plan_run_public(pid)
    print(f"   重启后再拉起：子 run {len(kids2)} 个（不得增加）· 状态 {st2['status']}")
    if len(kids2) != len(kids1):
        fails.append(f"续跑重复创建了子 run（{len(kids1)} → {len(kids2)}）")

    # ------------------------------------------------- 4) 半途重启：复用 + 继续跑
    snap2 = plans.apply_overrides(plans.PLAN_COLLAB, {
        "generate_window_min": 0, "duration_min": 1,
        "strategies": ["collaborative"],
        "kinds": {k: {"per_device": 2} for k in ("compute",)},
    })
    pr2 = db.create_plan_run({"plan_run_id": db.new_plan_run_id(),
                              "plan_id": "plan-5.4-collab", "plan_name": "半途重启",
                              "snapshot": snap2, "strategies": ["collaborative"],
                              "status": "running"})
    pid2 = pr2["plan_run_id"]
    # 手工只建一个子 run 并跑完一半，模拟"协调器中途被杀"
    unit = plans.expand_units(snap2, phase="collaborative")[0]
    engine._create_plan_unit_run(pid2, "collaborative", unit, [0.0, 0.0],
                                 config.current(), 1)
    kid = db.runs_for_plan(pid2)[0]["run_id"]
    db.claim_next_attempt(kid)             # 有一条执行到一半时被杀
    db.recover_interrupted()               # 重启恢复
    engine._plan_coordinator(pid2)         # 协调器续跑
    kids3 = db.runs_for_plan(pid2)
    st3 = engine.plan_run_public(pid2)
    ph3 = st3["phases"][0]
    print(f"③ 半途重启：子 run {len(kids3)} 个（不得增加，原为 1）· 状态 {st3['status']} · "
          f"完成 {ph3['completed']}/{ph3['tasks_total']}（该单元 repeats={unit['repeats']}）")
    if len(kids3) != 1:
        fails.append(f"半途重启重复创建子 run（{len(kids3)} 个）")
    # 只建了 1 个单元，续跑应把它跑完（不补建、也不重复建）
    if st3["status"] != "completed" or ph3["completed"] != ph3["tasks_total"]:
        fails.append(f"半途重启后未跑完：{st3['status']} "
                     f"{ph3['completed']}/{ph3['tasks_total']}")
    if ph3["completed"] != int(unit["repeats"]):
        fails.append(f"续跑完成的条数应等于该单元的 repeats（{unit['repeats']}），"
                     f"实为 {ph3['completed']}")

    # ------------------------------- 3.5) 阶段切换窗口的中断（最可能踩到的时刻）
    # 两阶段之间有一个"本地阶段已结束、协同阶段还没建"的窗口；若站点正好此时重启，
    # 恢复后必须：跳过已完成的本地阶段、只创建协同阶段，且**不能重跑本地阶段**
    # （重跑会白跑一遍并污染"总用时"对比）。
    snap4 = plans.apply_overrides(plans.PLAN_COLLAB, {
        "generate_window_min": 0, "duration_min": 1,
        "strategies": ["local", "collaborative"],
        "kinds": {k: {"per_device": 1} for k in
                  ("diagnosis", "compute", "sync", "routine")},
    })
    pr4 = db.create_plan_run({"plan_run_id": db.new_plan_run_id(),
                              "plan_id": "plan-5.4-collab", "plan_name": "阶段切换中断",
                              "snapshot": snap4,
                              "strategies": ["local", "collaborative"],
                              "status": "running"})
    pid4 = pr4["plan_run_id"]
    # 只把 local 阶段跑完（模拟"刚跑完本地就被杀"）
    for unit in plans.expand_units(snap4, phase="local"):
        engine._create_plan_unit_run(pid4, "local", unit, [0.0], config.current(), 1)
    for r in db.runs_for_plan(pid4):
        for _ in range(30):
            if not db.count_attempts(r["run_id"])["pending"]:
                break
            time.sleep(0.1)
    local_kids = [r["run_id"] for r in db.runs_for_plan(pid4)
                  if r["plan_phase"] == "local"]
    local_done_before = sum(int(db.count_attempts(r)["completed"]) for r in local_kids)
    db.recover_interrupted()
    engine._plan_coordinator(pid4)          # 恢复：应跳过 local、只建 collaborative
    after = db.runs_for_plan(pid4)
    by_phase = {}
    for r in after:
        by_phase.setdefault(r["plan_phase"], []).append(r)
    print(f"③.5 阶段切换中断：local 子 run {len(by_phase.get('local', []))} 个 · "
          f"collaborative 子 run {len(by_phase.get('collaborative', []))} 个 · "
          f"local 完成数 {local_done_before}（不得增加）")
    if len(by_phase.get("local", [])) != len(local_kids):
        fails.append("阶段切换恢复时重复创建了 local 子 run")
    want_collab = len(plans.expand_units(snap4, phase="collaborative"))
    if len(by_phase.get("collaborative", [])) != want_collab:
        fails.append(f"阶段切换恢复后协同阶段子 run 数不对"
                     f"（应 {want_collab}，实为 {len(by_phase.get('collaborative', []))}）")
    if sum(int(db.count_attempts(r)["completed"]) for r in local_kids) != local_done_before:
        fails.append("阶段切换恢复后 local 阶段被重跑（完成数变了）")
    st4 = engine.plan_run_public(pid4)
    if st4["status"] != "completed":
        fails.append(f"阶段切换恢复后方案未完成：{st4['status']}")
    if len(st4["phases"]) != 2:
        fails.append(f"阶段切换恢复后阶段数应为 2，实为 {len(st4['phases'])}")

    # ------------------------------------------------- 4.5) 协调器退避
    # 阶段长达 10 小时：协调器若固定 1s 轮询 6 个子 run，一夜要打几十万次查询。
    # 这里用一个"窗口很长、没有任何任务到期"的方案跑 10 秒，统计协调器查库次数。
    snap3 = plans.apply_overrides(plans.PLAN_COLLAB, {
        "generate_window_min": 600, "duration_min": 720,     # 大纲 5.5 的量级
        "strategies": ["collaborative"],
        "kinds": {k: {"per_device": 25} for k in
                  ("diagnosis", "compute", "sync", "routine")},
    })
    pr3 = db.create_plan_run({"plan_run_id": db.new_plan_run_id(),
                              "plan_id": "plan-5.5-network", "plan_name": "退避验证",
                              "snapshot": snap3, "strategies": ["collaborative"],
                              "status": "running"})
    pid3 = pr3["plan_run_id"]
    import threading as _th
    calls = {"coord": 0, "worker": 0}
    real_count = db.count_attempts

    def counting(run_id):
        # 按线程归属区分：方案运行里的 worker 也查库，混在一起看不出协调器是否退避
        if _th.current_thread().name.startswith("bench-plan-"):
            calls["coord"] += 1
        else:
            calls["worker"] += 1
        return real_count(run_id)

    db.count_attempts = counting
    th = _th.Thread(target=engine._plan_coordinator, args=(pid3,),
                    name=f"bench-plan-{pid3}", daemon=True)
    th.start()
    time.sleep(10.0)
    db.count_attempts = real_count
    kids_n = len(db.runs_for_plan(pid3))
    fixed = kids_n * 10            # 固定 1s 轮询在 10 秒内的次数
    print(f"④ 协调器退避：10 秒内协调器查库 {calls['coord']} 次"
          f"（固定 1s 轮询约 {fixed} 次）· worker 查库 {calls['worker']} 次"
          f"（{kids_n} 个子 run，按到期时间睡眠）")
    # 协调器必须明显退避：首轮 + 1s + 2s + 4s + 8s ≈ 5 轮 × kids_n
    if calls["coord"] > kids_n * 6:
        fails.append(f"协调器未退避：10 秒查库 {calls['coord']} 次（阈值 {kids_n * 6}）")
    # worker 也不能空转：按到期时间睡 5s，10 秒最多每 run 约 3 次
    if calls["worker"] > kids_n * 5:
        fails.append(f"worker 未按到期睡眠：10 秒查库 {calls['worker']} 次"
                     f"（阈值 {kids_n * 5}）")
    engine.cancel_plan_run(pid3)

    # ------------------------------------------------- 5) 自适应睡眠
    r5 = "run-neardue"
    db.create_run({"run_id": r5, "kind": "routine", "source": "clinic-1",
                   "mode": "local", "repeats": 1, "concurrency": 1,
                   "status": "queued"})
    soon = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() + 90)) + ".000+00:00"
    db.create_attempts(r5, 1, {"kind": "routine", "source": "clinic-1"},
                       not_before=[soon])
    due = db.next_due_in(r5)
    print(f"④ 下一条任务还有 {due:.0f}s 到期（worker 会直接睡到那时，不再空转查库）")
    if due is None or not (60 < due < 120):
        fails.append(f"next_due_in 计算不对：{due}")

    import shutil
    shutil.rmtree(tmp, ignore_errors=True)
    print()
    if fails:
        print("❌ 断点续跑回归未通过：")
        for f in fails:
            print("  -", f)
        return 1
    print("✅ 断点续跑回归通过（等待中任务不杀、执行中断的重跑、方案续跑不重复建 run、"
          "自适应睡眠正确）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
