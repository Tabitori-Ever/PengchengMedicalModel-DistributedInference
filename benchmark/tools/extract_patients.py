#!/usr/bin/env python3
"""Extract the offline patient dataset used by the benchmark site backend.

The deployed image ships ``/app/patients.json`` (see 设计方案 §6.1/§8.5): a JSON
array of ``{patient_id, bpCR, hospital, input:{dce_image, dwi_image, clinical,
radiomics, patient_ids}}`` so that diagnosis runs still work when the scheduler
is down.  This helper regenerates that file from ``test/test_dataset.json`` for
local testing only — the default output goes to ``/tmp``, never into the repo.

Usage:
    python benchmark/tools/extract_patients.py                 # -> /tmp/bench-patients.json
    python benchmark/tools/extract_patients.py --out /tmp/x.json --patients 1404920,584222
"""
import argparse
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATASET = REPO_ROOT / "test" / "test_dataset.json"
DEFAULT_OUT = Path("/tmp/bench-patients.json")

REQUIRED_KEYS = ("dce_image", "dwi_image", "clinical", "radiomics", "patient_ids")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dataset", default=str(DEFAULT_DATASET),
                    help=f"source dataset (default: {DEFAULT_DATASET})")
    ap.add_argument("--out", default=str(DEFAULT_OUT),
                    help=f"output file (default: {DEFAULT_OUT}, must be outside the repo)")
    ap.add_argument("--patients", default="",
                    help="optional comma-separated patient_id whitelist")
    ap.add_argument("--indent", type=int, default=None,
                    help="pretty-print indent (default: compact)")
    args = ap.parse_args()

    src = Path(args.dataset)
    if not src.is_file():
        print(f"error: dataset not found: {src}", file=sys.stderr)
        return 2
    out = Path(args.out)
    repo = str(REPO_ROOT.resolve())
    if str(out.resolve()).startswith(repo + os.sep):
        print(f"error: refusing to write inside the repository ({out}); "
              f"use /tmp (design: the image ships this file, the repo does not)",
              file=sys.stderr)
        return 2

    with src.open(encoding="utf-8") as fh:
        dataset = json.load(fh)
    if not isinstance(dataset, list):
        print("error: dataset must be a JSON array", file=sys.stderr)
        return 2

    wanted = {p.strip() for p in args.patients.split(",") if p.strip()}
    patients = []
    for entry in dataset:
        pid = str(entry.get("patient_id", "")).strip()
        if not pid or (wanted and pid not in wanted):
            continue
        inp = entry.get("input") or {}
        missing = [k for k in REQUIRED_KEYS if k not in inp]
        if missing:
            print(f"warning: patient {pid} is missing input keys {missing}",
                  file=sys.stderr)
        patients.append({"patient_id": pid, "bpCR": entry.get("bpCR"),
                         "hospital": entry.get("hospital"), "input": inp})

    if not patients:
        print("error: no patients selected", file=sys.stderr)
        return 2

    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as fh:
        json.dump(patients, fh, ensure_ascii=False, indent=args.indent)
    size_mb = out.stat().st_size / 1024 / 1024
    print(f"wrote {len(patients)} patients -> {out} ({size_mb:.1f} MB)")
    print("ids: " + ", ".join(p["patient_id"] for p in patients))
    return 0


if __name__ == "__main__":
    sys.exit(main())
