#!/usr/bin/env python3
"""v3.2 站点端到端验证：经 benchmark-site（而非直接调 Pod/scheduler）跑三模式对比，
并在 scheduler 缩容到 0 时验证站点自动降级仍能完成任务。

用法：
  python test/site_e2e.py --site http://localhost:30082                    # 三模式对比
  python test/site_e2e.py --site http://localhost:30082 --with-kubectl      # 含掉线演练
"""
import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request

OK, BAD = "✓", "✗"
_fail = []

SPECS = [
    ("diagnosis", "hospital-a", {"patient_id": "1404920"}),
    ("compute", "clinic-1",
     {"instruments": 4, "rows": 256, "intensity": 40, "partition_count": 3, "seed": 11}),
    ("routine", "clinic-1", {"jobs": 2, "rows": 128, "intensity": 30, "seed": 7}),
]


def http(base, path, payload=None, method="GET", timeout=900):
    if payload is not None and method == "GET":
        method = "POST"
    data = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
    req = urllib.request.Request(base + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with op.open(req, timeout=timeout) as r:
            body = r.read().decode()
        return json.loads(body) if body else {}, None
    except urllib.error.HTTPError as e:
        return None, f"HTTP {e.code}: {e.read().decode()[:160]}"
    except Exception as e:  # noqa: BLE001
        return None, f"{type(e).__name__}: {str(e)[:160]}"


def check(label, cond, detail=""):
    print(f"  {OK if cond else BAD} {label}" + (f"  {detail}" if detail else ""))
    if not cond:
        _fail.append(label)
    return cond


def run_experiment(base, kind, source, params, mode, repeats=3, force=False):
    body = {"kind": kind, "source": source, "params": params, "mode": mode,
            "repeats": repeats, "concurrency": 1, "label": f"e2e-{mode}"}
    if force:
        body["force_degraded"] = True
    r, err = http(base, "/api/runs", body)
    if err:
        return None, err
    rid = r.get("run_id") or (r.get("run") or {}).get("run_id")
    t0 = time.time()
    while time.time() - t0 < 900:
        d, e = http(base, f"/api/runs/{rid}")
        if e:
            return None, e
        prog = d.get("progress") or {}
        if not prog.get("pending") and not prog.get("running"):
            return d, None
        time.sleep(1)
    return None, "timeout"


def summarize(kind, d):
    atts = (d or {}).get("attempts") or []
    lat = [a.get("client_total_ms") for a in atts if a.get("client_total_ms") is not None]
    st = [a.get("status") for a in atts]
    modes = sorted({str(a.get("mode_used")) for a in atts})
    degs = sorted({bool(a.get("degraded")) for a in atts})
    return (f"n={len(atts)} status={st} mode={modes} degraded={degs} "
            f"client_ms={[round(x) for x in lat]}")


def kubectl_set(n, base=None):
    subprocess.run(["kubectl", "scale", "deploy/scheduler", f"--replicas={n}"],
                   capture_output=True, text=True)
    if n == 0:
        for _ in range(90):
            pods = subprocess.run(["kubectl", "get", "pods", "-l", "app=scheduler",
                                   "-o", "jsonpath={.items[*].metadata.name}"],
                                  capture_output=True, text=True).stdout.split()
            if not pods:
                return
            time.sleep(1)
    else:
        subprocess.run(["kubectl", "rollout", "status", "deploy/scheduler",
                        "--timeout=180s"], capture_output=True, text=True)
        if base:
            for _ in range(60):
                _, err = http(base, "/api/health", timeout=8)
                if err is None:
                    return
                time.sleep(2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", default="http://localhost:30082")
    ap.add_argument("--with-kubectl", action="store_true")
    ap.add_argument("--repeats", type=int, default=3)
    args = ap.parse_args()
    B = args.site.rstrip("/")

    print("=" * 78)
    print(f"v3.2 站点端到端验证  site={B}")
    print("=" * 78)

    print("\n[1] 站点自身健康")
    h, err = http(B, "/api/health", timeout=25)
    check("GET /api/health", h is not None, err or "")
    if h:
        check("调度器可达", (h.get("scheduler") or {}).get("reachable") is True)
        pods = h.get("pods") or {}
        check("六个边缘 Pod 本地执行能力就绪",
              all((v.get("local_mode_available") is True) for v in pods.values()),
              f"{sum(1 for v in pods.values() if v.get('local_mode_available'))}/{len(pods)}")
    check("内置患者数据可用于离线降级",
          ((h or {}).get("site", {}).get("patients", {}) or {}).get("count", 0) >= 1)

    print("\n[2] 三模式对比（协同 / 本地 / 强制降级）")
    results = {}
    for kind, source, params in SPECS:
        for mode, force in (("collaborative", False), ("local", False),
                            ("collaborative", True)):
            tag = "forced" if force else mode
            d, err = run_experiment(B, kind, source, params, mode,
                                    repeats=args.repeats, force=force)
            if err:
                check(f"{kind}/{tag} 完成", False, err)
                continue
            atts = d.get("attempts") or []
            ok = all(a.get("status") == "completed" for a in atts) and atts
            check(f"{kind}/{tag} 完成", bool(ok), summarize(kind, d))
            if force:
                check(f"{kind}/forced 标记降级=forced",
                      all(a.get("degrade_reason") == "forced" for a in atts))
            elif mode == "local":
                check(f"{kind}/local 由 Pod 自行编排",
                      all(a.get("mode_used") == "local" for a in atts))
            else:
                check(f"{kind}/collaborative 走调度器",
                      all(a.get("mode_used") == "collaborative" for a in atts))
            results[(kind, tag)] = d

    print("\n[3] 对比矩阵（含正确性列）")
    mx, err = http(B, "/api/compare/matrix", timeout=60)
    rows = (mx or {}).get("rows") if isinstance(mx, dict) else mx
    check("GET /api/compare/matrix", bool(rows), err or f"{len(rows or [])} 组")
    print(f"    {'任务':10s} {'模式':14s} {'n':>3s} {'成功':>4s} {'mean(ms)':>9s} "
          f"{'p50':>8s} {'p95':>9s} 正确性")
    for r in (rows or []):
        lat = r.get("client_total_ms") or {}
        c = r.get("correctness") or {}
        print(f"    {str(r.get('kind')):10s} {str(r.get('mode_used')):14s} "
              f"{lat.get('n'):>3} {r.get('success'):>4} {lat.get('mean'):>9.1f} "
              f"{lat.get('p50'):>8.1f} {lat.get('p95'):>9.1f} "
              f"{c.get('status')} ({c.get('match')}/{c.get('mismatch')})")
    diag = [r for r in (rows or []) if r.get("kind") == "diagnosis"]
    check("诊断正确性为一致", diag and all(
        (r.get("correctness") or {}).get("status") == "match" for r in diag))

    if args.with_kubectl:
        print("\n[4] 掉线演练：kubectl scale deploy/scheduler --replicas=0")
        kubectl_set(0, B)
        time.sleep(3)
        h2, _ = http(B, "/api/health", timeout=25)
        check("站点已识别调度器不可达",
              ((h2 or {}).get("scheduler") or {}).get("reachable") is False)

        print("    mode=auto 经站点下发（应自动降级并完成）")
        for kind, source, params in SPECS[:3]:
            d, err = run_experiment(B, kind, source, params, "auto", repeats=2)
            if err:
                check(f"{kind}/auto 掉线时完成", False, err)
                continue
            atts = d.get("attempts") or []
            check(f"{kind}/auto 掉线时完成",
                  all(a.get("status") == "completed" for a in atts) and atts,
                  summarize(kind, d))
            check(f"{kind}/auto 标记降级(scheduler_unreachable)",
                  all(a.get("degraded") and
                      a.get("degrade_reason") == "scheduler_unreachable" for a in atts),
                  "degrade_switch_ms=" +
                  str([a.get("degrade_switch_ms") for a in atts]))

        print("\n[5] 恢复调度器并确认协同路径回归")
        kubectl_set(1, B)
        d, err = run_experiment(B, "compute", "clinic-1",
                                {"instruments": 2, "rows": 128, "intensity": 20,
                                 "partition_count": 2, "seed": 5},
                                "auto", repeats=1)
        check("恢复后 auto 回到协同", d is not None and
              all(a.get("mode_used") == "collaborative"
                  for a in (d.get("attempts") or [])), summarize("compute", d or {}))

    print("\n" + "=" * 78)
    if _fail:
        print(f"结果: {len(_fail)} 项失败 -> " + "; ".join(_fail))
        return 1
    print("结果: 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
