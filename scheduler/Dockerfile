# ============================================================
# Scheduler image v3.2.0 — INCREMENTAL build
#
# No PyPI access on this host, so v3.2.0 layers on the previous release image
# (which already carries fastapi/uvicorn/pydantic/redis/kubernetes/sklearn).
#
# v3.2.0 additions:
#   scheduler/scheduler.py  - execution-mode selection (collaborative/local/auto),
#                             local delegation to the initiating pod, dependency
#                             probe + automatic degradation, unified result schema
#   scheduler/api.py        - mode / force_degraded / seed on the submit APIs
#   scheduler/task_manager.py - extra task fields
#   frontend/dist/          - rebuilt UI with the 执行模式 selector
#
# For a from-scratch build (needs network) use scheduler/Dockerfile.full.
# ============================================================
ARG BASE_IMAGE=k8s-master:5000/k8s-repo/inference-scheduler:v3.1.8
FROM ${BASE_IMAGE}

WORKDIR /app

# replace the app tree so removed modules cannot linger from the base image
RUN rm -rf /app/scheduler /app/frontend
COPY scheduler/ /app/scheduler/
COPY frontend/dist/ /app/frontend/dist/
COPY test/test_dataset.json /app/test/test_dataset.json
COPY model_profile.yaml /app/

EXPOSE 8000 9000

CMD ["python", "-m", "uvicorn", "scheduler.api:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-access-log"]
