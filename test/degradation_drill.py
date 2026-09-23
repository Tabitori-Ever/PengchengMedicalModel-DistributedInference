#!/usr/bin/env python3
"""v3.2 调度降级演练：把 scheduler 缩容到 0，验证边缘 Pod 仍能就地完成任务。

流程：
  1. 基线：scheduler 在线时提交四类任务（协同模式），记录时延；
  2. 缩容 scheduler 到 0（--with-kubectl）并确认调度入口不可达；
  3. 模拟站点/调用方降级路由：直接调用发起 Pod 的 /local/execute
     （degraded=true, degrade_reason=scheduler_unreachable），四类任务必须成功；
  4. 恢复 scheduler 并确认协同路径恢复。

用法：
  # 全流程（会真的把 default/scheduler 缩容到 0 再恢复）
  python test/degradation_drill.py --with-kubectl --kube-host <host>

  # 集群外只做直连 Pod 的降级验证（scheduler 已在别处被停掉）
  python test/degradation_drill.py --pods-only --hospital http://10.50.126.210:8006
"""
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

OK, BAD = "✓", "✗"
_fail = []

CLUSTER_IPS = {
    "scheduler": "http://localhost:30080",
    "hospital-a": "http://10.50.126.210:8006",
    "hospital-b": "http://10.50.78.57:8006",
    "clinic-1": "http://10.50.193.185:8007",
    "clinic-2": "http://10.50.168.231:8007",
    "clinic-3": "http://10.50.157.126:8007",
    "clinic-4": "http://10.50.171.207:8007",
}
DATASET = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "test", "test_dataset.json")


def http(url, payload=None, method="GET", timeout=900):
    if payload is not None and method == "GET":
        method = "POST"
    data = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    t0 = time.perf_counter()
    try:
        with op.open(req, timeout=timeout) as r:
            body = r.read().decode()
        return json.loads(body) if body else {}, round((time.perf_counter() - t0) * 1000, 2)
    except urllib.error.HTTPError as e:
        return {"_http_error": e.code, "_body": e.read().decode()[:200]}, round(
            (time.perf_counter() - t0) * 1000, 2)
    except Exception as e:  # noqa: BLE001
        return {"_error": f"{type(e).__name__}: {str(e)[:120]}"}, round(
            (time.perf_counter() - t0) * 1000, 2)


def check(label, cond, detail=""):
    print(f"  {OK if cond else BAD} {label}" + (f"  {detail}" if detail else ""))
    if not cond:
        _fail.append(label)
    return cond


def patient_input(pid):
    with open(DATASET) as f:
        for p in json.load(f):
            if p["patient_id"] == pid:
                return p["input"]
    with open(DATASET) as f:
        return json.load(f)[0]["input"]


def kubectl(args, check_ok=True):
    r = subprocess.run(["kubectl"] + args, capture_output=True, text=True)
    if check_ok and r.returncode != 0:
        print(f"    ! kubectl {' '.join(args)} -> {r.stderr.strip()[:160]}")
    return r


def scheduler_up():
    d, _ = http("http://localhost:30080/", timeout=3)
    return "_error" not in d and "_http_error" not in d


def set_replicas(n):
    kubectl(["scale", "deploy/scheduler", f"--replicas={n}"])
    if n == 0:
        for _ in range(60):
            pods = kubectl(["get", "pods", "-l", "app=scheduler", "-o",
                            "jsonpath={.items[*].metadata.name}"], check_ok=False).stdout.split()
            if not pods:
                break
            time.sleep(1)
    else:
        kubectl(["rollout", "status", "deploy/scheduler", "--timeout=180s"], check_ok=False)
        for _ in range(60):
            if scheduler_up():
                break
            time.sleep(2)


# --------------------------------------------------------------------- tasks --
def kind_params(kind, patient):
    if kind == "diagnosis":
        return {"patient_id": patient}, patient_input(patient)
    if kind == "compute":
        return {"instruments": 4, "rows": 256, "intensity": 40,
                "partition_count": 3, "seed": 11}, {}
    if kind == "sync":
        return {"bandwidth_mbps": 20.0, "concurrency": 4, "chunk_kb": 4}, {}
    return {"jobs": 2, "rows": 128, "intensity": 30, "seed": 7}, {}


def run_collaborative(kind, source, params, patient):
    body = {"source": source, "mode": "collaborative", **params}
    sub, _ = http("http://localhost:30080/schedule/" + kind, body)
    if "_error" in sub or "_http_error" in sub:
        return {"status": "unreachable", "_sub": sub}
    tid = sub.get("task_id")
    t0 = time.perf_counter()
    for _ in range(600):
        d, _ = http(f"http://localhost:30080/task/result/{tid}")
        if d.get("status") in ("completed", "finished", "failed"):
            d["_client_ms"] = round((time.perf_counter() - t0) * 1000, 2)
            return d
        time.sleep(0.25)
    return {"status": "timeout"}


def run_local(kind, source, params, inp, reason="scheduler_unreachable"):
    base = CLUSTER_IPS[source]
    payload = {"task_id": f"drill-{kind}-{int(time.time())}",
               "kind": kind, "source": source, "params": params, "input": inp,
               "degraded": True, "degrade_reason": reason}
    t0 = time.perf_counter()
    d, _ = http(base + "/local/execute", payload)
    client_ms = round((time.perf_counter() - t0) * 1000, 2)
    d["_client_ms"] = client_ms
    return d


def summarize(kind, d):
    """scheduler 路径的结果在 d['result'] 内，Pod 路径的结果就是顶层。"""
    r = d.get("result") if isinstance(d.get("result"), dict) else d
    m = r.get("metrics") or {}
    return (f"{kind:9s} status={d.get('status')} mode={r.get('mode')} "
            f"degraded={r.get('degraded')} "
            f"pipeline={m.get('pipeline_total_ms')}ms client={d.get('_client_ms')}ms")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--with-kubectl", action="store_true",
                    help="演练中真的把 default/scheduler 缩容到 0 再恢复")
    ap.add_argument("--pods-only", action="store_true",
                    help="跳过基线，只验证直连 Pod 的降级执行")
    ap.add_argument("--patient", default="1404920")
    args = ap.parse_args()

    print("=" * 78)
    print("v3.2 调度降级演练：scheduler 掉线 → 边缘 Pod 就地继续完成任务")
    print("=" * 78)

    spec = [("diagnosis", "hospital-a"), ("compute", "clinic-1"),
            ("sync", "clinic-2"), ("routine", "clinic-1")]

    baseline = {}
    if not args.pods_only:
        print("\n[1] 基线：scheduler 在线（协同模式）")
        check("调度入口可达", scheduler_up())
        for kind, source in spec:
            params, inp = kind_params(kind, args.patient)
            d = run_collaborative(kind, source, params, args.patient)
            baseline[kind] = d.get("_client_ms")
            check(f"协同 {kind} 完成", d.get("status") in ("completed", "finished"),
                  summarize(kind, d))

    if args.with_kubectl:
        print("\n[2] 停掉调度器：kubectl scale deploy/scheduler --replicas=0")
        set_replicas(0)
        time.sleep(3)
        check("调度入口已不可达", not scheduler_up(),
              "NodePort 30080 无响应（前端站点依赖它做降级判定）")
    else:
        print("\n[2] 跳过缩容（未指定 --with-kubectl）；假定调度器已不可用")

    print("\n[3] 降级执行：直接调用发起 Pod 的 /local/execute")
    degraded = {}
    for kind, source in spec:
        params, inp = kind_params(kind, args.patient)
        d = run_local(kind, source, params, inp)
        degraded[kind] = d.get("_client_ms")
        check(f"降级 {kind} 完成（{source} 就地执行）",
              d.get("status") == "completed", summarize(kind, d))
        if kind == "diagnosis":
            rd = d.get("result_detail") if isinstance(d.get("result_detail"), dict) else {}
            bp = (rd or (d.get("result") or {}).get("result_detail") or {}).get("bpCR_probability")
            check("降级诊断结果有效", bp is not None, f"bpCR={bp}")
        check("已标记 degraded + 原因", d.get("degraded") is True
              and d.get("degrade_reason") in ("scheduler_unreachable", "forced"))

    print("\n[4] 对比（客户端墙钟，毫秒）")
    print(f"    {'任务':10s} {'协同':>10s} {'降级/本地':>12s}")
    for kind, _ in spec:
        c = baseline.get(kind)
        l = degraded.get(kind)
        cs = f"{c:.0f}" if isinstance(c, (int, float)) else "—"
        ls = f"{l:.0f}" if isinstance(l, (int, float)) else "—"
        print(f"    {kind:10s} {cs:>10s} {ls:>12s}")

    if args.with_kubectl:
        print("\n[5] 恢复调度器：--replicas=1")
        set_replicas(1)
        check("调度入口恢复", scheduler_up())
        d = run_collaborative("routine", "clinic-1", {"jobs": 1, "rows": 64,
                                                     "intensity": 10}, args.patient)
        check("协同路径恢复", d.get("status") in ("completed", "finished"),
              summarize("routine", d))

    print("\n" + "=" * 78)
    if _fail:
        print(f"结果: {len(_fail)} 项失败 -> " + "; ".join(_fail))
        return 1
    print("结果: 全部通过 —— 调度器掉线期间四类任务均由边缘 Pod 就地完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
