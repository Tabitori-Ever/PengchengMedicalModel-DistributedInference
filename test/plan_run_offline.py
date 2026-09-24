#!/usr/bin/env python3
"""离线验证「测试方案运行」的调度骨架（不需要集群 / 不访问网络）。

它把 `engine._execute_attempt` 换成一个会 sleep 的假执行器（本地比协同慢，
便于检查对比方向），然后下发一个**缩放版** §5.4 方案，检查：

1. 任务按阶段顺序执行：先 `local`（未调度），跑完才切到 `collaborative`；
2. 任务确实按 `generate_window_s` **分散产生**（不是一次性全发）；
3. 阶段总用时、完成率、逐类对比、判定都能正确算出来；
4. 诊断类任务每个都分到了不同的患者（按数据集轮换）。

用法：
    python test/plan_run_offline.py            # 默认缩放：8 任务 × 2 阶段
    python test/plan_run_offline.py --keep-db  # 保留临时库以便排查
"""
import argparse
import json
import os
import pathlib
import sys
import tempfile
import time

HERE = pathlib.Path(__file__).resolve().parent
BACKEND = HERE.parent / "benchmark" / "backend"
sys.path.insert(0, str(BACKEND))

FAKE_LOCAL_MS = 120.0
FAKE_COLLAB_MS = 60.0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep-db", action="store_true")
    ap.add_argument("--window-s", type=float, default=3.0,
                    help="任务产生窗口（秒），越小跑得越快")
    args = ap.parse_args()

    tmp = pathlib.Path(tempfile.mkdtemp(prefix="planrun-"))
    db_path = tmp / "plan.db"
    os.environ["DB_PATH"] = str(db_path)

    import config
    import db
    import engine
    import plans

    db.init(str(db_path))

    # 两位患者，供诊断类任务轮换
    db.upsert_patients([{"patient_id": f"P{i}", "bpCR": 0.5 + i / 100}
                        for i in range(1, 4)], origin="test")
    # 四个发起方（两台"微型台式电子计算机"用 clinic-1 / clinic-2）
    config.update({"pod_urls": {s: f"http://{s}:8000" for s in
                                       ("clinic-1", "clinic-2", "hospital-a", "hospital-b")}})

    calls = []

    def fake_execute(attempt, run, cfg):
        """假执行器：不联网，只按模式 sleep 并返回一个"完成"记录。"""
        t0 = time.time()
        ms = FAKE_COLLAB_MS if run["mode"] == "collaborative" else FAKE_LOCAL_MS
        time.sleep(ms / 1000.0)
        calls.append({"run_id": run["run_id"], "phase": run.get("plan_phase"),
                      "kind": run["kind"], "source": run["source"],
                      "mode": run["mode"], "started": t0,
                      "params": attempt.get("params") or {}})
        return {"status": "completed", "client_total_ms": ms,
                "finished_at": db.now(), "error": None}

    engine._execute_attempt = fake_execute

    # 让任务产生时刻可复现：_deal_offsets 用 plan_run_id 作随机种子，
    # 随机 id 会让"窗口跨度"这个断言偶发失败（8 个均匀样本的极差本来就随机）。
    _ids = iter([f"plan-test-{i:04d}" for i in range(50)])
    db.new_plan_run_id = lambda: next(_ids)

    # 缩放版 §5.4：每设备每类 1 个任务（共 8），窗口 --window-s 秒
    body = {
        "plan_id": "plan-5.4-collab",
        "label": "离线骨架校验",
        "overrides": {
            "generate_window_min": args.window_s / 60.0,
            "duration_min": 1,
            "kinds": {k: {"per_device": 1} for k in
                      ("diagnosis", "compute", "sync", "routine")},
        },
    }
    # 0) 还没跑完就查结果：判定必须是 pending，不能因为完成率是 None 而抛异常
    #    （线上真实踩过：`None >= 100.0` 让 POST /api/plans/runs 直接 500）
    probe = engine.create_plan_run({
        "plan_id": "plan-5.4-collab",
        "overrides": {
            # 窗口 0：任务立即到期，本地阶段会很快跑完并让协调器走到"下一阶段"的判定点；
            # 这正是检验"取消后不得再开新阶段"的场景。
            "generate_window_min": 0,
            "duration_min": 60,
            "strategies": ["local", "collaborative"],
            "kinds": {k: {"per_device": 1} for k in
                      ("diagnosis", "compute", "sync", "routine")},
        },
    })
    early = engine.plan_run_public(probe["plan_run_id"])
    verdicts = {c["id"]: c["verdict"] for c in early["comparison"]["criteria"]}
    print(f"⓪ 空数据查询：状态 {early['status']} · 判定 {verdicts}")
    bad_early = {k: v for k, v in verdicts.items() if v != "pending"}

    # 取消：协调器必须停在这里，不能继续开后续阶段
    cancelled = engine.cancel_plan_run(probe["plan_run_id"])
    if cancelled is None or cancelled["status"] != "cancelled":
        bad_early["cancel"] = "取消失败"
    time.sleep(2.0)                     # 本地阶段跑完后，协调器才会尝试开第二阶段
    after = engine.plan_run_public(probe["plan_run_id"]) or {}
    leaked = [r for r in after.get("runs", []) if r["phase"] == "collaborative"]
    if leaked:
        bad_early["leak"] = f"取消后仍开了协同阶段（{len(leaked)} 个子 run）"
    calls.clear()                       # 只统计正式那次运行

    t_start = time.time()
    created = engine.create_plan_run(body)
    plan_run_id = created["plan_run_id"]
    print(f"plan_run_id = {plan_run_id}")
    print(f"配置：{plans.plan_total_tasks(created['config'])} 任务/阶段 · "
          f"策略 {created['strategies']} · 窗口 {args.window_s}s")

    # 轮询到结束
    detail = None
    for _ in range(900):
        detail = engine.plan_run_public(plan_run_id)
        if not detail["live"]:
            break
        time.sleep(0.2)
    assert detail is not None

    fails = []
    if bad_early:
        fails_early = True
    if bad_early:
        fails.append(f"未跑完时判定应全部为 pending，实为 {bad_early}")

    # 1) 阶段顺序：所有 local 调用必须早于任何 collaborative 调用
    loc = [c for c in calls if c["phase"] == "local"]
    col = [c for c in calls if c["phase"] == "collaborative"]
    print(f"\n执行调用：local {len(loc)} 次 · collaborative {len(col)} 次")
    if not loc or not col:
        fails.append("两个阶段没有都执行")
    else:
        last_local = max(c["started"] for c in loc)
        first_collab = min(c["started"] for c in col)
        print(f"  最后一条 local 开始于 +{last_local - t_start:.2f}s，"
              f"第一条 collaborative 开始于 +{first_collab - t_start:.2f}s")
        if first_collab < last_local:
            fails.append("阶段未按顺序执行（本地还没跑完就开始了协同）")

    # 2) 分散产生：同一阶段内首个与最后一个任务开始时间应接近窗口
    for phase, group in (("local", loc), ("collaborative", col)):
        if len(group) < 2:
            continue
        spread = max(c["started"] for c in group) - min(c["started"] for c in group)
        print(f"  {phase}: 任务产生跨度 {spread:.2f}s（窗口 {args.window_s}s）")
        if spread < args.window_s * 0.4:
            fails.append(f"{phase} 阶段任务未按窗口分散产生（跨度仅 {spread:.2f}s）")

    # 3) 完成率 / 阶段总用时 / 对比
    phases = {p["phase"]: p for p in detail["phases"]}
    for phase in ("local", "collaborative"):
        p = phases.get(phase)
        if not p:
            fails.append(f"缺少 {phase} 阶段汇总")
            continue
        print(f"\n[{phase}] 任务 {p['tasks_total']} · 完成 {p['completed']} · "
              f"失败 {p['failed']} · 完成率 {p['completion_pct']}% · "
              f"阶段总用时 {p['total_wall_ms']} ms · "
              f"单任务 mean {p['latency']['mean']} ms")
        if p["completed"] != p["tasks_total"]:
            fails.append(f"{phase} 阶段未全部完成")
        if not p["total_wall_ms"]:
            fails.append(f"{phase} 阶段缺少总用时")

    comp = detail["comparison"]
    ov = comp["overall"]
    print(f"\n总体：任务处理总用时（含重传代价，判定口径）本地 {ov['local_processing_ms']} ms vs "
          f"协同 {ov['collaborative_processing_ms']} ms → 提速 {ov['processing_gain_pct']}%")
    print(f"      仅成功执行（不含重传）本地 {ov['local_exec_ms']} ms vs "
          f"协同 {ov['collaborative_exec_ms']} ms")
    print(f"      重传次数 本地 {ov['retries_local']} · 协同 {ov['retries_collaborative']}")
    print(f"      阶段墙钟（含固定产生窗口）本地 {ov['local_wall_ms']} ms vs "
          f"协同 {ov['collaborative_wall_ms']} ms → {ov['wall_gain_pct']}%")
    print("逐类：")
    for e in comp["per_kind"]:
        print(f"  {e['kind']:10s} 本地 {e['modes']['local']['total_client_ms']:8.1f} ms · "
              f"协同 {e['modes']['collaborative']['total_client_ms']:8.1f} ms · "
              f"增益 {e['gain_pct']}%")
    if ov["processing_gain_pct"] is None:
        fails.append("总体对比缺失")
    elif ov["processing_gain_pct"] < 10:
        fails.append(f"假执行器下协同应更快（构造为 120ms vs 60ms），"
                     f"实得 {ov['processing_gain_pct']}%")
    # 阶段墙钟被固定产生窗口主导，两遍应接近——这正是判定不用墙钟的原因
    if ov["wall_gain_pct"] is not None and abs(ov["wall_gain_pct"]) > 60:
        fails.append(f"阶段墙钟差异异常（窗口主导下不应有 {ov['wall_gain_pct']}%）")
    print("判定：")
    for c in comp["criteria"]:
        print(f"  [{c['verdict']:7s}] {c['label']}（实测 {c['measured']} {c.get('unit') or ''}）")

    # 4) 诊断类任务患者轮换
    diag = [c for c in loc if c["kind"] == "diagnosis"]
    pids = [c["params"].get("patient_id") for c in diag]
    print(f"\n诊断类任务患者分配：{pids}")
    if diag and len(set(pids)) != len(pids):
        fails.append("诊断类任务出现了重复患者（应轮换）")

    print("\n" + ("✅ 骨架校验通过" if not fails else "❌ 未通过：\n  - " + "\n  - ".join(fails)))
    print(f"（临时库：{db_path}）")
    if not args.keep_db:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
