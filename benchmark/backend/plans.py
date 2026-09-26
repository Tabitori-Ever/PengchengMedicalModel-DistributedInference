"""测试方案目录 —— 严格对应《测试大纲》§5.4 协同调度测试 与 §5.5 医疗智联专网测试。

一个「测试方案」描述的是**怎么测**：几台设备、每类任务各多少个、任务参数、
任务在多长时间窗内随机产生、整场测试时长、用哪些调度策略、判定标准是什么。
它不预先决定结果——下发后由 `engine.create_plan_run` 展开成若干子 run，
按调度策略分阶段顺序执行（先本地、后协同），阶段内任务按时间窗随机"产生"。

字段口径（与大纲逐条对应，便于评审对照）：

| 大纲条目 | 本模块字段 |
| --- | --- |
| 测试对象 / 测试目的 | `test_object` / `purpose` |
| 测试时长 | `duration_min` |
| 操作步骤：各设备每类任务 N 个 | `devices[].` × `kinds[].per_device` |
| 操作步骤：在 M 分钟内随机产生 | `generate_window_min` |
| 操作步骤：切换调度策略再跑一遍 | `strategies` |
| 评判标准 | `criteria[]` |
| 5.5 的测试仪 / 损伤仪 | `attachments[]` |
"""
from typing import Any, Dict, List, Optional

# --------------------------------------------------------------------------- #
# 四类任务的默认参数：取平台固定套件里"中重载"档的量级，
# 保证单次任务足够长、调度差异可观测（轻载档协同会被固定编排开销淹没）。
# --------------------------------------------------------------------------- #
KIND_META: Dict[str, Dict[str, Any]] = {
    "diagnosis": {
        "label": "医疗模型远程调度（诊断）",
        "outline_name": "远端医疗大模型调用",
        "params": {"patient_cycle": True, "batch": 1},
        "param_note": "每个任务取 1 位患者（按数据集轮换），与固定套件同口径",
        "source_hint": "clinic",
        # 医疗中心不能执行模型 server 部分推理 → 诊断**没有本地执行策略**，
        # 只能走云边端协同；本地阶段不含此类任务。
        "strategies": ["collaborative"],
        "strategy_note": "无本地执行策略（医疗中心不能执行模型 server 部分推理）",
    },
    "compute": {
        "label": "医疗数据处理（算力）",
        "outline_name": "医疗数据处理",
        "params": {"instruments": 8, "rows": 3072, "intensity": 450,
                   "partition_count": 3},
        "param_note": "8 器械 × 3072 行 × 强度 450，3 分区",
        "source_hint": "clinic",
        "strategies": ["local", "collaborative"],
    },
    "sync": {
        "label": "患者数据库同步（通信）",
        "outline_name": "患者数据库同步",
        "params": {"bandwidth_mbps": 20.0, "concurrency": 4, "chunk_kb": 4},
        "param_note": "20 Mbps 链路 · 4 并发流 · 4 KB 分块",
        "source_hint": "clinic",
        "strategies": ["local", "collaborative"],
    },
    "routine": {
        "label": "医疗信息远程查询（日常）",
        "outline_name": "医疗信息远程查询",
        "params": {"jobs": 4, "rows": 8192, "intensity": 2400},
        "param_note": "每次 4 个日常作业，8192 行 × 强度 2400",
        "source_hint": "clinic",
        "strategies": ["local", "collaborative"],
    },
}

KIND_ORDER: List[str] = ["diagnosis", "compute", "sync", "routine"]

# --------------------------------------------------------------------------- #
# 任务数上限（容错，静默截断——不做任何提示）
#
# 为什么必须有上限：方案运行的任务数是由用户输入的，而 run 级校验的 MAX_REPEATS
# 对方案子 run 不生效。任务数过大时有两个真实故障：
#   ① 崩溃风险：每个任务一行 attempts，10 万级任务会把 SQLite 与内存打爆；
#   ② 静默算错：阶段/逐类统计的查询带 limit=20000，超过就被截断，
#      报出来的均值/总量是错的（比崩溃更危险，因为看不出来）。
# 因此既截输入、也截总数，保证任何输入都落在"能跑完且算得准"的范围内。
# --------------------------------------------------------------------------- #
MAX_TASKS_PER_UNIT = 1000   # 单个「设备 × 任务类型」可输入的任务数上限
MAX_TASKS_PER_PHASE = 1000  # 每阶段任务总数上限；超出按比例压缩到该值
MAX_DEVICES = 8             # 发起设备数上限

# 平台侧可自动判定的标准类型：
#   gain_gt     协同相对本地"总用时"的提速百分比须 > value
#   complete_gt 任务完成率须 > value（%）
#   external    依赖外部仪表（测试仪/损伤仪）测量，平台只记录配置与待填实测值
CRITERIA_TYPES = ("gain_gt", "complete_gt", "complete_gte", "external")


def _kinds(per_device: int, overrides: Optional[Dict[str, Dict[str, Any]]] = None
           ) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    overrides = overrides or {}
    for kind in KIND_ORDER:
        meta = KIND_META[kind]
        out.append({
            "kind": kind,
            "label": meta["label"],
            "outline_name": meta["outline_name"],
            "per_device": int(overrides.get(kind, {}).get("per_device", per_device)),
            "params": {**meta["params"], **overrides.get(kind, {}).get("params", {})},
            "param_note": meta["param_note"],
            "strategies": list(meta.get("strategies") or ["local", "collaborative"]),
            "strategy_note": meta.get("strategy_note"),
        })
    return out


def _devices(sources: Optional[List[Dict[str, str]]] = None) -> List[Dict[str, str]]:
    """大纲里的"微型台式电子计算机 B / C" ↔ 平台的端侧发起方。"""
    if sources:
        return sources
    return [
        {"id": "B", "name": "微型台式电子计算机B", "source": "clinic-1"},
        {"id": "C", "name": "微型台式电子计算机C", "source": "clinic-2"},
    ]


# --------------------------------------------------------------------------- #
# §5.4 协同调度测试
# --------------------------------------------------------------------------- #
PLAN_COLLAB: Dict[str, Any] = {
    "plan_id": "plan-5.4-collab",
    "name": "5.4 协同调度测试",
    "outline_ref": "《测试大纲》§5.4 协同调度测试",
    "outline_title": "协同调度测试",
    "test_object": "医疗智联专网云边端协同调度控制系统",
    "purpose": "验证医疗智联专网的云边端协同调度能力",
    "objective": ("端侧医疗设备产生医疗任务，验证控制系统能否调用云边端通算资源处理医疗任务，"
                  "与本地调度策略相比降低任务总完成时间。"),
    "duration_min": 20,
    "generate_window_min": 5,
    "devices": _devices(),
    "kinds": _kinds(5),
    "total_tasks_declared": 40,
    "strategies": ["local", "collaborative"],
    "batch_repeats": 1,
    # 任务保障：失败自动重传（不含首次的额度）
    "retries": 3,
    "retry_backoff_s": 0.5,
    "notes": [
        "诊断类（远端医疗大模型调用）无本地执行策略：医疗中心不能执行模型 server 部分推理，"
        "本地（未调度）阶段不含诊断任务；总用时对比只计入两阶段共有的任务类型。",
    ],
    "preconditions": [
        "医疗智联专网稳定运行",
        "云边端协同调度控制系统运作正常",
        "微型台式电子计算机和 CPE 设备接入正常，完成对应配置",
    ],
    "steps": [
        "微型台式电子计算机B和C设定各产生四类医疗任务，各设备每类任务 5 个，"
        "共 40 个医疗任务需求，在 5 分钟内由不同端侧医疗设备随机产生。",
        "记录各医疗任务发起时间与完成时间，统计使用本地调度策略下所有任务处理总用时。",
        "切换为云边端协同调度策略，医疗设备再次产生相同医疗任务需求。",
        "记录各医疗任务发起时间与完成时间，统计经协同调度后所有任务总用时。",
    ],
    "criteria": [
        {"id": "gain", "type": "gain_gt", "value": 0.0, "unit": "%",
         "label": "协同调度后所有任务总用时相较于未调度提升",
         "source": "platform"},
        {"id": "completion", "type": "complete_gte", "value": 100.0, "unit": "%",
         "label": "任务完成率（失败自动重传后）", "source": "platform"},
    ],
    "attachments": [],
}


# --------------------------------------------------------------------------- #
# §5.5 医疗智联专网测试
# --------------------------------------------------------------------------- #
PLAN_NETWORK: Dict[str, Any] = {
    "plan_id": "plan-5.5-network",
    "name": "5.5 医疗智联专网测试",
    "outline_ref": "《测试大纲》§5.5 医疗智联专网测试",
    "outline_title": "医疗智联专网测试",
    "test_object": "面向 AI 诊疗的医疗智联专网",
    "purpose": "验证面向 AI 诊疗的医疗智联专网整体性能",
    "objective": ("端侧医疗设备产生远端医疗大模型调用、患者数据库同步、医疗数据处理、"
                  "医疗信息远程查询四类医疗任务，验证在新质医疗任务需求下，"
                  "面向 AI 诊疗的医疗智联专网整体功能和服务能力。"),
    "duration_min": 720,          # 12 小时
    "generate_window_min": 600,   # 10 小时内随机产生
    "devices": _devices(),
    # 大纲原文「各设备每类50个，共200个」：2 设备 × 4 类 × 50 = 400 与明示总数 200
    # 不一致。本方案以大纲明示的**总数 200** 为准，按 25 个/设备/类 展开。
    "kinds": _kinds(25),
    "total_tasks_declared": 200,
    "notes": [
        "诊断类（远端医疗大模型调用）无本地执行策略：医疗中心不能执行模型 server 部分推理，"
        "本地（未调度）阶段不含诊断任务；总用时对比只计入两阶段共有的任务类型。",
        "大纲操作步骤写「各设备每类50个，共200个医疗任务需求」；"
        "按 2 台设备 × 4 类 × 50 个/设备/类 计算为 400 个，与明示的 200 个不一致。"
        "本方案取大纲明示的总数 200（25 个/设备/类），前端「自定义实验配置」可改回 50。",
    ],
    "strategies": ["local", "collaborative"],
    "batch_repeats": 1,
    "retries": 3,
    "retry_backoff_s": 0.5,
    "preconditions": [
        "医疗智联专网稳定运行",
        "各 CPE 上电启动正常，并完成相应配置",
        "各 CPE 接入正常",
        "测试仪与损伤仪接入正常",
        "医疗设备配置完成，运作正常",
    ],
    "steps": [
        "以 1000 帧/s 速率从医院园区测试仪持续往智算中心测试仪发送 512 字节大小的数据包，持续三小时。",
        "在该医院园区和智算中心之间使用损伤仪构建 2 条路径随机丢包各达 10% 的模拟路径受损情况。",
        "微型台式电子计算机B和C设定各产生四类医疗任务，各设备每类 50 个，"
        "共 200 个医疗任务需求，在 10 小时内随机产生。",
        "检查统计医院园区和智算中心之间传输丢包率。",
        "12 小时后检查医疗任务完成情况。",
    ],
    "criteria": [
        {"id": "loss", "type": "external", "value": 1.0, "unit": "%",
         "label": "医院园区与智算中心之间传输丢包率不超过 1%",
         "source": "测试仪 / 损伤仪（外部仪表实测）"},
        {"id": "completion", "type": "complete_gte", "value": 100.0, "unit": "%",
         "label": "医疗任务完成率（失败自动重传后）", "source": "platform"},
        {"id": "gain", "type": "gain_gt", "value": 0.0, "unit": "%",
         "label": "协同调度总用时较本地调度提升", "source": "platform"},
    ],
    # 大纲里的流量注入与损伤构建由外部仪表完成，平台记录配置以便报告引用
    "attachments": [
        {"id": "traffic", "name": "测试仪流量注入",
         "detail": "医院园区测试仪 → 智算中心测试仪：1000 帧/s · 512 字节/包 · 持续 3 小时",
         "fps": 1000, "packet_bytes": 512, "duration_min": 180,
         "verified_by": "外部测试仪"},
        {"id": "impairment", "name": "损伤仪路径受损",
         "detail": "医院园区 ↔ 智算中心构建 2 条路径，随机丢包各 10%",
         "paths": 2, "loss_pct": 10.0,
         "verified_by": "外部损伤仪"},
    ],
}


PLANS: Dict[str, Dict[str, Any]] = {p["plan_id"]: p for p in (PLAN_COLLAB, PLAN_NETWORK)}
PLAN_ORDER: List[str] = [PLAN_COLLAB["plan_id"], PLAN_NETWORK["plan_id"]]


# --------------------------------------------------------------------------- #
# 展开 / 校验
# --------------------------------------------------------------------------- #
def plan_total_tasks(plan: Dict[str, Any], phase: Optional[str] = None) -> int:
    """一阶段的任务总数——必须与 expand_units() 实际展开的一致（含上限截断），
    否则界面显示的任务数与真正下发的数量对不上。

    `phase=None` 表示"类型最全的那个阶段"（协同阶段），用于对外展示任务总数。
    """
    return sum(int(u["repeats"]) for u in expand_units(plan, phase))


def get_plan(plan_id: str) -> Dict[str, Any]:
    if plan_id not in PLANS:
        raise KeyError(f"unknown plan_id: {plan_id}")
    return PLANS[plan_id]


def apply_overrides(plan: Dict[str, Any], overrides: Dict[str, Any]) -> Dict[str, Any]:
    """把前端"自定义实验配置"的改动套到方案上，返回新的方案快照。

    可覆盖：devices（发起方）、kinds（每类任务数与参数）、
    generate_window_min（产生窗口）、duration_min（测试时长）、strategies。
    """
    out = {**plan}
    ov = overrides or {}

    if isinstance(ov.get("devices"), list) and ov["devices"]:
        devices = []
        for i, d in enumerate(ov["devices"]):
            if not isinstance(d, dict) or not d.get("source"):
                continue
            devices.append({"id": str(d.get("id") or chr(ord("A") + i)),
                            "name": str(d.get("name") or d["source"]),
                            "source": str(d["source"])})
        if devices:
            out["devices"] = devices[:MAX_DEVICES]

    if isinstance(ov.get("kinds"), (dict, list)):
        patch: Dict[str, Dict[str, Any]] = {}
        items = (ov["kinds"].items() if isinstance(ov["kinds"], dict)
                 else ((k.get("kind"), k) for k in ov["kinds"] if isinstance(k, dict)))
        for kind, spec in items:
            if kind in KIND_META and isinstance(spec, dict):
                patch[str(kind)] = spec
        out["kinds"] = _kinds(int(plan["kinds"][0]["per_device"]), patch)
        for spec in out["kinds"]:      # 单类输入上限（静默截断）
            spec["per_device"] = max(0, min(MAX_TASKS_PER_UNIT,
                                            int(spec.get("per_device") or 0)))

    # 产生窗口按分钟可以是小数（自定义时常用秒级窗口做缩比验证）
    if ov.get("generate_window_min") is not None:
        try:
            out["generate_window_min"] = max(0.0, float(ov["generate_window_min"]))
        except (TypeError, ValueError):
            pass
    if ov.get("duration_min") is not None:
        try:
            out["duration_min"] = max(0, int(ov["duration_min"]))
        except (TypeError, ValueError):
            pass

    if isinstance(ov.get("strategies"), list) and ov["strategies"]:
        wanted = [s for s in ov["strategies"] if s in ("local", "collaborative")]
        if wanted:
            # 顺序固定：先本地（未调度），再协同——与大纲"先统计本地、再切换协同"一致
            out["strategies"] = [s for s in ("local", "collaborative") if s in wanted]

    if ov.get("retries") is not None:
        try:
            out["retries"] = max(0, min(10, int(ov["retries"])))
        except (TypeError, ValueError):
            pass
    for key in ("name", "label", "objective"):
        if ov.get(key):
            out[key] = str(ov[key])
    out["customized"] = any(
        ov.get(k) not in (None, [], {}) for k in
        ("devices", "kinds", "generate_window_min", "duration_min", "strategies",
         "name", "retries")
    ) or bool(out.get("customized"))
    return out


def expand_units(plan: Dict[str, Any],
                 phase: Optional[str] = None) -> List[Dict[str, Any]]:
    """方案 → 执行单元列表：一台设备 × 一类任务 = 一个子 run。

    `phase` 给定时只返回该阶段适用的单元——诊断类没有本地执行策略，
    因此本地阶段不会出现诊断单元。
    """
    units: List[Dict[str, Any]] = []
    for device in (plan.get("devices") or [])[:MAX_DEVICES]:
        for spec in plan.get("kinds") or []:
            if phase and phase not in (spec.get("strategies")
                                       or ["local", "collaborative"]):
                continue
            count = int(spec.get("per_device") or 0)
            if count <= 0:
                continue
            units.append({
                "unit_id": f"{device['id']}·{spec['kind']}",
                "device_id": device["id"],
                "device_name": device.get("name") or device["source"],
                "source": device["source"],
                "kind": spec["kind"],
                "label": spec.get("label") or spec["kind"],
                "repeats": min(count, MAX_TASKS_PER_UNIT),
                "params": dict(spec.get("params") or {}),
                "strategies": list(spec.get("strategies")
                                   or ["local", "collaborative"]),
            })
    _apply_phase_budget(units)
    return units


def _apply_phase_budget(units: List[Dict[str, Any]]) -> None:
    """把一阶段的任务总数压到 MAX_TASKS_PER_PHASE（原地修改，静默）。

    按比例压缩而不是直接砍掉尾部单元——否则某个任务类型会整类消失，
    方案就不再覆盖四类任务了。每个单元至少保留 1 个任务。
    """
    total = sum(int(u["repeats"]) for u in units)
    if total <= MAX_TASKS_PER_PHASE or not units:
        return
    scale = MAX_TASKS_PER_PHASE / float(total)
    for u in units:
        u["repeats"] = max(1, int(int(u["repeats"]) * scale))
    # 取整后可能与上限有偏差：按序补齐 / 从最大的单元削减
    while sum(int(u["repeats"]) for u in units) < MAX_TASKS_PER_PHASE:
        for u in units:
            if sum(int(x["repeats"]) for x in units) >= MAX_TASKS_PER_PHASE:
                break
            u["repeats"] = int(u["repeats"]) + 1
    while sum(int(u["repeats"]) for u in units) > MAX_TASKS_PER_PHASE:
        biggest = max(units, key=lambda u: int(u["repeats"]))
        if int(biggest["repeats"]) <= 1:
            break
        biggest["repeats"] = int(biggest["repeats"]) - 1


def validate_plan(plan: Dict[str, Any]) -> List[str]:
    problems: List[str] = []
    if not plan.get("devices"):
        problems.append("至少需要一台发起设备")
    if plan_total_tasks(plan) <= 0:
        problems.append("任务总数必须大于 0")
    if not plan.get("strategies"):
        problems.append("至少需要一种调度策略")
    if int(plan.get("generate_window_min") or 0) > int(plan.get("duration_min") or 0) > 0:
        problems.append("任务产生窗口不能大于测试时长")
    return problems


def catalog_entry(plan: Dict[str, Any]) -> Dict[str, Any]:
    """给 GET /api/plans 的紧凑条目（含完整配置，前端点开即显示）。"""
    expanded = plan_total_tasks(plan)
    declared = int(plan.get("total_tasks_declared") or expanded)
    return {
        **plan,
        "total_tasks": declared,
        "total_tasks_expanded": expanded,
        "total_tasks_mismatch": declared != expanded,
        "kind_count": len(KIND_ORDER),
        "device_count": len(plan.get("devices") or []),
        "per_device_per_kind": (plan["kinds"][0]["per_device"] if plan.get("kinds") else 0),
        "tasks_per_phase": {ph: plan_total_tasks(plan, ph)
                            for ph in ("local", "collaborative")},
        "collaborative_only_kinds": [
            k["kind"] for k in plan.get("kinds") or []
            if "local" not in (k.get("strategies") or ["local", "collaborative"])],
        "common_kinds": [
            k["kind"] for k in plan.get("kinds") or []
            if "local" in (k.get("strategies") or ["local", "collaborative"])],
        "criteria_types": sorted({c["type"] for c in plan.get("criteria") or []}),
    }


def list_plans() -> List[Dict[str, Any]]:
    return [catalog_entry(PLANS[pid]) for pid in PLAN_ORDER]
