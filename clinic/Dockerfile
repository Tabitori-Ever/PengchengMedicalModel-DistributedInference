# ============================================================
# Clinic Pod image v3.2 (terminal) — INCREMENTAL build
#
# This host has NO egress to PyPI / the PyTorch index / npm, so the image is
# layered on top of the previous release image instead of rebuilding the whole
# dependency tree (same fastapi/pydantic/prometheus stack, no new packages).
#
# v3.2 additions:
#   clinic/app.py          - /local/* endpoints (local execution + degradation)
#   common/local_exec.py   - new shared local-execution runtime (stdlib only)
#
# For a from-scratch build (needs network) use clinic/Dockerfile.full.
# ============================================================
ARG BASE_IMAGE=k8s-master:5000/k8s-repo/clinic:v3.0
FROM ${BASE_IMAGE}

WORKDIR /app

COPY clinic/app.py /app/app.py
COPY clinic/run_routine.py /app/run_routine.py
COPY common/v3_common.py /app/common/v3_common.py
COPY common/local_exec.py /app/common/local_exec.py

# local-execution results survive pod restarts (hostPath mounted in k8s)
RUN mkdir -p /data/local-results

EXPOSE 8007

CMD ["python", "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8007", "--no-access-log"]
