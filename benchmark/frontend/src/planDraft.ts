import type { CreatePlanRunBody, Kind, PlanInfo, ReqMode } from './types';
import { KIND_ZH, KINDS } from './types';

/**
 * 「实验配置」在前端的可编辑草稿。
 *
 * 方案（PlanInfo）是只读的目录数据；用户点开方案后把它拷进草稿，
 * 任何改动都写回草稿，下发时作为 `overrides` 传给后端，
 * 后端把 overrides 套到方案上生成不可变的配置快照（plan_runs.snapshot）。
 */
/**
 * 任务数上限，与后端 `benchmark/backend/plans.py` 的常量保持一致。
 * 超限**静默截断**（不做任何提示）：既防崩溃，也防统计查询截断导致的静默算错。
 */
export const MAX_TASKS_PER_UNIT = 1000;   // 单个「设备 × 任务类型」的任务数上限
export const MAX_TASKS_PER_PHASE = 1000;  // 每阶段任务总数上限（超出按比例压缩）
export const MAX_DEVICES = 8;

/** 各任务类型适用的调度策略（诊断无本地执行策略，只能协同） */
export const KIND_STRATEGIES: Record<Kind, ReqMode[]> = {
  diagnosis: ['collaborative'],
  compute: ['local', 'collaborative'],
  sync: ['local', 'collaborative'],
  routine: ['local', 'collaborative'],
};

export function kindStrategies(kind: Kind): ReqMode[] {
  return KIND_STRATEGIES[kind] ?? ['local', 'collaborative'];
}

export interface DraftDevice {
  id: string;
  name: string;
  source: string;
}

export interface DraftKind {
  kind: Kind;
  per_device: number;
  params: Record<string, number | boolean | string>;
}

export interface Draft {
  /** 基于哪个方案（自定义时仍沿用所选方案作为模板） */
  basePlanId: string;
  name: string;
  duration_min: number;
  generate_window_min: number;
  /** 失败自动重传额度（首次之外） */
  retries: number;
  strategies: ReqMode[];
  devices: DraftDevice[];
  kinds: DraftKind[];
  /** 是否已偏离方案默认值 → UI 显示「自定义配置」 */
  customized: boolean;
}

/** 各任务类型可编辑的参数：类型、标签、范围、步长 */
export interface ParamField {
  key: string;
  label: string;
  kind: 'int' | 'float' | 'bool';
  min?: number;
  max?: number;
  step?: number;
  hint?: string;
}

export const KIND_PARAM_FIELDS: Record<Kind, ParamField[]> = {
  diagnosis: [
    { key: 'batch', label: '每任务患者数 batch', kind: 'int', min: 1, max: 16,
      hint: '一次诊断任务携带的患者数量' },
    { key: 'patient_cycle', label: '患者轮换', kind: 'bool',
      hint: '按数据集轮换患者，避免同一患者被反复推理' },
  ],
  compute: [
    { key: 'instruments', label: '器械数', kind: 'int', min: 1, max: 64 },
    { key: 'rows', label: '行数', kind: 'int', min: 1, max: 65536 },
    { key: 'intensity', label: '强度', kind: 'int', min: 1, max: 1000 },
    { key: 'partition_count', label: '分区数', kind: 'int', min: 1, max: 16 },
  ],
  sync: [
    { key: 'bandwidth_mbps', label: '带宽 Mbps', kind: 'float', min: 1, max: 10000, step: 0.5 },
    { key: 'concurrency', label: '并发流', kind: 'int', min: 1, max: 64 },
    { key: 'chunk_kb', label: '分块 KB', kind: 'int', min: 1, max: 4096 },
  ],
  routine: [
    { key: 'jobs', label: '作业数', kind: 'int', min: 1, max: 64 },
    { key: 'rows', label: '行数', kind: 'int', min: 1, max: 65536 },
    { key: 'intensity', label: '强度', kind: 'int', min: 1, max: 1000 },
  ],
};

export function draftFromPlan(plan: PlanInfo): Draft {
  return normalizeDraft({
    basePlanId: plan.plan_id,
    name: plan.name,
    duration_min: plan.duration_min,
    generate_window_min: plan.generate_window_min,
    retries: plan.retries ?? 3,
    strategies: [...plan.strategies],
    devices: plan.devices.map((d) => ({ ...d })),
    kinds: plan.kinds.map((k) => ({ kind: k.kind, per_device: k.per_device,
                                    params: { ...k.params } as Draft['kinds'][number]['params'] })),
    customized: false,
  });
}

/** 一份"从零开始"的自定义模板：1 台设备 × 4 类任务 × 1 个，立即可跑 */
export function customDraft(base: PlanInfo | null): Draft {
  const devices: DraftDevice[] = [{ id: 'B', name: '微型台式电子计算机B', source: 'clinic-1' }];
  const kinds: DraftKind[] = KINDS.map((kind) => {
    const src = base?.kinds.find((k) => k.kind === kind);
    return { kind, per_device: 1,
             params: { ...(src?.params ?? {}) } as DraftKind['params'] };
  });
  return normalizeDraft({
    basePlanId: base?.plan_id ?? 'plan-5.4-collab',
    name: '自定义实验配置',
    duration_min: 10,
    generate_window_min: 0,
    retries: base?.retries ?? 3,
    strategies: ['local', 'collaborative'],
    devices,
    kinds,
    customized: true,
  });
}

/** 单类任务数按上限截断 */
export function clampPerDevice(n: number): number {
  if (!Number.isFinite(n)) return 0;
  return Math.max(0, Math.min(MAX_TASKS_PER_UNIT, Math.trunc(n)));
}

/**
 * 把草稿归一化：设备数、单类任务数截断到上限，并按阶段预算压缩任务总数。
 * 界面与下发都走这一份结果，保证"看到的"与"下发的"一致。
 */
export function normalizeDraft(draft: Draft): Draft {
  const devices = draft.devices.slice(0, MAX_DEVICES);
  const kinds = draft.kinds.map((k) => ({ ...k, per_device: clampPerDevice(k.per_device) }));
  return { ...draft, devices, kinds: applyPhaseBudget(kinds, devices) };
}

/** 阶段任务总数上限：按比例压缩，每类至少 1 个（不整类消失） */
function applyPhaseBudget(kinds: DraftKind[], devices: DraftDevice[]): DraftKind[] {
  const n = devices.length || 1;
  const total = kinds.reduce((s, k) => s + k.per_device * n, 0);
  if (total <= MAX_TASKS_PER_PHASE || total === 0) return kinds;
  const scale = MAX_TASKS_PER_PHASE / total;
  const out = kinds.map((k) => ({
    ...k,
    per_device: k.per_device > 0 ? Math.max(1, Math.floor(k.per_device * scale)) : 0,
  }));
  const sum = () => out.reduce((s, k) => s + k.per_device * n, 0);
  while (sum() < MAX_TASKS_PER_PHASE && out.some((k) => k.per_device > 0)) {
    for (const k of out) {
      if (sum() >= MAX_TASKS_PER_PHASE) break;
      if (k.per_device > 0) k.per_device += 1;
    }
  }
  while (sum() > MAX_TASKS_PER_PHASE) {
    const biggest = out.reduce((a, b) => (b.per_device > a.per_device ? b : a), out[0]);
    if (biggest.per_device <= 1) break;
    biggest.per_device -= 1;
  }
  return out;
}

/** 指定阶段的任务总数（诊断不进本地阶段） */
export function phaseTasks(draft: Draft, phase: ReqMode): number {
  const d = normalizeDraft(draft);
  return d.kinds.reduce((s, k) => (
    kindStrategies(k.kind).includes(phase) ? s + k.per_device * d.devices.length : s), 0);
}

/** 类型最全的那个阶段的任务总数（协同阶段） */
export function draftTotalTasks(draft: Draft): number {
  return phaseTasks(draft, 'collaborative');
}

/** 各阶段累计任务数之和 */
export function draftTotalAttempts(draft: Draft): number {
  return draft.strategies.reduce((s, ph) => s + phaseTasks(draft, ph), 0);
}

export function draftOverrides(draft: Draft): CreatePlanRunBody['overrides'] {
  const d = normalizeDraft(draft);
  const kinds: Record<string, { per_device: number; params: Record<string, unknown> }> = {};
  for (const k of d.kinds) {
    const params: Record<string, unknown> = {};
    for (const f of KIND_PARAM_FIELDS[k.kind]) {
      const v = k.params[f.key];
      if (v !== undefined && v !== null && v !== '') params[f.key] = v;
    }
    kinds[k.kind] = { per_device: Math.max(0, k.per_device), params };
  }
  return {
    devices: d.devices.map((x) => ({ id: x.id, name: x.name, source: x.source })),
    kinds,
    generate_window_min: d.generate_window_min,
    duration_min: d.duration_min,
    retries: d.retries,
    strategies: d.strategies,
    ...(d.customized ? { name: d.name } : {}),
  };
}

export function draftProblems(draft: Draft): string[] {
  const out: string[] = [];
  if (draft.devices.length === 0) out.push('至少需要一台发起设备');
  if (draftTotalTasks(draft) <= 0) out.push('任务总数必须大于 0');
  if (draft.strategies.length === 0) out.push('至少选择一种调度策略');
  if (draft.duration_min <= 0) out.push('测试时长必须大于 0');
  if (draft.generate_window_min < 0) out.push('任务产生窗口不能为负');
  if (draft.generate_window_min > draft.duration_min) {
    out.push('任务产生窗口不能大于测试时长');
  }
  if (draft.retries < 0 || draft.retries > 10) out.push('重传额度应在 0–10 之间');
  const dupes = draft.devices.map((d) => d.source)
    .filter((s, i, arr) => arr.indexOf(s) !== i);
  if (dupes.length) out.push(`发起设备重复：${[...new Set(dupes)].join('、')}`);
  for (const k of draft.kinds) {
    if (k.per_device < 0) out.push(`${k.kind} 的任务数不能为负`);
  }
  return out;
}

/**
 * 任务到达速率（个/分钟）。方案要求「在 M 分钟内随机产生」，窗口越小、
 * 任务越挤：压缩窗口虽然跑得快，但两台设备的任务会几乎同时打到同一批 Pod 上，
 * 产生排队与相互干扰，测出来的不再是稳态调度差异。这里把它显式提示出来。
 */
export function arrivalPerMin(draft: Draft): number | null {
  if (draft.generate_window_min <= 0) return null;
  const total = draftTotalTasks(draft);
  if (total <= 0) return null;
  return total / draft.generate_window_min;
}

/** 窗口过窄时的提醒（null = 无需提醒） */
export function windowCaution(draft: Draft): string | null {
  const rate = arrivalPerMin(draft);
  if (rate === null) {
    return '窗口为 0：全部任务会立即同时产生，并发压力最大，只适合冒烟验证。';
  }
  const gapS = 60 / rate;
  if (gapS < 3) {
    return `平均每 ${gapS.toFixed(1)} 秒产生 1 个任务，任务会相互抢占同一批 Pod，`
         + '测到的主要是排队与干扰；建议把窗口放大到与大纲一致（如 5 / 600 分钟）。';
  }
  if (gapS < 10) {
    return `平均每 ${gapS.toFixed(1)} 秒产生 1 个任务，接近连续负载；`
         + '与大纲的稀疏到达相比，协同的并行收益会被放大。';
  }
  return null;
}

/** 预估执行时长：窗口 + 任务执行（粗估，仅用于提示用户） */
export function estimateMinutes(draft: Draft, perTaskSec = 2.5): number {
  const phases = draft.strategies.length ? draft.strategies : (['collaborative'] as ReqMode[]);
  return Math.round(phases.reduce((sum, ph) => (
    sum + draft.generate_window_min + (phaseTasks(draft, ph) * perTaskSec) / 60), 0));
}

export function fmtMinutes(min: number): string {
  if (!Number.isFinite(min) || min < 0) return '—';
  if (min < 1) return `${Math.round(min * 60)} 秒`;
  if (min < 60) return `${Math.round(min * 10) / 10} 分钟`;
  const h = Math.floor(min / 60);
  const m = Math.round(min % 60);
  return m ? `${h} 小时 ${m} 分` : `${h} 小时`;
}

/**
 * 结果区显示的任务类型名：优先用方案里对齐大纲的名称
 * （如「医疗模型远程调度（诊断）」），拿不到方案配置时退回通用简称。
 * 单页里配置区与结果区必须叫同一个名字，否则评审对不上。
 */
export function planKindLabel(
  plan: { kinds?: { kind: Kind; label?: string }[] } | null | undefined,
  kind: string,
): string {
  const hit = plan?.kinds?.find((k) => k.kind === kind);
  if (hit?.label) return hit.label;
  return KIND_ZH[kind as Kind] ?? kind;
}
