#!/usr/bin/env python3
"""任务数上限 + 诊断无本地执行策略 —— 回归测试。

背景（两条都是产品事实，不是偏好）：
1. **任务数上限**：方案任务数来自用户输入，而 run 级校验的 `MAX_REPEATS` 对方案
   子 run 不生效。任务数过大时有两个真实故障：① 崩溃风险（每任务一行 attempts）；
   ② **静默算错**——阶段/逐类统计查询带 `limit=20000`，超出即被截断，报出来的
   均值与总量是错的（比崩溃更危险）。因此要求：超限**静默截断**，不做任何提示，
   并且"界面显示的任务数"必须等于"实际展开的任务数"。
2. **诊断无本地执行策略**：医疗中心（hospital）不能执行医疗模型的 server 部分
   推理（layer4 + 融合 + 分类），只有数据中心能跑。因此诊断既不能 `mode=local`，
   也不能在 `auto` 下退化到本地，方案/套件里也不得把诊断排进本地阶段。

用法：
    python test/task_limits_test.py
"""
import os
import pathlib
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
BACKEND = HERE.parent / "benchmark" / "backend"
sys.path.insert(0, str(BACKEND))


def main() -> int:
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="limits-"))
    os.environ["DB_PATH"] = str(tmp / "l.db")

    import db
    import engine
    import plans
    import suites

    db.init(str(tmp / "l.db"))

    fails = []

    # ------------------------------------------------------------ 任务数上限
    print(f"上限：单类 {plans.MAX_TASKS_PER_UNIT} · 每阶段 {plans.MAX_TASKS_PER_PHASE} "
          f"· 设备 {plans.MAX_DEVICES}")

    cases = [0, 1, 5, 25, 100, 999, 1000, 1001, 100000, 10 ** 9]
    for n in cases:
        ov = plans.apply_overrides(plans.PLAN_COLLAB, {
            "kinds": {k: {"per_device": n} for k in
                      ("diagnosis", "compute", "sync", "routine")}})
        units = plans.expand_units(ov, phase="collaborative")
        total = sum(u["repeats"] for u in units)
        per_unit = [u["repeats"] for u in units]
        shown = plans.plan_total_tasks(ov)          # 界面会显示这个数
        ok = total <= plans.MAX_TASKS_PER_PHASE and all(
            p <= plans.MAX_TASKS_PER_UNIT for p in per_unit)
        consistent = shown == total
        flag = "✅" if ok and consistent else "❌"
        if n in (0, 1, 1000, 100000, 10 ** 9):
            print(f"  {flag} 输入 {n:>10} → 实际 {total:>5} · 显示 {shown:>5} · "
                  f"单元 {per_unit[:4]}{'…' if len(per_unit) > 4 else ''}")
        if not ok:
            fails.append(f"输入 {n} 超出上限（total={total}, per_unit={per_unit}）")
        if not consistent:
            fails.append(f"输入 {n}：界面显示 {shown} 与实际 {total} 不一致")
        if n > 0 and total == 0:
            fails.append(f"输入 {n} 被压成 0 个任务")
        # 每类至少留 1 个：不允许整类消失
        if n > 0 and any(p == 0 for p in per_unit):
            fails.append(f"输入 {n}：有任务类型被整类截断（{per_unit}）")

    # 设备数上限
    many = [{"id": chr(65 + i), "source": "clinic-1"} for i in range(50)]
    ov = plans.apply_overrides(plans.PLAN_COLLAB, {"devices": many})
    if len(ov["devices"]) != plans.MAX_DEVICES:
        fails.append(f"设备数未截断到 {plans.MAX_DEVICES}（实为 {len(ov['devices'])}）")
    else:
        print(f"  ✅ 输入 50 台设备 → 截断为 {len(ov['devices'])} 台")

    # 原方案不受影响
    for pid in plans.PLAN_ORDER:
        p = plans.get_plan(pid)
        declared = int(p.get("total_tasks_declared") or 0)
        actual = plans.plan_total_tasks(p, phase="collaborative")
        if declared != actual:
            fails.append(f"{pid} 声明总数 {declared} 与实际展开 {actual} 不一致")
    print(f"  ✅ 两个方案声明总数与实际展开一致")

    # ------------------------------------------------- 诊断无本地执行策略
    print("\n诊断：")
    for mode, expect_reject in (("local", True), ("collaborative", False),
                                ("auto", False)):
        try:
            engine.validate_spec({"kind": "diagnosis", "source": "hospital-a",
                                  "mode": mode, "repeats": 1, "params": {"input": {"x": 1}}})
            rejected = False
        except engine.RunError as exc:
            rejected = True
            msg = str(exc)
        tag = "✅" if rejected == expect_reject else "❌"
        if rejected != expect_reject:
            fails.append(f"诊断 mode={mode} 应{'拒绝' if expect_reject else '接受'}")
        if expect_reject:
            print(f"  {tag} 单次任务 mode={mode} → 拒绝：{msg}")
        else:
            print(f"  {tag} 单次任务 mode={mode} → 接受")

    # 套件：诊断套件 + local 必须拒绝
    try:
        engine.create_suite_run({"suite_id": "suite-diagnosis-20", "mode": "local"})
        fails.append("suite-diagnosis-20 + local 未被拒绝")
        print("  ❌ 诊断套件 mode=local 未被拒绝")
    except engine.RunError as exc:
        print(f"  ✅ 诊断套件 mode=local → 拒绝：{exc}")

    # 其它三类的 local 仍然可用
    for kind in ("compute", "sync", "routine"):
        try:
            engine.validate_spec({"kind": kind, "source": "clinic-1", "mode": "local",
                                  "repeats": 1, "params": {}})
            print(f"  ✅ {kind} mode=local → 接受（本地执行仍然可用）")
        except engine.RunError as exc:
            fails.append(f"{kind} 的 local 被误拒：{exc}")

    # 套件目录与方案目录都要声明"策略"
    for s in suites.list_suites():
        if "strategies" not in s:
            fails.append(f"{s['suite_id']} 未声明可用策略")
        if s["kind"] == "diagnosis" and "local" in s["strategies"]:
            fails.append("诊断套件不应声明支持 local")
    for p in plans.list_plans():
        if "collaborative_only_kinds" not in p:
            fails.append(f"{p['plan_id']} 未声明仅协同的任务类型")
        if p["collaborative_only_kinds"] != ["diagnosis"]:
            fails.append(f"{p['plan_id']} 仅协同类型应为 ['diagnosis']")
        if p["tasks_per_phase"]["local"] >= p["tasks_per_phase"]["collaborative"]:
            fails.append(f"{p['plan_id']} 本地阶段任务数应少于协同阶段")
        # 本地阶段不得展开出诊断单元
        local_units = plans.expand_units(p, phase="local")
        if any(u["kind"] == "diagnosis" for u in local_units):
            fails.append(f"{p['plan_id']} 本地阶段出现了诊断单元")
    print(f"  ✅ 套件/方案均正确声明策略（本地阶段不含诊断）")
    for p in plans.list_plans():
        print(f"     {p['plan_id']}: 本地 {p['tasks_per_phase']['local']} · "
              f"协同 {p['tasks_per_phase']['collaborative']} · "
              f"仅协同 {p['collaborative_only_kinds']}")

    import shutil
    shutil.rmtree(tmp, ignore_errors=True)
    print()
    if fails:
        print("❌ 未通过：")
        for f in fails:
            print("  -", f)
        return 1
    print("✅ 通过（上限静默截断且显示一致；诊断无本地执行策略，其余三类不受影响）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
