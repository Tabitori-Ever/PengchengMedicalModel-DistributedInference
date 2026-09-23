# ============================================================
# Hospital Pod image v3.2 (edge) — INCREMENTAL build
#
# No egress to PyPI / the PyTorch index on this host, so v3.2 layers on the
# previous release image: torch/torchvision, the DoubleTower code and the full
# model weights are already inside it.
#
# v3.2 additions:
#   hospital/app.py        - keeps the *full* DoubleTower resident and serves
#                            /local/diagnosis (end-to-end on this pod) plus the
#                            shared /local/* local-execution endpoints
#   common/local_exec.py   - new shared local-execution runtime (stdlib only)
#
# For a from-scratch build (needs network) use hospital/Dockerfile.full.
# ============================================================
ARG BASE_IMAGE=k8s-master:5000/k8s-repo/hospital:v3.0
FROM ${BASE_IMAGE}

WORKDIR /app

COPY hospital/app.py /app/app.py
COPY common/v3_common.py /app/common/v3_common.py
COPY common/local_exec.py /app/common/local_exec.py

# local-execution results survive pod restarts (hostPath mounted in k8s)
RUN mkdir -p /data/local-results

EXPOSE 8006

CMD ["python", "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8006", "--no-access-log"]
