#!/usr/bin/env python3
"""v3.2 调度模式测试：collaborative / local / auto / 强制降级。

打到一个 scheduler 实例（本地或 NodePort），逐模式提交四类任务并断言：
  * mode=local    → 由发起 Pod 就地执行（result.mode=local, orchestrator=pod）
  * mode=auto     → 依赖健康时协同；依赖不可用时自动降级并标记 degraded
  * force_degraded→ 直接降级执行
  * 诊断任务在本地/协同两种模式下 bpCR 必须一致（正确性校验）

用法：
  python test/scheduler_mode_test.py --base http://127.0.0.1:18000 \
        [--expect-auto-degrade] [--patient 1404920]
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

OK, BAD = "✓", "✗"
_fail = []


def http(url, payload=None, method="GET", timeout=900):
    if payload is not None and method == "GET":
        method = "POST"
    data = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with op.open(req, timeout=timeout) as r:
            body = r.read().decode()
        return json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        return {"_http_error": e.code, "_body": e.read().decode()[:300]}
    except Exception as e:  # noqa: BLE001
        return {"_error": f"{type(e).__name__}: {e}"}


def check(label, cond, detail=""):
    print(f"  {OK if cond else BAD} {label}" + (f"  {detail}" if detail else ""))
    if not cond:
        _fail.append(label)
    return cond


def submit_and_wait(base, kind, body, wait=900):
    t0 = time.perf_counter()
    sub = http(f"{base}/schedule/{kind}", body)
    if "_error" in sub or "_http_error" in sub:
        return {"_error": json.dumps(sub, ensure_ascii=False)[:200]}
    tid = sub.get("task_id")
    deadline = time.time() + wait
    while time.time() < deadline:
        d = http(f"{base}/task/result/{tid}")
        if d.get("status") in ("completed", "finished", "failed"):
            d["_client_ms"] = round((time.perf_counter() - t0) * 1000, 2)
            d["_submitted"] = sub
            return d
        time.sleep(1)
    return {"status": "timeout", "task_id": tid}


def res_of(d):
    return (d or {}).get("result") or {}


def line(d):
    r = res_of(d)
    m = r.get("metrics") or {}
    return (f"status={d.get('status')} mode={r.get('mode')} "
            f"degraded={r.get('degraded')} reason={r.get('degrade_reason')} "
            f"orchestrator={r.get('orchestrator')} executor={r.get('executor')} "
            f"e2e={m.get('e2e_total_ms')}ms client={d.get('_client_ms')}ms")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=os.environ.get("SCHEDULER_URL",
                                                     "http://127.0.0.1:18000"))
    ap.add_argument("--patient", default="1404920")
    ap.add_argument("--expect-auto-degrade", action="store_true",
                    help="断言 mode=auto 走降级（依赖不可用环境，如本地测试）")
    ap.add_argument("--expect-auto-collab", action="store_true",
                    help="断言 mode=auto 走协同（依赖健康环境，如集群内）")
    args = ap.parse_args()
    B = args.base.rstrip("/")
    print("=" * 74)
    print(f"v3.2 调度模式测试  base={B}")
    print("=" * 74)

    print("\n[1] 协同模式（回归）：诊断")
    collab = submit_and_wait(B, "diagnosis", {
        "source": "hospital-a", "patient_id": args.patient,
        "mode": "collaborative"})
    check("协同诊断完成", collab.get("status") in ("completed", "finished"), line(collab))
    r = res_of(collab)
    check("模式标记为 collaborative", r.get("mode") == "collaborative")
    bpcr_collab = (r.get("result_detail") or {}).get("bpCR_probability")
    check("返回 bpCR", bpcr_collab is not None, f"bpCR={bpcr_collab}")

    print("\n[2] 本地执行模式：诊断（医院就地完整模型）")
    local = submit_and_wait(B, "diagnosis", {
        "source": "hospital-a", "patient_id": args.patient, "mode": "local"})
    check("本地诊断完成", local.get("status") in ("completed", "finished"), line(local))
    rl = res_of(local)
    check("mode=local / orchestrator=pod",
          rl.get("mode") == "local" and rl.get("orchestrator") == "pod")
    check("未被标记降级", rl.get("degraded") is False)
    bpcr_local = (rl.get("result_detail") or {}).get("bpCR_probability")
    if bpcr_collab is not None and bpcr_local is not None:
        check("本地与协同 bpCR 一致", abs(bpcr_local - bpcr_collab) < 1e-3,
              f"local={bpcr_local} collab={bpcr_collab}")
    m = rl.get("metrics") or {}
    check("含委派/本机分段指标",
          "delegation_ms" in m and "pod_pipeline_ms" in m, json.dumps(m, ensure_ascii=False))

    print("\n[3] 本地执行模式：计算 + 日常（发起端=诊所）")
    lc = submit_and_wait(B, "compute", {
        "source": "clinic-1", "instruments": 4, "rows": 256, "intensity": 40,
        "partition_count": 3, "seed": 11, "mode": "local"})
    check("本地计算完成", lc.get("status") in ("completed", "finished"), line(lc))
    rd = (res_of(lc).get("result_detail") or {})
    check("3 分区串行完成", rd.get("partitions_ok") == 3,
          f"checksums={rd.get('checksums')}")
    lr = submit_and_wait(B, "routine", {
        "source": "clinic-1", "jobs": 2, "rows": 128, "intensity": 30,
        "mode": "local"})
    check("本地日常作业完成", lr.get("status") in ("completed", "finished"), line(lr))

    print("\n[4] 自动模式（auto）：按依赖健康度自动选择")
    auto = submit_and_wait(B, "compute", {
        "source": "clinic-1", "instruments": 2, "rows": 128, "intensity": 20,
        "partition_count": 2, "seed": 5, "mode": "auto"})
    check("auto 计算完成", auto.get("status") in ("completed", "finished"), line(auto))
    ra = res_of(auto)
    if args.expect_auto_collab:
        check("auto 计算保持协同（dc 健康）",
              ra.get("mode") == "collaborative" and ra.get("degraded") is False,
              f"mode={ra.get('mode')} reason={ra.get('degrade_reason')}")
    print(f"    auto(compute) 实际选择: mode={ra.get('mode')} "
          f"degraded={ra.get('degraded')} reason={ra.get('degrade_reason')}")

    # routine 的依赖是 Kubernetes Job API：本地/无 Job 权限环境下 auto 必须降级
    auto_r = submit_and_wait(B, "routine", {
        "source": "clinic-1", "jobs": 2, "rows": 128, "intensity": 30,
        "mode": "auto"})
    check("auto 日常作业完成", auto_r.get("status") in ("completed", "finished"),
          line(auto_r))
    rr = res_of(auto_r)
    print(f"    auto(routine) 实际选择: mode={rr.get('mode')} "
          f"degraded={rr.get('degraded')} reason={rr.get('degrade_reason')}")
    if args.expect_auto_degrade:
        check("auto 依赖不可用时降级为本地",
              rr.get("mode") == "local" and rr.get("degraded") is True,
              f"reason={rr.get('degrade_reason')}")
        check("降级原因=依赖不可用",
              rr.get("degrade_reason") in ("kube_job_api_unavailable",
                                           "dc_services_unreachable",
                                           "medical_server_unreachable",
                                           "hospital_unreachable"),
              f"reason={rr.get('degrade_reason')}")

    print("\n[5] 强制降级开关 force_degraded")
    fd = submit_and_wait(B, "routine", {
        "source": "clinic-1", "jobs": 1, "rows": 64, "intensity": 10,
        "mode": "collaborative", "force_degraded": True})
    check("强制降级任务完成", fd.get("status") in ("completed", "finished"), line(fd))
    rf = res_of(fd)
    check("标记 degraded=forced",
          rf.get("mode") == "local" and rf.get("degraded") is True
          and rf.get("degrade_reason") == "forced")

    print("\n[6] 任务列表精简视图包含模式字段")
    recent = http(f"{B}/tasks/recent?limit=5")
    ok = isinstance(recent, list) and any(
        "mode" in (t.get("result") or {}) for t in recent)
    check("/tasks/recent 含 mode/degraded", ok)

    print("\n" + "=" * 74)
    if _fail:
        print(f"结果: {len(_fail)} 项失败 -> " + "; ".join(_fail))
        return 1
    print("结果: 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
