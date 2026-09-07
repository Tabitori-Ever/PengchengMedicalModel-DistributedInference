"""v3.0 shared business helpers for compute / patient-db sync simulation.

Deterministic on purpose: identical inputs produce identical outputs on every
pod so peer-served chunks and worker partitions are verifiable (checksums
match) without shipping bulk data between nodes.
"""
import hashlib
import json
import os
import random
import time

# ---------------------------------------------------------------- compute ----
def gen_readings(rows: int, instruments: int, seed: int) -> list:
    """Simulate N instruments producing `rows` readings each (0..1 floats)."""
    rnd = random.Random(seed)
    out = []
    for _ in range(instruments):
        out.append([round(rnd.random(), 6) for _ in range(rows)])
    return out


def compute_partition(rows: int, instruments: int, intensity: int,
                      seed: int, partition: int, count: int) -> dict:
    """Heavy-ish CPU partition: generate readings then intensity passes of
    rolling statistics + incremental sha256 over a compact byte stream."""
    readings = gen_readings(rows, instruments, seed)
    start = time.perf_counter()
    digest = hashlib.sha256()
    digest.update(f"{partition}|{count}|{seed}|{instruments}".encode())
    acc = [0.0] * instruments
    n = len(readings)
    for _ in range(max(1, intensity)):
        for i, ch in enumerate(readings):
            s = 0.0
            for v in ch:
                s += v
            acc[i % instruments] += s / max(n, 1)
        digest.update(json.dumps(acc[-4:], ensure_ascii=False)[:1024].encode())
    cpu_ms = (time.perf_counter() - start) * 1000
    # simulated bytes processed = readings float32 + stats
    bytes_ = rows * instruments * 4 * max(1, intensity)
    return {
        "partition": partition,
        "count": count,
        "rows": rows,
        "instruments": instruments,
        "intensity": max(1, intensity),
        "bytes": bytes_,
        "checksum": digest.hexdigest()[:24],
        "cpu_ms": round(cpu_ms, 2),
    }


# ------------------------------------------------------------ patient db ----
def item_ids(version: str, count: int) -> list:
    """Deterministic pseudo patient-record ids for a DB version."""
    base = int(hashlib.sha1(version.encode()).hexdigest()[:8], 16)
    return [f"rec-{version}-{base + i:06d}" for i in range(count)]


def chunk_blob(item_id: str, chunk_size: int = 4096) -> str:
    """Deterministic payload for an item id (same on every pod)."""
    rnd = random.Random(int(hashlib.sha1(item_id.encode()).hexdigest()[:8], 16))
    blob = "".join(rnd.choice("0123456789abcdef") for _ in range(chunk_size))
    return blob


def hold_subset(item_ids_list: list, secret: str, ratio: float) -> list:
    """Local replica: which items this entity 'holds' (stable, deterministic)."""
    out = []
    for it in item_ids_list:
        h = int(hashlib.sha1(f"{secret}:{it}".encode()).hexdigest()[:8], 16)
        if h % 1000 < int(ratio * 1000):
            out.append(it)
    return out


def item_meta(item_id: str, blob: str) -> dict:
    return {
        "id": item_id,
        "size": len(blob),
        "hash": hashlib.sha256(blob.encode()).hexdigest()[:24],
    }


def my_node() -> str:
    return os.environ.get("NODE_NAME", "unknown")
