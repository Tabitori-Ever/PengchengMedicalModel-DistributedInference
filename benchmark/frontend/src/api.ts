import axios from 'axios';
import type {
  ApiMessage, Attempt, CompareGroup, CompareResp, CreateRunResp, GroupCorrectness,
  Kind, MatrixRow, Patient, ReqMode, Run, RunProgress, RunSpec, SiteConfig, SiteHealth,
  Stage, SuiteAgg, SuiteCompareResp, SuiteConfigRow, SuiteInfo, SuiteSpeedup,
} from './types';

/** 便于页面从一个地方拿到契约类型 */
export type {
  Attempt, CompareGroup, CompareResp, CreateRunResp, GroupCorrectness, MatrixRow,
  Run, RunProgress, RunSpec, RunSummary, Stage, SuiteAgg, SuiteCompareResp,
  SuiteConfigRow, SuiteInfo, SuiteSpeedup,
} from './types';

/**
 * 站点后端同源提供 /api/*（§6.1），因此 baseURL = '/api'，无需 CORS。
 * 实验提交可能同步等待一次完整任务（诊断 ~数十秒），故单独放宽超时。
 */
const http = axios.create({ baseURL: '/api', timeout: 20000 });
const httpLong = axios.create({ baseURL: '/api', timeout: 180000 });

/**
 * GET 网络错误自动重试一次（沿用 frontend/src/api.ts 约定）：
 * 集群 / 探测偶发抖动时页面不应直接变成错误页。
 */
function retryOnce(instance: typeof http) {
  instance.interceptors.response.use(undefined, async (error: any) => {
    const cfg = error?.config || {};
    const retriable = (cfg.method || 'get').toLowerCase() === 'get';
    if (retriable && !cfg.__retried && (error?.code === 'ECONNABORTED' || !error?.response)) {
      cfg.__retried = true;
      await new Promise((r) => setTimeout(r, 800));
      return instance.request(cfg);
    }
    return Promise.reject(error);
  });
}
retryOnce(http);
retryOnce(httpLong);

/** 单次任务下发体 */
export interface CreateRunBody {
  kind: Kind;
  source: string;
  params: Record<string, unknown>;
  mode: ReqMode;
  repeats: number;
  concurrency: number;
  label?: string;
}

/** 固定套件下发体：一个套件 = 一次 run，包含套件里冻结的全部任务 */
export interface CreateSuiteRunBody {
  suite_id: string;
  mode: ReqMode;
  source: string;
  concurrency: number;
  label?: string;
}

export interface CompareQuery {
  kind?: string;
  source?: string;
  mode?: string;
}

export const api = {
  // ---- 站点自身 / 配置 ----
  /** GET /api/health —— 站点 + scheduler 可达性 + 各 Pod 可达性/能力 */
  health: async (): Promise<SiteHealth> => (await http.get('/health')).data,

  /** GET /api/config */
  config: async (): Promise<SiteConfig> => (await http.get('/config')).data,

  /** PUT /api/config */
  updateConfig: async (body: SiteConfig): Promise<SiteConfig> =>
    (await http.put('/config', body)).data,

  // ---- 固定套件 ----
  /** GET /api/suites —— 冻结的测试套件（含档位清单） */
  suites: async (): Promise<SuiteInfo[]> => {
    const data = (await http.get('/suites')).data;
    if (Array.isArray(data)) return data as SuiteInfo[];
    return (data?.suites ?? []) as SuiteInfo[];
  },

  /** POST /api/runs —— 固定套件：一次下发整套任务 */
  createSuiteRun: async (body: CreateSuiteRunBody): Promise<CreateRunResp> =>
    (await httpLong.post('/runs', body)).data,

  // ---- 数据集 ----
  /** GET /api/patients —— 本地打包数据集，含 bpCR 真值（站点后端返回 {count,patients}） */
  patients: async (): Promise<Patient[]> => {
    const data = (await http.get('/patients')).data;
    if (Array.isArray(data)) return data as Patient[];
    return (data?.patients ?? data?.items ?? []) as Patient[];
  },

  // ---- 实验 ----
  /** POST /api/runs */
  createRun: async (body: CreateRunBody): Promise<CreateRunResp> =>
    (await httpLong.post('/runs', body)).data,

  /** GET /api/runs?limit= */
  listRuns: async (limit = 50): Promise<Run[]> => {
    const data = (await http.get('/runs', { params: { limit } })).data;
    return Array.isArray(data) ? data : (data?.runs ?? []);
  },

  /** GET /api/runs/{run_id} —— 详情 + 每次 attempt 明细 */
  runDetail: async (runId: string): Promise<Run> => {
    const data = (await http.get(`/runs/${encodeURIComponent(runId)}`)).data;
    return normalizeRun(data);
  },

  /** POST /api/runs/{run_id}/cancel */
  cancelRun: async (runId: string): Promise<ApiMessage> =>
    (await http.post(`/runs/${encodeURIComponent(runId)}/cancel`)).data,

  /** DELETE /api/runs/{run_id} */
  deleteRun: async (runId: string): Promise<ApiMessage> =>
    (await http.delete(`/runs/${encodeURIComponent(runId)}`)).data,

  // ---- 结果 / 对比 ----
  /** GET /api/results?limit= —— 跨实验的最近单次结果明细 */
  results: async (limit = 100): Promise<Attempt[]> => {
    const data = (await http.get('/results', { params: { limit } })).data;
    if (Array.isArray(data)) return data as Attempt[];
    return (data?.results ?? data?.attempts ?? []) as Attempt[];
  },

  /** GET /api/compare?kind=&source=&mode= */
  compare: async (q: CompareQuery = {}): Promise<CompareResp> =>
    (await http.get('/compare', { params: q })).data,

  /** GET /api/compare/matrix —— 全量 (kind × source × mode) 聚合表 */
  compareMatrix: async (): Promise<MatrixRow[]> => {
    const data = (await http.get('/compare/matrix')).data;
    if (Array.isArray(data)) return data as MatrixRow[];
    return (data?.rows ?? data?.matrix ?? data?.groups ?? []) as MatrixRow[];
  },

  /** GET /api/compare/suite?suite_id=&source= —— 同一套件在两种调度策略下的分档位对比 */
  compareSuite: async (suiteId: string, source?: string): Promise<SuiteCompareResp> => {
    const params: Record<string, string> = { suite_id: suiteId };
    if (source) params.source = source;
    const data = (await http.get('/compare/suite', { params })).data;
    return (data ?? {}) as SuiteCompareResp;
  },

  /** CSV / JSON 导出直链（浏览器下载） */
  exportCsvUrl: '/api/export.csv' as string,
  exportJsonUrl: '/api/export.json' as string,
};

/** 导出直链（放在模块级，便于 <a href> 直接使用） */
export const EXPORT_CSV_URL = '/api/export.csv';
export const EXPORT_JSON_URL = '/api/export.json';

/* ------------------------------------------------------------ 归一化工具
   后端字段口径以 §3 为准；此处只做防御性读取，不做任何数值推算。       */

function num(v: unknown): number | null {
  if (typeof v === 'number' && Number.isFinite(v)) return v;
  if (typeof v === 'string' && v.trim() !== '' && Number.isFinite(Number(v))) return Number(v);
  return null;
}

function firstNum(...vals: unknown[]): number | null {
  for (const v of vals) {
    const n = num(v);
    if (n !== null) return n;
  }
  return null;
}

/** attempt 的常用指标（兼容扁平字段与嵌套 metrics，§3） */
export interface AttemptMetrics {
  client_total_ms: number | null;
  queue_wait_ms: number | null;
  pipeline_total_ms: number | null;
  compute_ms_total: number | null;
  network_ms_total: number | null;
  degrade_switch_ms: number | null;
}

export function attemptMetrics(a: Attempt | null | undefined): AttemptMetrics {
  const m = a?.metrics || undefined;
  return {
    client_total_ms: firstNum(a?.client_total_ms, m?.e2e_total_ms),
    queue_wait_ms: firstNum(a?.queue_wait_ms, m?.queue_wait_ms),
    pipeline_total_ms: firstNum(a?.pipeline_total_ms, m?.pipeline_total_ms),
    compute_ms_total: firstNum(a?.compute_ms_total, m?.compute_ms_total),
    network_ms_total: firstNum(a?.network_ms_total, m?.network_ms_total),
    degrade_switch_ms: firstNum(a?.degrade_switch_ms, m?.degrade_switch_ms),
  };
}

/** stages：后端可能给数组、JSON 字符串，或（列表接口）干脆不给 */export function attemptStages(a: Attempt | null | undefined): Stage[] {
  const raw = a?.stages ?? a?.stages_json;
  if (raw === null || raw === undefined) return [];
  if (Array.isArray(raw)) return raw;
  if (typeof raw === 'string') {
    try {
      const parsed = JSON.parse(raw);
      return Array.isArray(parsed) ? (parsed as Stage[]) : [];
    } catch {
      return [];
    }
  }
  return [];
}

/**
 * Run.attempts 在列表接口里是「attempt 数」(number)，在详情接口里是明细数组。
 * 任何渲染路径都必须经过这里判型，否则会出现在数字上 .map() 的崩溃。
 */
export function asAttemptList(v: Attempt[] | number | null | undefined): Attempt[] {
  return Array.isArray(v) ? v : [];
}

/** 解析 params：优先用后端已解析好的 params，否则解析 params_json */export function runParams(r: Run | null | undefined): Record<string, unknown> | null {
  if (!r) return null;
  const p = r.params ?? r.spec?.params;
  if (p && typeof p === 'object') return p as Record<string, unknown>;
  const raw = r.params_json;
  if (typeof raw === 'string' && raw.trim() !== '') {
    try {
      const parsed = JSON.parse(raw);
      if (parsed && typeof parsed === 'object') return parsed as Record<string, unknown>;
    } catch { /* 忽略非法 JSON */ }
  }
  return null;
}

/** 统一取 spec：GET /api/runs 平铺在 run 上，详情接口嵌在 spec / run 里 */
export function runSpec(r: Run | null | undefined): RunSpec | null {
  if (!r) return null;
  const inner = (r.spec || r.run?.spec || r.run) as Run | undefined;
  const merged: RunSpec = {
    kind: (r.spec?.kind ?? inner?.kind ?? r.kind ?? '') as string,
    source: (r.spec?.source ?? inner?.source ?? r.source ?? '') as string,
    mode: r.spec?.mode ?? inner?.mode ?? r.mode,
    repeats: r.spec?.repeats ?? inner?.repeats ?? r.repeats,
    concurrency: r.spec?.concurrency ?? inner?.concurrency ?? r.concurrency,
    label: r.spec?.label ?? inner?.label ?? r.label ?? null,
    suite_id: r.spec?.suite_id ?? inner?.suite_id ?? r.suite_id ?? null,
    suite: r.spec?.suite ?? null,
    params: runParams(r) || undefined,
  };
  return merged;
}

/** 宽松版：轮询中可能只有 {run_id,status}，此时返回空 spec 而不是 undefined */
export function runSpecLoose(r: Run | null | undefined): RunSpec {
  return runSpec(r) || { kind: '', source: '' };
}

/**
 * GET /api/runs/{id} 的返回是 { run, progress, summary, attempts }：
 * 把它摊平成 Run，attempts 保留为明细列表。
 */
function normalizeRun(data: any): Run {
  if (!data || typeof data !== 'object') return { run_id: '' };
  const inner = (data.run && typeof data.run === 'object') ? data.run : null;
  if (!inner) return data as Run;
  const attempts = Array.isArray(data.attempts) ? (data.attempts as Attempt[]) : [];
  return {
    ...inner,
    run_id: inner.run_id ?? data.run_id,
    progress: data.progress ?? inner.progress ?? null,
    summary: data.summary ?? inner.summary ?? null,
    attempts,
    // 保留 run 自身平铺的 spec 字段，便于 runSpec() 统一读取
    kind: inner.kind,
    source: inner.source,
    mode: inner.mode,
    params: inner.params,
    params_json: inner.params_json,
    repeats: inner.repeats,
    concurrency: inner.concurrency,
    label: inner.label,
    suite_id: inner.suite_id,
  } as Run;
}

/** attempt 的常用指标（兼容扁平字段与嵌套 metrics，§3） */

/** 尝试从结果里取 checksums（可能在 attempt 上或 result_detail 里） */
export function attemptChecksums(a: Attempt | null | undefined): string[] | null {
  const c = a?.checksums ?? (a?.result_detail?.checksums as string[] | undefined);
  return Array.isArray(c) && c.length > 0 ? c : null;
}

/** 尝试从结果里取 bpCR 概率（actual） */
export function attemptPrediction(a: Attempt | null | undefined): number | null {
  return firstNum(a?.bpcr_actual, a?.prediction, a?.result_detail?.prediction);
}

/** /api/compare 的 groups：兼容数组与按 mode 键入的对象两种返回 */
export function compareGroups(resp: CompareResp | null | undefined): CompareGroup[] {
  if (!resp) return [];
  if (Array.isArray(resp.groups)) return resp.groups;
  if (resp.modes && typeof resp.modes === 'object') {
    return Object.entries(resp.modes).map(([mode, g]) => {
      const base: CompareGroup = { ...(g || {}) };
      base.mode = mode;
      return base;
    });
  }
  return [];
}

/** 分阶段均值：兼容 stages / stage_means / 平铺的 *_mean 字段 */
export function groupStageMeans(g: CompareGroup): { compute: number | null; network: number | null; queue: number | null } {
  const stages = (g.stages || g.stage_means || {}) as Record<string, number>;
  const pick = (...keys: string[]): number | null => {
    for (const k of keys) {
      const hit = Object.keys(stages).find((s) => s.toLowerCase().includes(k));
      if (hit !== undefined) {
        const n = num(stages[hit]);
        if (n !== null) return n;
      }
    }
    return null;
  };
  return {
    compute: firstNum(g.compute_ms_mean, pick('compute', '计算')),
    network: firstNum(g.network_ms_mean, pick('network', '网络', '传输')),
    queue: firstNum(g.queue_wait_ms_mean, pick('queue', '排队', 'wait')),
  };
}

/** 归一化 mode 标签：把 (mode_used, degraded) 折成图表用的三个桶 */
export function modeBucket(a: Pick<Attempt, 'mode_used' | 'degraded'>): 'collaborative' | 'local' | 'degraded' {
  const used = (a.mode_used || '').toLowerCase();
  if (a.degraded || used === 'degraded') return 'degraded';
  if (used === 'local') return 'local';
  return 'collaborative';
}

/** 从 attempt 归一化出对比视图需要的行 */
export interface CompareRow {
  key: string;
  bucket: 'collaborative' | 'local' | 'degraded';
  modeRequested: string;
  modeUsed: string;
  degraded: boolean;
  n: number;
  success: number;
  mean: number | null;
  p50: number | null;
  p95: number | null;
  min: number | null;
  max: number | null;
  compute: number | null;
  network: number | null;
  queue: number | null;
  executor: string | null;
  stages: Stage[];
  bpcrExpected: number | null;
  bpcrActual: number | null;
  checksums: string[] | null;
  /** 站点后端直接给出的正确性判定（/api/compare 的 correctness 字段） */
  serverCorrectness?: GroupCorrectness | null;
}

export function percentile(sorted: number[], q: number): number | null {
  if (sorted.length === 0) return null;
  if (sorted.length === 1) return sorted[0];
  const pos = (sorted.length - 1) * q;
  const lo = Math.floor(pos);
  const hi = Math.ceil(pos);
  if (lo === hi) return sorted[lo];
  return sorted[lo] + (sorted[hi] - sorted[lo]) * (pos - lo);
}

export function mean(vals: number[]): number | null {
  if (vals.length === 0) return null;
  return vals.reduce((s, v) => s + v, 0) / vals.length;
}

export function isSuccess(a: Attempt): boolean {
  const s = (a.status || '').toLowerCase();
  return s === 'completed' || s === 'finished';
}

/** 是否仍可能有 attempt 在跑（决定是否继续轮询） */
export function attemptLive(a: Attempt): boolean {
  const s = (a.status || '').toLowerCase();
  return s === 'running' || s === 'pending' || s === 'queued';
}

export function runLive(r: Run): boolean {
  const s = (r.status || '').toLowerCase();
  return s === 'running' || s === 'pending' || s === 'queued';
}

/** 轮询停止条件：pending 与 running 都为 0（套件跑完就停，不再无谓请求） */
export function runProgressDone(p: RunProgress | null | undefined): boolean {
  if (!p) return false;
  if (typeof p.pending !== 'number' && typeof p.running !== 'number') return false;
  return (p.pending ?? 0) === 0 && (p.running ?? 0) === 0;
}

export interface ProgressCounts {
  total: number;
  finished: number;
  completed: number;
  failed: number;
  pending: number;
  running: number;
}

/** 优先用后端的 progress；没有 progress 时退回 attempt 明细统计 */
export function progressCounts(r: Run | null | undefined, attempts: Attempt[] = []): ProgressCounts {
  const p = r?.progress || null;
  const fromAttempts = {
    total: attempts.length,
    finished: attempts.filter((a) => !attemptLive(a)).length,
    completed: attempts.filter(isSuccess).length,
    failed: attempts.filter((a) => (a.status || '').toLowerCase() === 'failed').length,
    pending: attempts.filter((a) => ['pending', 'queued'].includes((a.status || '').toLowerCase())).length,
    running: attempts.filter((a) => (a.status || '').toLowerCase() === 'running').length,
  };
  if (!p) return fromAttempts;
  const total = typeof p.total === 'number' ? p.total : fromAttempts.total;
  const pending = typeof p.pending === 'number' ? p.pending : fromAttempts.pending;
  const running = typeof p.running === 'number' ? p.running : fromAttempts.running;
  const completed = typeof p.completed === 'number' ? p.completed : fromAttempts.completed;
  const failed = typeof p.failed === 'number' ? p.failed : fromAttempts.failed;
  const cancelled = typeof p.cancelled === 'number' ? p.cancelled : 0;
  const finished = Math.max(0, total - pending - running);
  return {
    total,
    finished: total ? finished : fromAttempts.finished,
    completed: completed || fromAttempts.completed,
    failed: failed || fromAttempts.failed,
    pending,
    running,
  };
}

/** 从 /api/compare/suite 的聚合块里取数值（字段缺失即 null，不补 0） */
export function aggNum(v: unknown): number | null {
  return num(v);
}

/**
 * 协同相对本地的差值百分比：正数 = 协同更快（来自同一个档位的两种模式）。
 * 缺任一侧数据返回 null（页面显示「—」，不做推算）。
 */
export function suiteDeltaPct(collab: number | null, local: number | null): number | null {
  if (collab === null || local === null || local === 0) return null;
  return ((local - collab) / local) * 100;
}

export function errText(e: any): string {
  const status = e?.response?.status;
  const detail = e?.response?.data?.detail ?? e?.response?.data?.error;
  if (status) {
    const d = typeof detail === 'string' ? detail : detail ? JSON.stringify(detail) : '';
    return `HTTP ${status}${d ? ` · ${d}` : ''}`;
  }
  if (e?.code === 'ECONNABORTED') return '请求超时';
  return e?.message || '未知错误';
}
