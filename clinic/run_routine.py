#!/usr/bin/env python3
"""v3.0 routine task runner - executed as a short-lived Kubernetes Job pod.

Reads one JSON argument {rows, instruments, intensity, seed, partition, count},
performs a light CPU/IO routine computation and prints a single JSON result
line on stdout, then exits 0. The scheduler parses that line from the pod log
and deletes the Job afterwards.
"""
import json
import os
import sys

sys.path.insert(0, "/app")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from common import v3_common as v3  # noqa: E402

if __name__ == "__main__":
    args = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
    res = v3.compute_partition(
        rows=int(args.get("rows", 128)),
        instruments=int(args.get("instruments", 3)),
        intensity=int(args.get("intensity", 30)),
        seed=int(args.get("seed", 7)),
        partition=int(args.get("partition", 0)),
        count=int(args.get("count", 1)),
    )
    res.update({
        "job_pod": os.environ.get("POD_NAME", "?"),
        "node": os.environ.get("NODE_NAME", "?"),
        "role": "routine-job",
    })
    print(json.dumps(res, ensure_ascii=False))
