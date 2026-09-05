# ============================================================
# Medical Server image (~1.1GB)
# Contents: PyTorch + torchvision + DoubleTower model code
# Deployed on cloud node (node3)
# ============================================================
FROM python:3.11-slim AS builder

WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc g++ && \
    rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
    torch --index-url https://download.pytorch.org/whl/cpu \
    torchvision --index-url https://download.pytorch.org/whl/cpu

COPY medical-server/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# --- runtime stage ---
FROM python:3.11-slim

WORKDIR /app

COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

COPY medical-server/app.py /app/
COPY medical-server/models_my.py /app/
COPY medical-server/models/ /app/models/

EXPOSE 9001

CMD ["python", "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "9001", "--no-access-log"]
