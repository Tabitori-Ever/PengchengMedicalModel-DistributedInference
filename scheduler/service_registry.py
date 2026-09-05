"""
Entity service registry (v2.0).

With the pod-ised architecture, hospitals and clinics are real pods reachable
through per-entity ClusterIP services.  The scheduler resolves the HTTP base
URL of an entity through this registry instead of one shared service per
role (part1-service / medical-worker-service, which no longer exist).

Overridable through environment variables so local development can point to
localhost ports:
    HOSPITAL_A_URL / HOSPITAL_B_URL / CLINIC_1_URL / CLINIC_2_URL
"""
import os
from typing import Dict, Optional

# Service URLs inside the cluster
HOSPITAL_SERVICES: Dict[str, str] = {
    "hospital-a": os.getenv("HOSPITAL_A_URL", "http://hospital-a-service:8006"),
    "hospital-b": os.getenv("HOSPITAL_B_URL", "http://hospital-b-service:8006"),
}

CLINIC_SERVICES: Dict[str, str] = {
    "clinic-1": os.getenv("CLINIC_1_URL", "http://clinic-1-service:8007"),
    "clinic-2": os.getenv("CLINIC_2_URL", "http://clinic-2-service:8007"),
}

# Keep cloud-side services (unchanged from v1.x)
MEDICAL_SERVER_URL = os.getenv(
    "MEDICAL_SERVER_URL", "http://medical-server-service:9001")
PART2_URL = os.getenv("PART2_URL", "http://part2-service:8002")

# Bypass corporate proxy for internal K8s service calls
_no_proxy_additions = (
    "hospital-a-service,hospital-b-service,clinic-1-service,clinic-2-service,"
    "medical-worker-service,part1-service,medical-server-service,part2-service,"
    ".svc.cluster.local,.cluster.local"
)
for _env_var in ("NO_PROXY", "no_proxy"):
    _current = os.environ.get(_env_var, "")
    if _no_proxy_additions not in _current:
        os.environ[_env_var] = (_current + "," + _no_proxy_additions).strip(",")

HOSPITALS = ("hospital-a", "hospital-b")
CLINICS = ("clinic-1", "clinic-2")

# Runtime-added entities (created through the cluster editor, e.g. clinic-3).
# Kept in a process-local dict; registered by scheduler/cluster_api.py when a
# new clinic Deployment is created and unregistered when it is deleted.
_EXTRA_CLINICS: Dict[str, str] = {}


def register_clinic(name: str, base_url: Optional[str] = None) -> None:
    if base_url is None:
        base_url = f"http://{name}-service:8007"
    _EXTRA_CLINICS[name] = base_url
    CLINIC_SERVICES[name] = base_url


def unregister_clinic(name: str) -> None:
    _EXTRA_CLINICS.pop(name, None)
    CLINIC_SERVICES.pop(name, None)


def hospital_base_url(hospital: str) -> Optional[str]:
    return HOSPITAL_SERVICES.get(hospital)


def clinic_base_url(clinic: str) -> Optional[str]:
    return CLINIC_SERVICES.get(clinic)


def hospital_medical_infer_url(hospital: str) -> Optional[str]:
    base = hospital_base_url(hospital)
    return (base + "/medical/infer") if base else None


def hospital_alexnet_infer_url(hospital: str) -> Optional[str]:
    base = hospital_base_url(hospital)
    return (base + "/alexnet/infer") if base else None


def clinic_mem_query_url(clinic: str) -> Optional[str]:
    base = clinic_base_url(clinic)
    return (base + "/query/mem") if base else None


def pick_hospital(source: Optional[str]) -> Optional[str]:
    """Resolve the hospital a task should run its edge stage on.

    If the task carries a hospital/source id that is registered, use it;
    otherwise fall back to the first registered hospital (hospital-a).
    """
    if source in HOSPITAL_SERVICES:
        return source
    for h in HOSPITALS:
        if h in HOSPITAL_SERVICES:
            return h
    return None


def pick_clinic(source: Optional[str]) -> Optional[str]:
    """Resolve the clinic a memory-monitor task should run on."""
    if source in CLINIC_SERVICES:
        return source
    for c in CLINICS:
        if c in CLINIC_SERVICES:
            return c
    return None
