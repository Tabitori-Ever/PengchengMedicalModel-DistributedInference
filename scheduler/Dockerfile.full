# ============================================================
# Scheduler image (~180MB)
# Contents: FastAPI + Redis client + scikit-learn + scheduling engine
# No PyTorch needed — scheduler only orchestrates, doesn't run models
# ============================================================
FROM python:3.11-slim AS builder

WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc g++ && \
    rm -rf /var/lib/apt/lists/*

COPY scheduler/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# --- runtime stage ---
FROM python:3.11-slim

WORKDIR /app

# Only copy installed packages, not build tools
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

COPY scheduler/ /app/scheduler/
# v2.0: React frontend build output served at /app (mounted in api.py)
COPY frontend/dist/ /app/frontend/dist/
# v2.0.2: preprocessed test patients for /test/patients & /schedule/preprocessed
COPY test/test_dataset.json /app/test/test_dataset.json
COPY model_profile.yaml /app/

EXPOSE 8000 9000

# --workers 1 is sufficient because scheduler workers now run as separate
# multiprocessing.Process instances (not threads), so they don't share the
# GIL with the API server. Health checks are always responsive.
CMD ["python", "-m", "uvicorn", "scheduler.api:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-access-log"]
