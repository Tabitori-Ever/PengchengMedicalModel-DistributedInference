#!/usr/bin/env python3
"""采集「测试方案运行」（大纲 §5.4 / §5.5）的结果数据，存成报告数据源。

与 `collect_benchmark_data.py`（四类固定套件）并列：那个采的是 20 任务固定套件，
这个采的是**按大纲操作步骤下发的方案运行**（5.4 = 40 任务 / 5 分钟窗口，
5.5 = 200 任务 / 10 小时窗口），一次运行就是一个完整的两阶段测试。

用法：
    # 采集指定方案运行（可多个），并抓取当时的镜像版本等环境信息
    python test/collect_plan_data.py --site http://localhost:30082 \
        --plan-runs plan-a,plan-b --out plan_report_data.json

    # 不带 --plan-runs 时，自动取最近 N 个已完成的方案运行
    python test/collect_plan_data.py --site http://localhost:30082 --latest 2

数据源刻意只依赖站点 HTTP 接口（不直连数据库），这样报告数据可以随时重采，
也便于把采集脚本放在任意一台能访问站点的机器上跑。
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def http(base: str, path: str, timeout: int = 60) -> Any:
    url = base.rstrip("/") + path
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise SystemExit(f"GET {url} -> HTTP {e.code}: {e.read()[:200]!r}")
    except Exception as e:  # noqa: BLE001
        raise SystemExit(f"GET {url} 失败: {type(e).__name__}: {e}")


def pick_runs(site: str, latest: int) -> List[str]:
    data = http(site, f"/api/plans/runs?limit={max(latest * 3, 10)}")
    out = []
    for r in data.get("plan_runs", []):
        if r.get("status") in ("completed", "failed"):
            out.append(r["plan_run_id"])
        if len(out) >= latest:
            break
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", default="http://localhost:30082")
    ap.add_argument("--plan-runs", default="", help="逗号分隔的 plan_run_id")
    ap.add_argument("--latest", type=int, default=2,
                    help="未指定 --plan-runs 时取最近 N 个已完成的方案运行")
    ap.add_argument("--out", default="plan_report_data.json")
    ap.add_argument("--scheduler", default="http://localhost:30080")
    ap.add_argument("--manual", default="",
                    help="人工实测记录 JSON（外部仪表项，如损伤仪丢包率）；"
                         "结构见 plan_report_manual.example.json")
    args = ap.parse_args()

    site = args.site.rstrip("/")
    ids = [x.strip() for x in args.plan_runs.split(",") if x.strip()]
    if not ids:
        ids = pick_runs(site, args.latest)
    if not ids:
        raise SystemExit("找不到可用的方案运行（先下发一次，或显式给 --plan-runs）")

    print(f"站点 {site}｜方案运行 {len(ids)} 个")
    runs: List[Dict[str, Any]] = []
    for pid in ids:
        d = http(site, f"/api/plans/runs/{pid}")
        runs.append(d)
        phases = {p["phase"]: p for p in d.get("phases", [])}
        summary = " · ".join(
            f"{k} {v.get('completed')}/{v.get('tasks_total')}"
            f"({v.get('completion_pct')}%)" for k, v in sorted(phases.items()))
        print(f"  {pid} [{d.get('status')}] {d.get('plan_name')} | {summary}")

    # 环境信息：镜像版本（报告需要说明"这批数据是哪套镜像跑出来的"）
    env: Dict[str, Any] = {}
    # 平台各组件镜像版本：第三方报告的必要溯源信息。用 kubectl 抓；
    # 若采集脚本在集群外跑（拿不到 kubectl），跳过后报告只少这一节。
    try:
        import subprocess
        # kubectl 的 jsonpath 里需要字面的 {"\n"} 作为分隔符：Python 源码里写 \\n
        jp = ('jsonpath={range .items[*]}{.metadata.name}='
              '{.spec.template.spec.containers[0].image}{"\\n"}{end}')
        dep = subprocess.run(["kubectl", "get", "deploy", "-o", jp],
                             capture_output=True, text=True, timeout=30)
        if dep.returncode == 0 and dep.stdout.strip():
            images = {}
            for line in dep.stdout.strip().splitlines():
                if "=" in line:
                    name, image = line.split("=", 1)
                    images[name] = (image.rsplit(":", 1)[-1] if ":" in image
                                    else image)
            env["images"] = images
            print(f"  镜像版本 {len(images)} 个（来自 kubectl）")
    except Exception as e:  # noqa: BLE001
        print(f"  （跳过镜像版本采集：{type(e).__name__}）")

    try:
        cfg = http(site, "/api/config")
        env["db_path"] = cfg.get("db_path")
    except SystemExit:
        pass
    try:
        h = http(site, "/api/health", timeout=90)
        env["site_version"] = (h.get("site") or {}).get("version")
        pods = h.get("pods") or {}
        env["pod_capabilities"] = {k: v.get("capabilities") for k, v in pods.items()}
    except SystemExit:
        pass

    out: Dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "site": site,
        "scheduler": args.scheduler,
        "env": env,
        "plan_runs": runs,
    }
    # 人工实测记录（外部仪表项）：平台不自动判定，由测试方填入后随报告一起留档
    if args.manual:
        if not os.path.exists(args.manual):
            raise SystemExit(f"人工实测文件不存在: {args.manual}")
        with open(args.manual, encoding="utf-8") as f:
            out["manual"] = json.load(f)
        # 把人工实测值**写回判定项本身**，让 plan_report_data.json 自洽：
        # 否则数据里 verdict 还是 pending、而报告按实测值算出"通过"，
        # 两者对不上（校验器正是这么抓出来的）。
        n = 0
        for run in out["plan_runs"]:
            recs = (out["manual"] or {}).get(run.get("plan_run_id")) or {}
            if not isinstance(recs, dict):
                continue
            for crit in ((run.get("comparison") or {}).get("criteria") or []):
                rec = recs.get(crit.get("id"))
                if not isinstance(rec, dict):
                    continue
                val = rec.get("measured")
                if not isinstance(val, (int, float)):
                    continue
                limit = float(crit.get("value") or 0)
                crit["measured"] = val
                crit["verdict"] = "pass" if float(val) <= limit else "fail"
                crit["measured_by"] = rec.get("by") or "外部仪表"
                crit["measured_at"] = rec.get("at")
                crit["measured_note"] = rec.get("note")
                n += 1
        print(f"已并入人工实测记录 {n} 条（{args.manual}），并写回对应判定项")
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\n已写入 {args.out}（{os.path.getsize(args.out)} 字节）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
