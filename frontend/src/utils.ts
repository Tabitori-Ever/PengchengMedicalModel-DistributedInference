// Small shared helpers & lookups (v3.0: four kinds 诊断/计算/通信/日常)

import type { HospitalId, ModelKind, SourceId } from './types';

export const MODELS: ModelKind[] = ['diagnosis', 'compute', 'sync', 'routine'];

export const MODEL_LABEL: Record<string, string> = {
  diagnosis: '诊断',
  compute: '计算',
  sync: '通信',
  routine: '日常',
};

export const MODEL_COLOR: Record<string, string> = {
  diagnosis: '#0284c7',   // sky
  compute: '#7c3aed',     // violet
  sync: '#0e7490',        // teal/cyan dark
  routine: '#d97706',     // amber
};

// task sources: hospital = 边 (edge node), clinic = 端 (terminal pod)
export const SOURCES: SourceId[] = ['hospital-a', 'hospital-b', 'clinic-1', 'clinic-2'];
export const HOSPITALS: HospitalId[] = ['hospital-a', 'hospital-b'];

export const SOURCE_ROLE: Record<string, '边' | '端'> = {
  'hospital-a': '边',
  'hospital-b': '边',
  'clinic-1': '端',
  'clinic-2': '端',
};

export const ROLE_TEXT: Record<string, string> = {
  edge: '边',
  terminal: '端',
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
  running: '#2563eb',
  queued: '#a16207',
  finished: '#16a34a',
  completed: '#16a34a',
  failed: '#dc2626',
};

// Default image tags used by the cluster editor when adding new pods (v3.0)
export const IMG_CLINIC = '10.29.182.66:5000/k8s-repo/clinic:v3.0';
export const IMG_HOSPITAL = '10.29.182.66:5000/k8s-repo/hospital:v3.0';

export const KIND_LABEL: Record<string, string> = {
  hospital: '医院 Hospital',
  clinic: '诊所 Clinic',
};

export const AFFINITY_LABEL: Record<string, string> = {
  fixed: '固定节点',
  'role-edge': '边缘节点 (role=edge)',
  'spread-edge': '边缘分散 (反亲和)',
};

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

export function loadColor(pct: number): string {
  if (pct >= 0.85) return '#dc2626';
  if (pct >= 0.6) return '#d97706';
  if (pct >= 0.3) return '#eab308';
  return '#22c55e';
}
