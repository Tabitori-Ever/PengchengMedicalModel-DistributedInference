# ============================================================
# Hospital Pod image (~2GB)
# Merges medical-worker (DoubleTower front-end) + AlexNet part1
# (Conv1-Conv5 + avgpool) into one container.
# Deployed as hospital-a on node1, hospital-b on node2.
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

COPY hospital/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# --- runtime stage: drop gcc/g++ ---
FROM python:3.11-slim

WORKDIR /app

COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Application code: hospital app + DoubleTower (medical) + AlexNet part1 common
COPY hospital/app.py /app/
COPY medical-worker/models_my.py /app/
COPY medical-worker/models/ /app/models/
COPY common/model.py /app/common/model.py
COPY common/part1.pt /app/common/part1.pt

# Medical DoubleTower weights (123MB) - data does not leave the hospital pod
COPY z-鹏城医疗模型代码/worker/weights_bigcrop/multi/best_model_fold1.pth /app/weights/best_model_fold1.pth

EXPOSE 8006

CMD ["python", "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8006", "--no-access-log"]
