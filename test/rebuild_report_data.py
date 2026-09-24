#!/usr/bin/env python3
"""从对比站点数据库**离线重建**报告数据（不依赖集群/网络）。

为什么需要它：站点的 `/api/compare/suite` 默认聚合该套件的**全部历史运行**，
跨轮次重跑会把旧构建（例如诊断的旧切分点、老传输路径）的结果混进统计里。
本工具按指定的 run 集合重算逐档与总体统计，口径与站点后端 `engine.suite_compare`
完全一致（同样的 percentile 插值、同样的 success/fail 判定、同样的 spec_key 分组）。

用法：
  # 只统计最近一轮采集（每个套件取最近 2 次运行：协同 + 本地）
  python test/rebuild_report_data.py --db /data/bench/bench.db \
      --data benchmark_data.json --out benchmark_data.json --latest 2

  # 或显式指定运行
  python test/rebuild_report_data.py --db ... --runs run-a,run-b,...
"""
import argparse
import json
import os
import pathlib
import sqlite3
import sys
from typing import Any, Dict, List, Optional

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "benchmark" / "backend"))


def percentile(sorted_values: List[float], pct: float) -> float:
    """与站点后端 engine._percentile 同口径（线性插值）。"""
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return sorted_values[0]
    k = (len(sorted_values) - 1) * (pct / 100.0)
    lo = int(k)
    hi = min(lo + 1, len(sorted_values) - 1)
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (k - lo)


def stats(values: List[float]) -> Dict[str, Any]:
    clean = sorted(float(v) for v in values if isinstance(v, (int, float)))
    if not clean:
        return {"n": 0, "mean": None, "p50": None, "p95": None,
                "min": None, "max": None}
    return {"n": len(clean),
            "mean": round(sum(clean) / len(clean), 2),
            "p50": round(percentile(clean, 50), 2),
            "p95": round(percentile(clean, 95), 2),
            "min": round(clean[0], 2), "max": round(clean[-1], 2)}


def _mean(vals: List[float]) -> Optional[float]:
    clean = [float(v) for v in vals if isinstance(v, (int, float))]
    return round(sum(clean) / len(clean), 3) if clean else None


def select_runs(con, suite_ids: List[str], latest: int,
                explicit: List[str]) -> Dict[str, List[str]]:
    out: Dict[str, List[str]] = {}
    for sid in suite_ids:
        if explicit:
            rows = con.execute(
                f"""SELECT run_id FROM runs WHERE suite_id=? AND run_id IN
                    ({','.join('?' * len(explicit))}) ORDER BY created_at""",
                [sid] + explicit).fetchall()
        else:
            rows = con.execute(
                """SELECT run_id FROM runs WHERE suite_id=? AND status IN
                   ('completed','failed')
                   ORDER BY created_at DESC LIMIT ?""", (sid, latest)).fetchall()
        out[sid] = sorted(r["run_id"] for r in rows)  # run_id 前缀含时间戳，升序即时间序
    return out


def rebuild(con, suite: Dict[str, Any], run_ids: List[str]) -> Dict[str, Any]:
    import suites as suites_mod  # benchmark/backend/suites.py

    spec = suites_mod.SUITE_BY_ID.get(suite["suite_id"]) if hasattr(
        suites_mod, "SUITE_BY_ID") else None
    if spec is None:
        spec = suites_mod.get_suite(suite["suite_id"])
    marks = ",".join("?" * len(run_ids))
    rows = [dict(r) for r in con.execute(
        f"SELECT * FROM attempts WHERE run_id IN ({marks}) AND params_json IS NOT NULL",
        run_ids)] if run_ids else []

    by_mode: Dict[str, List[float]] = {}
    per_cfg: Dict[str, Dict[str, Any]] = {}
    attribution: Dict[str, Dict[str, List[float]]] = {}
    for r in rows:
        try:
            params = json.loads(r["params_json"] or "{}")
        except ValueError:
            continue
        metrics = {}
        try:
            metrics = json.loads(r.get("metrics_json") or "{}")
        except ValueError:
            pass
        detail = {}
        try:
            detail = json.loads(r.get("result_detail_json") or "{}")
        except ValueError:
            pass
        mode = r.get("mode_used") or r.get("mode_requested") or "?"
        lat = r.get("client_total_ms")
        finished = r.get("status")
        by_mode.setdefault(mode, [])
        if lat is not None:
            by_mode[mode].append(float(lat))
        label = "?"
        for sp in spec["specs"]:
            if all(params.get(k) == v for k, v in sp["params"].items()):
                label = sp["label"]
                break
        cfg = per_cfg.setdefault(label, {"label": label, "params": params,
                                         "modes": {}})
        cm = cfg["modes"].setdefault(mode, {"lat": [], "ok": 0, "fail": 0})
        if lat is not None:
            cm["lat"].append(float(lat))
        if finished == "completed":
            cm["ok"] += 1
        elif finished in ("failed", "cancelled", "timeout"):
            cm["fail"] += 1

        bucket = attribution.setdefault(mode, {
            "parallel_speedup": [], "dispatch_wall_ms": [], "compute_ms_total": [],
            "network_ms_total": [], "pipeline_speedup": []})
        for src, key in ((metrics, "dispatch_wall_ms"), (metrics, "compute_ms_total"),
                         (metrics, "network_ms_total"), (metrics, "parallel_speedup"),
                         (metrics, "pipeline_speedup")):
            val = src.get(key, detail.get(key))
            if isinstance(val, (int, float)):
                bucket.setdefault(key, []).append(val)
        for key in ("parallel_speedup", "pipeline_speedup"):
            val = detail.get(key)
            if isinstance(val, (int, float)):
                bucket.setdefault(key, []).append(val)

    overall = {}
    for mode, lats in by_mode.items():
        s = stats(lats)
        s["success"] = sum(1 for r in rows
                           if (r.get("mode_used") or r.get("mode_requested")) == mode
                           and r.get("status") == "completed")
        s["fail"] = sum(1 for r in rows
                        if (r.get("mode_used") or r.get("mode_requested")) == mode
                        and r.get("status") in ("failed", "cancelled", "timeout"))
        overall[mode] = s

    configs = []
    for label, cfg in per_cfg.items():
        entry = {"label": label, "params": cfg["params"], "modes": {}}
        for mode, cm in cfg["modes"].items():
            s = stats(cm["lat"])
            s["success"] = cm["ok"]
            s["fail"] = cm["fail"]
            entry["modes"][mode] = s
        configs.append(entry)
    order = [sp["label"] for sp in spec["specs"]]
    configs.sort(key=lambda e: order.index(e["label"]) if e["label"] in order else 99)

    speedup = None
    c, l = overall.get("collaborative"), overall.get("local")
    if c and l and c.get("mean") and l.get("mean"):
        speedup = {"collaborative_mean_ms": c["mean"], "local_mean_ms": l["mean"],
                   "collaborative_faster_pct": round(
                       (l["mean"] - c["mean"]) / l["mean"] * 100, 2),
                   "ratio": round(l["mean"] / c["mean"], 3)}
    extra: Dict[str, Any] = {}
    for mode, bucket in attribution.items():
        for key, vals in bucket.items():
            extra[f"{mode}_{key}_mean"] = _mean(vals)
    return {"suite_id": suite["suite_id"], "suite_name": spec["name"],
            "kind": spec["kind"], "source": suite.get("source"),
            "description": spec.get("description"),
            "run_ids": run_ids, "scope": "explicit-runs",
            "overall": overall, "configs": configs, "speedup": speedup,
            "extra": extra}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="/data/bench/bench.db")
    ap.add_argument("--data", default="benchmark_data.json")
    ap.add_argument("--out", default="")
    ap.add_argument("--latest", type=int, default=2,
                    help="每个套件取最近 N 次运行（默认 2：协同 + 本地）")
    ap.add_argument("--runs", default="", help="显式指定 run_id（逗号分隔）")
    args = ap.parse_args()

    db = args.db
    if not os.path.exists(db):
        raise SystemExit(f"数据库不存在: {db}")
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row

    data = json.loads(pathlib.Path(args.data).read_text())
    explicit = [x.strip() for x in args.runs.split(",") if x.strip()]
    suite_ids = [s["suite_id"] for s in data["suites"]]
    chosen = select_runs(con, suite_ids, args.latest, explicit)

    print(f"数据库 {db}｜套件 {len(suite_ids)} 个"
          + (f"｜显式运行 {len(explicit)} 个" if explicit else f"｜每套件最近 {args.latest} 次"))
    for i, s in enumerate(data["suites"]):
        runs = chosen.get(s["suite_id"]) or []
        if not runs:
            print(f"  ! {s['suite_id']} 没有匹配的运行，保留原数据")
            continue
        fresh = rebuild(con, s, runs)
        for k in ("overall", "configs", "speedup", "extra", "run_ids", "scope"):
            data["suites"][i][k] = fresh[k]
        sp = fresh.get("speedup") or {}
        print(f"  {s['kind']:10s} runs={len(runs)} 协同 {sp.get('collaborative_mean_ms')}ms "
              f"vs 本地 {sp.get('local_mean_ms')}ms → {sp.get('collaborative_faster_pct')}%")
        for c in fresh["configs"]:
            cm = (c["modes"].get("collaborative") or {})
            lm = (c["modes"].get("local") or {})
            g = (f"{100 * (lm['mean'] - cm['mean']) / lm['mean']:+.1f}%"
                 if cm.get("mean") and lm.get("mean") else "")
            print(f"      {c['label']:8s} 协同 {cm.get('mean') or 0:8.0f}(n={cm.get('n', 0)}) "
                  f"本地 {lm.get('mean') or 0:8.0f}(n={lm.get('n', 0)}) {g}")

    out = args.out or args.data
    pathlib.Path(out).write_text(json.dumps(data, ensure_ascii=False, indent=2))
    print(f"\n已写入 {out}（scope=explicit-runs，只含指定运行）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
