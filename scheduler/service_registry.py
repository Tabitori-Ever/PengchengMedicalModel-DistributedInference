"""Entity service registry (v3.0).

v3.0 topology: edge hospitals (worker front-end + v3 worker), terminal
clinics (initiators + v3 worker) and the data center (cloud node3) which
hosts medical-server, dc-services (patient DB) and this scheduler.

Overridable with env vars for local development:
  HOSPITAL_A_URL / HOSPITAL_B_URL / CLINIC_1_URL / CLINIC_2_URL
  MEDICAL_SERVER_URL / DC_SERVICES_URL
"""
import os
from typing import Dict, Optional

HOSPITAL_SERVICES: Dict[str, str] = {
    "hospital-a": os.getenv("HOSPITAL_A_URL", "http://hospital-a-service:8006"),
    "hospital-b": os.getenv("HOSPITAL_B_URL", "http://hospital-b-service:8006"),
}
CLINIC_SERVICES: Dict[str, str] = {
    "clinic-1": os.getenv("CLINIC_1_URL", "http://clinic-1-service:8007"),
    "clinic-2": os.getenv("CLINIC_2_URL", "http://clinic-2-service:8007"),
}

MEDICAL_SERVER_URL = os.getenv(
    "MEDICAL_SERVER_URL", "http://medical-server-service:9001")
DC_URL = os.getenv("DC_SERVICES_URL", "http://dc-services:8010")

HOSPITALS = ("hospital-a", "hospital-b")
CLINICS = ("clinic-1", "clinic-2")
ALL_SOURCES = HOSPITALS + CLINICS

# bypass corporate proxy for internal svc calls
_no_proxy = ("hospital-a-service,hospital-b-service,clinic-1-service,"
             "clinic-2-service,medical-server-service,dc-services,"
             ".svc.cluster.local,.cluster.local")
for _env in ("NO_PROXY", "no_proxy"):
    cur = os.environ.get(_env, "")
    if _no_proxy not in cur:
        os.environ[_env] = (cur + "," + _no_proxy).strip(",")


def is_hospital(source: str) -> bool:
    return source in HOSPITAL_SERVICES


def is_clinic(source: str) -> bool:
    return source in CLINIC_SERVICES


def is_source(source: Optional[str]) -> bool:
    return source in HOSPITAL_SERVICES or source in CLINIC_SERVICES


def hospital_base(source: str) -> Optional[str]:
    if source in HOSPITAL_SERVICES:
        return HOSPITAL_SERVICES[source]
    if source in CLINIC_SERVICES:
        return None
    return HOSPITAL_SERVICES.get("hospital-a")


def clinic_base(clinic: str) -> Optional[str]:
    return CLINIC_SERVICES.get(clinic)


def dc(path: str = "") -> str:
    return DC_URL + path


def medical_server_url() -> str:
    """Data center medical-server infer URL (normalizes trailing /infer)."""
    base = MEDICAL_SERVER_URL
    if base.endswith("/infer"):
        return base
    return base.rstrip("/") + "/infer"


def pick_hospital(source: Optional[str], prefer: Optional[str] = None) -> str:
    """Resolve the hospital used for the diagnosis worker stage.

    Clinic-originated diagnosis is forwarded to a hospital: explicit
    preference first, else the most-free hospital edge node.
    """
    if prefer in HOSPITAL_SERVICES:
        return prefer
    if source in HOSPITAL_SERVICES:
        return source
    try:
        from .node_usage import most_free_edge
        best = most_free_edge()  # 'node1' | 'node2'
        if best in ("node1", "node2"):
            return "hospital-a" if best == "node1" else "hospital-b"
    except Exception:
        pass
    return "hospital-a"


# Editor-created extra clinic entities: expose as schedulable sources too.
def register_clinic(name: str, base_url: Optional[str] = None) -> None:
    CLINIC_SERVICES.setdefault(name, base_url or f"http://{name}-service:8007")


def unregister_clinic(name: str) -> None:
    if name not in ("clinic-1", "clinic-2"):
        CLINIC_SERVICES.pop(name, None)
