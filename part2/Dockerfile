# ============================================================
# AlexNet Part2 image (~1.1GB)
# Contents: PyTorch + torchvision + AlexNet weights (part2.pt ~236MB)
# ============================================================
FROM python:3.11-slim AS builder

WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc g++ && \
    rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
    torch --index-url https://download.pytorch.org/whl/cpu \
    torchvision --index-url https://download.pytorch.org/whl/cpu

COPY part2/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# --- runtime stage ---
FROM python:3.11-slim

WORKDIR /app

COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

COPY part2/app.py /app/
COPY common/model.py /app/common/model.py
COPY common/part2.pt /app/common/part2.pt
COPY common/imagenet_classes.txt /app/common/imagenet_classes.txt

EXPOSE 8002

CMD ["python", "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8002", "--no-access-log"]
