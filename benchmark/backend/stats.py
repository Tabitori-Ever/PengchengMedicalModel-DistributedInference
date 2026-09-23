"""Aggregation + correctness statistics for the benchmark site.

Grouping follows 设计方案 §6.2/§6.4: every metric is reported per
(kind, source, mode_used, degraded) group so that collaborative / local /
degraded runs of the same task can be compared directly; ``group_by=spec``
additionally splits by parameter fingerprint (same params, same patient).

Correctness column
------------------
* diagnosis: ``bpcr_actual`` (probability returned by the pipeline) against
  ``bpcr_expected`` (label from the bundled dataset).  A probability is
  binarised at 0.5 when the expected value is a 0/1 label.
* compute: the attempt's ``checksums`` against the collaborative reference
  checksums for the same params fingerprint (same run preferred).  When no
  collaborative reference exists the column reports ``n/a``.
"""
import math
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

METRIC_COLUMNS: Tuple[str, ...] = (
    "client_total_ms", "queue_wait_ms", "pipeline_total_ms",
    "compute_ms_total", "network_ms_total", "degrade_switch_ms",
)

GROUP_FIELDS: Tuple[str, ...] = ("kind", "source", "mode_used", "degraded")


def _nums(rows: Iterable[Dict[str, Any]], key: str) -> List[float]:
    out: List[float] = []
    for row in rows:
        val = row.get(key)
        if val is None or isinstance(val, bool):
            continue
        try:
            out.append(float(val))
        except (TypeError, ValueError):
            continue
    return out


def percentile(values: Sequence[float], q: float) -> Optional[float]:
    """Linear-interpolation percentile (q in 0..100)."""
    vals = sorted(float(v) for v in values if v is not None)
    if not vals:
        return None
    if len(vals) == 1:
        return round(vals[0], 3)
    pos = (len(vals) - 1) * (float(q) / 100.0)
    lo, hi = math.floor(pos), math.ceil(pos)
    if lo == hi:
        return round(vals[lo], 3)
    return round(vals[lo] + (vals[hi] - vals[lo]) * (pos - lo), 3)


def _mean(values: Sequence[float]) -> Optional[float]:
    vals = [float(v) for v in values if v is not None]
    if not vals:
        return None
    return round(sum(vals) / len(vals), 3)


def latency_stats(values: Sequence[float]) -> Dict[str, Optional[float]]:
    vals = [float(v) for v in values if v is not None]
    if not vals:
        return {"n": 0, "mean": None, "p50": None, "p95": None,
                "min": None, "max": None}
    return {"n": len(vals), "mean": _mean(vals),
            "p50": percentile(vals, 50), "p95": percentile(vals, 95),
            "min": round(min(vals), 3), "max": round(max(vals), 3)}


# --------------------------------------------------------------------------- #
# correctness                                                                 #
# --------------------------------------------------------------------------- #
def _checksums(row: Dict[str, Any]) -> List[str]:
    detail = row.get("result_detail") or {}
    raw = detail.get("checksums") or detail.get("checksum") or []
    if isinstance(raw, str):
        raw = [raw]
    return [str(c) for c in raw if c not in (None, "")]


def _diagnosis_correctness(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    expected, actual = row.get("bpcr_expected"), row.get("bpcr_actual")
    if expected is None or actual is None:
        return None
    exp, act = float(expected), float(actual)
    method = "abs_diff<=1e-6"
    if exp in (0.0, 1.0) and act not in (0.0, 1.0):
        method = "binarise(actual>=0.5)==expected"
        matched = (1 if act >= 0.5 else 0) == int(exp)
    else:
        matched = abs(act - exp) <= 1e-6
    return {"matched": matched, "expected": exp, "actual": act,
            "delta": round(act - exp, 6), "method": method}


def build_checksum_refs(ref_rows: Sequence[Dict[str, Any]]
                        ) -> Dict[str, List[Dict[str, Any]]]:
    """spec_key -> collaborative reference checksums (from db.checksum_reference_rows)."""
    refs: Dict[str, List[Dict[str, Any]]] = {}
    for row in ref_rows:
        if row.get("status") != "completed":
            continue
        if row.get("mode_used") != "collaborative":
            continue
        sums = _checksums(row)
        if not sums:
            continue
        refs.setdefault(str(row.get("spec_key")), []).append(
            {"run_id": row.get("run_id"), "checksums": sorted(sums)})
    return refs


def _compute_correctness(row: Dict[str, Any],
                         refs: Dict[str, List[Dict[str, Any]]]
                         ) -> Optional[Dict[str, Any]]:
    if row.get("mode_used") == "collaborative":
        return None  # this attempt *is* the reference side
    sums = _checksums(row)
    if not sums:
        return None
    candidates = refs.get(str(row.get("spec_key"))) or []
    if not candidates:
        return None
    ref = next((c for c in candidates if c["run_id"] == row.get("run_id")),
               candidates[0])
    matched = sorted(sums) == sorted(ref["checksums"])
    return {"matched": matched, "checksums": sorted(sums),
            "reference": ref["checksums"], "reference_run_id": ref["run_id"],
            "method": "sorted(checksums) equality"}


def correctness(rows: Sequence[Dict[str, Any]], kind: str,
                refs: Optional[Dict[str, List[Dict[str, Any]]]] = None
                ) -> Dict[str, Any]:
    """Per-group correctness summary for one task kind."""
    checked = matched = mismatched = skipped = 0
    samples: List[Dict[str, Any]] = []
    if kind == "compute":
        refs = refs or {}
        for row in rows:
            verdict = _compute_correctness(row, refs)
            if verdict is None:
                skipped += 1
                continue
            checked += 1
            if verdict["matched"]:
                matched += 1
            else:
                mismatched += 1
                if len(samples) < 3:
                    samples.append({"attempt_id": row.get("id"),
                                    "run_id": row.get("run_id"), **verdict})
        method = "local checksums vs collaborative checksums (same params)"
    elif kind == "diagnosis":
        for row in rows:
            verdict = _diagnosis_correctness(row)
            if verdict is None:
                skipped += 1
                continue
            checked += 1
            if verdict["matched"]:
                matched += 1
            else:
                mismatched += 1
                if len(samples) < 3:
                    samples.append({"attempt_id": row.get("id"),
                                    "run_id": row.get("run_id"), **verdict})
        method = "bpcr_actual vs bpcr_expected (dataset label)"
    else:
        method = "not applicable for this task kind"

    if checked == 0:
        status = "n/a"
    elif mismatched == 0:
        status = "match"
    else:
        status = "mismatch"
    out: Dict[str, Any] = {"status": status, "checked": checked,
                           "match": matched, "mismatch": mismatched,
                           "skipped": skipped, "method": method}
    if samples:
        out["mismatch_samples"] = samples
    return out


# --------------------------------------------------------------------------- #
# grouping                                                                    #
# --------------------------------------------------------------------------- #
def _group_key(row: Dict[str, Any], group_by: Sequence[str]) -> Tuple[Any, ...]:
    return tuple(row.get(field) for field in group_by)


def summarize(rows: Sequence[Dict[str, Any]], kind: Optional[str] = None,
              refs: Optional[Dict[str, List[Dict[str, Any]]]] = None
              ) -> Dict[str, Any]:
    """n / success / fail + latency percentiles + stage means + correctness."""
    kind = kind or (rows[0].get("kind") if rows else None)
    total = len(rows)
    completed = sum(1 for r in rows if r.get("status") == "completed")
    failed = sum(1 for r in rows if r.get("status") == "failed")
    cancelled = sum(1 for r in rows if r.get("status") == "cancelled")
    pending = sum(1 for r in rows if r.get("status") in ("pending", "running"))
    latency = latency_stats(_nums(rows, "client_total_ms"))
    stage_means: Dict[str, Optional[float]] = {}
    metric_samples: Dict[str, int] = {}
    for col in METRIC_COLUMNS[1:]:
        vals = _nums(rows, col)
        stage_means[col] = _mean(vals)
        metric_samples[col] = len(vals)
    out: Dict[str, Any] = {
        "n": total,
        "success": completed,
        "fail": failed,
        "success_rate": round(completed / total, 4) if total else None,
        "client_total_ms": latency,
        "stage_means": stage_means,
        "metrics_samples": metric_samples,
    }
    if cancelled:
        out["cancelled"] = cancelled
    if pending:
        out["pending"] = pending
    if kind:
        out["correctness"] = correctness(rows, str(kind), refs)
    return out


def group_rows(rows: Sequence[Dict[str, Any]],
               group_by: Sequence[str] = GROUP_FIELDS,
               kind: Optional[str] = None,
               refs: Optional[Dict[str, List[Dict[str, Any]]]] = None
               ) -> List[Dict[str, Any]]:
    buckets: Dict[Tuple[Any, ...], List[Dict[str, Any]]] = {}
    for row in rows:
        buckets.setdefault(_group_key(row, group_by), []).append(row)

    groups: List[Dict[str, Any]] = []
    for key, bucket in buckets.items():
        group: Dict[str, Any] = {field: key[i] for i, field in enumerate(group_by)}
        group["degraded"] = bool(group.get("degraded"))
        group.update(summarize(bucket, bucket[0].get("kind") if bucket else kind, refs))
        spec_keys = sorted({str(r.get("spec_key")) for r in bucket if r.get("spec_key")})
        group["spec_keys"] = spec_keys
        first_params = next((r.get("params") for r in bucket if r.get("params")), {})
        group["params_sample"] = {k: v for k, v in (first_params or {}).items()
                                  if k != "input"}
        reasons = sorted({str(r.get("degrade_reason")) for r in bucket
                          if r.get("degrade_reason")})
        if reasons:
            group["degrade_reasons"] = reasons
        groups.append(group)
    groups.sort(key=lambda g: (str(g.get("kind")), str(g.get("source")),
                               str(g.get("mode_used")), bool(g.get("degraded")),
                               ",".join(g.get("spec_keys") or [])))
    return groups


def matrix(rows: Sequence[Dict[str, Any]],
           refs: Optional[Dict[str, List[Dict[str, Any]]]] = None
           ) -> List[Dict[str, Any]]:
    """Full (kind x source x mode_used x degraded) aggregate table."""
    return group_rows(rows, GROUP_FIELDS, refs=refs)
