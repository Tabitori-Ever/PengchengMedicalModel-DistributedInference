/** 数字与单位格式化：所有展示都必须带单位，且不隐藏样本量 n。 */

export function fmtMs(v: number | null | undefined, digits = 1): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return '—';
  return `${v.toFixed(digits)} ms`;
}

export function fmtMsShort(v: number | null | undefined): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return '—';
  if (Math.abs(v) >= 1000) return `${(v / 1000).toFixed(2)} s`;
  return `${v.toFixed(0)} ms`;
}

export function fmtNum(v: number | null | undefined, digits = 0): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return '—';
  return v.toFixed(digits);
}

export function fmtInt(v: number | null | undefined): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return '—';
  return String(Math.round(v));
}

export function fmtProb(v: number | null | undefined): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return '—';
  return v.toFixed(4);
}

export function fmtBytes(v: number | null | undefined): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return '—';
  const u = ['B', 'KB', 'MB', 'GB'];
  let n = v;
  let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i += 1; }
  return `${n.toFixed(i === 0 ? 0 : 1)} ${u[i]}`;
}

export function fmtTime(iso?: string | null): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const p = (x: number) => String(x).padStart(2, '0');
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

export function fmtClock(iso?: string | null): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const p = (x: number) => String(x).padStart(2, '0');
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

/** 样本量标注：n=… —— 每个聚合旁边都必须有 */
export function nLabel(n: number | null | undefined, success?: number | null): string {
  if (n === null || n === undefined) return 'n=0';
  if (success === null || success === undefined) return `n=${n}`;
  return `n=${n} · 成功 ${success}`;
}

/** 两个概率是否一致（诊断正确性校验的容差） */
export function probEq(a: number | null, b: number | null, tol = 1e-6): boolean | null {
  if (a === null || b === null) return null;
  return Math.abs(a - b) <= tol;
}

export function checksumsEqual(a: string[] | null, b: string[] | null): boolean | null {
  if (!a || !b) return null;
  if (a.length !== b.length) return false;
  return a.every((x, i) => x === b[i]);
}
