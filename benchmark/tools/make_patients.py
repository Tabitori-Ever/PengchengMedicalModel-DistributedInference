#!/usr/bin/env python3
"""Build-time helper: turn test/test_dataset.json into the benchmark site's
bundled patient file (/app/patients.json).

The site must be able to run diagnosis tasks while the scheduler is down (that
is the whole point of the degradation drill), and the scheduler is where
/test/patient/{id} normally lives - so the site image carries the inputs.

This runs *inside the image build*; the generated file is never committed to the
repository. For ad-hoc local runs use benchmark/tools/extract_patients.py, which
writes to /tmp instead.

Usage:
    python benchmark/tools/make_patients.py --dataset test/test_dataset.json \
                                            --out /app/patients.json
"""
import argparse
import json


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dataset", default="test/test_dataset.json")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    with open(args.dataset, encoding="utf-8") as f:
        data = json.load(f)

    required = ("dce_image", "dwi_image", "clinical", "radiomics", "patient_ids")
    kept = []
    for p in data:
        inp = p.get("input") or {}
        if not all(k in inp for k in required):
            continue
        kept.append({"patient_id": str(p["patient_id"]),
                     "bpCR": p.get("bpCR"),
                     "hospital": p.get("hospital"),
                     "input": inp})

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(kept, f, ensure_ascii=False)

    print(f"[make_patients] {len(kept)}/{len(data)} patients -> {args.out}")
    return 0 if kept else 1


if __name__ == "__main__":
    raise SystemExit(main())
