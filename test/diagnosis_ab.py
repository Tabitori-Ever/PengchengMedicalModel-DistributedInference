#!/usr/bin/env python3
"""诊断类协同路径的脱站点计时（不经过 benchmark-site）。

目的：把**测量工具本身**从链路上拿掉，判断协同路径的长尾停顿是架构固有的、
还是站点（单进程 + SQLite + 轮询）引入的。

**v3.14 起诊断没有本地执行策略**（医疗中心不能执行模型 server 半段），因此本脚本
只剩协同臂，不再做"协同 vs 本地"对照：
  协同：调度器 `/schedule/diagnosis {deliver:"direct"}` 拿执行计划
        → 按分片**直投执行者**（医院 `/medical/infer_forward`：前端就地 → 云端后端；
          数据中心 `/infer_full`：整段执行）
        → `/task/{id}/report` 回传记账

用法：
  python test/diagnosis_ab.py --scheduler http://localhost:30080 \
      --hospital http://10.50.126.210:8006 --batches 2,4,8 --repeats 5
"""
import argparse
import base64
import array
import json
import os
import statistics
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATASET = os.path.join(ROOT, "test", "test_dataset.json")
IMG_KEYS = ("dce_image", "dwi_image", "clinical", "radiomics")
SELF = {"entity": "ab-probe", "role": "terminal", "node": "probe"}

# 集群外运行时 Service 名无法解析：把计划里的 URL 改写成 ClusterIP
REWRITE = {
    "hospital-a-service:8006": "10.50.126.210:8006",
    "hospital-b-service:8006": "10.50.78.57:8006",
    "medical-server-service:9001": "10.50.50.58:9001",
    "clinic-1-service:8007": "10.50.193.185:8007",
    "clinic-2-service:8007": "10.50.168.231:8007",
}


def resolve(url: str) -> str:
    for name, ip in REWRITE.items():
        if name in url:
            return url.replace(name, ip)
    return url


def http(url, payload=None, timeout=600):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method="POST" if data else "GET",
                                 headers={"Content-Type": "application/json"})
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with op.open(req, timeout=timeout) as r:
        body = r.read().decode()
    return json.loads(body) if body else {}


def enc_f32(nested):
    flat = array.array("f", (float(x) for row in nested for v in row
                             for x in (v if isinstance(v, list) else [v])))
    if sys.byteorder == "big":
        flat.byteswap()
    shape, cur = [], nested
    while isinstance(cur, (list, tuple)):
        shape.append(len(cur))
        cur = cur[0] if len(cur) else None
    return {"__f32b64__": base64.b64encode(flat.tobytes()).decode(),
            "shape": shape, "dtype": "float32"}


def patient_inputs(pids):
    want = set(pids)
    out = {}
    with open(DATASET) as f:
        for p in json.load(f):
            if p["patient_id"] in want:
                out[p["patient_id"]] = p["input"]
    return [out[pid] for pid in pids]


def payloads(pids):
    items = []
    for pid, inp in zip(pids, patient_inputs(pids)):
        item = {k: enc_f32(inp[k]) for k in IMG_KEYS}
        item["patient_id"] = pid
        item["patient_ids"] = [pid]
        items.append(item)
    return items


def run_collab(scheduler, pids, repeats):
    items = {it["patient_id"]: it for it in payloads(pids)}
    lats = []
    for i in range(repeats):
        t0 = time.perf_counter()
        plan = http(scheduler + "/schedule/diagnosis",
                    {"source": "hospital-a", "mode": "collaborative",
                     "deliver": "direct", "patient_ids": pids})
        execs = plan.get("executors") or []
        if not execs:
            print("    未拿到计划:", str(plan)[:150])
            continue

        def one(spec):
            url = resolve(spec["url"])
            def pat(pid):
                r = http(url, items[pid])
                return r.get("predictions") or []
            with ThreadPoolExecutor(max_workers=max(1, len(spec["patient_ids"]))) as p:
                outs = list(p.map(pat, spec["patient_ids"]))
            return [x for o in outs for x in o]

        with ThreadPoolExecutor(max_workers=len(execs)) as pool:
            results = list(pool.map(one, execs))
        preds = [x for r in results for x in r]
        elapsed = (time.perf_counter() - t0) * 1000
        try:
            http(scheduler + f"/task/{plan['task_id']}/report",
                 {"results": [{"executor": s["entity"], "state": "succeeded",
                               "predictions": r, "ms": elapsed}
                              for s, r in zip(execs, results)],
                  "client_total_ms": elapsed})
        except Exception:  # noqa: BLE001
            pass
        lats.append(elapsed)
        if len(preds) < len(pids):
            print(f"    协同结果不全: {len(preds)}/{len(pids)}")
    return lats


def stat(vals):
    if not vals:
        return {}
    s = sorted(vals)
    return {"n": len(s), "mean": round(statistics.mean(s), 1),
            "p50": round(statistics.median(s), 1), "max": round(s[-1], 1)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scheduler", default="http://localhost:30080")
    ap.add_argument("--batches", default="2,4,8")
    ap.add_argument("--repeats", type=int, default=5)
    ap.add_argument("--no-rewrite", action="store_true",
                    help="集群内运行时无需改写（Service 名可解析）")
    ap.add_argument("--seed-pids", default="1404920,1584882,1661073,1664718,"
                                           "1668124,1686243,1727894,1732297")
    args = ap.parse_args()
    allp = args.seed_pids.split(",")
    batches = [int(x) for x in args.batches.split(",")]

    print("=" * 78)
    print("诊断协同路径脱站点计时（不经过 benchmark-site）")
    print("诊断没有本地执行策略（医疗中心不能执行模型 server 半段），故无本地臂")
    print("=" * 78)
    print(f"{'批量':>4s} {'协同 mean':>12s} {'协同 p50':>10s} {'协同 max':>10s} "
          f"{'样本':>5s}")
    for n in batches:
        pids = allp[:n]
        c = stat(run_collab(args.scheduler, pids, args.repeats))
        if not c:
            print(f"{n:>4d} {'—':>12s} {'—':>10s} {'—':>10s} {0:>5d}")
            continue
        print(f"{n:>4d} {c.get('mean', 0):>12.0f} {c.get('p50', 0):>10.0f} "
              f"{c.get('max', 0):>10.0f} {c.get('n', 0):>5d}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
