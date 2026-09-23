#!/usr/bin/env python3
"""固定套件对比：同一套 20 个任务，分别用「云边端协同」与「本地执行」跑一遍。

由站点后端下发与统计（`/api/runs` + `/api/compare/suite`），因此两种策略跑的是
**逐字节相同**的任务集，唯一差异是调度策略。

验收断言：协同平均时延须比本地快 **≥ --min-gain（默认 10%）**。

用法：
  python test/suite_compare.py --site http://localhost:30082
  python test/suite_compare.py --site http://127.0.0.1:18099 --source hospital-a --min-gain 10
"""
import argparse
import json
import sys
import time
import urllib.error
import urllib.request

OK, BAD = "✓", "✗"


def http(base, path, payload=None, timeout=1800):
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode()
        req = urllib.request.Request(base + path, data=data, method="POST",
                                     headers={"Content-Type": "application/json"})
    else:
        req = urllib.request.Request(base + path)
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with op.open(req, timeout=timeout) as r:
            body = r.read().decode()
        return json.loads(body) if body else {}, None
    except urllib.error.HTTPError as e:
        return None, f"HTTP {e.code}: {e.read().decode()[:200]}"
    except Exception as e:  # noqa: BLE001
        return None, f"{type(e).__name__}: {str(e)[:200]}"


def dispatch(base, suite_id, mode, source, concurrency, label):
    body = {"suite_id": suite_id, "mode": mode, "source": source,
            "concurrency": concurrency, "label": label}
    r, err = http(base, "/api/runs", body)
    if err:
        raise SystemExit(f"[{mode}] 下发失败: {err}")
    return r.get("run_id")


def wait_run(base, run_id, poll=2.0):
    t0 = time.time()
    while True:
        d, err = http(base, f"/api/runs/{run_id}")
        if err:
            raise SystemExit(f"查询 {run_id} 失败: {err}")
        prog = d.get("progress") or {}
        done = int(prog.get("completed") or 0) + int(prog.get("failed") or 0)
        total = int(prog.get("total") or 0) or len(d.get("attempts") or [])
        print(f"    {run_id} 进度 {done}/{total}", end="\r", flush=True)
        if not prog.get("pending") and not prog.get("running"):
            print()
            return d
        time.sleep(poll)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", default="http://localhost:30082")
    ap.add_argument("--suite", default="suite-compute-20")
    ap.add_argument("--source", default=None, help="缺省用套件默认发起方")
    ap.add_argument("--concurrency", type=int, default=1)
    ap.add_argument("--min-gain", type=float, default=10.0,
                    help="协同相对本地的最小加速百分比（验收线）")
    ap.add_argument("--strategies", default="collaborative,local")
    args = ap.parse_args()
    B = args.site.rstrip("/")

    suites, err = http(B, "/api/suites")
    if err:
        raise SystemExit(f"取套件失败: {err}")
    suite = next((s for s in suites["suites"] if s["suite_id"] == args.suite), None)
    if suite is None:
        raise SystemExit(f"套件 {args.suite} 不存在，可选：{[s['suite_id'] for s in suites['suites']]}")
    source = args.source or suite["default_source"]

    print("=" * 84)
    print(f"固定套件对比 · {suite['name']}（{suite['task_count']} 个任务，发起方 {source}）")
    for sp in suite["specs"]:
        p = sp["params"]
        print(f"    {sp['label']:4s} 仪器 {p['instruments']:>2} 行数 {p['rows']:>5} "
              f"强度 {p['intensity']:>4} 分区 {p['partition_count']} × {sp['repeats']} 次")
    print("=" * 84)

    for mode in [m.strip() for m in args.strategies.split(",") if m.strip()]:
        print(f"\n[{mode}] 下发中…")
        rid = dispatch(B, args.suite, mode, source, args.concurrency, f"{args.suite}-{mode}")
        d = wait_run(B, rid)
        atts = d.get("attempts") or []
        lats = [a.get("client_total_ms") for a in atts if a.get("client_total_ms") is not None]
        ok = sum(1 for a in atts if a.get("status") == "completed")
        print(f"    完成 {ok}/{len(atts)}，时延范围 {min(lats):.0f}–{max(lats):.0f} ms")

    print("\n" + "=" * 84)
    cmp_data, err = http(B, f"/api/compare/suite?suite_id={args.suite}&source={source}")
    if err:
        raise SystemExit(f"对比失败: {err}")

    overall = cmp_data.get("overall") or {}
    if not overall:
        print("无数据"); return 1

    print(f"{'档位':6s} {'仪器':>4s} {'行数':>6s} {'强度':>5s} | "
          f"{'协同 n':>7s} {'协同 mean':>10s} {'协同 p95':>9s} | "
          f"{'本地 n':>7s} {'本地 mean':>10s} {'本地 p95':>9s} | {'协同快':>7s}")
    for cfg in cmp_data.get("configs") or []:
        p = cfg["params"]
        c = (cfg["modes"] or {}).get("collaborative") or {}
        l = (cfg["modes"] or {}).get("local") or {}
        gain = ""
        if c.get("mean") and l.get("mean"):
            gain = f"{100 * (l['mean'] - c['mean']) / l['mean']:6.1f}%"
        print(f"{cfg['label']:6s} {p.get('instruments'):>4} {p.get('rows'):>6} "
              f"{p.get('intensity'):>5} | {c.get('n', 0):>7} {_fmt(c.get('mean')):>10} "
              f"{_fmt(c.get('p95')):>9} | {l.get('n', 0):>7} {_fmt(l.get('mean')):>10} "
              f"{_fmt(l.get('p95')):>9} | {gain:>7}")

    print("-" * 84)
    for mode, s in overall.items():
        print(f"总计 {mode:14s} n={s.get('n')} 成功 {s.get('success')} "
              f"mean={_fmt(s.get('mean'))}ms p50={_fmt(s.get('p50'))}ms p95={_fmt(s.get('p95'))}ms")
    sp = cmp_data.get("speedup")
    if not sp:
        print("\n两种策略都需要至少一次套件数据才能给出结论")
        return 1
    gain = sp["collaborative_faster_pct"]
    print(f"\n结论：协同 {sp['collaborative_mean_ms']}ms vs 本地 {sp['local_mean_ms']}ms "
          f"→ 协同快 {gain}%（比值 {sp['ratio']}×）")
    if gain >= args.min_gain:
        print(f"{OK} 达到验收线（≥{args.min_gain}%）")
        return 0
    print(f"{BAD} 未达验收线（需 ≥{args.min_gain}%，实际 {gain}%）")
    return 1


def _fmt(v):
    return "—" if v is None else f"{v:.0f}"


if __name__ == "__main__":
    sys.exit(main())
