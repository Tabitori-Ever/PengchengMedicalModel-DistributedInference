// Small shared helpers & lookups

export const MODEL_LABEL: Record<string, string> = {
  medical: '医疗 bpCR',
  alexnet: 'AlexNet 分类',
  clinic: '内存监控',
};

export const MODEL_COLOR: Record<string, string> = {
  medical: '#7c3aed',   // violet
  alexnet: '#ea580c',   // orange
  clinic: '#0891b2',    // cyan
  hospital: '#0ea5e9',  // sky (kind)
};

// Default image tags for newly added clinic pods (v2.0.1 = metrics fix)
export const IMG_CLINIC = '10.29.182.66:5000/k8s-repo/clinic:v2.0.1';
export const IMG_HOSPITAL = '10.29.182.66:5000/k8s-repo/hospital:v2.0';

export const STATUS_LABEL: Record<string, string> = {
  running: '运行中',
  queued: '排队中',
  finished: '已完成',
  completed: '已完成',
  failed: '失败',
};

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
