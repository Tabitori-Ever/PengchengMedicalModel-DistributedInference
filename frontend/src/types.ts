// Shared API types for the v2.0 control plane

export type ModelKind = 'medical' | 'alexnet' | 'clinic';

export interface TaskItem {
  id: string;
  model: string;
  source?: string;
  priority?: number;
  status: string;
  stage?: string;
  node?: string;
  progress?: number;
  start_time?: string;
  end_time?: string;
  duration_ms?: number;
  error?: string;
  result?: any;
  latency?: string;
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

export interface TaskResult {
  task_id: string;
  status: string;
  stage?: string;
  progress?: number;
  node?: string;
  result?: {
    bpCR_probability?: number;
    class_id?: number;
    class_name?: string;
    score?: number;
    predictions?: { patient_id?: string; bpCR_probability?: number; prediction?: number }[];
    usage_percent?: number;
    usage_bytes?: number;
    limit_bytes?: number;
    source?: string;
    pod?: string;
    namespace?: string;
    measured_at?: string;
    metrics?: Record<string, number>;
    [k: string]: any;
  } | null;
  error?: string;
  duration_ms?: number;
}

// ---------------- cluster model ----------------
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
