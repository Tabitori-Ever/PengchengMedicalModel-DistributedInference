"""Node free-resource utility (v3.0, non-blocking since v3.3).

Loads node CPU/memory usage from the Kubernetes metrics-server and computes
free-capacity scores. Used by the data-center scheduler to pick hospital
targets (diagnosis forwarding), compute partition workers and routine Job
nodes.

v3.3 fix - the lookup used to be performed **synchronously on the request
path** with a 3s TTL: every clinic-originated task that fell outside the cache
window paid a full metrics-API + node-list round trip (~4s in this cluster),
which silently dominated the measured task latency and made collaborative runs
look far worse than they are. The refresh now happens in a background thread
and callers only ever read the cached snapshot.
"""
import json
import os
import ssl
import threading
import time
import urllib.request
from typing import Dict, Optional


def _parse_cpu_cores(value: str) -> Optional[float]:
    if not value:
        return None
    v = str(value).strip()
    mult = 1.0
    if v.endswith("n"):
        mult, v = 1e-9, v[:-1]
    elif v.endswith("u"):
        mult, v = 1e-6, v[:-1]
    elif v.endswith("m"):
        mult, v = 1e-3, v[:-1]
    try:
        return float(v) * mult
    except ValueError:
        return None


def _parse_mem_bytes(value: str) -> Optional[int]:
    if not value:
        return None
    v = str(value).strip()
    mult = 1
    for suf, m in (("Ki", 1024), ("Mi", 1024 ** 2), ("Gi", 1024 ** 3),
                   ("Ti", 1024 ** 4), ("K", 1000), ("M", 1000 ** 2),
                   ("G", 1000 ** 3), ("T", 1000 ** 4)):
        if v.endswith(suf):
            mult, v = m, v[:-len(suf)]
            break
    try:
        return int(float(v) * mult)
    except ValueError:
        return None


# 本集群 API server 的服务证书与本 Pod 注入的 SA CA 不是同一条链，证书校验会直接失败，
# 导致节点负载长期为 null。校验失败一次后记住并改用「不校验证书」的上下文（集群内网调用）。
_tls_unverified = False


def _ssl_ctx(verify: bool) -> "ssl.SSLContext":
    ctx = ssl.create_default_context()
    if verify:
        ca_path = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
        if os.path.exists(ca_path):
            ctx.load_verify_locations(ca_path)
    else:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _kube_api_get(path: str, timeout: float = 5.0):
    global _tls_unverified
    token_path = "/var/run/secrets/kubernetes.io/serviceaccount/token"
    if not os.path.exists(token_path):
        raise RuntimeError("not running in-cluster")
    with open(token_path) as f:
        token = f.read().strip()
    url = "https://kubernetes.default.svc" + path

    def _call(verify: bool):
        req = urllib.request.Request(url)
        req.add_header("Authorization", f"Bearer {token}")
        req.add_header("Accept", "application/json")
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            urllib.request.HTTPSHandler(context=_ssl_ctx(verify)),
        )
        with opener.open(req, timeout=timeout) as r:
            return json.loads(r.read().decode())

    if _tls_unverified:
        return _call(False)
    try:
        return _call(True)
    except Exception as e:  # noqa: BLE001 - 证书链不匹配时退化为不校验
        if "CERTIFICATE_VERIFY_FAILED" in str(e) or "certificate verify failed" in str(e):
            _tls_unverified = True
            return _call(False)
        raise


_lock = threading.Lock()
_cache: Dict = {}
_cache_ts = 0.0
# how often the background thread refreshes; callers never block on it
_REFRESH_S = float(os.environ.get("NODE_USAGE_REFRESH_S", "10"))
# node allocatable barely changes - cache it much longer than the live usage
_ALLOC_TTL = float(os.environ.get("NODE_USAGE_ALLOC_TTL_S", "300"))
_alloc_cache: Dict[str, dict] = {}
_alloc_ts = 0.0
_started = False


def _allocatable() -> Dict[str, dict]:
    """{node: {cpu, memory}} from the node list (long-lived cache)."""
    global _alloc_ts
    now = time.time()
    if _alloc_cache and now - _alloc_ts < _ALLOC_TTL:
        return dict(_alloc_cache)
    out: Dict[str, dict] = {}
    try:
        for item in _kube_api_get("/api/v1/nodes").get("items", []):
            a = ((item.get("status") or {}).get("allocatable") or {})
            out[item["metadata"]["name"]] = {
                "cpu": _parse_cpu_cores(a.get("cpu")),
                "memory": _parse_mem_bytes(a.get("memory")),
            }
    except Exception:  # noqa: BLE001
        return dict(_alloc_cache)
    _alloc_cache.clear()
    _alloc_cache.update(out)
    _alloc_ts = now
    return dict(out)


def refresh() -> Dict[str, dict]:
    """One synchronous refresh (used by the background thread and force=True)."""
    global _cache_ts, _cache
    out: Dict[str, dict] = {}
    try:
        usage_by_node = {}
        for item in _kube_api_get("/apis/metrics.k8s.io/v1beta1/nodes").get("items", []):
            u = item.get("usage", {})
            cpu = _parse_cpu_cores(u.get("cpu"))
            mem = _parse_mem_bytes(u.get("memory"))
            if cpu is not None and mem is not None:
                usage_by_node[item["metadata"]["name"]] = (cpu, mem)

        for name, alloc in _allocatable().items():
            u = usage_by_node.get(name)
            if not u:
                continue
            ca, ma = alloc.get("cpu"), alloc.get("memory")
            cpu_u = max(0.0, min(1.0, (u[0] / ca) if ca else 0.0))
            mem_u = max(0.0, min(1.0, (u[1] / ma) if ma else 0.0))
            free = 0.5 * (1 - cpu_u) + 0.5 * (1 - mem_u)
            out[name] = {"cpu": round(cpu_u, 3), "memory": round(mem_u, 3),
                         "free": round(free, 3)}
    except Exception:  # noqa: BLE001
        return dict(_cache)
    with _lock:
        _cache = out
        _cache_ts = time.time()
    return dict(out)


def _loop() -> None:
    while True:
        try:
            refresh()
        except Exception:  # noqa: BLE001
            pass
        time.sleep(_REFRESH_S)


def _ensure_started() -> None:
    """Start the background refresher once, off the request path."""
    global _started
    with _lock:
        if _started:
            return
        _started = True
    t = threading.Thread(target=_loop, name="node-usage", daemon=True)
    t.start()


def node_loads(force: bool = False) -> Dict[str, dict]:
    """{node: {cpu, memory (0..1 usage), free_score (0..1)}} - never blocks.

    The first call starts the background refresher and may return `{}` until the
    first snapshot lands; `free_edge_nodes()` tolerates that (it falls back to a
    neutral score).
    """
    _ensure_started()
    if force:
        return refresh()
    with _lock:
        return dict(_cache)


def free_edge_nodes() -> list:
    """Free-capacity ordering of edge nodes (used for forward/partition)."""
    loads = node_loads()
    return sorted(["node1", "node2"], key=lambda n: -loads.get(n, {}).get("free", 0.5))


def most_free_edge() -> str:
    edge = free_edge_nodes()
    return edge[0] if edge else "node1"
