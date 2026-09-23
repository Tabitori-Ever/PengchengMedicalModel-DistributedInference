#!/usr/bin/env python3
"""端侧算力约束下的「云边端协同 vs 本地执行」交叉点实验。

诊断类任务走的是**串行模型拆分**（边端前端 → 云端后端）。在端侧算力充裕时本地必然更快：
拆分要多搬中间特征（本集群 pod 间有效吞吐仅 3–5 MB/s），而端侧跑完整模型只要几十毫秒。
协同的适用条件是**端侧算力受限**——这正是"端"设备的常态。

本脚本通过在目标 Pod 内制造 CPU 竞争（占满 cgroup 配额）来模拟"算力受限 / 被其它业务占满的
边缘节点"，在同一批量下对比两种策略，从而测出交叉点。

用法：
  python test/edge_constraint_probe.py --site http://localhost:30082 \
      --pod hospital-a --load 0  --batch 4 --repeats 5 --label 端侧空闲
  python test/edge_constraint_probe.py --site http://localhost:30082 \
      --pod hospital-a --load 24 --batch 4 --repeats 5 --label 端侧满载
  python test/edge_constraint_probe.py --site http://localhost:30082 --pod hospital-a --clean
"""
import argparse
import json
import subprocess
import sys
import time
import urllib.request

MARKER = "edge-load-burner"
PATIENTS = ["1404920", "1584882", "1661073", "1664718",
            "1668124", "1686243", "1727894", "1732297"]


def kubectl(args, timeout=180):
    return subprocess.run(["kubectl"] + args, capture_output=True, text=True,
                          timeout=timeout)


def pod_name(entity: str) -> str:
    r = kubectl(["get", "pods", "-l", f"hospital={entity}", "-o",
                 "jsonpath={.items[0].metadata.name}"])
    name = r.stdout.strip()
    if not name:
        r = kubectl(["get", "pods", "-l", f"clinic={entity}", "-o",
                     "jsonpath={.items[0].metadata.name}"])
        name = r.stdout.strip()
    if not name:
        raise SystemExit(f"找不到实体 {entity} 的 Pod")
    return name


def set_load(pod: str, n: int) -> None:
    """把 Pod 内的 CPU 竞争进程调到 n 个（0 = 清空）。"""
    # 清掉旧的（用标记匹配，避免依赖 ps/pkill —— slim 镜像里没有）
    kubectl(["exec", pod, "--", "sh", "-c",
             "for p in /proc/[0-9]*; do "
             "c=$(tr '\\0' ' ' < $p/cmdline 2>/dev/null); "
             f"case \"$c\" in *'{MARKER}'*|*'do :; done'*|*'while True: pass'*) "
             "kill ${p#/proc/} 2>/dev/null;; esac; done; echo cleaned"])
    if n > 0:
        # NOTE: the redirections must come *before* the trailing comment, and
        # the burners must not inherit the exec pipes or `kubectl exec` will
        # block until they exit.
        kubectl(["exec", pod, "--", "sh", "-c",
                 f"M={MARKER}; for i in $(seq 1 {n}); do "
                 "(while :; do :; done) </dev/null >/dev/null 2>&1 & done; "
                 "sleep 1; echo started"])
    time.sleep(3)


def cpu_cores(pod: str, seconds: float = 2.0) -> float:
    """Pod 实际占用的 CPU 核数（cgroup usage 差值）。"""
    r = kubectl(["exec", pod, "--", "sh", "-c",
                 "a=$(grep usage_usec /sys/fs/cgroup/cpu.stat | cut -d' ' -f2); "
                 f"sleep {seconds}; "
                 "b=$(grep usage_usec /sys/fs/cgroup/cpu.stat | cut -d' ' -f2); "
                 f"echo $(( (b-a)/{int(seconds * 1000000)} )) "])
    try:
        return float(r.stdout.strip() or 0) + (
            # 整数除法会丢小数，用两次采样补精度
            _cpu_frac(pod, seconds))
    except ValueError:
        return 0.0


def _cpu_frac(pod: str, seconds: float) -> float:
    r = kubectl(["exec", pod, "--", "sh", "-c",
                 "a=$(grep usage_usec /sys/fs/cgroup/cpu.stat | cut -d' ' -f2); "
                 f"sleep {seconds}; "
                 "b=$(grep usage_usec /sys/fs/cgroup/cpu.stat | cut -d' ' -f2); "
                 f"echo $(( (b-a) % {int(seconds * 1000000)} )) "])
    try:
        rem = float(r.stdout.strip() or 0)
    except ValueError:
        return 0.0
    return rem / (seconds * 1000000.0)


def http(base: str, path: str, payload=None, timeout=3600):
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode()
        req = urllib.request.Request(base + path, data=data, method="POST",
                                     headers={"Content-Type": "application/json"})
    else:
        req = urllib.request.Request(base + path)
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with op.open(req, timeout=timeout) as r:
        body = r.read().decode()
    return json.loads(body) if body else {}


def probe(site: str, mode: str, batch: int, repeats: int, label: str):
    pids = PATIENTS[:batch]
    r = http(site, "/api/runs", {
        "kind": "diagnosis", "source": "hospital-a", "mode": mode,
        "params": {"patient_ids": pids}, "repeats": repeats,
        "concurrency": 1, "label": label})
    rid = r["run_id"]
    t0 = time.time()
    while time.time() - t0 < 1800:
        d = http(site, f"/api/runs/{rid}")
        prog = d.get("progress") or {}
        if not prog.get("pending") and not prog.get("running"):
            break
        time.sleep(1)
    atts = d.get("attempts") or []
    lat = [a.get("client_total_ms") for a in atts if a.get("client_total_ms")]
    ok = sum(1 for a in atts if a.get("status") == "completed")
    if not lat:
        raise SystemExit(f"[{mode}] 无有效数据")
    return {"mean": sum(lat) / len(lat), "min": min(lat), "max": max(lat),
            "ok": ok, "n": len(lat)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", default="http://localhost:30082")
    ap.add_argument("--pod", default="hospital-a")
    ap.add_argument("--load", type=int, default=None, help="Pod 内 CPU 竞争进程数")
    ap.add_argument("--clean", action="store_true", help="只清理竞争进程后退出")
    ap.add_argument("--batch", type=int, default=4)
    ap.add_argument("--repeats", type=int, default=5)
    ap.add_argument("--label", default="")
    ap.add_argument("--json-out", default="")
    args = ap.parse_args()

    pod = pod_name(args.pod)
    if args.clean:
        set_load(pod, 0)
        print(f"已清理 {pod} 内的竞争进程")
        return 0
    if args.load is not None:
        set_load(pod, args.load)

    cores = cpu_cores(pod)
    limit = 4.0
    r = kubectl(["exec", pod, "--", "sh", "-c",
                 "cat /sys/fs/cgroup/cpu.max 2>/dev/null || echo '?"])
    try:
        quota, period = (r.stdout.split() + ["?"])[:2]
        limit = float(quota) / float(period)
    except (ValueError, IndexError):
        pass

    label = args.label or f"competition={args.load}"
    print("=" * 72)
    print(f"端侧算力约束实验 · Pod {pod} · 竞争进程 {args.load} · "
          f"配额 {limit:.2f} 核 · 实测占用 {cores:.2f} 核")
    print(f"批量 {args.batch} 例 × {args.repeats} 次 · {label}")
    print("=" * 72)
    out = {"pod": pod, "load": args.load, "cpu_limit": limit,
           "cpu_used": cores, "batch": args.batch, "repeats": args.repeats,
           "label": label}
    for mode in ("local", "collaborative"):
        res = probe(args.site, mode, args.batch, args.repeats, f"{label}-{mode}")
        out[mode] = res
        print(f"  {mode:14s} mean={res['mean']:8.0f}ms  min={res['min']:7.0f}ms  "
              f"max={res['max']:7.0f}ms  成功 {res['ok']}/{res['n']}")
    l, c = out["local"]["mean"], out["collaborative"]["mean"]
    gain = (l - c) / l * 100
    out["collaborative_faster_pct"] = round(gain, 2)
    print(f"  => 协同{'快' if gain > 0 else '慢'} {abs(gain):.1f}% "
          f"(协同 {c:.0f}ms vs 本地 {l:.0f}ms)")
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        print(f"  已写入 {args.json_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
