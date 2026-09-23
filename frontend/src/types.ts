// Shared API types for the v3.0 control plane
// v3 keeps four task kinds: diagnosis / compute / sync / routine.

export type ModelKind = 'diagnosis' | 'compute' | 'sync' | 'routine';

/**
 * v3.2 执行模式：
 *   collaborative 云边端协同（数据中心编排多角色协作，默认）
 *   local         本地执行（由发起 Pod 自行编排、就地优先执行）
 *   auto          自动（依赖/调度器不可用时自动降级为本地执行）
 */
export type ExecMode = 'collaborative' | 'local' | 'auto';

export type SourceId = 'hospital-a' | 'hospital-b' | 'clinic-1' | 'clinic-2';
export type HospitalId = 'hospital-a' | 'hospital-b';

export type TaskStatus = 'queued' | 'running' | 'finished' | 'completed' | 'failed';

export interface TaskItem {
  id: string;
  model: string;               // one of the four ModelKind values
  source?: string;             // hospital-a | hospital-b | clinic-1 | clinic-2
  priority?: number;
  status: TaskStatus | string;
  stage?: string;
  node?: string;
  progress?: number;
  start_time?: string;
  end_time?: string;
  duration_ms?: number;
  error?: string;
  result?: any;                // DiagnosisResult | ComputeResult | SyncResult | RoutineResult
}

export interface TaskStats {
  total: number;
  running: number;
  queue_length?: number;
  by_model: Record<string, number>;
  by_node?: Record<string, number>;
  by_stage?: Record<string, number>;
}

export interface Patient {
  patient_id: string;
  bpCR?: number;
  hospital?: string;
}

/** Live poll payload of GET /task/result/{id} */
export interface TaskResult {
  task_id: string;
  status: string;
  stage?: string;
  progress?: number;
  node?: string;
  result?: any;
  error?: string;
  duration_ms?: number;
}

// ---------------------------------------------------------------------------
// v3 finished-task result payloads (stored by the scheduler under task.result)
// ---------------------------------------------------------------------------
export interface InitiatorInfo {
  entity: string;              // hospital-a | hospital-b | clinic-1 | clinic-2
  role: string;                // edge | terminal
  node: string;
  priority?: number;
  deadline?: string;
  queued_at?: string;
}

export interface StageInfo {
  name: string;
  actor: string;
  node: string;
  ms: number;
  detail?: string;
}

export interface PredictionRow {
  patient_id?: string;
  bpCR_probability?: number;
  prediction?: number;
}

// -------- diagnosis --------
export interface DiagnosisResult {
  initiator: InitiatorInfo;
  forwarded_to?: 'hospital-a' | 'hospital-b' | null;
  stages: StageInfo[];
  result_detail: {
    predictions?: PredictionRow[];
    bpCR_probability?: number;
    worker_latency_ms?: number;
    server_latency_ms?: number;
  };
  metrics?: Record<string, number>;
}

// -------- compute --------
export interface PartitionRow {
  partition?: number;
  actor?: string;
  node?: string;
  rows?: number;
  bytes?: number;
  checksum?: string;
  cpu_ms?: number;
  ms?: number;
  failed?: boolean;
  error?: string;
  [k: string]: any;
}

export interface ComputeResult {
  initiator: InitiatorInfo;
  stages: StageInfo[];
  produced: { instruments: number; rows: number; samples: number };
  partitions: PartitionRow[];
  result_detail: {
    partitions_ok: number;
    total_bytes: number;
    aggregate_cpu_ms: number;
    checksums: string[];
  };
  metrics?: Record<string, number>;
}

// -------- sync --------
export interface SyncChunk {
  id: string;
  ok: boolean;
  peer: string;
  size?: number;
  hash?: string;
  ms?: number;
  error?: string;
}

export interface SyncBackup {
  backup_id?: string;
  ts?: string;
  total_items?: number;
  hash?: string;
  [k: string]: any;
}

export interface SyncResult {
  initiator: InitiatorInfo;
  stages: StageInfo[];
  result_detail: {
    db_version?: number | string;
    cloud_items?: number;
    missing?: number;
    pulled?: number;
    failed_chunks?: number;
    bytes_pulled?: number;
    pull_ms?: number;
    concurrency?: number;
    bandwidth_mbps?: number;
    peers?: string[];
    chunks?: SyncChunk[];
    uploaded?: number;
    cloud_total?: number;
    backup?: SyncBackup;
  };
  metrics?: Record<string, number>;
}

// -------- routine --------
export interface RoutineJob {
  job: string;
  node: string;
  state: string;               // succeeded | failed | timeout
  succeeded?: boolean;
  timeout?: boolean;
  deleted?: boolean;
  pod?: string;
  output?: { cpu_ms?: number; bytes?: number; checksum?: string; [k: string]: any };
}

export interface RoutineResult {
  initiator: InitiatorInfo;
  stages: StageInfo[];
  result_detail: {
    succeeded: number;
    jobs: RoutineJob[];
  };
  metrics?: Record<string, number>;
}

export type TaskPayload =
  | DiagnosisResult
  | ComputeResult
  | SyncResult
  | RoutineResult;

// ---------------- cluster model (unchanged) ----------------
export type AffinityKind = 'fixed' | 'role-edge' | 'spread-edge';
export type EntityKind = 'hospital' | 'clinic';

export interface Resources {
  requests: { cpu?: string; memory?: string };
  limits: { cpu?: string; memory?: string };
}

export interface EntityModel {
  kind: EntityKind;
  affinity: AffinityKind;
  node: string | null;
  replicas: number;
  image: string;
  resources: Resources;
  labels: Record<string, string>;
}

export interface PodInfo {
  name: string;
  node?: string | null;
  ready: boolean;
  phase?: string;
}

export interface EntityLive extends EntityModel {
  pods: PodInfo[];
}

export interface NodeInfo {
  name: string;
  role?: string | null;
  ready?: boolean;
  load?: { cpu?: number; memory?: number; gpu?: number } | null;
  capacity?: { cpu?: string; memory?: string };
}

export interface ReadonlyDeploy {
  name: string;
  replicas?: number;
  image?: string;
  pods: PodInfo[];
}

export interface ClusterStatus {
  version: string;
  ok: boolean;
  error?: string | null;
  nodes: NodeInfo[];
  entities: Record<string, EntityLive>;
  readonly: ReadonlyDeploy[];
}

export interface ClusterDefault {
  version: string;
  editable: Record<string, EntityModel>;
}

export interface ClusterSummary {
  version: string;
  ok: boolean;
  error?: string | null;
  nodes: NodeInfo[];
  editable: number;
  editable_pods: number;
  readonly_pods: number;
}

export interface ValidateResp {
  ok: boolean;
  errors: string[];
  warnings: string[];
}

export interface ApplyResult {
  ok: boolean;
  errors?: string[];
  warnings?: string[];
  applied?: { name: string; action: string; message: string }[];
}

// ---------------------------------------------------------------------------
// GET /health — scheduler health map.
// keys: scheduler | redis | hospital-a | hospital-b | clinic-1 | clinic-2 |
//       'datacenter(patient-db)' | medical_server
// values are heterogeneous by design ('ok' | object | 'unavailable' | any
// other string), so the frontend renders them defensively.
// ---------------------------------------------------------------------------
export type HealthValue =
  | string
  | number
  | boolean
  | Record<string, unknown>
  | null;

export type HealthMap = Record<string, HealthValue>;

/** Normalized status used by the live schematic dots. */
export type HealthState = 'ok' | 'degraded' | 'down' | 'unknown';
