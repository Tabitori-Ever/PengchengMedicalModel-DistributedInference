import axios from 'axios';
import type {
  ApplyResult, ClusterDefault, ClusterStatus, ClusterSummary, HealthMap,
  HospitalId, Patient, SourceId, TaskItem, TaskResult, TaskStats, ValidateResp,
} from './types';

// Same origin in production (scheduler serves /app + REST API under /).
const http = axios.create({ baseURL: '/', timeout: 20000 });

// 集群偶发抖动（kubelet/网络瞬时中断）时，GET 请求自动重试一次，避免页面直接超时。
http.interceptors.response.use(undefined, async (error: any) => {
  const cfg = error?.config || {};
  const retriable = (cfg.method || 'get').toLowerCase() === 'get';
  if (retriable && !cfg.__retried && (error?.code === 'ECONNABORTED' || !error?.response)) {
    cfg.__retried = true;
    await new Promise((r) => setTimeout(r, 800));
    return http.request(cfg);
  }
  return Promise.reject(error);
});

export interface SubmitResp {
  task_id: string;
  status: string;
}

/** Body accepted by POST /schedule/diagnosis */
export interface DiagnosisBody {
  source: SourceId;
  patient_id?: string;
  target_hospital?: HospitalId | 'auto' | null;
  priority?: number;
  deadline?: string;
}

/** Body accepted by POST /schedule/compute */
export interface ComputeBody {
  source: SourceId;
  instruments?: number;
  rows?: number;
  intensity?: number;
  partition_count?: number;
  priority?: number;
  deadline?: string;
}

/** Body accepted by POST /schedule/sync */
export interface SyncBody {
  source: SourceId;
  bandwidth_mbps?: number;
  concurrency?: number;
  chunk_kb?: number;
  priority?: number;
  deadline?: string;
}

/** Body accepted by POST /schedule/routine */
export interface RoutineBody {
  source: SourceId;
  jobs?: number;
  rows?: number;
  intensity?: number;
  priority?: number;
  deadline?: string;
}

export const api = {
  // ---- tasks ----
  listTasks: async (): Promise<TaskItem[]> =>
    (await http.get('/tasks')).data,

  taskStats: async (): Promise<TaskStats> =>
    (await http.get('/tasks/stats')).data,

  /** Live dashboards: trimmed newest tasks (small payload). */
  tasksRecent: async (limit = 20): Promise<TaskItem[]> =>
    (await http.get('/tasks/recent', { params: { limit } })).data,

  taskDetail: async (id: string): Promise<TaskItem> =>
    (await http.get(`/tasks/${id}`)).data,

  taskResult: async (id: string): Promise<TaskResult> =>
    (await http.get(`/task/result/${id}`)).data,

  deleteTask: async (id: string): Promise<{ message: string }> =>
    (await http.delete(`/tasks/${id}`)).data,

  clearTasks: async (): Promise<{ count: number }> =>
    (await http.delete('/tasks/clear')).data,

  // ---- submit: four v3 task kinds ----
  submitDiagnosis: async (body: DiagnosisBody): Promise<SubmitResp> =>
    (await http.post('/schedule/diagnosis', body)).data,

  submitCompute: async (body: ComputeBody): Promise<SubmitResp> =>
    (await http.post('/schedule/compute', body)).data,

  submitSync: async (body: SyncBody): Promise<SubmitResp> =>
    (await http.post('/schedule/sync', body)).data,

  submitRoutine: async (body: RoutineBody): Promise<SubmitResp> =>
    (await http.post('/schedule/routine', body)).data,

  // ---- test dataset ----
  patients: async (): Promise<Patient[]> =>
    (await http.get('/test/patients')).data,

  // ---- cluster ----
  clusterStatus: async (): Promise<ClusterStatus> =>
    (await http.get('/cluster/status')).data,

  /** Lightweight snapshot for dashboards (fast). */
  clusterSummary: async (): Promise<ClusterSummary> =>
    (await http.get('/cluster/summary')).data,

  clusterDefault: async (): Promise<ClusterDefault> =>
    (await http.get('/cluster/default')).data,

  clusterValidate: async (editable: Record<string, unknown>): Promise<ValidateResp> =>
    (await http.post('/cluster/validate', { editable })).data,

  clusterApply: async (editable: Record<string, unknown>): Promise<ApplyResult> =>
    (await http.put('/cluster/apply', { editable })).data,

  clusterReset: async (): Promise<ApplyResult> =>
    (await http.post('/cluster/reset')).data,

  clusterRestart: async (deployment: string): Promise<{ ok: boolean }> =>
    (await http.post('/cluster/restart', { deployment })).data,

  // ---- platform health ----
  /** GET /health — scheduler reachability probe for every platform service. */
  health: async (): Promise<HealthMap> =>
    (await http.get('/health')).data,
};

/** Send 'auto' target_hospital as null so the backend picks the least-loaded hospital. */
export function normalizeTarget(t?: HospitalId | 'auto' | null): HospitalId | undefined {
  if (!t || t === 'auto') return undefined;
  return t;
}
