# This package is the inference scheduler.
# The app is at scheduler.api:app (imported by uvicorn directly).
# Do NOT import api here — it has module-level side effects
# (Prometheus server, worker processes) that must not run on
# every package import (e.g., during multiprocessing spawn).
