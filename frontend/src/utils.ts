// Small shared helpers & lookups (v3.0: four kinds 诊断/计算/通信/日常)
// 三级命名（数据中心 / 医疗中心 / 医院）统一取自 ./terms。

import type { HealthState, HospitalId, ModelKind, NodeInfo, SourceId } from './types';
import { TIER_LABEL, entityRole, nodeRoleText } from './terms';

// 实体显示名统一从 terms 再导出（含 SOURCE_NAME 别名）
export { entityName, entityName as SOURCE_NAME } from './terms';

export const MODELS: ModelKind[] = ['diagnosis', 'compute', 'sync', 'routine'];

export const MODEL_LABEL: Record<string, string> = {
  diagnosis: '诊断',
  compute: '计算',
  sync: '通信',
  routine: '日常',
};

export const MODEL_EN: Record<string, string> = {
  diagnosis: 'DIAGNOSIS',
  compute: 'COMPUTE',
  sync: 'SYNC',
  routine: 'ROUTINE',
};

// Schematic ink tones: brass / olive / slate-grey / burnt orange.
export const MODEL_COLOR: Record<string, string> = {
  diagnosis: '#946b3c',   // brass
  compute: '#4f5744',     // olive
  sync: '#5a6360',        // graphite green
  routine: '#a4562a',     // burnt ochre
};

// 发起端：hospital-* = 医疗中心，clinic-* = 医院
export const SOURCES: SourceId[] = ['hospital-a', 'hospital-b', 'clinic-1', 'clinic-2'];
export const HOSPITALS: HospitalId[] = ['hospital-a', 'hospital-b'];

/** 发起端 id → 角色名（医疗中心 / 医院） */
export const SOURCE_ROLE: Record<string, string> = {
  'hospital-a': entityRole('hospital-a'),
  'hospital-b': entityRole('hospital-b'),
  'clinic-1': entityRole('clinic-1'),
  'clinic-2': entityRole('clinic-2'),
};

/** 后端 role 字段 → 中文角色名 */
export const ROLE_TEXT: Record<string, string> = {
  edge: TIER_LABEL.medical,
  terminal: TIER_LABEL.hospital,
  '?': '?',
};


export const STATUS_LABEL: Record<string, string> = {
  running: '运行中',
  queued: '排队中',
  finished: '已完成',
  completed: '已完成',
  failed: '失败',
  not_found: '未找到',
};

export const STATUS_COLOR: Record<string, string> = {
  running: '#946b3c',
  queued: '#776f60',
  finished: '#565c46',
  completed: '#565c46',
  failed: '#9c3b1e',
};

// Default image tags used by the cluster editor when adding new pods (v3.0)
export const IMG_CLINIC = '10.29.182.66:5000/k8s-repo/clinic:v3.0';
export const IMG_HOSPITAL = '10.29.182.66:5000/k8s-repo/hospital:v3.0';

export const KIND_LABEL: Record<string, string> = {
  hospital: TIER_LABEL.medical,
  clinic: TIER_LABEL.hospital,
};

export const AFFINITY_LABEL: Record<string, string> = {
  fixed: '固定节点',
  'role-edge': '业务节点 (role=edge)',
  'spread-edge': '业务节点分散 (反亲和)',
};

/** Compact affinity wording for the dense list view. */
export const AFFINITY_SHORT: Record<string, string> = {
  fixed: '固定',
  'role-edge': '业务 role=edge',
  'spread-edge': '业务分散',
};

/** Read-only platform deployments (mirrors the scheduler READONLY_APPS list). */
export const READONLY_LABEL: Record<string, { zh: string; en: string }> = {
  'medical-server': { zh: '医疗推理服务', en: '' },
  'dc-services': { zh: '患者库服务', en: '' },
  redis: { zh: '队列 / 记录', en: '' },
  scheduler: { zh: '调度', en: '' },
  monitoring: { zh: '指标观测', en: '' },
  prediction: { zh: '预测服务', en: '' },
};

/** Stable left-to-right / top-to-bottom node ordering used by map, list, arch. */
export const NODE_ORDER = ['node1', 'node2', 'node3', 'desktop-jm5iec6'];

export function sortNodes(nodes: NodeInfo[]): NodeInfo[] {
  const rank = (n: NodeInfo) => {
    const i = NODE_ORDER.indexOf(n.name);
    return i < 0 ? NODE_ORDER.length : i;
  };
  return [...(nodes || [])].sort((a, b) => rank(a) - rank(b) || a.name.localeCompare(b.name));
}

/** 业务节点判定：只有 node1 / node2 承载医疗中心 / 医院实体。 */
export function isEdgeNode(node: NodeInfo | undefined | null, name?: string): boolean {
  const key = name ?? node?.name ?? '';
  if (key === 'node1' || key === 'node2') return true;
  return key ? node?.role === 'edge' : false;
}

/** 节点角色（纯中文）：数据中心 / 医疗中心 · 医院 / 控制面 */
export function nodeRoleLabel(role?: string | null, name?: string): { zh: string; en: string } {
  return { zh: nodeRoleText(role, name), en: '' };
}

/** `10.29.182.66:5000/k8s-repo/clinic:v3.0` → `clinic:v3.0` (tag only). */
export function imageTag(image?: string): string {
  if (!image) return '—';
  const seg = image.split('/').pop() || image;
  return seg || '—';
}

// ---------------------------------------------------------------------------
// Health normalisation (GET /health values are heterogeneous)
// ---------------------------------------------------------------------------
const OK_WORDS = ['ok', 'healthy', 'running', 'ready', 'up', 'alive', 'true', 'green'];
const DEGRADED_WORDS = ['degraded', 'warning', 'warn', 'partial', 'unstable'];
const DOWN_WORDS = ['unavailable', 'down', 'error', 'failed', 'fail', 'dead',
  'false', 'timeout', 'refused', 'unreachable', 'not ready', 'notready'];

export function healthState(v: unknown): HealthState {
  if (v === undefined || v === null) return 'unknown';
  if (typeof v === 'boolean') return v ? 'ok' : 'down';
  if (typeof v === 'number') return v > 0 ? 'ok' : 'down';

  if (typeof v === 'string') {
    const s = v.trim().toLowerCase();
    if (!s) return 'unknown';
    if (OK_WORDS.includes(s)) return 'ok';
    if (DEGRADED_WORDS.includes(s)) return 'degraded';
    if (DOWN_WORDS.some((w) => s.includes(w))) return 'down';
    if (OK_WORDS.some((w) => s.startsWith(w))) return 'ok';
    return 'unknown';
  }

  const o = v as Record<string, unknown>;
  if (o.ok === false || o.ready === false) return 'down';
  const st = String(o.status ?? o.state ?? o.health ?? '').trim().toLowerCase();
  if (st) {
    if (DEGRADED_WORDS.includes(st)) return 'degraded';
    if (OK_WORDS.includes(st)) return 'ok';
    if (DOWN_WORDS.some((w) => st.includes(w))) return 'down';
  }
  if (o.ok === true || o.ready === true) return 'ok';
  return 'unknown';
}

/** Short human-readable detail for a defensive health value. */
export function healthDetail(v: unknown): string {
  if (v === undefined || v === null) return '无响应数据';
  if (typeof v === 'string') return v === 'ok' ? 'ok' : v;
  if (typeof v === 'boolean') return v ? 'ok' : 'unavailable';
  if (typeof v === 'number') return String(v);
  const o = v as Record<string, unknown>;
  const st = o.status ?? o.state ?? o.health;
  if (st !== undefined && st !== null) return String(st);
  try {
    return JSON.stringify(o).slice(0, 120);
  } catch {
    return 'object';
  }
}

export function fmtPct(v?: number | null): string {
  if (v === undefined || v === null || Number.isNaN(Number(v))) return '—';
  return `${(Number(v) * 100).toFixed(0)}%`;
}

export function fmtMemBytes(v?: number | null): string {
  if (v === undefined || v === null || Number.isNaN(Number(v))) return '—';
  const n = Number(v);
  if (n >= 1024 ** 3) return `${(n / 1024 ** 3).toFixed(2)} GiB`;
  if (n >= 1024 ** 2) return `${(n / 1024 ** 2).toFixed(1)} MiB`;
  return `${(n / 1024).toFixed(0)} KiB`;
}

export function fmtTime(iso?: string): string {
  if (!iso) return '—';
  try {
    return new Date(iso).toLocaleString('zh-CN', {
      hour12: false, month: '2-digit', day: '2-digit',
      hour: '2-digit', minute: '2-digit', second: '2-digit',
    });
  } catch {
    return iso;
  }
}

export function fmtMs(v?: number): string {
  if (v === undefined || v === null || Number.isNaN(Number(v))) return '—';
  return `${Number(v).toFixed(1)} ms`;
}

export function clamp(n: number, lo: number, hi: number): number {
  return Math.min(hi, Math.max(lo, n));
}

/** Deterministic, sortable serialization used for "unsaved changes" diff. */
export function canonicalEntities(editable: Record<string, any>): string {
  const out: Record<string, any> = {};
  Object.keys(editable)
    .sort()
    .forEach((k) => {
      const e = { ...editable[k] };
      delete e.pods;
      out[k] = JSON.parse(JSON.stringify(e));
    });
  return JSON.stringify({ editable: out }, (key, value) =>
    value === null ? undefined : value);
}

/** Node load colour semantics: low = olive/green, mid = brass, high = orange/red. */
export function loadColor(pct: number): string {
  if (pct >= 0.85) return '#b23a12';
  if (pct >= 0.6) return '#ed821b';
  if (pct >= 0.3) return '#a67d48';
  return '#6b7a4a';
}

/** Highest of cpu / memory load for a node (0-1). */
export function nodeLoad(n: { load?: { cpu?: number; memory?: number } | null } | undefined): number {
  if (!n?.load) return 0;
  return Math.max(Number(n.load.cpu || 0), Number(n.load.memory || 0));
}
