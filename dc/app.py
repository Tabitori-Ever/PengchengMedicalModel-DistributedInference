"""
Data Center Services (v3.0 - cloud node3).

Hosts the simulated patient database (cloud master + backups) and a compute
worker so the data center participates in v3 collaborative tasks.

Endpoints
  /health /metrics
  POST /v3/compute                      - collaborative compute partition
  GET  /db/state                        - manifest: version, item ids, size
  GET  /db/item/{id}                    - fetch one record blob
  POST /db/upload                       - cloud update (records from edges)
  POST /db/backup                       - snapshot backup of full DB
  GET  /db/backups                      - recent backups
"""
import hashlib
import os
import sys
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel
from typing import List, Optional
from prometheus_client import Counter, Histogram, generate_latest

sys.path.insert(0, "/app")
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path and Path(_ROOT).is_dir():
    sys.path.insert(0, str(_ROOT))
from common import v3_common as v3          # noqa: E402

DB_VERSION = os.environ.get("DB_VERSION", "v3-db-1")
DB_ITEM_COUNT = int(os.environ.get("DB_ITEM_COUNT", "64"))
CHUNK_KB = int(os.environ.get("CHUNK_KB", "4"))
CHUNK_SIZE = CHUNK_KB * 1024

app = FastAPI(title="Data Center Services v3.0")

compute_requests = Counter("v3_compute_requests_total", "v3 compute partitions", ["entity"])
db_requests = Counter("patient_db_requests_total", "patient db calls", ["op"])

# cloud master state
_base_ids: List[str] = v3.item_ids(DB_VERSION, DB_ITEM_COUNT)
_uploaded: dict = {}          # uid -> blob (updates pushed from edges)
_uploads_log: list = []       # [{uid, size, hash, ts}]
_backups: list = []           # [{backup_id, ts, version, total_items, hash}]


class ComputeRequest(BaseModel):
    rows: int = 256
    instruments: int = 4
    intensity: int = 40
    seed: int = 1
    partition: int = 0
    count: int = 1


class UploadItem(BaseModel):
    id: str
    blob: str


class UploadRequest(BaseModel):
    items: List[UploadItem] = []


@app.get("/")
def root():
    return {"service": "datacenter", "node": os.environ.get("NODE_NAME", "node3"),
            "role": "cloud", "version": "3.0", "status": "running"}


@app.get("/health")
def health():
    return {"service": "datacenter", "status": "ok",
            "node": os.environ.get("NODE_NAME", "node3")}


# ------------------------------ compute worker ------------------------------
@app.post("/v3/compute")
def v3_compute(req: ComputeRequest):
    compute_requests.labels(entity="datacenter").inc()
    res = v3.compute_partition(rows=req.rows, instruments=req.instruments,
                               intensity=req.intensity, seed=req.seed,
                               partition=req.partition, count=req.count)
    res.update({"actor": "datacenter", "node": os.environ.get("NODE_NAME", "node3"),
                "role": "cloud", "pod": "dc-services"})
    return res


# ------------------------------ patient database -----------------------------
@app.get("/db/state")
def db_state():
    db_requests.labels(op="state").inc()
    ids = _base_ids + sorted(_uploaded.keys())
    return {"db_version": DB_VERSION, "chunk_bytes": CHUNK_SIZE,
            "base_items": len(_base_ids), "uploaded_items": len(_uploaded),
            "total_items": len(ids), "ids": ids[:500],
            "backups": len(_backups), "uploads": _uploads_log[-20:]}


@app.get("/db/item/{item_id}")
def db_item(item_id: str):
    db_requests.labels(op="item").inc()
    if item_id in _uploaded:
        blob = _uploaded[item_id]
    elif item_id in _base_ids:
        blob = v3.chunk_blob(item_id, CHUNK_SIZE)
    else:
        raise HTTPException(status_code=404, detail=f"item {item_id} not found")
    meta = v3.item_meta(item_id, blob)
    return {"id": item_id, "size": meta["size"], "hash": meta["hash"], "blob": blob}


@app.post("/db/upload")
def db_upload(req: UploadRequest):
    db_requests.labels(op="upload").inc()
    if not req.items:
        raise HTTPException(status_code=400, detail="no items")
    accepted = []
    for it in req.items:
        expected = hashlib.sha256(it.blob.encode()).hexdigest()[:24]
        meta = v3.item_meta(it.id, it.blob)
        if meta["hash"] != expected:
            continue  # corrupt chunk -> dropped
        _uploaded[it.id] = it.blob
        _uploads_log.append({"uid": it.id, "size": meta["size"],
                             "hash": meta["hash"],
                             "ts": time.strftime("%H:%M:%S")})
        accepted.append(it.id)
    return {"ok": True, "accepted": len(accepted), "ids": accepted[:100],
            "total_uploaded": len(_uploaded)}


@app.post("/db/backup")
def db_backup():
    db_requests.labels(op="backup").inc()
    ids = _base_ids + sorted(_uploaded.keys())
    digest = hashlib.sha256()
    digest.update(DB_VERSION.encode())
    for i in ids:
        digest.update(i.encode())
    backup = {"backup_id": f"bk-{time.strftime('%m%d%H%M%S')}-{len(_backups)}",
              "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
              "version": DB_VERSION, "total_items": len(ids),
              "chunk_bytes": CHUNK_SIZE,
              "hash": digest.hexdigest()[:24]}
    _backups.append(backup)
    if len(_backups) > 20:
        _backups.pop(0)
    return backup


@app.get("/db/backups")
def db_backups():
    db_requests.labels(op="backups").inc()
    return {"backups": list(reversed(_backups))}


@app.get("/metrics")
def metrics():
    return Response(content=generate_latest(), media_type="text/plain")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8010)
