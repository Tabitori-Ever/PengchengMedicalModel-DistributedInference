/* ===========================================================================
   数据集目录（真实 Database/ 文件夹优先，前端静态目录兜底）

   任务类型与全部参数都不再由界面选择：目录树的每个 JSON 文件自带
   kind / name / description / params，界面只负责「浏览目录 → 选中文件」，
   提交载荷直接由所选文件的 params 构造（见 buildSubmission）。

   数据来源有两套，按顺序尝试：

   1. 真实目录（后端）：GET /files/tree 列目录、GET /files/get?path=… 读文件；
      运维把文件放进 Database/ 后无需改前端即可选到。
   2. 静态兜底：/app/datasets/index.json + /app/datasets/<文件夹>/<文件名>.json，
      后端不可用时（例如 /files/tree 返回 500）仍可浏览与提交。

   - 目录树仅在被打开时读一次；单个文件内容只在被选中时读取。
   =========================================================================== */

import type { ComputeBody, DiagnosisBody, RoutineBody, SyncBody } from '../api';
import type { ModelKind } from '../types';
import type { IdentityId } from './identity';

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
  /** 该任务类型各参数的含义（静态目录提供，真实目录用内置说明补齐） */
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
export const TREE_PATH = 'files/tree';
export const GET_PATH = 'files/get';

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

/** GET /files/get?path=… —— 中文目录名与文件名逐段 URL 编码 */
export function fileGetUrl(path: string): string {
  const segs = String(path || '')
    .split('/')
    .filter((s) => s.length > 0)
    .map(encodeURIComponent);
  return `/${GET_PATH}?path=${segs.join('/')}`;
}

/** 真实目录的根显示名固定为中文（后端给的是容器目录名 Database） */
function rootLabel(v: unknown): string {
  const s = String(v ?? '').trim();
  if (!s || /^[A-Za-z0-9_./\\-]+$/.test(s)) return '数据集';
  return s;
}

function isKind(v: unknown): v is DatasetKind {
  return KINDS.includes(v as DatasetKind);
}

const KIND_BY_NAME: Record<string, DatasetKind> = {
  计算: 'compute',
  通信: 'sync',
  日常: 'routine',
  诊断: 'diagnosis',
};

/** 真实目录不携带参数说明，这里按任务类型内置一份（与静态目录一致） */
const PARAM_HELP: Record<string, ParamHelp[]> = {
  compute: [
    { k: '仪器数', v: '参与本次计算的监护仪器台数，仪器越多数据来源越多' },
    { k: '行数', v: '每台仪器采集的数据行数（采样点数），行数越多数据量越大' },
    { k: '强度', v: '每行数据的计算强度（单行运算量倍数），数值越大 CPU 负载越高' },
    { k: '分区数', v: '把计算拆成几份并行下发（份数≈参与协作的实体数，本集群最多 3 份）' },
  ],
  sync: [
    { k: '带宽', v: '传输带宽上限（Mbps），数值越低越省流量' },
    { k: '并发', v: '同时建立的传输连接数，越高越快但占用越多' },
    { k: '分块', v: '每个数据块的大小（KB），决定校验与重传的粒度' },
  ],
  routine: [
    { k: '任务数', v: '本次下发的一次性日常任务个数，每个任务落到一个边缘节点执行' },
    { k: '行数', v: '每个任务处理的数据行数（工作量基准）' },
    { k: '强度', v: '每个任务的计算强度（单行运算量倍数）' },
  ],
  diagnosis: [
    { k: '患者编号', v: '用于模型推理的患者标识（取自测试数据集）' },
    { k: '转诊', v: 'auto 表示由调度器挑选当前最空闲的医疗中心执行推理' },
  ],
};

function helpFor(folder: { id?: unknown; name?: unknown }): ParamHelp[] {
  const id = String(folder?.id || '');
  const name = String(folder?.name || '');
  return PARAM_HELP[id] || PARAM_HELP[KIND_BY_NAME[name]] || [];
}

/** 把任意来源的目录 JSON 归一化成 DatasetIndex（缺参数说明时用内置说明补齐） */
function normalizeIndex(data: any): DatasetIndex {
  const folders: DatasetFolder[] = (Array.isArray(data?.folders) ? data.folders : []).map((f: any) => {
    const params = (Array.isArray(f?.params) ? f.params : [])
      .map((x: any) => ({ k: String(x?.k || ''), v: String(x?.v || '') }))
      .filter((x: ParamHelp) => x.k && x.v);
    return {
      id: String(f?.id || ''),
      name: String(f?.name || f?.id || ''),
      files: (Array.isArray(f?.files) ? f.files : []).map((x: any) => ({
        path: String(x?.path || ''),
        name: String(x?.name || ''),
        description: String(x?.description || ''),
        bytes: Number.isFinite(Number(x?.bytes)) ? Number(x.bytes) : undefined,
      })),
      params: params.length ? params : helpFor(f || {}),
    };
  });
  return { root: rootLabel(data?.root), folders };
}

/** 静态兜底目录：/app/datasets/index.json */
async function fetchStaticIndex(): Promise<DatasetIndex> {
  const res = await fetch(datasetUrl(INDEX_PATH));
  if (!res.ok) throw new Error(`目录不可用（HTTP ${res.status}）`);
  return normalizeIndex(await res.json());
}

/** 真实目录：GET /files/tree（运维放进 Database/ 的文件都会出现） */
async function fetchRealTree(): Promise<DatasetIndex> {
  const res = await fetch(`/${TREE_PATH}`);
  if (!res.ok) throw new Error(`目录不可用（HTTP ${res.status}）`);
  return normalizeIndex(await res.json());
}

/** 读取目录树：真实目录优先，失败（后端不可用）回退到前端静态目录 */
export async function fetchIndex(): Promise<DatasetIndex> {
  try {
    const real = await fetchRealTree();
    if (real.folders.length) return real;
  } catch {
    /* 回退静态目录 */
  }
  return fetchStaticIndex();
}

/** 选中文件时才读取该文件内容，并校验它自带的 kind */
export async function fetchEntry(meta: DatasetFileMeta): Promise<DatasetEntry> {
  let data: any = null;

  // 真实目录优先
  try {
    const res = await fetch(fileGetUrl(meta.path));
    if (res.ok) data = await res.json();
  } catch {
    /* 回退静态文件 */
  }
  // 真实文件不可用 / 内容非法时回退到打包的静态文件
  if (!data || typeof data !== 'object' || Array.isArray(data) || !isKind(data.kind)) {
    const res = await fetch(datasetUrl(meta.path));
    if (!res.ok) throw new Error(`文件不可读（HTTP ${res.status}）`);
    data = await res.json();
  }

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
 * 所选文件 + 登录身份 → 提交载荷。
 * 字段名与后端请求体一致，数值全部来自文件内容，界面不做任何改写。
 * 执行模式不再由界面选择：调度固定走云边端协同（后端默认），因此载荷只含
 * 文件自带参数与 source，不再附加 mode / force_degraded。
 */
export function buildSubmission(entry: DatasetEntry, source: IdentityId): Submission {
  const p = entry.params as Record<string, unknown>;
  switch (entry.kind) {
    case 'compute':
      return {
        kind: 'compute',
        body: {
          source: source as ComputeBody['source'],
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
          source: source as SyncBody['source'],
          bandwidth_mbps: int(p.bandwidth_mbps),
          concurrency: int(p.concurrency),
          chunk_kb: int(p.chunk_kb),
        },
      };
    case 'routine':
      return {
        kind: 'routine',
        body: {
          source: source as RoutineBody['source'],
          jobs: int(p.jobs),
          rows: int(p.rows),
          intensity: int(p.intensity),
        },
      };
    default:
      return {
        kind: 'diagnosis',
        body: {
          source: source as DiagnosisBody['source'],
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
