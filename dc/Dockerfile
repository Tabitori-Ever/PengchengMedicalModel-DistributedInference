# ============================================================
# Data Center Services image v3.0 (~200MB, no PyTorch)
# Patient database (cloud master + backups) + compute worker.
# Deployed on node3 (cloud / data center).
# ============================================================
FROM python:3.11-slim AS builder

WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc g++ && \
    rm -rf /var/lib/apt/lists/*

COPY dc/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# --- runtime stage: drop gcc/g++ ---
FROM python:3.11-slim

WORKDIR /app

COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

COPY dc/app.py /app/app.py
COPY common/v3_common.py /app/common/v3_common.py

EXPOSE 8010

CMD ["python", "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8010", "--no-access-log"]
