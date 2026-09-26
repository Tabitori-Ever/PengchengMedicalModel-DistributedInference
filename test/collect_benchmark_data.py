#!/usr/bin/env python3
"""采集四类任务的「云边端协同 vs 本地执行」对比数据，输出给报告渲染器。

流程（每个套件）：
  1. 预热：先各跑一个轻量任务，避免冷启动（首次容器/模型/连接）污染第一档数据；
  2. 用「云边端协同」下发整套固定任务，等全部完成；
  3. 用「本地执行」下发**同一套**固定任务，等全部完成；
  4. 读取 `/api/compare/suite` 得到逐档统计与总体加速比。

输出一份 JSON（默认 `benchmark_data.json`），可直接交给
`test/render_benchmark_report.py --data <file>` 生成 HTML 报告。

用法：
  python test/collect_benchmark_data.py --site http://localhost:30082 \
      --out benchmark_data.json [--suites suite-compute-20,suite-diagnosis-20,...]
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from typing import Dict, List
from datetime import datetime, timezone

# 负载轴说明（报告里用）
LOAD_AXIS = {
    "compute": "算力负载（仪器数 × 行数 × 强度），固定 3 分区",
    "diagnosis": "单次任务携带的患者数（批量大小）",
    "sync": "链路带宽与分块大小",
    "routine": "一次性日常作业的数量",
}

# 预热用的轻量任务（不进套件统计）
WARMUP = {
    "compute": {"instruments": 2, "rows": 128, "intensity": 20,
                "partition_count": 3, "seed": 1},
    "diagnosis": {"patient_id": "1404920"},
    "sync": {"bandwidth_mbps": 200.0, "concurrency": 4, "chunk_kb": 4},
    "routine": {"jobs": 1, "rows": 64, "intensity": 5, "seed": 1},
}


def http(base, path, payload=None, timeout=3600, retries=2):
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode()
        req = urllib.request.Request(base + path, data=data, method="POST",
                                     headers={"Content-Type": "application/json"})
    else:
        req = urllib.request.Request(base + path)
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    last = None
    for _ in range(retries + 1):
        try:
            with op.open(req, timeout=timeout) as r:
                body = r.read().decode()
            return (json.loads(body) if body else {}), None
        except urllib.error.HTTPError as e:
            return None, f"HTTP {e.code}: {e.read().decode()[:200]}"
        except Exception as e:  # noqa: BLE001
            last = f"{type(e).__name__}: {str(e)[:160]}"
            time.sleep(1.5)
    return None, last


def dispatch_suite(base, suite_id, mode, source, concurrency):
    """One experiment = the whole fixed suite, executed with a single strategy."""
    payload = {"suite_id": suite_id, "mode": mode, "concurrency": concurrency}
    if source:
        payload["source"] = source
    r, err = http(base, "/api/runs", payload)
    if err:
        raise SystemExit(f"[{suite_id}/{mode}] 下发失败: {err}")
    return r.get("run_id")


def warmup(base, kind, mode, source):
    """One throwaway light task on the *same* strategy, so the first measured
    attempt does not pay container/model/connection cold start."""
    params = dict(WARMUP[kind])
    body = {"kind": kind, "source": source, "params": params, "mode": mode,
            "repeats": 1, "concurrency": 1, "label": "warmup"}
    r, err = http(base, "/api/runs", body)
    if err:
        print(f"    ! 预热下发失败({mode}): {err}")
        return
    wait_run(base, r.get("run_id"), poll=0.5, quiet=True)


def wait_run(base, run_id, poll=2.0, quiet=False):
    t0 = time.time()
    while True:
        d, err = http(base, f"/api/runs/{run_id}")
        if err:
            raise SystemExit(f"查询 {run_id} 失败: {err}")
        prog = d.get("progress") or {}
        done = int(prog.get("completed") or 0) + int(prog.get("failed") or 0)
        total = int(prog.get("total") or 0) or len(d.get("attempts") or [])
        if not quiet:
            print(f"      {run_id} {done}/{total}  ({time.time() - t0:.0f}s)",
                  end="\r", flush=True)
        if not prog.get("pending") and not prog.get("running"):
            if not quiet:
                print(f"      {run_id} {done}/{total} 完成 ({time.time() - t0:.0f}s)")
            return d
        time.sleep(poll)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", default="http://localhost:30082")
    ap.add_argument("--scheduler", default="http://localhost:30080")
    ap.add_argument("--out", default="benchmark_data.json")
    ap.add_argument("--suites", default="", help="逗号分隔；默认全部")
    ap.add_argument("--modes", default="collaborative,local")
    ap.add_argument("--no-warmup", action="store_true")
    ap.add_argument("--analysis", default="benchmark_analysis.json",
                    help="附加分析小节（切分点/脱站点/交叉点），存在则并入报告数据")
    args = ap.parse_args()

    site = args.site.rstrip("/")
    suites_all, err = http(site, "/api/suites")
    if err:
        raise SystemExit(f"取套件失败: {err}")
    wanted = [s.strip() for s in args.suites.split(",") if s.strip()]
    suites = [s for s in suites_all["suites"]
              if not wanted or s["suite_id"] in wanted]
    if not suites:
        raise SystemExit("没有匹配的套件")

    modes = [m.strip() for m in args.modes.split(",") if m.strip()]
    # 每个套件本次采集产生的 run_ids：对比接口只统计这些，避免混入历史运行
    runs_by_suite: Dict[str, List[str]] = {}
    print(f"站点 {site}｜套件 {len(suites)} 个｜策略 {modes}")

    skipped: List[str] = []
    for s in suites:
        kind = s["kind"]
        source = s["default_source"]
        # 每个套件声明的可用策略：诊断没有本地执行策略（医疗中心不能执行模型的
        # server 半段），只跑协同，不会去下发一个必然被拒的运行。
        allowed = list(s.get("strategies") or ["collaborative", "local"])
        use_modes = [m for m in modes if m in allowed]
        dropped = [m for m in modes if m not in allowed]
        print(f"\n[{s.get('name')}] {s['task_count']} 个任务，发起方 {source}")
        if dropped:
            note = s.get("strategy_note") or "该套件不支持此策略"
            print(f"    跳过 {dropped}：{note}")
            skipped.append(f"{s['suite_id']}:{','.join(dropped)}")
        for mode in use_modes:
            if not args.no_warmup:
                print(f"    预热 {kind}/{mode} …", flush=True)
                warmup(site, kind, mode, source)
            print(f"    下发 {mode} …", flush=True)
            rid = dispatch_suite(site, s["suite_id"], mode, source,
                                 s["default_concurrency"])
            runs_by_suite.setdefault(s["suite_id"], []).append(rid)
            wait_run(site, rid)

    print("\n采集结果 …")
    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "site": site,
        "scheduler": args.scheduler,
        "load_axis": LOAD_AXIS,
        "skipped_strategies": skipped,
        "cluster": cluster_facts(),
        "suites": [],
    }
    for s in suites:
        ids = runs_by_suite.get(s["suite_id"]) or []
        cmp_data, cerr = http(site, f"/api/compare/suite?suite_id={s['suite_id']}"
                                    f"&source={s['default_source']}"
                                    f"&run_ids={','.join(ids)}")
        if cerr:
            raise SystemExit(f"[{s['suite_id']}] 对比失败: {cerr}")
        cmp_data["load_axis"] = LOAD_AXIS.get(s["kind"], "")
        cmp_data["description"] = s.get("description")
        cmp_data["specs"] = s.get("specs")
        out["suites"].append(cmp_data)
        sp = cmp_data.get("speedup") or {}
        gain = sp.get("collaborative_faster_pct")
        print(f"  {s['kind']:10s} 协同 {sp.get('collaborative_mean_ms')}ms vs "
              f"本地 {sp.get('local_mean_ms')}ms → "
              + (f"协同快 {gain}%" if gain is not None else "数据不足"))

    merged = 0
    if args.analysis and os.path.exists(args.analysis):
        try:
            with open(args.analysis, encoding="utf-8") as f:
                extra = json.load(f)
            for k, v in extra.items():
                if k not in out:
                    out[k] = v
                    merged += 1
        except Exception as e:  # noqa: BLE001
            print(f"    分析小节合并失败: {e}")
    if merged:
        print(f"    已并入 {merged} 个分析小节（{args.analysis}）")
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\n已写入 {args.out}")
    return 0


# 集群事实：运行时从 kubectl 读取，避免硬编码版本过期
_IMAGE_KEYS = {
    "inference-scheduler": "scheduler", "hospital": "hospital-a",
    "clinic": "clinic-1", "medical-server": "medical-server",
    "benchmark-site": "benchmark-site", "dc": "dc-services",
}
_NODE_ROLE = {"node1": "边缘（医院 A）", "node2": "边缘（医院 B）",
              "node3": "云（数据中心）", "desktop-jm5iec6": "控制面 / 运维"}


def _kubectl(args: list) -> str:
    """跑一条 kubectl 并返回 stdout（失败返回空串）。

    注意：jsonpath 里含空格，**不能**把整条命令按空白切分后再传参，
    否则 `{range .items[*]}...` 会被拆成多个参数。
    """
    import subprocess
    try:
        r = subprocess.run(["kubectl"] + args, capture_output=True,
                           text=True, timeout=30)
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:  # noqa: BLE001
        return ""


def _gi(mem: str) -> str:
    """7889344Ki → 7.5Gi（报告里更易读）。"""
    try:
        if mem.endswith("Ki"):
            return f"{int(mem[:-2]) / 1024 / 1024:.1f}Gi"
        if mem.endswith("Mi"):
            return f"{int(mem[:-2]) / 1024:.1f}Gi"
    except ValueError:
        pass
    return mem or "—"


def cluster_facts() -> dict:
    """拓扑 + 实际部署的镜像 tag（读不到 kubectl 时留空，报告会显示 —）。"""
    nodes = []
    for name in ("node1", "node2", "node3", "desktop-jm5iec6"):
        cpu = _kubectl(["get", "node", name, "-o",
                        "jsonpath={.status.allocatable.cpu}"])
        mem = _kubectl(["get", "node", name, "-o",
                        "jsonpath={.status.allocatable.memory}"])
        pods = _kubectl(["get", "pods", "--field-selector",
                         f"spec.nodeName={name}", "-o",
                         'jsonpath={range .items[*]}{.metadata.labels.app}{","}{end}'])
        if not cpu and not mem:
            continue
        nodes.append({"name": name, "role": _NODE_ROLE.get(name, "—"),
                      "cpu": cpu or "—", "memory": _gi(mem),
                      "pods": ", ".join(sorted({p for p in pods.split(",") if p}))})
    images = {}
    for img, deploy in _IMAGE_KEYS.items():
        ref = _kubectl(["get", "deploy", deploy, "-o",
                        "jsonpath={.spec.template.spec.containers[0].image}"])
        images[img] = ref.rsplit(":", 1)[-1] if ref else "—"
    return {"nodes": nodes, "images": images,
            "notes": {"python": "3.11", "platform": "Kubernetes v1.30.14",
                      "measurement": "站点后端墙钟 client_total_ms（跨模式唯一可比口径）"}}


if __name__ == "__main__":
    sys.exit(main())
