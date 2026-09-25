/* ===========================================================================
   用户调用平台 · 会话状态（纯函数，便于在无 DOM 环境下验证）

   登录身份即任务位置（source），登录后不再选择位置；用户只做两件事：
   在「顶部任务类型 → 对应文件夹」里点选一个数据集文件，再点执行。任务类型与
   全部参数都写在文件名/文件内容里，这里只保存：

     kind       顶部横条当前选中的任务类型（诊断 / 计算 / 通信 / 日常）
     source     日志身份 / 任务位置（由登录身份决定，提交时作为 source）
     index      数据集目录（真实 /files/tree，失败回退 /app/datasets/index.json）
     file       当前类型文件夹中已选中的文件（含文件内容 params）
     records    本次会话的提交记录，最新在前（同一列里原地更新）
     focusList  侧栏历史任务聚焦出来的记录（可同时显示多条）

   提交按所选文件自带的 kind 派发到对应接口（见 sendSubmission），
   载荷由 dataset.ts 的 buildSubmission 构造，界面不改写任何数值。
   =========================================================================== */

import { api } from '../api';
import type { ComputeBody, DiagnosisBody, RoutineBody, SyncBody } from '../api';
import { isTerminalStatus } from '../components/TaskResultView';
import type { ModelKind, TaskItem, TaskResult } from '../types';
import { MODEL_LABEL, MODELS } from '../utils';
import type { DatasetEntry, DatasetFileMeta, DatasetFolder, DatasetIndex, Submission } from './dataset';
import { fetchEntry } from './dataset';
import type { IdentityId } from './identity';
import { IDENTITIES } from './identity';

/** 默认任务位置：医院 1（登录前的占位值） */
export const DEFAULT_SOURCE: IdentityId = 'clinic-1';

/** 任务位置合法值校验（历史任务/文件里可能带来任意字符串） */
export function asSource(v?: string | null): IdentityId {
  const key = String(v ?? '');
  return (IDENTITIES as string[]).includes(key) ? (key as IdentityId) : DEFAULT_SOURCE;
}

/** 一条任务记录：本次会话提交的记录，或侧栏历史任务聚焦出来的记录 */
export interface UserRecord {
  id: string;
  kind: ModelKind;
  /** 数据集文件名（例：计算_规模中等_1）；历史任务无此信息时为空串 */
  fileName: string;
  source: IdentityId;
  taskId: string;
  live: TaskResult | null;
  task: TaskItem | null;
  /** 来自侧栏历史任务，而不是本次会话的提交 */
  history: boolean;
}

export interface UserState {
  kind: ModelKind;
  source: IdentityId;
  index: DatasetIndex | null;
  file: DatasetEntry | null;
  records: UserRecord[];
  /** 聚焦显示的历史任务记录：单条（单击）或多条（多选后「显示所选」） */
  focusList: UserRecord[];
}

let seq = 0;
export const nextId = (prefix = 'r'): string => `${prefix}${(seq += 1)}`;

export function initialState(): UserState {
  return {
    kind: MODELS[0],
    source: DEFAULT_SOURCE,
    index: null,
    file: null,
    records: [],
    focusList: [],
  };
}

/** 任务类型 → 数据集文件夹（index.json 里 id 与类型同名） */
export function folderFor(index: DatasetIndex | null, kind: ModelKind): DatasetFolder | null {
  if (!index) return null;
  return index.folders.find((f) => f.id === kind)
    || index.folders.find((f) => f.name === MODEL_LABEL[kind])
    || null;
}

/** 切换任务类型：提交卡立即改列该类型的文件夹，已选文件随文件夹失效 */
export function selectKind(s: UserState, kind: ModelKind): UserState {
  if (s.kind === kind) return s;
  return { ...s, kind, file: null };
}

export function selectSource(s: UserState, source: IdentityId): UserState {
  return { ...s, source };
}

export function withIndex(s: UserState, index: DatasetIndex): UserState {
  return { ...s, index };
}

/** 点选文件后的状态推进：只有类型一致才接受（文件自带的 kind 必须等于当前类型） */
export function pickFile(s: UserState, entry: DatasetEntry): UserState {
  return entry.kind === s.kind ? { ...s, file: entry } : s;
}

/** 读取目录里的一个条目：只读取这一个文件，校验它自带的 kind */
export async function loadEntry(
  meta: DatasetFileMeta,
  load: (m: DatasetFileMeta) => Promise<DatasetEntry> = fetchEntry,
): Promise<DatasetEntry> {
  const entry = await load(meta);
  if (!entry.kind) throw new Error('文件缺少任务类型');
  return entry;
}

export function addRecord(s: UserState, rec: UserRecord): UserState {
  return { ...s, records: [rec, ...s.records] };
}

export function patchRecord(s: UserState, id: string, patch: Partial<UserRecord>): UserState {
  return { ...s, records: s.records.map((r) => (r.id === id ? { ...r, ...patch } : r)) };
}

/** 历史任务 → 聚焦记录（不进入 records，因此不打断本次会话） */
export function recordForTask(task: TaskItem): UserRecord {
  return {
    id: `h${task.id}`,
    kind: (task.model as ModelKind) || 'diagnosis',
    fileName: '',
    source: asSource(task.source),
    taskId: task.id,
    live: null,
    task,
    history: true,
  };
}

/** 单击历史任务：只聚焦这一条 */
export function focusTask(s: UserState, task: TaskItem): UserState {
  return { ...s, focusList: [recordForTask(task)] };
}

/** 多选历史任务：「显示所选」把选中的任务一起放进主列 */
export function focusTasks(s: UserState, tasks: TaskItem[]): UserState {
  return { ...s, focusList: tasks.map(recordForTask) };
}

/** 点历史卡片：已选则取消，未选则**放在最上方**（新选的最靠前） */
export function toggleFocus(s: UserState, task: TaskItem): UserState {
  const id = String(task.id);
  if (s.focusList.some((r) => r.taskId === id)) return removeFocus(s, id);
  return { ...s, focusList: [recordForTask(task), ...s.focusList] };
}

export function patchFocus(s: UserState, taskId: string, patch: Partial<UserRecord>): UserState {
  if (!s.focusList.some((r) => r.taskId === taskId)) return s;
  return { ...s, focusList: s.focusList.map((r) => (r.taskId === taskId ? { ...r, ...patch } : r)) };
}

/** 关闭主列里某一条聚焦记录 */
export function removeFocus(s: UserState, taskId: string): UserState {
  return { ...s, focusList: s.focusList.filter((r) => r.taskId !== taskId) };
}

export function clearFocus(s: UserState): UserState {
  return s.focusList.length ? { ...s, focusList: [] } : s;
}

/** 新建任务：只重置提交卡与聚焦记录，本次会话的记录保留 */
export function newTask(s: UserState): UserState {
  return { ...initialState(), index: s.index, records: s.records };
}

export function recordStatus(r: UserRecord): string {
  return String(r.live?.status || r.task?.status || 'queued');
}

export function isPending(r: UserRecord): boolean {
  return !isTerminalStatus(recordStatus(r));
}

/** 需要轮询的记录 id（本次会话里尚未结束的提交） */
export function pendingIds(s: UserState): string[] {
  return s.records.filter(isPending).map((r) => r.id);
}

// ---------------------------------------------------------------------------
// 提交派发
// ---------------------------------------------------------------------------

/** 只用到四个提交接口，便于测试时替换成记录用的假客户端 */
export interface SubmitClient {
  submitDiagnosis(body: DiagnosisBody): Promise<{ task_id: string }>;
  submitCompute(body: ComputeBody): Promise<{ task_id: string }>;
  submitSync(body: SyncBody): Promise<{ task_id: string }>;
  submitRoutine(body: RoutineBody): Promise<{ task_id: string }>;
}

/** 按文件自带的类型派发到对应接口，返回 task_id */
export async function sendSubmission(sub: Submission, client: SubmitClient = api): Promise<string> {
  let resp: { task_id: string };
  if (sub.kind === 'diagnosis') resp = await client.submitDiagnosis(sub.body);
  else if (sub.kind === 'compute') resp = await client.submitCompute(sub.body);
  else if (sub.kind === 'sync') resp = await client.submitSync(sub.body);
  else resp = await client.submitRoutine(sub.body);
  return String(resp.task_id);
}

/** 提交成功后立刻落到记录里的轻量实时结果：让新记录当即显示进度条与状态 */
export function queuedResult(taskId: string, status?: string): TaskResult {
  return { task_id: taskId, status: String(status || 'queued'), progress: 0 };
}
