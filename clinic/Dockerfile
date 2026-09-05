# ============================================================
# Clinic Pod image (~200MB, no PyTorch)
# Lightweight FastAPI service exposing a pod memory-usage query
# task. Default target = own pod (cgroup), optional explicit
# target via kube metrics-server API.
# ============================================================
FROM python:3.11-slim AS builder

WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc g++ && \
    rm -rf /var/lib/apt/lists/*

COPY clinic/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# --- runtime stage: drop gcc/g++ ---
FROM python:3.11-slim

WORKDIR /app

COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

COPY clinic/app.py /app/

EXPOSE 8007

CMD ["python", "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8007", "--no-access-log"]
