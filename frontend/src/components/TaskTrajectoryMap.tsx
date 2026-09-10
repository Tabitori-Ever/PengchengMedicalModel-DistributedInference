import { useMemo, useState } from 'react';
import type { ModelKind, TaskItem } from '../types';
import {
  MODEL_COLOR, MODEL_EN, MODEL_LABEL, MODELS, SOURCE_ROLE, STATUS_LABEL, fmtMs,
} from '../utils';

/* ===========================================================================
   任务执行轨迹 · TASK TRAJECTORY MAP

   A three-tier schematic (云 CLOUD / 边 EDGE / 端 TERMINAL) that draws the
   canonical execution flow of the selected task kind as dim "ghost" lines and
   overlays the concrete execution path of one real task with animated brass /
   orange strokes.

   Everything is plain inline SVG + CSS (dash-offset keyframes, SMIL
   animateMotion for the travelling dot). No data is fetched here: tasks are
   handed down by ArchitecturePage, which polls api.listTasks() on its ticker.
   =========================================================================== */

// ---------------------------------------------------------------------------
// canvas + geometry tables
// ---------------------------------------------------------------------------
const VB_W = 1240;
const VB_H = 660;

type Tier = 'cloud' | 'edge' | 'terminal';
type Side = 'top' | 'right' | 'bottom' | 'left';

interface Box { x: number; y: number; w: number; h: number }
interface Pt { x: number; y: number }

const TIER_ORDER: Record<Tier, number> = { cloud: 0, edge: 1, terminal: 2 };

/** Canonical node boxes (centre + size) — cloud row, edge row, terminal row. */
const NODE_BOX: Record<string, Box> = {
  'scheduler': { x: 620, y: 80, w: 164, h: 56 },
  'medical-server': { x: 380, y: 80, w: 164, h: 56 },
  'dc-services': { x: 860, y: 80, w: 164, h: 56 },
  'redis': { x: 1080, y: 80, w: 150, h: 56 },
  'hospital-a': { x: 430, y: 300, w: 164, h: 56 },
  'hospital-b': { x: 810, y: 300, w: 164, h: 56 },
  'clinic-1': { x: 430, y: 520, w: 164, h: 56 },
  'clinic-2': { x: 810, y: 520, w: 164, h: 56 },
};

/**
 * Routine-only pseudo nodes: one dashed "Job pod" circle per edge node that
 * actually ran a一次性 Job (the scheduler only ever picks node1 / node2).
 * The circle slot is keyed by the Kubernetes node name, so several Jobs on the
 * same node collapse onto one pseudo node — and unknown nodes get no highlight.
 */
const JOB_SLOTS: Record<string, { x: number; y: number; r: number }> = {
  'node1': { x: 264, y: 300, r: 28 },
  'node2': { x: 960, y: 300, r: 28 },
};
const MAX_JOBS = Object.keys(JOB_SLOTS).length;
const JOB_PREFIX = 'job@';

/** Orthogonal "jog" rows — all of them live in box-free inter-tier gaps. */
const CE_ROWS = [130, 146, 162, 178, 200, 216, 232, 248, 264];
const ET_ROWS = [344, 360, 376, 392, 408, 440, 456, 472, 488];
const TERM_ROWS = [504, 520, 536];

/** Vertical lanes that stay clear of the hospital boxes (edge tier). */
const TERM_LANES: Record<string, { up: number[]; down: number[] }> = {
  'clinic-1': { up: [290, 300], down: [520, 540] },
  'clinic-2': { up: [990, 1010], down: [700, 712] },
};

/** Per-(node, side) port offsets, so parallel edges never share a port. */
const OFFSETS = [0, -18, 18, -36, 36, -54, 54, -70, 70, -14, 14, -26, 26, -44, 44, -8, 8];

const NODE_META: Record<string, { name: string; caption: string; tier: Tier }> = {
  'scheduler': { name: 'scheduler', caption: '任务调度 SCHEDULER', tier: 'cloud' },
  'medical-server': { name: 'medical-server', caption: '医学推理 MEDICAL SERVER', tier: 'cloud' },
  'dc-services': { name: 'dc-services', caption: '数据中心 / 患者库', tier: 'cloud' },
  'redis': { name: 'redis', caption: '任务状态 STATE STORE', tier: 'cloud' },
  'hospital-a': { name: 'hospital-a', caption: '医院 Pod · 边 EDGE', tier: 'edge' },
  'hospital-b': { name: 'hospital-b', caption: '医院 Pod · 边 EDGE', tier: 'edge' },
  'clinic-1': { name: 'clinic-1', caption: '诊所 Pod · 端 TERMINAL', tier: 'terminal' },
  'clinic-2': { name: 'clinic-2', caption: '诊所 Pod · 端 TERMINAL', tier: 'terminal' },
};

const TIER_LABEL: { no: string; text: string; y: number }[] = [
  { no: '01', text: '云 · CLOUD', y: 106 },
  { no: '02', text: '边 · EDGE', y: 316 },
  { no: '03', text: '端 · TERMINAL', y: 536 },
];

function isJob(id: string): boolean {
  return id.startsWith(JOB_PREFIX);
}

/** `job@node1` → `node1` (the Kubernetes node that ran the一次性 Job). */
function jobNodeOf(id: string): string {
  return id.slice(JOB_PREFIX.length);
}

function tierOf(id: string): Tier {
  if (isJob(id)) return 'edge';
  return NODE_META[id]?.tier ?? 'cloud';
}

function boxOf(id: string): Box | null {
  if (isJob(id)) {
    const s = JOB_SLOTS[jobNodeOf(id)];
    if (!s) return null;
    return { x: s.x, y: s.y, w: s.r * 2, h: s.r * 2 };
  }
  return NODE_BOX[id] || null;
}

function clamp(n: number, lo: number, hi: number): number {
  return Math.min(hi, Math.max(lo, n));
}

function portOf(b: Box, side: Side, off: number): Pt {
  if (side === 'top') return { x: b.x + clamp(off, -(b.w / 2 - 10), b.w / 2 - 10), y: b.y - b.h / 2 };
  if (side === 'bottom') return { x: b.x + clamp(off, -(b.w / 2 - 10), b.w / 2 - 10), y: b.y + b.h / 2 };
  if (side === 'left') return { x: b.x - b.w / 2, y: b.y + clamp(off, -(b.h / 2 - 8), b.h / 2 - 8) };
  return { x: b.x + b.w / 2, y: b.y + clamp(off, -(b.h / 2 - 8), b.h / 2 - 8) };
}

interface Plan {
  from: string;
  to: string;
  sideA: Side;
  sideB: Side;
  /** jog-row index (cloud↔edge / edge↔terminal) or cloud↔terminal row index */
  row: number;
  /** vertical lane for cloud↔terminal routes, -1 otherwise */
  lane: number;
  yRow: number;
}

function planEdge(from: string, to: string, rowIdx: number, lane: number): Plan | null {
  const A = boxOf(from);
  const B = boxOf(to);
  if (!A || !B) return null;
  const tA = tierOf(from);
  const tB = tierOf(to);
  const d = TIER_ORDER[tB] - TIER_ORDER[tA];

  if (d === 0) {
    // same tier → one horizontal run, side ports
    const r = B.x >= A.x;
    return { from, to, sideA: r ? 'right' : 'left', sideB: r ? 'left' : 'right', row: -1, lane: -1, yRow: -1 };
  }
  if (Math.abs(d) === 1) {
    // adjacent tiers → vertical port, jog row in the gap, vertical port
    const down = d > 0;
    return {
      from, to,
      sideA: down ? 'bottom' : 'top',
      sideB: down ? 'top' : 'bottom',
      row: rowIdx, lane: -1, yRow: -1,
    };
  }
  // two tiers apart → dive into a free lane, then approach the terminal edge
  const term = tA === 'terminal' ? from : to;
  const tb = boxOf(term) as Box;
  const side: Side = lane < tb.x ? 'left' : 'right';
  const down = d > 0;
  return {
    from, to,
    sideA: down ? 'bottom' : side,
    sideB: down ? side : 'bottom',
    row: rowIdx, lane, yRow: rowIdx % TERM_ROWS.length,
  };
}

function buildPath(p: Plan, offA: number, offB: number): Pt[] {
  const A = boxOf(p.from) as Box;
  const B = boxOf(p.to) as Box;
  const tA = tierOf(p.from);
  const tB = tierOf(p.to);
  const d = TIER_ORDER[tB] - TIER_ORDER[tA];

  if (p.lane < 0) {
    if (p.row < 0) {
      // same tier
      const y = A.y + clamp(offA, -(A.h / 2 - 8), A.h / 2 - 8);
      const ax = p.sideA === 'right' ? A.x + A.w / 2 : A.x - A.w / 2;
      const bx = p.sideB === 'right' ? B.x + B.w / 2 : B.x - B.w / 2;
      return [{ x: ax, y }, { x: bx, y }];
    }
    const cloudEdge = (tA === 'cloud' && tB === 'edge') || (tA === 'edge' && tB === 'cloud');
    const rows = cloudEdge ? CE_ROWS : ET_ROWS;
    const R = rows[p.row % rows.length];
    const a = portOf(A, p.sideA, offA);
    const b = portOf(B, p.sideB, offB);
    return [a, { x: a.x, y: R }, { x: b.x, y: R }, b];
  }

  const R1 = CE_ROWS[p.row % CE_ROWS.length];
  const Y = TERM_ROWS[p.yRow];
  if (d > 0) {
    const a = portOf(A, 'bottom', offA);
    const b = portOf(B, p.sideB, Y - B.y);
    return [a, { x: a.x, y: R1 }, { x: p.lane, y: R1 }, { x: p.lane, y: Y }, b];
  }
  const a = portOf(A, p.sideA, Y - A.y);
  const b = portOf(B, 'bottom', offB);
  return [a, { x: p.lane, y: a.y }, { x: p.lane, y: R1 }, { x: b.x, y: R1 }, b];
}

function toPathD(pts: Pt[]): string {
  if (!pts.length) return '';
  const head = `M ${pts[0].x} ${pts[0].y}`;
  return pts.slice(1).reduce((acc, p) => `${acc} L ${p.x} ${p.y}`, head);
}

/** Midpoint of the longest segment — keeps labels away from junctions. */
function polyMid(pts: Pt[]): { p: Pt; horizontal: boolean } {
  let best = 0;
  let bestLen = -1;
  for (let i = 1; i < pts.length; i += 1) {
    const L = Math.hypot(pts[i].x - pts[i - 1].x, pts[i].y - pts[i - 1].y);
    if (L > bestLen) {
      bestLen = L;
      best = i;
    }
  }
  if (best === 0) return { p: pts[0] || { x: 0, y: 0 }, horizontal: true };
  const a = pts[best - 1];
  const b = pts[best];
  return {
    p: { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 },
    horizontal: Math.abs(b.x - a.x) >= Math.abs(b.y - a.y),
  };
}

// ---------------------------------------------------------------------------
// task → semantic edge model
// ---------------------------------------------------------------------------
type Tone = 'ghost' | 'past' | 'current' | 'pending' | 'failed';

interface SemEdge {
  key: string;
  from: string;
  to: string;
  label: string;
  phase: string;
}

interface Ctx {
  source: string;
  hospital?: string | null;
  actors?: string[];
  peers?: string[];
  jobs?: JobCtx[];
}

interface JobCtx {
  id: string;
  /** short job name shown inside the pseudo node */
  name: string;
  node: string;
  state: string;
  /** how many Jobs of this task ran on that node */
  count?: number;
}

/** Entity name → schematic node id (unknown actors fall through as null). */
function nodeForEntity(raw: unknown): string | null {
  if (typeof raw !== 'string') return null;
  const v = raw.trim().toLowerCase();
  if (!v) return null;
  if (NODE_BOX[v] && !v.startsWith('scheduler') && v !== 'redis') return v;
  if (v === 'datacenter' || v === 'data-center' || v === 'dc' || v === 'dc-services'
    || v === 'patient-db' || v === 'cloud' || v === 'node3') return 'dc-services';
  if (v === 'medical-server' || v === 'medical_server' || v === 'medicalserver') return 'medical-server';
  if (v === 'scheduler') return 'scheduler';
  if (v === 'redis') return 'redis';
  const hospital = /^hospital[-_]?([ab])$/.exec(v);
  if (hospital) return `hospital-${hospital[1]}`;
  const clinic = /^clinic[-_]?([12])$/.exec(v);
  if (clinic) return `clinic-${clinic[1]}`;
  return null;
}

function isClinicNode(id: string | null | undefined): boolean {
  return !!id && id.startsWith('clinic-');
}

function semEdge(from: string, to: string, label: string, phase: string): SemEdge | null {
  if (!from || !to || from === to) return null;
  return { key: `${from}→${to}#${label}`, from, to, label, phase };
}

/** Canonical (kind-representative) semantic edges. */
function buildEdges(kind: ModelKind, ctx: Ctx): SemEdge[] {
  const out: (SemEdge | null)[] = [];
  const src = ctx.source;

  if (kind === 'diagnosis') {
    out.push(semEdge(src, 'scheduler', '提交', '提交'));
    const hospital = ctx.hospital || null;
    if (hospital && hospital !== src && isClinicNode(src)) {
      out.push(semEdge(src, hospital, '转诊', '转诊'));
    }
    if (hospital) out.push(semEdge(hospital, 'medical-server', 'worker', 'worker'));
    out.push(semEdge('medical-server', src, '结果', '结果'));
  } else if (kind === 'compute') {
    const actors = Array.from(new Set([...(ctx.actors || []), 'dc-services'])).filter(Boolean);
    const parts = actors.filter((a) => a !== src);
    out.push(semEdge(src, 'scheduler', '提交', '提交'));
    parts.forEach((a) => out.push(semEdge('scheduler', a, '分区', '分区')));
    actors.filter((a) => a !== 'dc-services').forEach((a) => out.push(semEdge(a, 'dc-services', '聚合', '聚合')));
    out.push(semEdge('dc-services', src, '结果', '结果'));
  } else if (kind === 'sync') {
    const peers = Array.from(new Set(ctx.peers || [])).filter((p) => p && p !== src);
    out.push(semEdge(src, 'scheduler', '提交', '提交'));
    peers.forEach((p) => out.push(semEdge(p, src, '拉取', '拉取')));
    out.push(semEdge(src, 'dc-services', '上传/备份', '上传'));
    out.push(semEdge('dc-services', src, '结果', '结果'));
  } else {
    const jobs = (ctx.jobs || []).slice(0, MAX_JOBS);
    out.push(semEdge(src, 'scheduler', '提交', '提交'));
    jobs.forEach((j) => {
      out.push(semEdge('scheduler', j.id, 'Job创建', 'Job创建'));
      out.push(semEdge(j.id, 'scheduler', '日志', '日志'));
      out.push(semEdge('scheduler', j.id, '删除', '删除'));
    });
  }
  return out.filter((e): e is SemEdge => !!e);
}

const CANON: Record<ModelKind, Ctx> = {
  diagnosis: { source: 'clinic-1', hospital: 'hospital-a' },
  compute: { source: 'clinic-1', actors: ['clinic-1', 'dc-services', 'hospital-b'] },
  sync: { source: 'clinic-1', peers: ['hospital-a', 'hospital-b', 'clinic-2', 'dc-services'] },
  routine: {
    source: 'clinic-1',
    jobs: [
      { id: `${JOB_PREFIX}node1`, name: 'job-0', node: 'node1', state: 'succeeded' },
      { id: `${JOB_PREFIX}node2`, name: 'job-1', node: 'node2', state: 'succeeded' },
    ],
  },
};

const PHASES: Record<ModelKind, string[]> = {
  diagnosis: ['提交', '转诊', 'worker', '结果'],
  compute: ['提交', '分区', '聚合', '结果'],
  sync: ['提交', '拉取', '上传', '结果'],
  routine: ['提交', 'Job创建', '日志', '删除'],
};

const STAGE_TEXT: Record<string, string> = {
  scheduler: '排队 / 调度',
  queued: '排队 / 调度',
  worker: '医院 worker 前端',
  server: '云端 server 融合',
  compute: '分区协同计算',
  sync: 'P2P 拉取 / 云端备份',
  routine: '一次性 Job 执行',
  finished: '已完成',
  completed: '已完成',
  failed: '失败',
};

/** Live context rebuilt from the real task payload (defensive everywhere). */
function ctxFromTask(kind: ModelKind, task: TaskItem): Ctx | null {
  const src = nodeForEntity(task.source);
  if (!src) return null;
  const res: any = task.result || null;
  const detail: any = res?.result_detail || null;

  if (kind === 'diagnosis') {
    const forwarded = nodeForEntity(res?.forwarded_to);
    const hospital = forwarded || (isClinicNode(src) ? null : src);
    return { source: src, hospital };
  }
  if (kind === 'compute') {
    const raw: any[] = Array.isArray(res?.partitions) ? res.partitions : [];
    const actors = raw
      .map((p) => nodeForEntity(p?.actor))
      .filter((a): a is string => !!a);
    return { source: src, actors: Array.from(new Set([src, 'dc-services', ...actors])) };
  }
  if (kind === 'sync') {
    const raw: any[] = Array.isArray(detail?.peers) ? detail.peers : [];
    const peers = raw.map((p) => nodeForEntity(p)).filter((p): p is string => !!p);
    return { source: src, peers: Array.from(new Set([...peers, 'dc-services'])) };
  }
  const rawJobs: any[] = Array.isArray(detail?.jobs) ? detail.jobs : [];
  // one pseudo node per distinct execution node; unknown nodes get no highlight
  const byNode = new Map<string, { id: string; name: string; node: string; state: string; count: number }>();
  rawJobs.forEach((j, i) => {
    const node = String(j?.node || '');
    if (!JOB_SLOTS[node]) return;
    const tail = String(j?.job || `job-${i}`).slice(-5) || `job-${i}`;
    const prev = byNode.get(node);
    if (prev) {
      prev.count += 1;
      if (String(j?.state) === 'failed') prev.state = 'failed';
    } else {
      byNode.set(node, { id: `${JOB_PREFIX}${node}`, name: tail, node, state: String(j?.state || 'unknown'), count: 1 });
    }
  });
  const jobs = Array.from(byNode.values()).slice(0, MAX_JOBS);
  if (!jobs.length) {
    // running / queued routine task — the scheduler has not reported jobs yet
    return {
      source: src,
      jobs: [
        { id: `${JOB_PREFIX}node1`, name: 'job-0', node: 'node1', state: 'pending', count: 1 } as any,
        { id: `${JOB_PREFIX}node2`, name: 'job-1', node: 'node2', state: 'pending', count: 1 } as any,
      ],
    };
  }
  return { source: src, jobs: jobs as any };
}

/** Which phase a running task currently sits in. */
function currentPhase(kind: ModelKind, task: TaskItem, ctx: Ctx | null): string {
  const stage = String(task.stage || '').toLowerCase();
  const status = String(task.status || '').toLowerCase();
  if (status === 'failed') return '';
  const done = status === 'finished' || status === 'completed' || stage === 'finished' || stage === 'completed';
  if (done) return PHASES[kind][PHASES[kind].length - 1];
  if (status === 'queued' || !stage || stage === 'scheduler') return '提交';
  if (kind === 'diagnosis') {
    if (stage === 'worker') return isClinicNode(ctx?.source) && ctx?.hospital ? '转诊' : '提交';
    if (stage === 'server') return 'worker';
    return '提交';
  }
  if (kind === 'compute') return stage === 'compute' ? '分区' : '提交';
  if (kind === 'sync') return stage === 'sync' ? '拉取' : '提交';
  return stage === 'routine' ? 'Job创建' : '提交';
}

/** Nodes that the running task is touching right now. */
function currentNodesOf(kind: ModelKind, task: TaskItem, ctx: Ctx | null): Set<string> {
  const out = new Set<string>();
  const stage = String(task.stage || '').toLowerCase();
  const status = String(task.status || '').toLowerCase();
  if (status !== 'running') return out;
  const stageNode = nodeForEntity(task.node);
  if (kind === 'diagnosis') {
    if (stage === 'worker' && stageNode) out.add(stageNode);
    if (stage === 'server') out.add('medical-server');
  } else if (kind === 'compute') {
    if (stageNode) out.add(stageNode);
    out.add('dc-services');
  } else if (kind === 'sync') {
    out.add('dc-services');
  } else if (kind === 'routine') {
    (ctx?.jobs || []).forEach((j) => out.add(j.id));
  }
  if (stage === 'scheduler') out.add('scheduler');
  return out;
}

// ---------------------------------------------------------------------------
// view model
// ---------------------------------------------------------------------------
interface DrawnEdge extends SemEdge {
  tone: Tone;
  d: string;
  mid: { p: Pt; horizontal: boolean };
  live: boolean;
}

interface ViewModel {
  edges: DrawnEdge[];
  nodeIds: string[];
  activeNodes: Set<string>;
  currentNodes: Set<string>;
  jobs: JobCtx[];
}

function buildView(kind: ModelKind, task: TaskItem | null): ViewModel {
  const ctx = task ? ctxFromTask(kind, task) : null;
  const ghost = buildEdges(kind, CANON[kind]);
  const live = ctx ? buildEdges(kind, ctx) : [];
  const liveKeys = new Set(live.map((e) => e.key));

  const phase = task ? currentPhase(kind, task, ctx) : '';
  const order = PHASES[kind];
  const curIdx = order.indexOf(phase);
  const failed = String(task?.status || '').toLowerCase() === 'failed';

  const toneOf = (e: SemEdge): Tone => {
    if (failed) return 'failed';
    if (!task || curIdx < 0) return 'current';
    const i = order.indexOf(e.phase);
    if (i < 0) return 'current';
    if (i === curIdx) return 'current';
    return i < curIdx ? 'past' : 'pending';
  };

  const all: { e: SemEdge; tone: Tone; live: boolean }[] = [
    ...ghost.filter((e) => !liveKeys.has(e.key)).map((e) => ({ e, tone: 'ghost' as Tone, live: false })),
    ...live.map((e) => ({ e, tone: toneOf(e), live: true })),
  ];

  // port-offset allocation so parallel edges never overlap on a shared port
  const counter: Record<string, number> = {};
  const take = (node: string, side: Side): number => {
    const k = `${node}|${side}`;
    const n = counter[k] || 0;
    counter[k] = n + 1;
    return OFFSETS[n % OFFSETS.length];
  };
  // cloud↔terminal edges get direction-specific lanes, so an upward edge can
  // never run along the same column as a downward one
  const laneCounter: Record<string, number> = {};

  const edges: DrawnEdge[] = [];
  const nodeIds = new Set<string>();
  all.forEach(({ e, tone, live: isLive }, idx) => {
    const tA = tierOf(e.from);
    const tB = tierOf(e.to);
    const d = TIER_ORDER[tB] - TIER_ORDER[tA];
    let lane = -1;
    if (Math.abs(d) === 2) {
      const term = tA === 'terminal' ? e.from : e.to;
      const lanes = TERM_LANES[term];
      if (lanes) {
        const up = d < 0;
        const k = `${term}|${up ? 'up' : 'down'}`;
        const n = laneCounter[k] || 0;
        laneCounter[k] = n + 1;
        const list = up ? lanes.up : lanes.down;
        lane = list[n % list.length];
      }
    }
    const p = planEdge(e.from, e.to, idx, lane);
    if (!p) return;
    if (p.lane < 0 && lane >= 0) return; // no free lane → skip rather than cross boxes
    let offA = 0;
    let offB = 0;
    if (d === 0) {
      offA = take(p.from, p.sideA);
      offB = offA;
    } else if (Math.abs(d) === 1) {
      offA = take(p.from, p.sideA);
      offB = take(p.to, p.sideB);
    } else if (d > 0) {
      offA = take(p.from, 'bottom');
    } else {
      offB = take(p.to, 'bottom');
    }
    const pts = buildPath(p, offA, offB);
    edges.push({ ...e, tone, d: toPathD(pts), mid: polyMid(pts), live: isLive });
    nodeIds.add(e.from);
    nodeIds.add(e.to);
  });

  const activeNodes = new Set<string>();
  live.forEach((e) => {
    activeNodes.add(e.from);
    activeNodes.add(e.to);
  });

  const jobCtx = kind === 'routine' ? (ctx || CANON.routine) : null;
  return {
    edges,
    nodeIds: Array.from(nodeIds).filter((id) => {
      if (!isJob(id)) return true;
      return !!jobCtx && (jobCtx.jobs || []).some((j) => j.id === id);
    }),
    activeNodes,
    currentNodes: task ? currentNodesOf(kind, task, ctx) : new Set<string>(),
    jobs: jobCtx?.jobs || [],
  };
}

// ---------------------------------------------------------------------------
// task helpers
// ---------------------------------------------------------------------------
function sortTasks(list: TaskItem[]): TaskItem[] {
  return [...list].sort((a, b) => String(b?.start_time || '').localeCompare(String(a?.start_time || '')));
}

function shortId(id: unknown): string {
  const s = String(id || '');
  return s.length > 14 ? `…${s.slice(-12)}` : s || '—';
}

function durationText(t: TaskItem): string {
  if (typeof t.duration_ms === 'number' && Number.isFinite(t.duration_ms)) return fmtMs(t.duration_ms);
  const status = String(t.status || '').toLowerCase();
  if (status === 'running' && t.start_time) {
    const t0 = Date.parse(t.start_time);
    if (Number.isFinite(t0)) return `运行 ${fmtMs(Math.max(0, Date.now() - t0))}`;
  }
  return '—';
}

function statusLabel(status: unknown): string {
  const s = String(status || '');
  return STATUS_LABEL[s] || s || '—';
}

function factsFor(kind: ModelKind, task: TaskItem): { k: string; v: string }[] {
  const res: any = task.result || null;
  const detail: any = res?.result_detail || null;
  const out: { k: string; v: string }[] = [];

  if (kind === 'diagnosis') {
    const forwarded = res?.forwarded_to;
    const src = nodeForEntity(task.source);
    out.push({ k: '转诊', v: forwarded ? `→ ${forwarded}` : isClinicNode(src) ? '未转诊' : '本院执行' });
    const p = detail?.bpCR_probability;
    out.push({ k: 'bpCR 概率', v: typeof p === 'number' ? p.toFixed(4) : '—' });
    out.push({ k: 'worker / server', v: `${fmtMs(detail?.worker_latency_ms)} / ${fmtMs(detail?.server_latency_ms)}` });
  } else if (kind === 'compute') {
    const parts: any[] = Array.isArray(res?.partitions) ? res.partitions : [];
    const actors = Array.from(new Set(
      parts.map((x) => nodeForEntity(x?.actor)).filter((x): x is string => !!x),
    ));
    out.push({ k: '分区 / 成功', v: `${parts.length} / ${detail?.partitions_ok ?? '—'}` });
    out.push({ k: '计算分区', v: actors.length ? actors.join(' · ') : '—' });
    out.push({ k: '总字节', v: typeof detail?.total_bytes === 'number' ? String(detail.total_bytes) : '—' });
  } else if (kind === 'sync') {
    const peers: any[] = Array.isArray(detail?.peers) ? detail.peers : [];
    const backup = detail?.backup;
    out.push({ k: '已拉取 / 缺失', v: `${detail?.pulled ?? '—'} / ${detail?.missing ?? '—'}` });
    out.push({ k: '云端备份', v: backup?.backup_id ? String(backup.backup_id) : '—' });
    out.push({ k: 'peer', v: peers.length ? peers.join(' · ') : '—' });
  } else {
    const jobs: any[] = Array.isArray(detail?.jobs) ? detail.jobs : [];
    const nodes = Array.from(new Set(jobs.map((j) => String(j?.node || '—'))));
    out.push({ k: 'Job 数 / 成功', v: `${jobs.length} / ${detail?.succeeded ?? '—'}` });
    out.push({ k: '执行节点', v: nodes.length ? nodes.join(' · ') : '—' });
    out.push({
      k: 'Job 名称',
      v: jobs.length ? jobs.map((j) => String(j?.job || '—').slice(-8)).join(' · ') : '—',
    });
  }
  return out;
}

// ---------------------------------------------------------------------------
// component
// ---------------------------------------------------------------------------
const TONES: Tone[] = ['ghost', 'past', 'current', 'pending', 'failed'];

const LEGEND: { tone: Tone; text: string }[] = [
  { tone: 'ghost', text: '标准路径 GHOST' },
  { tone: 'past', text: '已执行 DONE' },
  { tone: 'current', text: '当前阶段 CURRENT' },
  { tone: 'pending', text: '待执行 PENDING' },
  { tone: 'failed', text: '失败 FAILED' },
];

export default function TaskTrajectoryMap({
  tasks, initialKind = 'diagnosis',
}: {
  /** latest tasks from GET /tasks (ArchitecturePage polls it on its ticker) */
  tasks?: TaskItem[];
  /** kind selected on first paint (deep-link / test hook) */
  initialKind?: ModelKind;
}) {
  const [kind, setKind] = useState<ModelKind>(initialKind);
  const [selId, setSelId] = useState<string | null>(null);
  const [autoFollow, setAutoFollow] = useState(true);

  const kindTasks = useMemo(() => {
    const list = (Array.isArray(tasks) ? tasks : []).filter((t) => t && t.model === kind);
    return sortTasks(list).slice(0, 6);
  }, [tasks, kind]);

  const traced = useMemo(() => {
    if (!kindTasks.length) return null;
    if (autoFollow) return kindTasks[0];
    return kindTasks.find((t) => t.id === selId) || kindTasks[0];
  }, [kindTasks, autoFollow, selId]);

  const view = useMemo(() => buildView(kind, traced), [kind, traced]);

  const toneClass = (t: Tone) => `tm-edge ${t}`;
  const labelOf = (id: string): { name: string; caption: string } => {
    if (isJob(id)) {
      const node = jobNodeOf(id);
      const job = view.jobs.find((j) => j.id === id);
      const extra = job && (job.count || 1) > 1 ? ` ×${job.count}` : '';
      return { name: job?.name ? `…${job.name}` : 'Job pod', caption: `${node}${extra}` };
    }
    return NODE_META[id] || { name: id, caption: '' };
  };

  const liveDots = view.edges.filter((e) => e.live && (e.tone === 'current' || e.tone === 'past')).slice(0, 3);

  return (
    <section className="card tmap">
      <div className="card-headrow">
        <h3 className="card-title">任务执行轨迹 · TASK TRAJECTORY MAP</h3>
        <span className="muted xs mono">
          {traced
            ? `${MODEL_EN[kind]} · ${shortId(traced.id)} · ${statusLabel(traced.status)}`
            : `${MODEL_EN[kind]} · 标准路径`}
        </span>
      </div>

      <p className="eyebrow tmap-caption">
        云 / 边 / 端 三层拓扑上的实际执行连线 —— 选择任务类型与具体任务，粗亮动画路径即该任务真实走过的链路；
        细灰虚线为同类任务的标准执行路径
      </p>

      <div className="tmap-controls">
        <div className="seg tmap-tabs" role="tablist" aria-label="任务类型">
          {MODELS.map((m) => (
            <button
              key={m}
              type="button"
              role="tab"
              aria-selected={m === kind}
              className={`seg-btn ${m === kind ? 'on' : ''}`}
              onClick={() => { setKind(m); setSelId(null); }}
            >
              <i className="tm-tab-dot" style={{ background: MODEL_COLOR[m] }} />
              {MODEL_LABEL[m]}
            </button>
          ))}
        </div>

        <div className="tmap-picker">
          <div className="tmap-picker-head">
            <span className="eyebrow">任务选择 · TRACE TARGET</span>
            <button
              type="button"
              className={`mini ${autoFollow ? 'on' : ''}`}
              aria-pressed={autoFollow}
              onClick={() => setAutoFollow((v) => !v)}
            >
              自动跟随最新
            </button>
          </div>
          {kindTasks.length === 0 ? (
            <div className="tmap-empty xs muted">该类型暂无任务记录</div>
          ) : (
            <ul className="tmap-list">
              {kindTasks.map((t) => {
                const on = traced?.id === t.id;
                return (
                  <li key={t.id}>
                    <button
                      type="button"
                      className={`tmap-row ${on ? 'sel' : ''}`}
                      aria-pressed={on}
                      onClick={() => { setSelId(t.id); setAutoFollow(false); }}
                      title={`${t.id} · ${t.source || '—'} · ${t.model}`}
                    >
                      <span className={`tmap-row-dot s-${String(t.status || '')}`} />
                      <span className="tmap-row-id mono">{shortId(t.id)}</span>
                      <span className="tmap-row-st">{statusLabel(t.status)}</span>
                      <span className="tmap-row-src mono">{t.source || '—'}</span>
                      <span className="tmap-row-dur mono">{durationText(t)}</span>
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      </div>

      <div className="tmap-info">
        {traced ? (
          <>
            <InfoCell k="任务ID" v={shortId(traced.id)} mono title={traced.id} />
            <span className="tmi-cell">
              <i className="tmi-k">状态</i>
              <span className={`badge s-${String(traced.status || '')}`}>{statusLabel(traced.status)}</span>
            </span>
            <span className="tmi-cell">
              <i className="tmi-k">发起端</i>
              <b className="tmi-v">{traced.source || '—'}</b>
              <i className="tmi-sub">{SOURCE_ROLE[String(traced.source || '')] || '?'}</i>
            </span>
            <InfoCell k="阶段" v={STAGE_TEXT[String(traced.stage || '')] || String(traced.stage || '—')} />
            <InfoCell k="耗时" v={durationText(traced)} mono />
            {factsFor(kind, traced).map((f) => (
              <InfoCell key={f.k} k={f.k} v={f.v} mono />
            ))}
          </>
        ) : (
          <div className="tmap-none">
            <b>暂无任务</b>
            <span>· 显示标准执行路径</span>
            <i className="tmap-none-en mono">NO TASK · CANONICAL FLOW ONLY</i>
          </div>
        )}
      </div>

      <div className="tmap-canvas">
        <svg
          className="tmap-svg"
          viewBox={`0 0 ${VB_W} ${VB_H}`}
          width="100%"
          role="img"
          aria-label={`${MODEL_LABEL[kind]} 任务执行轨迹图`}
        >
          <title>{`${MODEL_LABEL[kind]} 任务执行轨迹`}</title>
          <defs>
            {TONES.map((t) => (
              <marker
                key={t}
                id={`tm-arrow-${t}`}
                viewBox="0 0 8 8"
                refX="7"
                refY="4"
                markerWidth="7.5"
                markerHeight="7.5"
                markerUnits="userSpaceOnUse"
                orient="auto"
              >
                <path className={`tm-arrow ${t}`} d="M0,0.7 L7.6,4 L0,7.3 Z" />
              </marker>
            ))}
          </defs>

          <g className="tm-guides">
            <line className="tm-rule" x1="72" y1="20" x2="72" y2="640" />
            <line className="tm-sep" x1="72" y1="190" x2={VB_W - 12} y2="190" />
            <line className="tm-sep" x1="72" y1="424" x2={VB_W - 12} y2="424" />
            {TIER_LABEL.map((t) => (
              <text
                key={t.no}
                className="tm-tier mono"
                x="44"
                y={t.y}
                textAnchor="middle"
                transform={`rotate(-90 44 ${t.y})`}
              >
                {`${t.no} ${t.text}`}
              </text>
            ))}
          </g>

          <g className="tm-edges ghost-layer">
            {view.edges.filter((e) => e.tone === 'ghost').map((e) => (
              <g key={`g-${e.key}`} className={toneClass(e.tone)}>
                <path className="tm-line" d={e.d} markerEnd={`url(#tm-arrow-${e.tone})`} />
                <text
                  className="tm-edge-label"
                  x={e.mid.horizontal ? e.mid.p.x : (e.mid.p.x > VB_W - 150 ? e.mid.p.x - 8 : e.mid.p.x + 8)}
                  y={e.mid.horizontal ? e.mid.p.y - 6 : e.mid.p.y + 3.5}
                  textAnchor={e.mid.horizontal ? 'middle' : (e.mid.p.x > VB_W - 150 ? 'end' : 'start')}
                >
                  {e.label}
                </text>
              </g>
            ))}
          </g>

          <g className="tm-edges live-layer">
            {['pending', 'past', 'current', 'failed'].flatMap((tone) => view.edges
              .filter((e) => e.live && e.tone === tone)
              .map((e) => (
                <g key={`l-${e.key}`} className={toneClass(e.tone)}>
                  <path className="tm-line" d={e.d} markerEnd={`url(#tm-arrow-${e.tone})`} />
                  <text
                    className="tm-edge-label"
                    x={e.mid.horizontal ? e.mid.p.x : (e.mid.p.x > VB_W - 150 ? e.mid.p.x - 8 : e.mid.p.x + 8)}
                    y={e.mid.horizontal ? e.mid.p.y - 6 : e.mid.p.y + 3.5}
                    textAnchor={e.mid.horizontal ? 'middle' : (e.mid.p.x > VB_W - 150 ? 'end' : 'start')}
                  >
                    {e.label}
                  </text>
                </g>
              )))}
          </g>

          <g className="tm-dots">
            {liveDots.map((e) => (
              <circle key={`d-${e.key}`} className={`tm-dot ${e.tone}`} r="3.4">
                <animateMotion dur="3.2s" repeatCount="indefinite" path={e.d} />
              </circle>
            ))}
          </g>

          <g className="tm-nodes">
            {view.nodeIds.map((id) => {
              const b = boxOf(id);
              if (!b) return null;
              const meta = labelOf(id);
              const active = view.activeNodes.has(id);
              const current = view.currentNodes.has(id);
              const job = isJob(id);
              return (
                <g key={id} className={`tm-node ${job ? 'job' : ''} ${active ? 'active' : ''} ${current ? 'current' : ''}`}>
                  <title>
                    {job
                      ? `${meta.name} · Job pod on ${meta.caption}`
                      : `${meta.name} · ${meta.caption}`}
                  </title>
                  {(active || current) && (job ? (
                    <circle className="tm-ring" cx={b.x} cy={b.y} r={b.w / 2 + 5} />
                  ) : (
                    <rect
                      className="tm-ring"
                      x={b.x - b.w / 2 - 5}
                      y={b.y - b.h / 2 - 5}
                      width={b.w + 10}
                      height={b.h + 10}
                      rx="4"
                    />
                  ))}
                  {job ? (
                    <>
                      <circle className="tm-box job-box" cx={b.x} cy={b.y} r={b.w / 2} />
                      <text className="tm-node-name" x={b.x} y={b.y - 1} textAnchor="middle">{meta.name}</text>
                      <text className="tm-node-cap" x={b.x} y={b.y + 12} textAnchor="middle">{meta.caption}</text>
                    </>
                  ) : (
                    <>
                      <rect
                        className="tm-box"
                        x={b.x - b.w / 2}
                        y={b.y - b.h / 2}
                        width={b.w}
                        height={b.h}
                        rx="3"
                      />
                      <rect
                        className={`tm-tier-flag ${tierOf(id)}`}
                        x={b.x - b.w / 2}
                        y={b.y - b.h / 2}
                        width="3"
                        height={b.h}
                      />
                      <text className="tm-node-name" x={b.x} y={b.y - 3} textAnchor="middle">{meta.name}</text>
                      <text className="tm-node-cap" x={b.x} y={b.y + 12} textAnchor="middle">{meta.caption}</text>
                    </>
                  )}
                </g>
              );
            })}
          </g>
        </svg>
      </div>

      <div className="tmap-legend">
        {LEGEND.map((l) => (
          <span className="tm-legend-item" key={l.tone}>
            <i className={`tm-legend-line ${l.tone}`} />
            {l.text}
          </span>
        ))}
        <span className="tm-legend-note muted xs">
          {!traced
            ? '尚无该类型任务，仅绘制标准路径'
            : view.edges.some((e) => e.live)
              ? `节点 ${view.activeNodes.size} 个 / 连线 ${view.edges.filter((e) => e.live).length} 条来自任务 ${shortId(traced.id)}`
              : '该任务发起端未知，未绘制实际连线'}
        </span>
      </div>
    </section>
  );
}

function InfoCell({
  k, v, mono, title,
}: { k: string; v: string; mono?: boolean; title?: string }) {
  return (
    <span className="tmi-cell" title={title || `${k} ${v}`}>
      <i className="tmi-k">{k}</i>
      <b className={`tmi-v ${mono ? 'mono' : ''}`}>{v}</b>
    </span>
  );
}
