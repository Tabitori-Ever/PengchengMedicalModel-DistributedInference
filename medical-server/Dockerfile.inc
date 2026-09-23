# ============================================================
# Medical Server image v1.1 - INCREMENTAL build (no PyPI access on this host)
#
# v1.1 change (medical-server/app.py only):
#   /infer now accepts the compact {"__f32b64__", "shape"} feature payload in
#   addition to the legacy nested float lists. The edge worker used to send
#   ~7MB of float *text* per diagnosis; base64 float32 cuts the payload ~70%
#   and moves the conversion into C, taking the server stage from ~440ms to
#   ~130ms (and the worker stage from ~2066ms to ~117ms) with bit-identical
#   bpCR output.
#
# Deploy this BEFORE the v3.4 edge / v3.3.3 scheduler images: the new scheduler
# asks the worker for compact features and forwards them verbatim, so an old
# server would reject them.
#
# For a from-scratch build see medical-server/Dockerfile (needs network).
# ============================================================
FROM k8s-master:5000/k8s-repo/medical-server:v1.0

WORKDIR /app

COPY medical-server/app.py /app/app.py
COPY medical-server/models_my.py /app/models_my.py
COPY medical-server/models/ /app/models/

EXPOSE 9001

CMD ["python", "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "9001", "--no-access-log"]
