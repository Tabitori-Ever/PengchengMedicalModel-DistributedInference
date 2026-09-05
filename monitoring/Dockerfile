# ============================================================
# Monitoring service image (~120MB)
# Contents: FastAPI + requests (Prometheus queries)
# Lightest image — no ML dependencies
# ============================================================
FROM python:3.11-slim AS builder

WORKDIR /app

COPY monitoring/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# --- runtime stage ---
FROM python:3.11-slim

WORKDIR /app

COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

COPY monitoring/app.py /app/app.py

EXPOSE 8005

# Fixed: run app:app from /app/app.py
CMD ["python", "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8005", "--no-access-log"]
