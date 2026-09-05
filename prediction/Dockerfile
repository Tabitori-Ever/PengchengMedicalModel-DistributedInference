# ============================================================
# Prediction service image (~200MB)
# Contents: scikit-learn + Redis client + Prometheus queries
# No PyTorch — lightweight latency prediction only
# ============================================================
FROM python:3.11-slim AS builder

WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc g++ && \
    rm -rf /var/lib/apt/lists/*

COPY prediction/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# --- runtime stage ---
FROM python:3.11-slim

WORKDIR /app

COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

COPY prediction/ /app/prediction/

EXPOSE 8003

CMD ["python", "-m", "uvicorn", "prediction.app:app", "--host", "0.0.0.0", "--port", "8003", "--no-access-log"]
