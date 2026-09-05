# ============================================================
# Medical Worker image (~1.1GB)
# Contents: PyTorch + torchvision + DoubleTower model code
# Deployed on edge nodes (node1/node2)
# ============================================================
FROM python:3.11-slim AS builder

WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc g++ && \
    rm -rf /var/lib/apt/lists/*

# Install PyTorch CPU-only to keep image smaller
RUN pip install --no-cache-dir \
    torch --index-url https://download.pytorch.org/whl/cpu \
    torchvision --index-url https://download.pytorch.org/whl/cpu

COPY medical-worker/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# --- runtime stage: drop gcc/g++ ---
FROM python:3.11-slim

WORKDIR /app

COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Application code
COPY medical-worker/app.py /app/
COPY medical-worker/models_my.py /app/
COPY medical-worker/models/ /app/models/

# data_utils: create placeholder if source file is absent
# (only needed for CSV-based batch inference, not the HTTP API)
COPY medical-worker/data_utils.py /app/

EXPOSE 8006

CMD ["python", "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8006", "--no-access-log"]
