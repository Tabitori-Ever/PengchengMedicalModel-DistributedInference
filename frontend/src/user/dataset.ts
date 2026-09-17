/* ===========================================================================
   数据集目录（前端静态目录 /app/datasets/）

   任务类型与全部参数都不再由界面选择：目录树的每个 JSON 文件自带
   kind / name / description / params，界面只负责「浏览目录 → 选中文件」，
   提交载荷直接由所选文件的 params 构造（见 buildSubmission）。

   - index.json         仅目录结构（文件夹 + 文件名/说明/字节数），进弹窗时读一次
   - <文件夹>/<文件名>.json  单个文件内容，仅在被选中时读取
   =========================================================================== */

import type { ComputeBody, DiagnosisBody, RoutineBody, SyncBody } from '../api';
import type { ModelKind, SourceId } from '../types';

export type DatasetKind = ModelKind;

export const KINDS: DatasetKind[] = ['compute', 'sync', 'routine', 'diagnosis'];

/** index.json 里的一个文件条目（不含内容） */
export interface DatasetFileMeta {
  path: string;
  name: string;
  description: string;
  bytes?: number;
}

/** 参数说明：k = 参数名（如「行数」），v = 含义 */
export interface ParamHelp {
  k: string;
  v: string;
}

export interface DatasetFolder {
  id: string;
  name: string;
  files: DatasetFileMeta[];
  /** 该任务类型各参数的含义（来自 index.json，用于弹窗里的「参数说明」） */
  params?: ParamHelp[];
}

export interface DatasetIndex {
  root: string;
  folders: DatasetFolder[];
}

/** 已选数据集文件：目录信息 + 文件内容（params 与提交字段同名） */
export interface DatasetEntry extends DatasetFileMeta {
  kind: DatasetKind;
  params: Record<string, unknown>;
}

/** 提交载荷：kind 决定接口，body 就是该接口需要的字段 */
export type Submission =
  | { kind: 'diagnosis'; body: DiagnosisBody }
  | { kind: 'compute'; body: ComputeBody }
  | { kind: 'sync'; body: SyncBody }
  | { kind: 'routine'; body: RoutineBody };

export const INDEX_PATH = 'index.json';

function base(): string {
  return import.meta.env?.BASE_URL || '/';
}

/** /app/datasets/<文件夹名>/<文件名>.json —— 中文目录名与文件名逐段 URL 编码 */
export function datasetUrl(path: string): string {
  const segs = String(path || '')
    .split('/')
    .filter((s) => s.length > 0)
    .map(encodeURIComponent);
  return `${base()}datasets/${segs.join('/')}`;
}

function isKind(v: unknown): v is DatasetKind {
  return KINDS.includes(v as DatasetKind);
}

/** 读取目录树（进弹窗时一次） */
export async function fetchIndex(): Promise<DatasetIndex> {
  const res = await fetch(datasetUrl(INDEX_PATH));
  if (!res.ok) throw new Error(`目录不可用（HTTP ${res.status}）`);
  const data: any = await res.json();
  const folders: DatasetFolder[] = Array.isArray(data?.folders) ? data.folders : [];
  return {
    root: String(data?.root || '数据集'),
    folders: folders.map((f) => ({
      id: String(f?.id || ''),
      name: String(f?.name || f?.id || ''),
      files: (Array.isArray(f?.files) ? f.files : []).map((x: any) => ({
        path: String(x?.path || ''),
        name: String(x?.name || ''),
        description: String(x?.description || ''),
        bytes: Number.isFinite(Number(x?.bytes)) ? Number(x.bytes) : undefined,
      })),
      params: (Array.isArray(f?.params) ? f.params : [])
        .map((x: any) => ({ k: String(x?.k || ''), v: String(x?.v || '') }))
        .filter((x: ParamHelp) => x.k && x.v),
    })),
  };
}

/** 选中文件时才读取该文件内容，并校验它自带的 kind */
export async function fetchEntry(meta: DatasetFileMeta): Promise<DatasetEntry> {
  const res = await fetch(datasetUrl(meta.path));
  if (!res.ok) throw new Error(`文件不可读（HTTP ${res.status}）`);
  const data: any = await res.json();
  if (!data || typeof data !== 'object' || Array.isArray(data)) throw new Error('文件内容无法解析');
  if (!isKind(data.kind)) throw new Error('文件缺少任务类型');
  const params = data.params && typeof data.params === 'object' ? data.params : {};
  return {
    path: meta.path,
    name: String(data.name || meta.name || ''),
    description: String(data.description || meta.description || ''),
    bytes: meta.bytes,
    kind: data.kind,
    params: params as Record<string, unknown>,
  };
}

const num = (v: unknown): number | undefined => {
  const n = Number(v);
  return Number.isFinite(n) ? n : undefined;
};

const int = (v: unknown): number | undefined => {
  const n = num(v);
  return n === undefined ? undefined : Math.round(n);
};

/**
 * 所选文件 + 任务位置 → 提交载荷。
 * 字段名与后端请求体一致，数值全部来自文件内容，界面不做任何改写。
 */
export function buildSubmission(entry: DatasetEntry, source: SourceId): Submission {
  const p = entry.params as Record<string, unknown>;
  switch (entry.kind) {
    case 'compute':
      return {
        kind: 'compute',
        body: {
          source,
          instruments: int(p.instruments),
          rows: int(p.rows),
          intensity: int(p.intensity),
          partition_count: int(p.partition_count),
        },
      };
    case 'sync':
      return {
        kind: 'sync',
        body: {
          source,
          bandwidth_mbps: int(p.bandwidth_mbps),
          concurrency: int(p.concurrency),
          chunk_kb: int(p.chunk_kb),
        },
      };
    case 'routine':
      return {
        kind: 'routine',
        body: {
          source,
          jobs: int(p.jobs),
          rows: int(p.rows),
          intensity: int(p.intensity),
        },
      };
    default:
      return {
        kind: 'diagnosis',
        body: {
          source,
          patient_id: p.patient_id === undefined || p.patient_id === null
            ? undefined
            : String(p.patient_id),
          target_hospital: p.target_hospital === undefined || p.target_hospital === null
            ? undefined
            : String(p.target_hospital) as DiagnosisBody['target_hospital'],
        },
      };
  }
}

/** 文件体积（mono 小字用） */
export function fmtSize(bytes?: number): string {
  const n = Number(bytes);
  if (!Number.isFinite(n) || n <= 0) return '—';
  if (n >= 1024 * 1024) return `${(n / 1024 / 1024).toFixed(2)} MB`;
  if (n >= 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${Math.round(n)} B`;
}
