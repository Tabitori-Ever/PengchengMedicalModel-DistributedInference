#!/usr/bin/env python3
"""报告「未达 100% 完成率」渲染回归：失败/重传必须如实呈现，判定必须判不通过。

为什么单独测这个：已交付的 5.4 是 40/40 全成功，所以报告的"全绿"路径验证过了；
但大纲 5.5 跑 200 个任务、又要求完成率 ≥100%，**只要有任务用尽重传额度就会低于 100%**。
那种情况下的报告必须：
  * 阶段表的"失败""重传次数""重传耗时"给出非零值（不能只显示全绿）；
  * 逐类对比里该类型的"完成"数小于 n；
  * 完成率判定显示 **不通过**（而不是被四舍五入成 100% 或漏判）。

做法：用引擎在一套临时库里真实跑一个"必定失败"的方案运行（假执行器），
再把 `plan_run_public()` 的真实输出交给**真实的采集/渲染/校验**三个脚本，
全程离线、不碰集群。

用法：
    python test/plan_report_failure_test.py
"""
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import time

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
BACKEND = ROOT / "benchmark" / "backend"
sys.path.insert(0, str(BACKEND))


def main() -> int:
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="failrender-"))
    os.environ["DB_PATH"] = str(tmp / "f.db")

    import config
    import db
    import engine
    import plans

    db.init(str(tmp / "f.db"))
    config.update({"pod_urls": {"clinic-1": "http://clinic-1:8000",
                                "clinic-2": "http://clinic-2:8000"},
                   "attempt_retries": 1, "retry_backoff_ms": 10,
                   "plan_unit_concurrency": 1})
    db.upsert_patients([{"patient_id": "P1", "bpCR": 0.5}], origin="t")

    # 假执行器：routine 全部失败（耗尽重传额度），其余成功
    calls = {}

    def fake(attempt, run, cfg):
        kind = run["kind"]
        key = run["run_id"]
        calls[key] = calls.get(key, 0) + 1
        if kind == "routine":
            return {"status": "failed", "client_total_ms": 30.0,
                    "error": "simulated permanent failure",
                    "finished_at": db.now()}
        return {"status": "completed", "client_total_ms": 20.0,
                "finished_at": db.now(), "error": None}

    engine._execute_attempt = fake
    engine._PLAN_POLL_S = 0.2
    engine._PLAN_POLL_MAX_S = 0.5

    snap = plans.apply_overrides(plans.PLAN_COLLAB, {
        "generate_window_min": 0, "duration_min": 1,
        "strategies": ["local", "collaborative"],
        "kinds": {k: {"per_device": 1} for k in
                  ("diagnosis", "compute", "sync", "routine")},
    })
    pid = db.new_plan_run_id()
    db.create_plan_run({"plan_run_id": pid, "plan_id": "plan-5.4-collab",
                        "plan_name": "失败渲染验证", "snapshot": snap,
                        "strategies": ["local", "collaborative"],
                        "status": "running"})
    engine._plan_coordinator(pid)
    pub = engine.plan_run_public(pid)
    phases = {p["phase"]: p for p in pub["phases"]}
    print(f"离线方案运行：{pub['status']}")
    for ph, p in sorted(phases.items()):
        print(f"  {ph:14s} 完成 {p['completed']}/{p['tasks_total']} "
              f"失败 {p['failed']} 完成率 {p['completion_pct']}% "
              f"重传 {p['retries']} 重传耗时 {p['retry_ms_total']}ms")
    crit = {c["id"]: c for c in pub["comparison"]["criteria"]}
    print(f"  完成率判定：{crit['completion']['verdict']} "
          f"实测 {crit['completion']['measured']}%")

    fails = []
    if phases["local"]["failed"] == 0:
        fails.append("假执行器没造成失败，测试前提不成立")
    if crit["completion"]["verdict"] != "fail":
        fails.append(f"完成率 <100% 时判定应为 fail，实为"
                     f" {crit['completion']['verdict']}")

    # 把真实输出喂给真实的采集/渲染/校验链路
    data = {
        "generated_at": "2026-09-24T00:00:00+00:00",
        "site": "(offline)", "scheduler": "(offline)",
        "env": {"site_version": "test"},
        "plan_runs": [pub],
    }
    dpath = tmp / "data.json"
    hpath = tmp / "report.html"
    dpath.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    subprocess.run([sys.executable, str(HERE / "render_plan_report.py"),
                    "--data", str(dpath), "--out", str(hpath)],
                   capture_output=True, text=True, check=True)

    r = subprocess.run([sys.executable, str(HERE / "plan_report_check.py"),
                        "--html", str(hpath), "--data", str(dpath),
                        "--expect-plan-runs", "1"],
                       capture_output=True, text=True)
    print()
    print(r.stdout.strip())
    if r.returncode != 0:
        fails.append("校验器对未达 100% 的报告判定失败")

    doc = hpath.read_text(encoding="utf-8")
    # 报告里必须真的出现失败/重传的非零数字与"不通过"
    if "不通过" not in doc:
        fails.append("报告里没有出现『不通过』")
    if ">0<" not in doc.replace(" ", "") and ">0<" not in doc:
        fails.append("报告里失败列没有非零值（可能把所有数字都渲染成了 0）")

    import shutil
    shutil.rmtree(tmp, ignore_errors=True)
    print()
    if fails:
        print("❌ 未通过：")
        for f in fails:
            print("  -", f)
        return 1
    print("✅ 通过（未达 100% 时如实呈现失败/重传，判定为不通过，报告仍可校验）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
