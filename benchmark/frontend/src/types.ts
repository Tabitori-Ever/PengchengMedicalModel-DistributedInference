/* ==========================================================================
   测试与性能对比平台 · 站点后端 /api/* 的数据契约
   本文件只做「类型 + 宽松归一化」：后端真实返回的字段一律如实呈现，
   缺失即视为未知（而不是编造 0）。
   ========================================================================== */

/** 四类任务 */
export type Kind = 'diagnosis' | 'compute' | 'sync' | 'routine';

/** 调度策略（界面上只有两种：云边端协同 / 本地执行） */
export type ReqMode = 'collaborative' | 'local' | 'auto';

/** 实际执行的模式 */
export type UsedMode = 'collaborative' | 'local';

/** 发起实体（边 = 医院，端 = 诊所） */
export type Source =
  | 'hospital-a' | 'hospital-b'
  | 'clinic-1' | 'clinic-2' | 'clinic-3' | 'clinic-4';

export type DegradeReason =
  | 'scheduler_unreachable' | 'dependency_unhealthy' | 'forced' | string;

export type RunStatus =
  | 'pending' | 'running' | 'completed' | 'failed' | 'cancelled' | string;

/** 单次 attempt 记录 */
export interface Attempt {
  id?: number | string;
  run_id?: string;
  attempt_index?: number;
  kind: Kind | string;
  source: string;
  mode_requested?: ReqMode | string | null;
  mode_used?: UsedMode | string | null;
  degraded?: boolean;
  degrade_reason?: DegradeReason | null;
  orchestrator?: string | null;
  executor?: string | null;
  task_id?: string | null;
  /** 站点后端墙钟测量的端到端时延，唯一跨模式可比口径 */
  client_total_ms?: number | null;
  queue_wait_ms?: number | null;
  pipeline_total_ms?: number | null;
  compute_ms_total?: number | null;
  network_ms_total?: number | null;
  degrade_switch_ms?: number | null;
  /** 后端可能返回解析后的数组，也可能只给 JSON 字符串 */
  stages?: Stage[] | string | null;
  stages_json?: Stage[] | string | null;
  status?: RunStatus;
  error?: string | null;
  created_at?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  bpcr_expected?: number | null;
  bpcr_actual?: number | null;
  /** 诊断结果明细，部分后端会内联在 attempt 上 */
  prediction?: number | null;
  checksums?: string[] | null;
  result_detail?: ResultDetail | null;
  spec_key?: string | null;
  /** 兼容：部分实现把 metrics 嵌套返回 */
  metrics?: Metrics | null;
  /** 站点侧路由细节与指标来源说明 */
  detail?: Record<string, unknown> | null;
  metrics_source?: string | null;
  [k: string]: unknown;
}

/** stages[] 单元素 */
export interface Stage {
  name?: string;
  actor?: string;
  node?: string;
  ms?: number | null;
  detail?: string;
  compute_ms?: number | null;
  network_ms?: number | null;
}

/** metrics 块 */
export interface Metrics {
  queue_wait_ms?: number | null;
  pipeline_total_ms?: number | null;
  e2e_total_ms?: number | null;
  compute_ms_total?: number | null;
  network_ms_total?: number | null;
  degrade_switch_ms?: number | null;
}

/** 各类型 result_detail（字段随 kind 不同） */
export interface ResultDetail {
  prediction?: number | null;
  predictions?: unknown[];
  partitions_ok?: number | null;
  total_bytes?: number | null;
  aggregate_cpu_ms?: number | null;
  checksums?: string[];
  missing?: number | null;
  pulled?: number | null;
  bytes_pulled?: number | null;
  uploaded?: boolean | number | null;
  backup?: boolean | number | null;
  pull_ms?: number | null;
  upload_ms?: number | null;
  backup_ms?: number | null;
  jobs?: unknown[];
  succeeded?: number | null;
  [k: string]: unknown;
}

/** 实验的 spec（POST /api/runs 回显里嵌套为 spec 字段） */
export interface RunSpec {
  kind: Kind | string;
  source: string;
  params?: Record<string, unknown> | null;
  mode?: ReqMode | string;
  repeats?: number;
  concurrency?: number;
  label?: string | null;
  /** 固定套件实验才有；单次任务为 null */
  suite_id?: string | null;
  suite?: string | null;
}

/** 聚合块：站点后端返回 {n, mean, p50, p95, min, max} */
export interface Agg {
  n?: number | null;
  mean?: number | null;
  p50?: number | null;
  p95?: number | null;
  min?: number | null;
  max?: number | null;
}

export interface RunProgress {
  total?: number;
  pending?: number;
  running?: number;
  completed?: number;
  failed?: number;
  cancelled?: number;
}

/** 实验对象（GET /api/runs、GET /api/runs/{run_id}）
   注意：GET /api/runs 把 spec 字段平铺在 run 上（kind/source/mode/params…），
   POST /api/runs 与详情接口则可能嵌套在 spec / run 字段里——两者都支持。 */
export interface Run {
  run_id: string;
  spec?: RunSpec | null;
  /** 详情接口把 run 行放在这个字段里 */
  run?: Run | null;
  status?: RunStatus;
  label?: string | null;
  created_at?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  /** 平铺的 spec 字段 */
  kind?: Kind | string;
  source?: string;
  mode?: ReqMode | string;
  params?: Record<string, unknown> | null;
  params_json?: string | null;
  repeats?: number;
  concurrency?: number;
  /** 固定套件实验的套件 id（单次任务为 null） */
  suite_id?: string | null;
  progress?: RunProgress | null;
  /** attempt 数（列表接口给数字）或明细列表（详情接口给数组） */
  attempts?: Attempt[] | number | null;
  summary?: RunSummary | RunSummary[] | null;
  /** 或只给聚合摘要 */
  total?: number;
  completed?: number;
  failed?: number;
  running?: number;
  pending?: number;
  created?: string | null;
  [k: string]: unknown;
}

export interface RunSummary {
  mode?: string | null;
  mode_used?: string | null;
  n?: number;
  success?: number;
  fail?: number;
  success_rate?: number;
  mean?: number | null;
  p50?: number | null;
  p95?: number | null;
  max?: number | null;
  min?: number | null;
  degraded?: boolean;
  client_total_ms?: Agg | null;
  stage_means?: Record<string, number> | null;
  [k: string]: unknown;
}

export interface CreateRunResp {
  run_id: string;
  suite_id?: string | null;
  spec?: RunSpec;
  status?: RunStatus;
  attempts?: number;
}

/* ------------------------------------------------------- 固定套件（frozen suite） */

/** GET /api/suites 的一档负载 */
export interface SuiteLevel {
  label: string;
  repeats: number;
  params: Record<string, unknown>;
}

/** GET /api/suites 的一个固定套件 */
export interface SuiteInfo {
  suite_id: string;
  name: string;
  kind: Kind | string;
  default_source: string;
  default_concurrency: number;
  description?: string;
  /** 套件里固定的任务总数（档位 × 重复次数） */
  task_count: number;
  specs: SuiteLevel[];
}

export interface SuitesResp {
  count?: number;
  suites?: SuiteInfo[];
}

/** GET /api/compare/suite → overall / configs[].modes 里的单个模式聚合 */
export interface SuiteAgg {
  n?: number | null;
  mean?: number | null;
  p50?: number | null;
  p95?: number | null;
  min?: number | null;
  max?: number | null;
  success?: number | null;
  fail?: number | null;
}

/** GET /api/compare/suite → configs[] 的一行（一个档位） */
export interface SuiteConfigRow {
  spec_key?: string;
  label?: string;
  params?: Record<string, unknown>;
  modes?: Record<string, SuiteAgg | undefined>;
  [k: string]: unknown;
}

/** 协同相对本地的加速比；数据不足时为 null */
export interface SuiteSpeedup {
  collaborative_mean_ms?: number | null;
  local_mean_ms?: number | null;
  collaborative_faster_pct?: number | null;
  ratio?: number | null;
  [k: string]: unknown;
}

export interface SuiteCompareResp {
  suite_id?: string;
  suite_name?: string;
  kind?: string;
  source?: string | null;
  overall?: Record<string, SuiteAgg | undefined>;
  configs?: SuiteConfigRow[];
  speedup?: SuiteSpeedup | null;
  [k: string]: unknown;
}

/** 站点配置（GET/PUT /api/config） */
export interface SiteConfig {
  scheduler_url?: string;
  pod_urls?: Record<string, string>;
  auto_degrade?: boolean;
  probe_timeout_ms?: number;
  concurrency?: number;
  patients_path?: string;
  db_path?: string;
  entities?: string[];
  [k: string]: unknown;
}

export function healthAutoDegrade(h: SiteHealth | null | undefined): boolean {
  if (!h) return false;
  return h.auto_degrade ?? h.config?.auto_degrade ?? false;
}

export interface Patient {
  patient_id: string;
  /** 数据集自带真值（bpCR） */
  bpCR?: number;
  bpcr?: number;
  hospital?: string;
  [k: string]: unknown;
}

/** GET /api/patients 的信封（站点后端返回 {count, patients:[…]}） */
export interface PatientsResp {
  count?: number;
  patients?: Patient[];
  items?: Patient[];
  source?: string;
  path?: string;
  [k: string]: unknown;
}

/** 站点后端给出的正确性判定 */
export interface GroupCorrectness {
  status?: string;
  checked?: number;
  match?: number;
  mismatch?: number;
  skipped?: number;
  method?: string;
  [k: string]: unknown;
}

/** GET /api/health 中的单实体探测结果 */
export interface EntityHealth {
  reachable?: boolean;
  ok?: boolean;
  url?: string;
  version?: string;
  entity?: string;
  node?: string;
  status_code?: number;
  latency_ms?: number;
  checked_ms?: number;
  /** 站点后端用它表示该 Pod 是否已具备本地执行接口 */
  local_mode_available?: boolean;
  local_health?: (Record<string, unknown> & { capabilities?: Record<string, string>; node?: string }) | null;
  capabilities?: Record<string, string>;
  error?: string | null;
  [k: string]: unknown;
}

/** GET /api/health —— 站点自身信息（后端嵌套在 site 字段里） */
export interface SiteSelf {
  service?: string;
  version?: string;
  status?: string;
  time?: string;
  db_path?: string;
  [k: string]: unknown;
}

/** GET /api/health */
export interface SiteHealth {
  /** 站点自身（可能平铺，也可能嵌套在 site 里） */
  status?: string;
  ok?: boolean;
  version?: string;
  service?: string;
  db_path?: string;
  site?: SiteSelf | null;
  /** scheduler 可达性探测 */
  scheduler?: EntityHealth | boolean | null;
  scheduler_url?: string;
  scheduler_reachable?: boolean;
  /** 边缘 Pod 探测结果：entity → 详情 */
  pods?: Record<string, EntityHealth> | null;
  entities?: Record<string, EntityHealth> | null;
  /** 当前配置快照 */
  config?: SiteConfig | null;
  auto_degrade?: boolean;
  [k: string]: unknown;
}

/** /api/compare 中一个 mode 分组 */
export interface CompareGroup {
  mode?: string | null;
  mode_used?: string | null;
  kind?: string | null;
  source?: string | null;
  degraded?: boolean | null;
  n?: number;
  success?: number;
  fail?: number;
  success_rate?: number;
  mean?: number | Agg | null;
  p50?: number | null;
  p95?: number | null;
  max?: number | null;
  min?: number | null;
  /** 嵌套聚合块 */
  client_total_ms?: Agg | null;
  stages?: Record<string, number> | null;
  stage_means?: Record<string, number> | null;
  metrics_samples?: Record<string, number> | null;
  compute_ms_mean?: number | null;
  network_ms_mean?: number | null;
  queue_wait_ms_mean?: number | null;
  executor?: string | null;
  mode_requested?: string | null;
  bpcr_expected?: number | null;
  bpcr_actual?: number | null;
  bpcr_mean?: number | null;
  /** 站点后端给出的正确性判定（bpcr / checksum 比对结果） */
  correctness?: GroupCorrectness | null;
  [k: string]: unknown;
}

/** GET /api/compare */
export interface CompareResp {
  kind?: string;
  source?: string;
  /** 聚合分组数组 */
  groups?: CompareGroup[];
  /** 兼容按 mode 键入的对象形式 */
  modes?: Record<string, CompareGroup>;
  count?: number;
  filters?: Record<string, unknown>;
  [k: string]: unknown;
}

/** GET /api/compare/matrix 的一行（与 CompareGroup 同构，另带 kind/source） */
export type MatrixRow = CompareGroup;

export interface ApiMessage {
  message?: string;
  ok?: boolean;
  deleted?: number;
  [k: string]: unknown;
}

/* --------------------------------------------------------------- 常量表 */

export const KINDS: Kind[] = ['diagnosis', 'compute', 'sync', 'routine'];

export const KIND_ZH: Record<Kind, string> = {
  diagnosis: '诊断',
  compute: '计算',
  sync: '通信',
  routine: '日常',
};

export const HOSPITALS: Source[] = ['hospital-a', 'hospital-b'];
export const CLINICS: Source[] = ['clinic-1', 'clinic-2', 'clinic-3', 'clinic-4'];
export const SOURCES: Source[] = [...HOSPITALS, ...CLINICS];

/** 界面上的调度策略只有两种：云边端协同 / 本地执行 */
export const STRATEGIES: ReqMode[] = ['collaborative', 'local'];

export const REQ_MODES: ReqMode[] = STRATEGIES;

export const MODE_ZH: Record<string, string> = {
  collaborative: '云边端协同',
  local: '本地执行',
  auto: '自动',
  degraded: '降级',
};

export const DEGRADE_REASON_ZH: Record<string, string> = {
  scheduler_unreachable: '调度器不可达',
  dependency_unhealthy: '依赖不健康',
  forced: '外部指定',
};

export const STATUS_ZH: Record<string, string> = {
  pending: '排队中',
  running: '执行中',
  completed: '已完成',
  finished: '已完成',
  failed: '失败',
  error: '错误',
  cancelled: '已取消',
  queued: '排队中',
};

/** 每个模式在图表里的固定色（协同=黄铜、本地=橄榄） */
export const MODE_COLOR: Record<string, string> = {
  collaborative: '#946b3c',
  local: '#565c46',
  degraded: '#b9681a',
  auto: '#8e897b',
};

export function modeColor(mode?: string | null): string {
  return MODE_COLOR[(mode || '').toLowerCase()] || '#8e897b';
}

export function statusZh(s?: string | null): string {
  if (!s) return '—';
  return STATUS_ZH[s] || s;
}

/* ------------------------------------------------------------------ 测试方案 */
/* 对应《测试大纲》§5.4 / §5.5 的方案目录与方案运行（GET/POST /api/plans*）。   */

/** 方案里的一条任务类型配置：每台设备的任务数 + 该类任务的任务参数 */
export interface PlanKindCfg {
  kind: Kind;
  label: string;
  outline_name: string;
  per_device: number;
  params: Record<string, number | boolean | string | null>;
  param_note: string;
}

/** 大纲里的「微型台式电子计算机 B / C」↔ 平台的端侧发起方 */
export interface PlanDevice {
  id: string;
  name: string;
  source: string;
}

export type PlanCriterionType = 'gain_gt' | 'complete_gt' | 'complete_gte' | 'external';

export interface PlanCriterion {
  id: string;
  type: PlanCriterionType;
  value: number;
  unit: string;
  label: string;
  source: string;
}

/** 5.5 的测试仪 / 损伤仪：由外部仪表验证，平台只记录配置 */
export interface PlanAttachment {
  id: string;
  name: string;
  detail: string;
  verified_by: string;
  [k: string]: unknown;
}

export interface PlanInfo {
  plan_id: string;
  name: string;
  outline_ref: string;
  outline_title: string;
  test_object: string;
  purpose: string;
  objective: string;
  duration_min: number;
  generate_window_min: number;
  /** 失败自动重传额度（首次之外） */
  retries?: number;
  retry_backoff_s?: number;
  devices: PlanDevice[];
  kinds: PlanKindCfg[];
  strategies: ReqMode[];
  total_tasks: number;
  total_tasks_expanded: number;
  total_tasks_mismatch: boolean;
  kind_count: number;
  device_count: number;
  per_device_per_kind: number;
  preconditions: string[];
  steps: string[];
  criteria: PlanCriterion[];
  attachments: PlanAttachment[];
  notes?: string[];
  customized?: boolean;
  label?: string;
  [k: string]: unknown;
}

/** 一个阶段（一种调度策略跑一遍）的实测汇总 */
export interface PlanPhaseStats {
  phase: string;
  tasks_total: number;
  completed: number;
  failed: number;
  pending: number;
  completion_pct: number | null;
  total_wall_ms: number | null;
  latency: SuiteAgg;
  /** 失败重传记账 */
  retries?: number;
  retried_tasks?: number;
  retry_ms_total?: number;
  exec_ms?: number;
  processing_ms?: number;
  units?: unknown[];
}

export interface PlanKindMode {
  n: number;
  completed: number;
  total_client_ms: number | null;
  mean: number | null;
  p50?: number | null;
  p95?: number | null;
  min?: number | null;
  max?: number | null;
  /** 失败重传次数（任务保障） */
  retries?: number;
  retried_tasks?: number;
  /** 失败尝试累计浪费的墙钟 */
  retry_ms_total?: number;
  /** 含重传代价的处理总用时（判定口径）；该阶段无此类任务时为 null */
  processing_ms?: number | null;
}

/** 逐类对比：同一类任务在两种策略下的表现 */
export interface PlanCompareEntry {
  kind: Kind;
  modes: Record<string, PlanKindMode | undefined>;
  gain_pct: number | null;
}

export interface PlanCriterionResult extends PlanCriterion {
  verdict: 'pass' | 'fail' | 'pending';
  measured: number | null;
  measured_wall_gain_pct?: number | null;
  measured_retries?: number | null;
  note?: string;
}

export interface PlanComparison {
  per_kind: PlanCompareEntry[];
  totals: Record<string, PlanPhaseStats | undefined>;
  overall: {
    local_wall_ms: number | null;
    collaborative_wall_ms: number | null;
    wall_gain_pct: number | null;
    /** 只算成功那一次执行 */
    local_exec_ms: number | null;
    collaborative_exec_ms: number | null;
    /** 含重传代价（判定口径） */
    local_processing_ms: number | null;
    collaborative_processing_ms: number | null;
    processing_gain_pct: number | null;
    retries_local: number | null;
    retries_collaborative: number | null;
  };
  criteria: PlanCriterionResult[];
}

export interface PlanRunChild {
  run_id: string;
  phase: string;
  unit: string;
  kind: Kind;
  source: string;
  status: string;
  repeats: number;
}

export interface PlanRunByKind {
  kind: Kind;
  phase: string;
  unit_id: string;
  total: number;
  ok: number;
  fail: number;
  retries?: number;
  retry_ms_total?: number;
  latency: SuiteAgg;
}

/** 一次方案运行：配置快照 + 进度 + 各阶段汇总 + 逐类对比 + 判定 */
export interface PlanRun {
  plan_run_id: string;
  plan_id: string;
  plan_name: string;
  label?: string | null;
  status: string;
  current_phase: string | null;
  error?: string | null;
  created_at: string;
  started_at?: string | null;
  finished_at?: string | null;
  strategies: ReqMode[];
  config: PlanInfo;
  total_tasks_per_phase: number;
  phases: PlanPhaseStats[];
  progress: Record<string, RunProgress | undefined>;
  runs: PlanRunChild[];
  live: boolean;
  by_kind: PlanRunByKind[];
  comparison: PlanComparison;
}

export interface PlanRunBrief {
  plan_run_id: string;
  plan_id: string;
  plan_name: string;
  label?: string | null;
  status: string;
  current_phase: string | null;
  strategies: ReqMode[];
  created_at: string;
  finished_at?: string | null;
}

/** POST /api/plans/runs：可覆盖方案的任意配置项 */
export interface CreatePlanRunBody {
  plan_id: string;
  label?: string;
  overrides?: {
    devices?: { id?: string; name?: string; source: string }[];
    kinds?: Record<string, { per_device?: number; params?: Record<string, unknown> }>;
    generate_window_min?: number;
    duration_min?: number;
    strategies?: ReqMode[];
    name?: string;
    retries?: number;
  };
}
