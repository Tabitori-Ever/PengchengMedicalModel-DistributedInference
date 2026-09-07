#!/usr/bin/env python3
"""v3.0 comprehensive test - 四类任务混合 (diagnosis/compute/sync/routine).

Submits a mixed suite initiated from hospital / clinic sources, waits for
completion and prints a detailed execution table. Saves full results to
test/output/v3_comprehensive.json.

Usage:
  python test/run_v3_comprehensive.py [--base http://localhost:30080]
                                      [--diagnosis 2 --compute 1 --sync 1 --routine 3]
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

BASE = os.environ.get("SCHEDULER_URL", "http://localhost:30080")
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")


def post(path: str, body: dict):
    req = urllib.request.Request(BASE + path, method="POST",
                                 data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{path} -> HTTP {e.code}: {e.read()[:200]}")


def get(path: str):
    with urllib.request.urlopen(BASE + path, timeout=60) as r:
        return json.loads(r.read())


def poll(task_id: str, wait: float = 600):
    t0 = time.time()
    while time.time() - t0 < wait:
        d = get(f"/task/result/{task_id}")
        if d.get("status") in ("finished", "completed", "failed"):
            return d
        time.sleep(2)
    return {"status": "timeout", "task_id": task_id}


def main():
    global BASE
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=BASE)
    ap.add_argument("--diagnosis", type=int, default=2)
    ap.add_argument("--compute", type=int, default=1)
    ap.add_argument("--sync", type=int, default=1)
    ap.add_argument("--routine", type=int, default=3)
    args = ap.parse_args()
    BASE = args.base

    patients = get("/test/patients")
    pid = patients[0]["patient_id"] if patients else None

    suite = []
    # diagnosis: 1 from hospital-a + (clinics) forwarded automatically
    suite.append({"kind": "diagnosis", "source": "hospital-a",
                  "patient_id": pid, "note": "医院直接执行"})
    for i in range(max(0, args.diagnosis - 1)):
        clinic = "clinic-1" if i % 2 == 0 else "clinic-2"
        suite.append({"kind": "diagnosis", "source": clinic,
                      "patient_id": pid, "note": "clinic 发起(自动转诊)"})
    suite += [{"kind": "compute", "source": "hospital-a",
               "instruments": 4, "partition_count": 3} for _ in range(args.compute)]
    suite += [{"kind": "sync", "source": "clinic-1",
               "bandwidth_mbps": 20.0, "concurrency": 4} for _ in range(args.sync)]
    suite += [{"kind": "routine", "source": "clinic-2",
               "jobs": 1, "rows": 128, "intensity": 30} for _ in range(args.routine)]

    print(f"[v3.0 综合测试] 共 {len(suite)} 个任务 -> {BASE}\n")
    rows = []
    for item in suite:
        kind = item.pop("kind")
        note = item.pop("note", "")
        body = {"source": item.pop("source"), **item}
        try:
            tid = post(f"/schedule/{kind}", body)["task_id"]
        except RuntimeError as e:
            print(f"  ✗ 提交失败 {kind}/{body.get('source')}: {e}")
            continue
        rows.append({"kind": kind, "source": body["source"], "task_id": tid,
                     "note": note, "status": "running"})
        print(f"  · 已提交 [{kind:9s}] {body['source']} -> {tid}  {note}")

    print("\n等待完成...\n")
    final = []
    for row in rows:
        d = poll(row["task_id"])
        row.update({"status": d.get("status"),
                    "error": d.get("error"),
                    "result": d.get("result")})
        final.append(row)
        rd = d.get("result") or {}
        summary = "—"
        det = rd.get("result_detail") or {}
        if row["kind"] == "diagnosis":
            fwd = rd.get("forwarded_to")
            summary = (f"bpCR={det.get('bpCR_probability')}"
                       + (f", 转诊→{fwd}" if fwd else ", 医院直接执行"))
        elif row["kind"] == "compute":
            summary = f"分区 {len(det.get('checksums', []))} ok, 字节 {det.get('total_bytes')}"
        elif row["kind"] == "sync":
            summary = (f"拉取 {det.get('pulled')}/{det.get('missing')} 分块, "
                       f"{det.get('bytes_pulled')}B, 上传 {det.get('uploaded')}, "
                       f"备份 {str(det.get('backup', {}).get('backup_id'))}")
        elif row["kind"] == "routine":
            summary = f"Job 成功 {det.get('succeeded')}"
        print(f"[{row['status']:9s}] {row['kind']:9s} {row['source']} "
              f"{row['task_id']}  {summary}"
              + (f"  ERR={row['error']}" if row.get("error") else ""))

    os.makedirs(OUT_DIR, exist_ok=True)
    out = os.path.join(OUT_DIR, "v3_comprehensive.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(final, f, ensure_ascii=False, indent=2)
    print(f"\n结果已保存: {out}")
    failed = [r for r in final if r.get("status") != "finished"
              and r.get("status") != "completed"]
    print(f"通过 {len(final) - len(failed)}/{len(final)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
