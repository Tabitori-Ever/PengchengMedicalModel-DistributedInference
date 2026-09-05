import axios from 'axios';
import type {
  ApplyResult, ClusterDefault, ClusterStatus, Patient, TaskItem,
  TaskResult, TaskStats, ValidateResp,
} from './types';

// Same origin in production (scheduler serves /app + REST API).
const http = axios.create({ baseURL: '/', timeout: 30000 });

export const api = {
  // ---- tasks ----
  listTasks: async (): Promise<TaskItem[]> =>
    (await http.get('/tasks')).data,

  taskStats: async (): Promise<TaskStats> =>
    (await http.get('/tasks/stats')).data,

  taskDetail: async (id: string): Promise<TaskItem> =>
    (await http.get(`/tasks/${id}`)).data,

  taskResult: async (id: string): Promise<TaskResult> =>
    (await http.get(`/task/result/${id}`)).data,

  clearTasks: async (): Promise<{ count: number }> =>
    (await http.delete('/tasks/clear')).data,

  deleteTask: async (id: string): Promise<{ message: string }> =>
    (await http.delete(`/tasks/${id}`)).data,

  // ---- submit ----
  submitPreprocessed: async (body: {
    patient_id: string; hospital: string; priority: number; deadline: string;
  }): Promise<{ task_id: string }> =>
    (await http.post('/schedule/preprocessed', body)).data,

  submitAlexNet: async (body: {
    hospital: string; priority: number; deadline: string; image: unknown;
  }): Promise<{ task_id: string }> =>
    (await http.post('/schedule/task', {
      model: 'alexnet',
      hospital: body.hospital,
      priority: body.priority,
      deadline: body.deadline,
      input: { image: body.image },   // scheduler 期望 input.image
    })).data,

  submitClinic: async (body: {
    clinic: string; target_pod?: string; namespace?: string; priority: number;
  }): Promise<{ task_id: string }> =>
    (await http.post('/schedule/clinic', body)).data,

  // ---- test dataset ----
  patients: async (): Promise<Patient[]> =>
    (await http.get('/test/patients')).data,

  // ---- cluster ----
  clusterStatus: async (): Promise<ClusterStatus> =>
    (await http.get('/cluster/status')).data,

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
};
