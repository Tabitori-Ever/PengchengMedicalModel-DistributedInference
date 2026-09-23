# ============================================================
# benchmark-site v1.0 - 测试下发 / 结果采集 / 性能对比站点
#
# INCREMENTAL build: this host has no PyPI egress, so the image layers on the
# scheduler image, which already carries fastapi / uvicorn / pydantic /
# requests - exactly the backend's dependency set (stdlib sqlite3 for storage).
#
# Contents:
#   /app/backend/*      FastAPI backend (/api/* per 设计方案 §6.1)
#   /app/static/*       built React UI, served by the backend at "/"
#   /app/patients.json  bundled patient inputs, so diagnosis still runs when the
#                       scheduler (and its /test/patient endpoint) is down
#
# The site is deliberately independent of the scheduler pod: that is what makes
# the "scheduler offline -> degrade to local execution" scenario testable.
# ============================================================
ARG BASE_IMAGE=k8s-master:5000/k8s-repo/inference-scheduler:v3.1.8
FROM ${BASE_IMAGE}

WORKDIR /app/backend

COPY benchmark/backend/ /app/backend/
COPY benchmark/frontend/dist/ /app/static/
COPY benchmark/tools/make_patients.py /app/build/make_patients.py
COPY test/test_dataset.json /app/build/test_dataset.json

# Bundled patient inputs, produced at build time so the 20MB dataset is never
# duplicated into the repository.
RUN python /app/build/make_patients.py --dataset /app/build/test_dataset.json \
        --out /app/patients.json \
 && rm -rf /app/build

ENV PYTHONUNBUFFERED=1 \
    DB_PATH=/data/bench/bench.db \
    PATIENTS_PATH=/app/patients.json \
    STATIC_DIR=/app/static \
    PORT=8090

RUN mkdir -p /data/bench

EXPOSE 8090

CMD ["python", "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8090", "--no-access-log"]
