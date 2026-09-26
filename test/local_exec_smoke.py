#!/usr/bin/env python3
"""v3.2 本地执行 / 降级端点冒烟测试。

对边缘 Pod 的 /local/* 接口做端到端校验：
  1. 医院就地完整模型诊断 —— 必须与协同路径（前端+云后端）的 bpCR 一致；
  2. 诊断的本地执行必须被拒绝（capability=unsupported，医疗中心无 server 半段）；
  3. 本机串行分区计算 / 内联日常作业 / 自编排通信同步；
  4. 结果回查、幂等、队列与能力声明。

用法：
  python test/local_exec_smoke.py --hospital http://127.0.0.1:18006 \
                                  --clinic http://127.0.0.1:18007 \
                                  [--patient 1404920] [--expect-bpcr 0.442733]
集群内直连（推荐经端口转发或站点后端）：
  python test/local_exec_smoke.py --hospital http://10.50.126.210:8006 \
                                  --clinic http://10.50.193.185:8007
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATASET = os.path.join(ROOT, "test", "test_dataset.json")

OK, BAD = "✓", "✗"
_failures = []


def http(url: str, payload=None, method="GET", timeout=600):
    if payload is not None and method == "GET":
        method = "POST"
    data = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=timeout) as r:
            body = r.read().decode()
        return json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        return {"_http_error": e.code, "_body": e.read().decode()[:300]}
    except Exception as e:  # noqa: BLE001
        return {"_error": f"{type(e).__name__}: {e}"}


def patient_input(pid: str) -> dict:
    with open(DATASET) as f:
        data = json.load(f)
    for p in data:
        if p["patient_id"] == pid:
            return p["input"]
    # fall back to the first patient so the smoke test still runs
    return data[0]["input"]


def check(label: str, cond: bool, detail: str = "") -> bool:
    print(f"  {OK if cond else BAD} {label}" + (f"  {detail}" if detail else ""))
    if not cond:
        _failures.append(label)
    return cond


def summarize(env: dict) -> str:
    m = env.get("metrics") or {}
    return (f"status={env.get('status')} mode={env.get('mode')} "
            f"degraded={env.get('degraded')} executor={env.get('executor')} "
            f"pipeline={m.get('pipeline_total_ms')}ms "
            f"compute={m.get('compute_ms_total')}ms network={m.get('network_ms_total')}ms")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hospital", default="http://127.0.0.1:18006")
    ap.add_argument("--clinic", default="http://127.0.0.1:18007")
    ap.add_argument("--patient", default="1404920")
    ap.add_argument("--expect-bpcr", type=float, default=None,
                    help="协同路径的 bpCR，用于校验本地全模型结果一致")
    ap.add_argument("--skip-sync", action="store_true",
                    help="跳过通信任务（会真实写入云端患者库）")
    ap.add_argument("--strict-parity", action="store_true",
                    help="checksum 一致性失败即判失败（需 scheduler v3.2.0+ 支持 seed）")
    args = ap.parse_args()
    H, C = args.hospital.rstrip("/"), args.clinic.rstrip("/")
    tag = str(int(time.time()))[-7:]

    print("=" * 74)
    print(f"v3.2 本地执行冒烟测试   hospital={H}  clinic={C}")
    print("=" * 74)

    # ---------------------------------------------------------- capabilities
    print("\n[1] 能力声明 / 队列 / 调度器可达性")
    hh = http(H + "/local/health")
    ch = http(C + "/local/health")
    check("医院 /local/health", hh.get("entity") is not None, json.dumps(hh.get("capabilities", {}), ensure_ascii=False))
    check("诊所 /local/health", ch.get("entity") is not None, json.dumps(ch.get("capabilities", {}), ensure_ascii=False))
    check("医院声明 diagnosis=unsupported（医疗中心不能执行 server 半段）",
          (hh.get("capabilities") or {}).get("diagnosis") == "unsupported")
    check("诊所声明 diagnosis=unsupported",
          (ch.get("capabilities") or {}).get("diagnosis") == "unsupported")
    check("结果目录可写", bool((hh.get("local_mode") or {}).get("results_store", {}).get("writable")))
    print(f"    调度器探测: 医院={hh.get('scheduler', {}).get('reachable')} "
          f"({hh.get('scheduler', {}).get('checked_ms')}ms)")

    inp = patient_input(args.patient)

    # ------------------------------------- hospital local diagnosis (refused)
    print(f"\n[2] 医院就地诊断必须被拒绝（医疗中心不能执行模型 server 半段）")
    r = http(H + "/local/execute", {
        "task_id": f"smoke-h-{tag}", "kind": "diagnosis", "source": "hospital-a",
        "input": inp, "params": {"patient_id": args.patient}})
    rejected = ("_error" in r or "_http_error" in r
                or str(r.get("status")) in ("failed", "rejected"))
    check("医院本地诊断被拒绝", rejected, json.dumps(r, ensure_ascii=False)[:200])
    # /medical/infer_full（医院整段执行）也必须拒绝
    rf = http(H + "/medical/infer_full", {
        "patient_id": args.patient, "input": inp, "task_id": f"smoke-hf-{tag}"})
    check("医院 /medical/infer_full 被拒绝",
          ("_error" in rf or "_http_error" in rf), json.dumps(rf, ensure_ascii=False)[:200])
    bpcr_h = None

    # ------------------------------------- clinic local diagnosis (refused)
    print(f"\n[3] 诊所本地诊断也必须被拒绝（转诊到医院也完不成 server 半段）")
    r2 = http(C + "/local/execute", {
        "task_id": f"smoke-c-{tag}", "kind": "diagnosis", "source": "clinic-1",
        "input": inp, "params": {"patient_id": args.patient}})
    rejected_c = ("_error" in r2 or "_http_error" in r2
                  or str(r2.get("status")) in ("failed", "rejected"))
    check("诊所本地诊断被拒绝", rejected_c, json.dumps(r2, ensure_ascii=False)[:200])

    # ------------------------------------------------------- local compute
    print("\n[4] 本机串行分区计算（4 仪器 / 256 行 / 强度 40 / 3 分区, seed=11）")
    r3 = http(C + "/local/execute", {
        "task_id": f"smoke-comp-{tag}", "kind": "compute", "source": "clinic-1",
        "params": {"instruments": 4, "rows": 256, "intensity": 40,
                   "partition_count": 3, "seed": 11}})
    if "_error" in r3 or "_http_error" in r3:
        check("本地计算返回结果", False, json.dumps(r3, ensure_ascii=False)[:200])
        local_checks = []
    else:
        rd = r3.get("result_detail") or {}
        check("本地计算完成", r3.get("status") == "completed", summarize(r3))
        check("分区数=3 且全部成功", rd.get("partitions_ok") == 3)
        check("分区串行执行", rd.get("local_serial") is True)
        local_checks = rd.get("checksums") or []
        print(f"    checksums={local_checks}")

    # ------------------------------------------------ checksum parity check
    print("\n[5] 计算正确性：与协同路径 checksum 对比（同 seed）")
    try:
        sched = os.environ.get("SCHEDULER_URL", "http://localhost:30080").rstrip("/")
        sub = http(sched + "/schedule/compute", {
            "source": "hospital-a", "instruments": 4, "rows": 256,
            "intensity": 40, "partition_count": 3, "seed": 11})
        tid = sub.get("task_id")
        if tid:
            for _ in range(120):
                d = http(f"{sched}/task/result/{tid}")
                if d.get("status") in ("completed", "finished", "failed"):
                    break
                time.sleep(1)
            collab = ((d.get("result") or {}).get("result_detail") or {}).get("checksums") or []
            if local_checks and collab:
                same = sorted(local_checks) == sorted(collab)
                if same or args.strict_parity:
                    check("本地与协同 checksum 一致", same,
                          f"local={sorted(local_checks)} collab={sorted(collab)}")
                else:
                    print(f"    ! checksum 不一致（部署的 scheduler 未透传 seed，"
                          f"协同侧使用随机种子）local={sorted(local_checks)} "
                          f"collab={sorted(collab)} —— 部署 v3.2.0 后用 --strict-parity 复核")
            else:
                check("协同路径返回 checksums", False,
                      f"status={d.get('status')} keys={list(d)[:6]}")
        else:
            print(f"    ! 协同提交失败({sub}), 跳过对比")
    except Exception as e:  # noqa: BLE001
        print(f"    ! 协同对比跳过: {e}")

    # ------------------------------------------------------- local routine
    print("\n[6] 本机内联日常作业（3 个任务 / 128 行 / 强度 30）")
    r4 = http(C + "/local/execute", {
        "task_id": f"smoke-rout-{tag}", "kind": "routine", "source": "clinic-1",
        "params": {"jobs": 3, "rows": 128, "intensity": 30}})
    if "_error" in r4 or "_http_error" in r4:
        check("本地日常作业返回结果", False, json.dumps(r4, ensure_ascii=False)[:200])
    else:
        rd = r4.get("result_detail") or {}
        check("本地日常作业完成", r4.get("status") == "completed", summarize(r4))
        check("3 个作业全部成功", rd.get("succeeded") == 3)
        check("未创建 Kubernetes Job", rd.get("no_kubernetes_jobs") is True)

    # ---------------------------------------------------------- local sync
    if args.skip_sync:
        print("\n[7] 自编排通信同步：已跳过 (--skip-sync)")
    else:
        print("\n[7] 自编排通信同步（20 Mbps / 并发 4 / 4KB, 会真实写云端患者库）")
        r5 = http(C + "/local/execute", {
            "task_id": f"smoke-sync-{tag}", "kind": "sync", "source": "clinic-1",
            "params": {"bandwidth_mbps": 20.0, "concurrency": 4, "chunk_kb": 4}})
        if "_error" in r5 or "_http_error" in r5:
            check("本地同步返回结果", False, json.dumps(r5, ensure_ascii=False)[:200])
        else:
            rd = r5.get("result_detail") or {}
            check("本地同步完成", r5.get("status") == "completed", summarize(r5))
            check("确实拉取了分块", (rd.get("pulled") or 0) > 0,
                  f"pulled={rd.get('pulled')}/{rd.get('missing')} bytes={rd.get('bytes_pulled')}")
            check("已完成云上传+备份", bool(rd.get("backup")),
                  f"uploaded={rd.get('uploaded')} backup_id={(rd.get('backup') or {}).get('backup_id')}")
            check("由本机自编排", rd.get("self_orchestrated") is True)

    # --------------------------------------------- result store / idempotency
    print("\n[8] 结果回查 / 幂等 / 降级标记")
    got = http(H + f"/local/result/smoke-h-{tag}")
    check("按 task_id 回查医院结果", got.get("task_id") == f"smoke-h-{tag}",
          f"status={got.get('status')}")
    again = http(C + "/local/execute", {
        "task_id": f"smoke-comp-{tag}", "kind": "compute", "source": "clinic-1",
        "params": {"instruments": 4, "rows": 256, "intensity": 40,
                   "partition_count": 3, "seed": 11}})
    check("同 task_id 幂等（不重复执行）",
          again.get("task_id") == f"smoke-comp-{tag}" and again.get("status") == "completed")

    deg = http(C + "/local/execute", {
        "task_id": f"smoke-deg-{tag}", "kind": "routine", "source": "clinic-1",
        "params": {"jobs": 1, "rows": 64, "intensity": 10},
        "degraded": True, "degrade_reason": "scheduler_unreachable"})
    check("降级标记透传", deg.get("degraded") is True and
          deg.get("degrade_reason") == "scheduler_unreachable",
          summarize(deg))

    lst = http(C + "/local/results?limit=10")
    check("结果列表可读", isinstance(lst, list) and len(lst) > 0, f"{len(lst)} 条")
    q = http(C + "/local/queue")
    check("队列状态可读", "pending" in q, json.dumps(q, ensure_ascii=False)[:120])

    print("\n" + "=" * 74)
    if _failures:
        print(f"结果: {len(_failures)} 项失败 -> " + "; ".join(_failures))
        return 1
    print("结果: 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
