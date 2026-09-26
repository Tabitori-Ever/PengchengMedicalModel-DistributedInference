"""Fixed benchmark suites (调度模式对比用固定任务集).

A suite is a *frozen* list of specs so that repeated comparisons (云边端协同 vs
本地执行) measure exactly the same work. Editing a suite changes the experiment -
keep the composition stable and bump the id if it must change.

四类任务各有一套，每套 20 个任务（多档负载 × 重复），用于逐档对比：

`suite-compute-20`   计算：5 档负载（算力递增）× 4 次重复，固定 3 分区。
                     协同把 3 个分区并发派给「发起端 + 数据中心 + 伙伴边缘端」，
                     本地只能在发起 Pod 上串行跑 3 份。
`suite-diagnosis-20` 诊断：批量 1/2/4/8 例 × 5 次重复。单次诊断是**串行模型拆分**
                     （边端前端 → 云端后端），本地必然更快；批量下协同可以做
                     两段流水线（边端算第 i+1 例的前端时云端在算第 i 例的后端）。
`suite-sync-20`      通信：4 档链路带宽 × 5 次重复。协同由数据中心持有分块归属表，
                     开 `concurrency` 条**多源并行流**（各源各自计价带宽）；
                     本地无中心分块表，单流顺序拉取。
`suite-routine-20`   日常：4 档任务数（2/4/6/8）× 5 次重复，作业负载固定较重。
                     协同把一次性 Job **并发**派发到空闲边缘节点与数据中心，
                     本地在发起 Pod 内联串行执行。

注：`bandwidth_mbps` 与算力模型都是平台内置的**模拟模型**（见 common/v3_common.py
与 scheduler/scheduler.py），它们的口径在报告的「口径与假设」一节中逐条说明。
"""
from typing import Any, Dict, List

# ---------------------------------------------------------------- compute ----
_COMPUTE_LEVELS: List[Dict[str, Any]] = [
    {"label": "对照", "instruments": 4, "rows": 512, "intensity": 60},
    {"label": "中载", "instruments": 8, "rows": 2048, "intensity": 300},
    {"label": "重载", "instruments": 8, "rows": 3072, "intensity": 450},
    {"label": "特重", "instruments": 8, "rows": 4096, "intensity": 600},
    {"label": "极重", "instruments": 16, "rows": 4096, "intensity": 900},
]
_COMPUTE_SEED = 1100
_COMPUTE_REPEATS = 4


def _compute_specs() -> List[Dict[str, Any]]:
    return [{
        "label": lv["label"],
        "repeats": _COMPUTE_REPEATS,
        "params": {"instruments": lv["instruments"], "rows": lv["rows"],
                   "intensity": lv["intensity"], "partition_count": 3,
                   "seed": _COMPUTE_SEED + i},
    } for i, lv in enumerate(_COMPUTE_LEVELS)]


# -------------------------------------------------------------- diagnosis ----
# 负载维度 = 单次任务携带的患者数（批量）。批量越大，流水线的重叠越充分。
_DIAGNOSIS_PATIENTS = ["1404920", "1584882", "1661073", "1664718",
                       "1668124", "1686243", "1727894", "1732297"]
_DIAGNOSIS_LEVELS = [1, 2, 4, 8]
_DIAGNOSIS_REPEATS = 5


def _diagnosis_specs() -> List[Dict[str, Any]]:
    return [{
        "label": f"{n} 例",
        "repeats": _DIAGNOSIS_REPEATS,
        "params": {"patient_ids": _DIAGNOSIS_PATIENTS[:n]},
    } for n in _DIAGNOSIS_LEVELS]


# ------------------------------------------------------------------- sync ----
# 负载维度 = 链路带宽（越低越慢）与分块大小；同一 DB 状态下 missing 集合一致。
_SYNC_LEVELS = [
    {"label": "省流 8Mbps", "bandwidth_mbps": 8.0, "concurrency": 4, "chunk_kb": 4},
    {"label": "普通 20Mbps", "bandwidth_mbps": 20.0, "concurrency": 4, "chunk_kb": 4},
    {"label": "高速 60Mbps", "bandwidth_mbps": 60.0, "concurrency": 8, "chunk_kb": 4},
    {"label": "极速 120Mbps", "bandwidth_mbps": 120.0, "concurrency": 8, "chunk_kb": 16},
]
_SYNC_REPEATS = 5


def _sync_specs() -> List[Dict[str, Any]]:
    return [{"label": lv["label"], "repeats": _SYNC_REPEATS,
             "params": {k: v for k, v in lv.items() if k != "label"}}
            for lv in _SYNC_LEVELS]


# ---------------------------------------------------------------- routine ----
# 负载维度 = 一次性 Job 的数量（作业本身负载固定较重，使计算不被编排开销淹没）。
_ROUTINE_LEVELS = [2, 4, 6, 8]
_ROUTINE_REPEATS = 5
_ROUTINE_WORK = {"rows": 8192, "intensity": 2400}


def _routine_specs() -> List[Dict[str, Any]]:
    return [{
        "label": f"{n} 个作业",
        "repeats": _ROUTINE_REPEATS,
        "params": {"jobs": n, "rows": _ROUTINE_WORK["rows"],
                   "intensity": _ROUTINE_WORK["intensity"], "seed": 2100 + n},
    } for n in _ROUTINE_LEVELS]


SUITES: Dict[str, Dict[str, Any]] = {
    "suite-compute-20": {
        "suite_id": "suite-compute-20",
        "name": "计算标准套件 · 20 任务",
        "kind": "compute",
        "default_source": "clinic-1",
        "default_concurrency": 1,
        "description": "5 档算力负载 × 4 次重复，固定 3 分区",
        "strategies": ["collaborative", "local"],
        "specs": _compute_specs(),
    },
    "suite-diagnosis-20": {
        "suite_id": "suite-diagnosis-20",
        "name": "诊断标准套件 · 20 任务",
        "kind": "diagnosis",
        "default_source": "hospital-a",
        "default_concurrency": 1,
        "description": "批量 1/2/4/8 例 × 5 次重复",
        # 诊断无本地执行策略：医疗中心不能执行模型 server 部分推理
        "strategies": ["collaborative"],
        "strategy_note": "无本地执行策略（医疗中心不能执行模型 server 部分推理）",
        "specs": _diagnosis_specs(),
    },
    "suite-sync-20": {
        "suite_id": "suite-sync-20",
        "name": "通信标准套件 · 20 任务",
        "kind": "sync",
        "default_source": "clinic-2",
        "default_concurrency": 1,
        "description": "4 档链路带宽 × 5 次重复",
        "strategies": ["collaborative", "local"],
        "specs": _sync_specs(),
    },
    "suite-routine-20": {
        "suite_id": "suite-routine-20",
        "name": "日常标准套件 · 20 任务",
        "kind": "routine",
        "default_source": "clinic-1",
        "default_concurrency": 1,
        "description": "2/4/6/8 个一次性作业 × 5 次重复",
        "strategies": ["collaborative", "local"],
        "specs": _routine_specs(),
    },
}

# 报表里按这个顺序展示四类任务
SUITE_ORDER = ["suite-compute-20", "suite-diagnosis-20", "suite-sync-20",
               "suite-routine-20"]


def list_suites() -> List[Dict[str, Any]]:
    """Public suite metadata incl. the frozen task list (for the UI)."""
    out = []
    for sid in SUITE_ORDER:
        s = SUITES.get(sid)
        if not s:
            continue
        total = sum(int(sp["repeats"]) for sp in s["specs"])
        out.append({
            "suite_id": s["suite_id"],
            "name": s["name"],
            "kind": s["kind"],
            "default_source": s["default_source"],
            "default_concurrency": s["default_concurrency"],
            "description": s["description"],
            "strategies": list(s.get("strategies") or ["collaborative", "local"]),
            "strategy_note": s.get("strategy_note"),
            "task_count": total,
            "specs": [{"label": sp["label"], "repeats": sp["repeats"],
                       "params": sp["params"]} for sp in s["specs"]],
        })
    return out


def get_suite(suite_id: str) -> Dict[str, Any]:
    suite = SUITES.get(suite_id)
    if suite is None:
        raise KeyError(f"unknown suite '{suite_id}'; available: {sorted(SUITES)}")
    return suite


def expand(suite: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Flatten a suite into the per-attempt task list (spec × repeats)."""
    items: List[Dict[str, Any]] = []
    for spec in suite["specs"]:
        for _ in range(int(spec["repeats"])):
            items.append({"label": spec["label"], "params": dict(spec["params"])})
    return items
