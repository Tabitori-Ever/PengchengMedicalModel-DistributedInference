import { useEffect, useMemo, useState } from 'react';
import type { ModelKind, TaskItem } from '../types';
import {
  MODEL_COLOR, MODEL_EN, MODEL_LABEL, MODELS, SOURCE_ROLE, STATUS_LABEL, fmtMs,
} from '../utils';

/* ===========================================================================
   任务执行轨迹 · TASK TRAJECTORY MAP

   A three-tier schematic (云 CLOUD / 边 EDGE / 端 TERMINAL) that draws the
   canonical execution flow of the selected task kind as faint "unrelated"
   lines and then walks the concrete execution path of one real task **in time
   order**: an ordered step timeline (built from the task payload) drives a
   cursor, and every node / edge is painted with one of four sequential states

     已执行 past       – solid brass, animated dash flow
     当前   current    – orange, pulsing ring, travelling dot
     待执行 pending    – very faint grey dashed
     无关   unrelated  – faintest grey (not part of this task at all)

   The cursor either follows the live `stage` of a running task, stops on the
   failing step of a failed task, or auto-plays once through a finished task
   (~900 ms per step, ~1.2 s dwell on the last one).

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
  'scheduler': { name: 'scheduler', caption: '调度器 SCHEDULER', tier: 'cloud' },
  'medical-server': { name: 'medical-server', caption: '医学推理 SERVER', tier: 'cloud' },
  'dc-services': { name: 'dc-services', caption: '数据中心 / 患者库', tier: 'cloud' },
  'redis': { name: 'redis', caption: '状态库 REDIS', tier: 'cloud' },
  'hospital-a': { name: 'hospital-a', caption: '医院 Pod · EDGE', tier: 'edge' },
  'hospital-b': { name: 'hospital-b', caption: '医院 Pod · EDGE', tier: 'edge' },
  'clinic-1': { name: 'clinic-1', caption: '诊所 Pod · 端', tier: 'terminal' },
  'clinic-2': { name: 'clinic-2', caption: '诊所 Pod · 端', tier: 'terminal' },
};

/** inline 24×24 icon per node — no icon library, no external assets */
type IconKind = 'scheduler' | 'server' | 'database' | 'cache' | 'hospital' | 'clinic' | 'job';

const NODE_ICON: Record<string, IconKind> = {
  'scheduler': 'scheduler',
  'medical-server': 'server',
  'dc-services': 'database',
  'redis': 'cache',
  'hospital-a': 'hospital',
  'hospital-b': 'hospital',
  'clinic-1': 'clinic',
  'clinic-2': 'clinic',
};

function iconKindOf(id: string): IconKind {
  if (isJob(id)) return 'job';
  return NODE_ICON[id] || 'server';
}

/* icon-tile geometry: a hairline medallion plus a start-anchored text column,
   laid out inside the routing box so the verified edge geometry is untouched */
const MEDAL_R = 22;
const RING_R = 27;
const MEDAL_INSET = 30;
const TEXT_INSET = 62;

/** The cloud tier reads as one whole: the Data Center container panel. */
const DC_PANEL = { x: 280, y: 14, w: 894, h: 106 };
const DC_NODES = ['scheduler', 'medical-server', 'dc-services', 'redis'];
const DC_NODE_SET = new Set(DC_NODES);

/** Icon geometry authored in a 0..24 box; stroked/filled via the .tm-icon CSS. */
function iconShapes(kind: IconKind) {
  const r2 = (v: number) => Math.round(v * 100) / 100;
  const gearTeeth = [0, 45, 90, 135, 180, 225, 270, 315].map((deg) => {
    const t = (deg * Math.PI) / 180;
    return (
      <line
        key={deg}
        x1={r2(12 + 5.2 * Math.cos(t))}
        y1={r2(12 + 5.2 * Math.sin(t))}
        x2={r2(12 + 8.2 * Math.cos(t))}
        y2={r2(12 + 8.2 * Math.sin(t))}
      />
    );
  });
  switch (kind) {
    case 'scheduler': // 齿轮 / 调度
      return (
        <>
          <circle cx="12" cy="12" r="5.2" />
          <circle cx="12" cy="12" r="1.9" />
          {gearTeeth}
        </>
      );
    case 'server': // 服务器机架
      return (
        <>
          <rect x="3.6" y="4.4" width="16.8" height="6.2" rx="1.4" />
          <rect x="3.6" y="13.4" width="16.8" height="6.2" rx="1.4" />
          <circle className="tm-icon-dot" cx="7" cy="7.5" r="0.95" />
          <circle className="tm-icon-dot" cx="7" cy="16.5" r="0.95" />
        </>
      );
    case 'database': // 数据库圆柱
      return (
        <>
          <ellipse cx="12" cy="6.2" rx="7.2" ry="2.8" />
          <path d="M4.8 6.2 V17.4 Q4.8 20.2 12 20.2 Q19.2 20.2 19.2 17.4 V6.2" />
          <path d="M4.8 11.8 Q4.8 14.6 12 14.6 Q19.2 14.6 19.2 11.8" />
        </>
      );
    case 'cache': // 缓存堆叠
      return (
        <>
          <rect x="4.2" y="4.4" width="15.6" height="4.1" rx="1" />
          <rect x="4.2" y="9.9" width="15.6" height="4.1" rx="1" />
          <rect x="4.2" y="15.4" width="15.6" height="4.1" rx="1" />
          <circle className="tm-icon-dot" cx="7.1" cy="6.45" r="0.8" />
          <circle className="tm-icon-dot" cx="7.1" cy="11.95" r="0.8" />
          <circle className="tm-icon-dot" cx="7.1" cy="17.45" r="0.8" />
        </>
      );
    case 'hospital': // 医院十字建筑
      return (
        <>
          <path d="M4.8 20 V7.6 A1.6 1.6 0 0 1 6.4 6 H17.6 A1.6 1.6 0 0 1 19.2 7.6 V20" />
          <path d="M2.6 20 H21.4" />
          <path d="M12 9.4 V14.2" />
          <path d="M9.6 11.8 H14.4" />
        </>
      );
    case 'clinic': // 听诊器
      return (
        <>
          <path d="M8 4 V10.4 A4 4 0 0 0 16 10.4 V4" />
          <circle cx="8" cy="3.4" r="1.3" />
          <circle cx="16" cy="3.4" r="1.3" />
          <path d="M12 14.4 V15.6" />
          <circle cx="12" cy="18.2" r="2.4" />
        </>
      );
    default: // job: 立方体
      return (
        <>
          <path d="M12 3.4 L20.2 7.9 V16.9 L12 21.4 L3.8 16.9 V7.9 Z" />
          <path d="M12 12.4 L20.2 7.9" />
          <path d="M12 12.4 V21.4" />
          <path d="M12 12.4 L3.8 7.9" />
        </>
      );
  }
}

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
function polyMid(pts: Pt[]): LabelPlacement {
  let best = 0;
  let bestLen = -1;
  for (let i = 1; i < pts.length; i += 1) {
    const L = Math.hypot(pts[i].x - pts[i - 1].x, pts[i].y - pts[i - 1].y);
    if (L > bestLen) {
      bestLen = L;
      best = i;
    }
  }
  if (best === 0) return { x: pts[0]?.x || 0, y: pts[0]?.y || 0, anchor: 'middle' as const };
  const a = pts[best - 1];
  const b = pts[best];
  const x = (a.x + b.x) / 2;
  const y = (a.y + b.y) / 2;
  if (Math.abs(b.x - a.x) >= Math.abs(b.y - a.y)) return { x, y: y - 6, anchor: 'middle' as const };
  return x > VB_W - 150
    ? { x: x - 8, y: y + 3.5, anchor: 'end' as const }
    : { x: x + 8, y: y + 3.5, anchor: 'start' as const };
}

/**
 * Edge label placement. Prefers the midpoint of the longest segment, but walks
 * a few candidate anchors until the rendered text no longer sits on a node box
 * (the map owns every box, so the check is cheap and deterministic).
 */
interface LabelPlacement {
  x: number;
  y: number;
  anchor: 'start' | 'middle' | 'end';
}

interface Box2 { x1: number; y1: number; x2: number; y2: number }

const LABEL_SIZE = 9.5;

function textWidth(text: string): number {
  let w = 0;
  for (const ch of text) w += /[\u3000-\u9fff\uff00-\uffef]/.test(ch) ? 9.7 : 5.9;
  return w;
}

function labelBox(x: number, y: number, text: string, anchor: LabelPlacement['anchor']): Box2 {
  const w = textWidth(text);
  const left = anchor === 'middle' ? x - w / 2 : anchor === 'end' ? x - w : x;
  return { x1: left, y1: y - LABEL_SIZE, x2: left + w, y2: y + 2.5 };
}

const LABEL_AVOID: Box2[] = [
  ...Object.values(NODE_BOX).map((b) => ({
    x1: b.x - b.w / 2 - 1, y1: b.y - b.h / 2 - 1, x2: b.x + b.w / 2 + 1, y2: b.y + b.h / 2 + 1,
  })),
  ...Object.values(JOB_SLOTS).map((s) => ({
    x1: s.x - s.r - 1, y1: s.y - s.r - 1, x2: s.x + s.r + 1, y2: s.y + s.r + 1,
  })),
];

function hitsBox(b: Box2): boolean {
  return LABEL_AVOID.some((r) => b.x1 < r.x2 && r.x1 < b.x2 && b.y1 < r.y2 && r.y1 < b.y2);
}

function placeLabel(pts: Pt[], text: string): LabelPlacement {
  const segs: { i: number; len: number; horizontal: boolean }[] = [];
  for (let i = 1; i < pts.length; i += 1) {
    const dx = pts[i].x - pts[i - 1].x;
    const dy = pts[i].y - pts[i - 1].y;
    segs.push({ i, len: Math.hypot(dx, dy), horizontal: Math.abs(dx) >= Math.abs(dy) });
  }
  segs.sort((a, b) => b.len - a.len);

  for (const seg of segs.slice(0, 3)) {
    const a = pts[seg.i - 1];
    const b = pts[seg.i];
    for (const t of [0.5, 0.34, 0.66]) {
      const px = a.x + (b.x - a.x) * t;
      const py = a.y + (b.y - a.y) * t;
      const cands: LabelPlacement[] = seg.horizontal
        ? [
          { x: px, y: py - 6, anchor: 'middle' },
          { x: px, y: py + 11, anchor: 'middle' },
        ]
        : [
          { x: px + 8, y: py + 3.5, anchor: 'start' },
          { x: px - 8, y: py + 3.5, anchor: 'end' },
        ];
      for (const c of cands) {
        if (!hitsBox(labelBox(c.x, c.y, text, c.anchor))) return c;
      }
    }
  }
  return polyMid(pts);
}

// ---------------------------------------------------------------------------
// task → semantic edge model
// ---------------------------------------------------------------------------
/**
 * Sequential paint states. NOTE: the CSS class for 已执行 (passed) is `.past`
 * — it predates the timeline and is reused verbatim.
 */
type Tone = 'ghost' | 'past' | 'current' | 'pending' | 'unrelated' | 'failed';
type NodeState = Tone;

interface SemEdge {
  key: string;
  from: string;
  to: string;
  label: string;
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

function semEdge(from: string, to: string, label: string): SemEdge | null {
  if (!from || !to || from === to) return null;
  return { key: `${from}→${to}#${label}`, from, to, label };
}

/** Semantic edge key — shared by the edge builder and the step bindings. */
function edgeKey(from: string, to: string, label: string): string {
  return `${from}→${to}#${label}`;
}

/** Canonical (kind-representative) semantic edges. */
function buildEdges(kind: ModelKind, ctx: Ctx): SemEdge[] {
  const out: (SemEdge | null)[] = [];
  const src = ctx.source;

  if (kind === 'diagnosis') {
    out.push(semEdge(src, 'scheduler', '提交'));
    const hospital = ctx.hospital || null;
    if (hospital && hospital !== src && isClinicNode(src)) {
      out.push(semEdge(src, hospital, '转诊'));
    }
    if (hospital) out.push(semEdge(hospital, 'medical-server', 'worker'));
    out.push(semEdge('medical-server', src, '结果'));
  } else if (kind === 'compute') {
    const actors = Array.from(new Set([...(ctx.actors || []), 'dc-services'])).filter(Boolean);
    const parts = actors.filter((a) => a !== src);
    out.push(semEdge(src, 'scheduler', '提交'));
    parts.forEach((a) => out.push(semEdge('scheduler', a, '分区')));
    actors.filter((a) => a !== 'dc-services').forEach((a) => out.push(semEdge(a, 'dc-services', '聚合')));
    out.push(semEdge('dc-services', src, '结果'));
  } else if (kind === 'sync') {
    const peers = Array.from(new Set(ctx.peers || [])).filter((p) => p && p !== src);
    out.push(semEdge(src, 'scheduler', '提交'));
    peers.forEach((p) => out.push(semEdge(p, src, '拉取')));
    out.push(semEdge(src, 'dc-services', '上传/备份'));
    out.push(semEdge('dc-services', src, '结果'));
  } else {
    const jobs = (ctx.jobs || []).slice(0, MAX_JOBS);
    out.push(semEdge(src, 'scheduler', '提交'));
    jobs.forEach((j) => {
      out.push(semEdge('scheduler', j.id, 'Job创建'));
      out.push(semEdge(j.id, 'scheduler', '日志'));
      out.push(semEdge('scheduler', j.id, '删除'));
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

function num(v: unknown): number | undefined {
  if (v === null || v === undefined || v === '') return undefined;
  const n = Number(v);
  return Number.isFinite(n) ? n : undefined;
}

/** Live context rebuilt from the real task payload (defensive everywhere). */
function ctxFromTask(kind: ModelKind, task: TaskItem): Ctx | null {
  const src = nodeForEntity(task.source);
  if (!src) return null;
  const res: any = task.result || null;
  const detail: any = res?.result_detail || null;

  if (kind === 'diagnosis') {
    const forwarded = nodeForEntity(res?.forwarded_to);
    // a running clinic task has no forwarded_to yet — the scheduler already
    // parked it on the executing hospital in task.node
    const live = nodeForEntity(task.node);
    const hospital = forwarded
      || (!isClinicNode(src) ? src : (live && live.startsWith('hospital-') ? live : null));
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
  const byNode = new Map<string, JobCtx>();
  rawJobs.forEach((j, i) => {
    const node = String(j?.node || '');
    if (!JOB_SLOTS[node]) return;
    const tail = String(j?.job || `job-${i}`).slice(-5) || `job-${i}`;
    const prev = byNode.get(node);
    if (prev) {
      prev.count = (prev.count || 1) + 1;
      if (String(j?.state) === 'failed') prev.state = 'failed';
    } else {
      byNode.set(node, {
        id: `${JOB_PREFIX}${node}`, name: tail, node, state: String(j?.state || 'unknown'), count: 1,
      });
    }
  });
  const jobs = Array.from(byNode.values()).slice(0, MAX_JOBS);
  if (!jobs.length) {
    // running / queued routine task — the scheduler has not reported jobs yet
    return {
      source: src,
      jobs: [
        { id: `${JOB_PREFIX}node1`, name: 'job-0', node: 'node1', state: 'pending', count: 1 },
        { id: `${JOB_PREFIX}node2`, name: 'job-1', node: 'node2', state: 'pending', count: 1 },
      ],
    };
  }
  return { source: src, jobs };
}

// ---------------------------------------------------------------------------
// ordered step timeline (the execution order the cursor walks through)
// ---------------------------------------------------------------------------
interface Step {
  /** chip text, e.g. 提交 / 分区 2 */
  label: string;
  /** chip tooltip / secondary text */
  detail?: string;
  /** measured duration when the payload exposes one */
  durationMs?: number;
  /** nodes that take part in this step */
  nodes: string[];
  /** semantic edges that carry this step */
  edges: string[];
  /** stable key used to locate the failing step */
  ref: string;
}

function metricsMs(task: TaskItem | null, key: string): number | undefined {
  const m: any = (task?.result as any)?.metrics;
  return m ? num(m[key]) : undefined;
}

function stagesOf(task: TaskItem | null): any[] {
  const s: any = (task?.result as any)?.stages;
  return Array.isArray(s) ? s : [];
}

function detailOf(task: TaskItem | null): any {
  return (task?.result as any)?.result_detail || {};
}

/**
 * diagnosis — ①提交 ②调度/转诊 ③worker ④server ⑤结果回传
 * ordering: fixed pipeline; durations from stages[].ms / result_detail
 */
function stepsDiagnosis(task: TaskItem | null, ctx: Ctx): Step[] {
  const detail = detailOf(task);
  const stages = stagesOf(task);
  const stageMs = (name: string) => {
    const hit = stages.find((s) => String(s?.name) === name);
    return num(hit?.ms);
  };
  const src = ctx.source;
  const hospital = ctx.hospital || null;
  const forwarded = !!(hospital && hospital !== src && isClinicNode(src));
  const steps: Step[] = [];

  steps.push({
    label: '提交',
    detail: `${src} → scheduler`,
    nodes: [src, 'scheduler'],
    edges: [edgeKey(src, 'scheduler', '提交')],
    durationMs: metricsMs(task, 'queue_wait_ms'),
    ref: 'submit',
  });
  steps.push({
    label: forwarded ? '转诊' : '调度选点',
    detail: forwarded ? `${src} → ${hospital}` : 'scheduler 选医院 / 排优先级',
    nodes: forwarded ? [src, hospital as string] : ['scheduler'],
    edges: forwarded ? [edgeKey(src, hospital as string, '转诊')] : [],
    ref: 'dispatch',
  });
  if (hospital) {
    steps.push({
      label: 'worker 前端',
      detail: `${hospital} 医院 Pod 提特征`,
      nodes: [hospital],
      edges: [edgeKey(hospital, 'medical-server', 'worker')],
      durationMs: stageMs('worker') ?? num(detail.worker_latency_ms),
      ref: 'worker',
    });
  }
  steps.push({
    label: 'server 融合',
    detail: 'medical-server 双塔融合推理',
    nodes: ['medical-server'],
    edges: [],
    durationMs: stageMs('server') ?? num(detail.server_latency_ms),
    ref: 'server',
  });
  steps.push({
    label: '结果回传',
    detail: `medical-server → ${src}`,
    nodes: ['medical-server', src],
    edges: [edgeKey('medical-server', src, '结果')],
    ref: 'result',
  });
  return steps;
}

/**
 * compute — ①提交 ②调度选点 ③产数 ④各分区协同计算 ⑤数据聚合 ⑥结果回传
 * ordering: partitions sorted by result.partitions[].partition
 */
function stepsCompute(task: TaskItem | null, ctx: Ctx): Step[] {
  const res: any = task?.result || null;
  const detail = detailOf(task);
  const src = ctx.source;
  const rawParts: any[] = Array.isArray(res?.partitions) ? res.partitions : [];
  const parts = rawParts.slice().sort((a, b) => (num(a?.partition) ?? 0) - (num(b?.partition) ?? 0));
  const produced = res?.produced || null;
  // every actor that executes a partition (the source only when it is one) …
  const partTargets = Array.from(new Set([...(ctx.actors || []), 'dc-services'])).filter((a) => a !== src);
  // … and every actor whose partition must be aggregated in the data center
  const aggSources = Array.from(new Set([src, ...(ctx.actors || [])])).filter((a) => a !== 'dc-services');
  const steps: Step[] = [];

  steps.push({
    label: '提交',
    detail: `${src} → scheduler`,
    nodes: [src, 'scheduler'],
    edges: [edgeKey(src, 'scheduler', '提交')],
    durationMs: metricsMs(task, 'queue_wait_ms'),
    ref: 'submit',
  });
  steps.push({
    label: '调度选点',
    detail: partTargets.length ? `分区执行体 ${partTargets.join(' / ')}` : 'scheduler 选择执行体',
    nodes: ['scheduler'],
    edges: [],
    ref: 'dispatch',
  });
  steps.push({
    label: '产数',
    detail: produced
      ? `${produced.instruments ?? '—'} 仪器 × ${produced.rows ?? '—'} 行 = ${produced.samples ?? '—'} 样本`
      : '发起端生成仪器流数据',
    nodes: [src],
    edges: [],
    ref: 'produce',
  });

  if (parts.length) {
    parts.forEach((p, i) => {
      const actor = nodeForEntity(p?.actor);
      const idx = num(p?.partition) ?? i;
      const ok = !p?.failed;
      steps.push({
        label: `分区 ${idx + 1}`,
        detail: `${p?.actor ?? actor ?? '未知执行体'}${ok ? '' : ' · 失败'}${p?.rows ? ` · ${p.rows} 行` : ''}`,
        nodes: actor ? [actor] : [],
        edges: actor ? [edgeKey('scheduler', actor, '分区')] : [],
        durationMs: num(p?.ms),
        ref: `p:${idx}`,
      });
    });
  } else {
    steps.push({
      label: '协同分区计算',
      detail: '等待各分区结果（payload 尚未回报分区）',
      nodes: ['dc-services'],
      edges: [edgeKey('scheduler', 'dc-services', '分区')],
      ref: 'p:0',
    });
  }

  steps.push({
    label: '数据聚合',
    detail: 'dc-services 聚合校验 checksum',
    nodes: ['dc-services'],
    edges: aggSources.map((a) => edgeKey(a, 'dc-services', '聚合')),
    durationMs: num(detail.aggregate_cpu_ms),
    ref: 'aggregate',
  });
  steps.push({
    label: '结果回传',
    detail: `dc-services → ${src}`,
    nodes: ['dc-services', src],
    edges: [edgeKey('dc-services', src, '结果')],
    ref: 'result',
  });
  return steps;
}

/**
 * sync — ①提交 ②取云清单/计算缺失 ③P2P 并行拉取 ④云端上传 ⑤云端备份 ⑥结果回传
 * ordering: peers in result_detail.peers order; durations from the chunk ms
 */
function stepsSync(task: TaskItem | null, ctx: Ctx): Step[] {
  const detail = detailOf(task);
  const src = ctx.source;
  const chunks: any[] = Array.isArray(detail?.chunks) ? detail.chunks : [];
  const peers = (ctx.peers || []).filter((p) => p && p !== src);
  const backup = detail?.backup;
  const steps: Step[] = [];

  steps.push({
    label: '提交',
    detail: `${src} → scheduler`,
    nodes: [src, 'scheduler'],
    edges: [edgeKey(src, 'scheduler', '提交')],
    durationMs: metricsMs(task, 'queue_wait_ms'),
    ref: 'submit',
  });
  steps.push({
    label: '取云清单',
    detail: detail?.missing !== undefined
      ? `云端 ${detail?.cloud_items ?? '—'} 项 · 缺失 ${detail.missing} 项`
      : 'dc-services 清单 → 计算缺失分块',
    nodes: ['dc-services', src],
    edges: [],
    ref: 'manifest',
  });

  if (peers.length) {
    peers.forEach((peer, i) => {
      const mine = chunks.filter((c) => nodeForEntity(c?.peer) === peer);
      const okN = mine.filter((c) => c?.ok).length;
      const ms = mine.reduce((acc, c) => acc + (num(c?.ms) || 0), 0);
      steps.push({
        label: `拉取 ${i + 1}`,
        detail: mine.length ? `${peer} · ${okN}/${mine.length} 分块` : `${peer} · 并行拉取缺失分块`,
        nodes: [peer],
        edges: [edgeKey(peer, src, '拉取')],
        durationMs: mine.length ? Math.round(ms * 100) / 100 : undefined,
        ref: `peer:${peer}`,
      });
    });
  } else {
    steps.push({
      label: '并行拉取',
      detail: '等待 peer 回报分块',
      nodes: [],
      edges: [],
      ref: 'peer:none',
    });
  }

  steps.push({
    label: '云端上传',
    detail: detail?.uploaded !== undefined
      ? `上传 ${detail.uploaded} 项本地更新`
      : `${src} → dc-services 上传本地更新`,
    nodes: [src, 'dc-services'],
    edges: [edgeKey(src, 'dc-services', '上传/备份')],
    durationMs: num(detail?.upload_ms),
    ref: 'upload',
  });
  steps.push({
    label: '云端备份',
    detail: backup?.backup_id ? `备份 ${backup.backup_id}` : 'dc-services 全量备份',
    nodes: ['dc-services'],
    edges: [edgeKey(src, 'dc-services', '上传/备份')],
    durationMs: num(detail?.backup_ms),
    ref: 'backup',
  });
  steps.push({
    label: '结果回传',
    detail: `dc-services → ${src}`,
    nodes: ['dc-services', src],
    edges: [edgeKey('dc-services', src, '结果')],
    ref: 'result',
  });
  return steps;
}

/**
 * routine — ①提交 ②读取空闲节点 ③创建 Job ④Job 执行并输出 ⑤读取日志 ⑥删除 Job ⑦结果回传
 * ordering: one 创建 step per execution node, in result_detail.jobs order
 */
function stepsRoutine(task: TaskItem | null, ctx: Ctx): Step[] {
  const detail = detailOf(task);
  const src = ctx.source;
  const rawJobs: any[] = Array.isArray(detail?.jobs) ? detail.jobs : [];
  const jobs = (ctx.jobs || []).slice(0, MAX_JOBS);
  const jobIds = jobs.map((j) => j.id);
  const nodesUsed = Array.from(new Set(rawJobs.map((j) => String(j?.node || '')).filter(Boolean)));
  const wall = rawJobs.reduce((acc, j) => acc + (num(j?.wall_ms) || 0), 0);
  const steps: Step[] = [];

  steps.push({
    label: '提交',
    detail: `${src} → scheduler`,
    nodes: [src, 'scheduler'],
    edges: [edgeKey(src, 'scheduler', '提交')],
    durationMs: metricsMs(task, 'queue_wait_ms'),
    ref: 'submit',
  });
  steps.push({
    label: '读取空闲节点',
    detail: nodesUsed.length ? `空闲边缘节点 ${nodesUsed.join(' / ')}` : 'scheduler 读取节点空闲度',
    nodes: ['scheduler'],
    edges: [],
    ref: 'nodes',
  });

  jobs.forEach((j) => {
    steps.push({
      label: `创建 Job ${jobNodeOf(j.id).replace('node', '#')}`,
      detail: `${j.node}${(j.count || 1) > 1 ? ` · ${j.count} 个 Job` : ''}`,
      nodes: [j.id],
      edges: [edgeKey('scheduler', j.id, 'Job创建')],
      ref: `job:${j.id}`,
    });
  });
  steps.push({
    label: 'Job 执行输出',
    detail: rawJobs.length
      ? `${rawJobs.length} 个 Job · 成功 ${detail?.succeeded ?? '—'} · 输出 JSON`
      : '一次性 Job 执行并输出 JSON',
    nodes: jobIds,
    edges: [],
    durationMs: rawJobs.length ? Math.round(wall * 100) / 100 : undefined,
    ref: 'exec',
  });
  steps.push({
    label: '读取日志',
    detail: 'scheduler 读取 Job pod 日志',
    nodes: jobIds,
    edges: jobIds.map((id) => edgeKey(id, 'scheduler', '日志')),
    ref: 'logs',
  });
  steps.push({
    label: '删除 Job',
    detail: '回收一次性 Job pod',
    nodes: jobIds,
    edges: jobIds.map((id) => edgeKey('scheduler', id, '删除')),
    ref: 'delete',
  });
  steps.push({
    label: '结果回传',
    detail: `scheduler → ${src}`,
    nodes: ['scheduler', src],
    edges: [],
    ref: 'result',
  });
  return steps;
}

function buildSteps(kind: ModelKind, task: TaskItem | null, ctx: Ctx): Step[] {
  if (kind === 'diagnosis') return stepsDiagnosis(task, ctx);
  if (kind === 'compute') return stepsCompute(task, ctx);
  if (kind === 'sync') return stepsSync(task, ctx);
  return stepsRoutine(task, ctx);
}

/**
 * Running task → cursor from `stage` (+ the first partition/peer/job step when
 * the payload already exposes one). Failed → the failing step. Finished → last.
 */
function stageStepIndex(kind: ModelKind, task: TaskItem, steps: Step[]): number {
  const last = Math.max(0, steps.length - 1);
  const at = (ref: string, fallback = 0) => {
    const i = steps.findIndex((s) => s.ref === ref);
    return i < 0 ? fallback : i;
  };
  const status = String(task.status || '').toLowerCase();
  const stage = String(task.stage || '').toLowerCase();

  if (status === 'failed') return failureStepIndex(kind, task, steps);
  if (status === 'queued') return 0;
  const done = status === 'finished' || status === 'completed'
    || stage === 'finished' || stage === 'completed';
  if (done) return last;

  if (kind === 'diagnosis') {
    if (stage === 'server') return at('server', last);
    if (stage === 'worker') return at('worker', at('dispatch'));
    return at('dispatch');
  }
  if (kind === 'compute') {
    if (stage === 'compute') return at('p:0', at('dispatch'));
    return at('dispatch');
  }
  if (kind === 'sync') {
    if (stage === 'sync') {
      const i = steps.findIndex((s) => s.ref.startsWith('peer:'));
      return i < 0 ? at('manifest') : i;
    }
    return at('manifest');
  }
  if (stage === 'routine') return at('exec', at('nodes'));
  return at('nodes');
}

/** Which step blew up — per-partition / per-job detail when available. */
function failureStepIndex(kind: ModelKind, task: TaskItem, steps: Step[]): number {
  const last = Math.max(0, steps.length - 1);
  const res: any = task.result || null;
  const detail = detailOf(task);
  const at = (ref: string, fallback: number = last) => {
    const i = steps.findIndex((s) => s.ref === ref);
    return i < 0 ? fallback : i;
  };
  if (kind === 'compute') {
    const parts: any[] = Array.isArray(res?.partitions) ? res.partitions : [];
    const bad = parts.find((p) => p?.failed);
    if (bad) return at(`p:${num(bad?.partition) ?? 0}`, at('p:0'));
  }
  if (kind === 'routine') {
    const jobs: any[] = Array.isArray(detail?.jobs) ? detail.jobs : [];
    if (jobs.some((j) => String(j?.state) === 'failed' || j?.timeout)) return at('exec', last);
  }
  const stage = String(task.stage || '').toLowerCase();
  if (kind === 'diagnosis') {
    if (stage === 'server') return at('server', last);
    if (stage === 'worker') return at('worker', last);
    return at('submit');
  }
  if (kind === 'compute') return at('p:0', at('dispatch'));
  if (kind === 'sync') return at('peer:none', at('manifest'));
  return at('exec', last);
}

// ---------------------------------------------------------------------------
// view model (geometry — independent of the cursor, so playback never relayouts)
// ---------------------------------------------------------------------------
interface DrawnEdge extends SemEdge {
  d: string;
  label_at: LabelPlacement;
  live: boolean;
}

interface ViewModel {
  edges: DrawnEdge[];
  nodeIds: string[];
  liveKeys: Set<string>;
  liveNodes: Set<string>;
  jobs: JobCtx[];
  ctx: Ctx | null;
}

function buildView(kind: ModelKind, task: TaskItem | null): ViewModel {
  const ctx = task ? ctxFromTask(kind, task) : null;
  const ghost = buildEdges(kind, CANON[kind]);
  const live = ctx ? buildEdges(kind, ctx) : [];
  const liveKeys = new Set(live.map((e) => e.key));
  const all = [
    ...ghost.filter((e) => !liveKeys.has(e.key)).map((e) => ({ e, live: false })),
    ...live.map((e) => ({ e, live: true })),
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
  all.forEach(({ e, live: isLive }, idx) => {
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
    edges.push({ ...e, d: toPathD(pts), label_at: placeLabel(pts, e.label), live: isLive });
    nodeIds.add(e.from);
    nodeIds.add(e.to);
  });

  const jobCtx = kind === 'routine' ? (ctx || CANON.routine) : null;
  const jobs = jobCtx?.jobs || [];
  return {
    edges,
    nodeIds: Array.from(nodeIds).filter((id) => {
      if (!isJob(id)) return true;
      return jobs.some((j) => j.id === id);
    }),
    liveKeys,
    liveNodes: new Set(live.flatMap((e) => [e.from, e.to])),
    jobs,
    ctx,
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

function isFinished(task: TaskItem | null): boolean {
  if (!task) return false;
  const s = String(task.status || '').toLowerCase();
  const stage = String(task.stage || '').toLowerCase();
  return s === 'finished' || s === 'completed' || stage === 'finished' || stage === 'completed';
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
// playback state
// ---------------------------------------------------------------------------
/**
 * 'live'   – cursor follows the running task's stage (no replay)
 * 'auto'   – looping demo playback (2.5 s per step, 3 s dwell, then ① again)
 * 'manual' – user-driven cursor (step buttons / chip clicks)
 */
type PlayMode = 'live' | 'auto' | 'manual';

interface PlayState {
  id: string;
  index: number;
  mode: PlayMode;
}

const STEP_MS = 2500;
const DWELL_MS = 3000;

function initialPlayState(id: string, task: TaskItem | null, stepsLen: number, reduced: boolean): PlayState {
  const last = Math.max(0, stepsLen - 1);
  // no traced task → canonical skeleton only, nothing plays
  if (!id) return { id, index: 0, mode: 'manual' };
  if (stepsLen < 2) return { id, index: last, mode: 'manual' };
  if (isFinished(task)) {
    // reduced-motion users land on the end state instead of an autoplay loop
    return reduced ? { id, index: last, mode: 'manual' } : { id, index: 0, mode: 'auto' };
  }
  return { id, index: 0, mode: 'live' };
}

type StepAction = 'reset' | 'prev' | 'next' | 'auto';

/**
 * Pure cursor transition behind the ⏮ / ◀ / ▶ / ▶自动 buttons.
 * Manual actions always clamp (no wrap) and take over from autoplay; 'auto'
 * toggles the looping demo and restarts from ① when it is switched on.
 */
function nextPlayState(
  state: PlayState,
  action: StepAction,
  id: string,
  cursor: number,
  lastStep: number,
  canAuto: boolean,
): PlayState {
  if (!id) return state;
  const at = Math.min(Math.max(0, cursor), lastStep);
  switch (action) {
    case 'reset':
      return { id, index: 0, mode: 'manual' };
    case 'prev':
      return { id, index: Math.max(0, at - 1), mode: 'manual' };
    case 'next':
      return { id, index: Math.min(lastStep, at + 1), mode: 'manual' };
    default:
      if (!canAuto) return state;
      return state.id === id && state.mode === 'auto'
        ? { ...state, mode: 'manual' }
        : { id, index: 0, mode: 'auto' };
  }
}

function usePrefersReducedMotion(): boolean {
  const [reduced, setReduced] = useState(false);
  useEffect(() => {
    if (typeof window === 'undefined' || !window.matchMedia) return undefined;
    const mq = window.matchMedia('(prefers-reduced-motion: reduce)');
    setReduced(!!mq.matches);
    const on = () => setReduced(!!mq.matches);
    if (mq.addEventListener) mq.addEventListener('change', on);
    else if (mq.addListener) mq.addListener(on);
    return () => {
      if (mq.removeEventListener) mq.removeEventListener('change', on);
      else if (mq.removeListener) mq.removeListener(on);
    };
  }, []);
  return reduced;
}

// ---------------------------------------------------------------------------
// component
// ---------------------------------------------------------------------------
const TONES: Tone[] = ['ghost', 'past', 'current', 'pending', 'unrelated', 'failed'];

const LEGEND: { state: Tone; text: string }[] = [
  { state: 'past', text: '已执行 PASSED' },
  { state: 'current', text: '当前 CURRENT' },
  { state: 'pending', text: '待执行 PENDING' },
  { state: 'unrelated', text: '无关 UNRELATED' },
  { state: 'failed', text: '失败 FAILED' },
  { state: 'ghost', text: '无任务 · 标准路径' },
];

const CIRCLED = ['①', '②', '③', '④', '⑤', '⑥', '⑦', '⑧', '⑨', '⑩',
  '⑪', '⑫', '⑬', '⑭', '⑮', '⑯', '⑰', '⑱', '⑲', '⑳'];

function circled(i: number): string {
  return CIRCLED[i] || `(${i + 1})`;
}

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
  const [play, setPlay] = useState<PlayState>({ id: '', index: 0, mode: 'auto' });
  const reduced = usePrefersReducedMotion();

  const kindTasks = useMemo(() => {
    const list = (Array.isArray(tasks) ? tasks : []).filter((t) => t && t.model === kind);
    return sortTasks(list).slice(0, 6);
  }, [tasks, kind]);

  const traced = useMemo(() => {
    if (!kindTasks.length) return null;
    if (autoFollow) return kindTasks[0];
    return kindTasks.find((t) => t.id === selId) || kindTasks[0];
  }, [kindTasks, autoFollow, selId]);

  // geometry only depends on kind + traced task, never on the cursor
  const view = useMemo(() => buildView(kind, traced), [kind, traced]);

  // ordered execution timeline — real task when available, canonical otherwise
  const steps = useMemo(
    () => buildSteps(kind, traced, view.ctx || CANON[kind]),
    [kind, traced, view],
  );

  const tracedId = traced?.id || '';
  const stepsLen = steps.length;

  const autoState = useMemo(
    () => initialPlayState(tracedId, traced, stepsLen, reduced),
    [tracedId, traced, stepsLen, reduced],
  );
  // state derived synchronously for the task at hand (no first-frame flash)
  const active = play.id === tracedId && tracedId ? play : autoState;

  const dataCursor = useMemo(
    () => (traced && stepsLen ? stageStepIndex(kind, traced, steps) : 0),
    [kind, traced, steps, stepsLen],
  );
  const lastStep = Math.max(0, stepsLen - 1);
  const cursor = traced
    ? Math.min(active.mode === 'live' ? dataCursor : active.index, lastStep)
    : -1;

  // keep the stored state in sync when the traced task changes
  useEffect(() => {
    setPlay((s) => (s.id === tracedId ? s : autoState));
  }, [tracedId, autoState]);

  // autoplay: slower cadence, dwell on the last step, then loop back to ①
  useEffect(() => {
    if (active.mode !== 'auto' || stepsLen < 2 || reduced) return undefined;
    const delay = active.index >= lastStep ? DWELL_MS : STEP_MS;
    const id = window.setTimeout(() => {
      setPlay((s) => {
        if (s.mode !== 'auto') return s;
        return { ...s, index: s.index >= lastStep ? 0 : s.index + 1 };
      });
    }, delay);
    return () => window.clearTimeout(id);
  }, [active, stepsLen, lastStep, reduced]);

  // ---- step / edge / node state maps ----
  const stepTones = useMemo<Tone[]>(() => {
    if (!traced || !stepsLen) return [];
    const failed = String(traced.status || '').toLowerCase() === 'failed';
    return steps.map((_, i) => {
      if (i === cursor) return failed ? 'failed' : 'current';
      return i < cursor ? 'past' : 'pending';
    });
  }, [steps, stepsLen, cursor, traced]);

  const edgeStep = useMemo(() => {
    const m = new Map<string, number>();
    steps.forEach((s, i) => {
      s.edges.forEach((k) => {
        const prev = m.get(k);
        if (prev === undefined || i < prev) m.set(k, i);
      });
    });
    return m;
  }, [steps]);

  const edgeTone = useMemo(() => {
    const m = new Map<string, Tone>();
    view.edges.forEach((e) => {
      if (!traced) { m.set(e.key, 'ghost'); return; }
      const i = edgeStep.get(e.key);
      if (i === undefined) { m.set(e.key, e.live ? 'pending' : 'unrelated'); return; }
      m.set(e.key, stepTones[i] || 'pending');
    });
    return m;
  }, [view, traced, edgeStep, stepTones]);

  const nodeTone = useMemo(() => {
    const m = new Map<string, NodeState>();
    const curTone: NodeState = stepTones[cursor] || 'current';
    view.nodeIds.forEach((id) => {
      if (!traced) { m.set(id, 'ghost'); return; }
      let past = false;
      let cur = false;
      let pend = false;
      steps.forEach((s, i) => {
        if (!s.nodes.includes(id)) return;
        if (i === cursor) cur = true;
        else if (i < cursor) past = true;
        else pend = true;
      });
      if (cur) m.set(id, curTone);
      else if (past) m.set(id, 'past');
      else if (pend) m.set(id, 'pending');
      else m.set(id, 'unrelated');
    });
    return m;
  }, [view, steps, cursor, stepTones, traced]);

  // ---- interaction: manual stepping takes over from autoplay immediately ----
  const canStep = !!traced && stepsLen > 0;
  const canAuto = canStep && stepsLen >= 2 && !reduced;
  const applyAction = (action: StepAction) => {
    if (!canStep) return;
    setPlay((s) => nextPlayState(s, action, tracedId, Math.max(0, cursor), lastStep, canAuto));
  };
  const onReset = () => applyAction('reset');
  const onPrev = () => applyAction('prev');
  const onNext = () => applyAction('next');
  const onAuto = () => applyAction('auto');
  const jumpTo = (i: number) => {
    if (!canStep) return;
    setPlay({ id: tracedId, index: Math.min(Math.max(0, i), lastStep), mode: 'manual' });
  };

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

  const totalMs = useMemo(() => {
    const known = steps.map((s) => s.durationMs).filter((d): d is number => typeof d === 'number');
    return known.length ? known.reduce((a, b) => a + b, 0) : undefined;
  }, [steps]);

  // only the current step's edges carry the travelling dot
  const dotEdges = useMemo(() => {
    if (reduced || !traced || cursor < 0) return [];
    const cur = steps[cursor];
    if (!cur) return [];
    return view.edges.filter((e) => cur.edges.includes(e.key)).slice(0, 3);
  }, [view, steps, cursor, reduced, traced]);

  const curStep = cursor >= 0 ? steps[cursor] : null;
  const dcLive = !!curStep && curStep.nodes.some((n) => DC_NODE_SET.has(n));
  const dcSummary = useMemo(() => {
    const list = Array.isArray(tasks) ? tasks : [];
    let running = 0;
    let queued = 0;
    list.forEach((t) => {
      const st = String(t?.status || '').toLowerCase();
      if (st === 'running') running += 1;
      else if (st === 'queued') queued += 1;
    });
    return { running, queued };
  }, [tasks]);

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
        云 / 边 / 端 三层拓扑按时间顺序回放 —— 可用「上一步 / 下一步」手动逐步，或「▶ 自动」循环演示；
        黄铜为已执行、橙色脉冲为当前步骤、淡灰虚线为待执行、最淡灰为与本任务无关的其它链路
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
            <InfoCell
              k="步骤"
              v={curStep ? `${circled(cursor)} ${curStep.label}` : `— / ${stepsLen}`}
              mono
              title={curStep?.detail || ''}
            />
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
            {/* 云数据中心 as one container: encloses scheduler + medical-server
                + dc-services + redis. Connectors cross its hairline border
                perpendicular, every jog row stays outside it. */}
            <g className={`tm-dc ${dcLive ? 'live' : ''}`}>
              <rect
                className="tm-dc-panel"
                x={DC_PANEL.x}
                y={DC_PANEL.y}
                width={DC_PANEL.w}
                height={DC_PANEL.h}
                rx="3"
              />
              <rect className="tm-dc-bar" x={DC_PANEL.x} y={DC_PANEL.y} width="3" height={DC_PANEL.h} />
              <text className="tm-dc-title" x={DC_PANEL.x + 16} y={DC_PANEL.y + 21}>
                DATA CENTER · 云数据中心
              </text>
              <text
                className="tm-dc-meta mono"
                x={DC_PANEL.x + DC_PANEL.w - 16}
                y={DC_PANEL.y + 21}
                textAnchor="end"
              >
                {`组件 ${DC_NODES.length} · 运行中 ${dcSummary.running} · 排队 ${dcSummary.queued}`}
              </text>
              <line
                className="tm-dc-rule"
                x1={DC_PANEL.x + 14}
                y1={DC_PANEL.y + 29}
                x2={DC_PANEL.x + DC_PANEL.w - 14}
                y2={DC_PANEL.y + 29}
              />
            </g>
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
            {['ghost', 'unrelated', 'pending'].flatMap((tone) => view.edges
              .filter((e) => edgeTone.get(e.key) === tone)
              .map((e) => (
                <g key={`g-${e.key}`} className={toneClass(tone as Tone)}>
                  <path className="tm-line" d={e.d} markerEnd={`url(#tm-arrow-${tone})`} />
                  <text
                    className="tm-edge-label"
                    x={e.label_at.x}
                    y={e.label_at.y}
                    textAnchor={e.label_at.anchor}
                  >
                    {e.label}
                  </text>
                </g>
              )))}
          </g>

          <g className="tm-edges live-layer">
            {['past', 'current', 'failed'].flatMap((tone) => view.edges
              .filter((e) => edgeTone.get(e.key) === tone)
              .map((e) => (
                <g key={`l-${e.key}`} className={toneClass(tone as Tone)}>
                  <path className="tm-line" d={e.d} markerEnd={`url(#tm-arrow-${tone})`} />
                  <text
                    className="tm-edge-label"
                    x={e.label_at.x}
                    y={e.label_at.y}
                    textAnchor={e.label_at.anchor}
                  >
                    {e.label}
                  </text>
                </g>
              )))}
          </g>

          <g className="tm-dots">
            {dotEdges.map((e) => (
              <circle key={`d-${e.key}`} className={`tm-dot ${edgeTone.get(e.key) || 'current'}`} r="3.4">
                <animateMotion dur="3.2s" repeatCount="indefinite" path={e.d} />
              </circle>
            ))}
          </g>

          <g className="tm-nodes">
            {view.nodeIds.map((id) => {
              const b = boxOf(id);
              if (!b) return null;
              const meta = labelOf(id);
              const state = nodeTone.get(id) || 'ghost';
              const focused = state === 'past' || state === 'current' || state === 'failed';
              const ringed = state === 'current' || state === 'failed';
              const job = isJob(id);

              if (job) {
                const r = b.w / 2;
                return (
                  <g key={id} className={['tm-node', 'job', focused ? 'active' : '', state].filter(Boolean).join(' ')}>
                    <title>{`${meta.name} · Job pod on ${meta.caption}`}</title>
                    {ringed && <circle className="tm-ring" cx={b.x} cy={b.y} r={r + 5} />}
                    <circle className="tm-box job-box" cx={b.x} cy={b.y} r={r} />
                    <g className="tm-icon tm-icon-sm" transform={`translate(${b.x - 7} ${b.y - 16}) scale(0.5833)`}>
                      {iconShapes('job')}
                    </g>
                    <text className="tm-node-name" x={b.x} y={b.y + 9} textAnchor="middle">{meta.name}</text>
                    <text className="tm-node-cap" x={b.x} y={b.y + 19} textAnchor="middle">{meta.caption}</text>
                  </g>
                );
              }

              const mx = b.x - b.w / 2 + MEDAL_INSET;
              const tx = b.x - b.w / 2 + TEXT_INSET;
              return (
                <g key={id} className={['tm-node', focused ? 'active' : '', state].filter(Boolean).join(' ')}>
                  <title>{`${meta.name} · ${meta.caption}`}</title>
                  {ringed && <circle className="tm-ring" cx={mx} cy={b.y} r={RING_R} />}
                  <rect
                    className={`tm-tier-flag ${tierOf(id)}`}
                    x={b.x - b.w / 2}
                    y={b.y - 14}
                    width="3"
                    height="28"
                  />
                  <circle className="tm-medal" cx={mx} cy={b.y} r={MEDAL_R} />
                  <g className="tm-icon" transform={`translate(${mx - 12} ${b.y - 12})`}>
                    {iconShapes(iconKindOf(id))}
                  </g>
                  <text className="tm-node-name" x={tx} y={b.y - 3} textAnchor="start">{meta.name}</text>
                  <text className="tm-node-cap" x={tx} y={b.y + 12} textAnchor="start">{meta.caption}</text>
                </g>
              );
            })}
          </g>
        </svg>
      </div>

      <div className="tmap-timeline">
        <div className="tm-tl-head">
          <span className="tm-tl-title">
            <span className="eyebrow">执行时序 · STEP TIMELINE</span>
            {active.mode === 'auto' && !reduced && (
              <i className="tm-loop mono">循环演示 · LOOP</i>
            )}
          </span>
          <span className="tm-tl-ctl">
            <button
              type="button"
              className="mini"
              onClick={onReset}
              disabled={!canStep}
              title="回到第 ① 步"
            >
              ⏮ 重置
            </button>
            <button
              type="button"
              className="mini"
              onClick={onPrev}
              disabled={!canStep || cursor <= 0}
              title="上一步（切换到手动）"
            >
              ◀ 上一步
            </button>
            <button
              type="button"
              className="mini"
              onClick={onNext}
              disabled={!canStep || cursor >= lastStep}
              title="下一步（切换到手动）"
            >
              下一步 ▶
            </button>
            <button
              type="button"
              className={`mini ${active.mode === 'auto' ? 'on' : ''}`}
              onClick={onAuto}
              disabled={!canStep || stepsLen < 2 || reduced}
              aria-pressed={active.mode === 'auto'}
              title={reduced ? '已启用「减少动态效果」，自动循环关闭' : '自动循环演示（每步 2.5s，末步停留 3s 后回到 ①）'}
            >
              {active.mode === 'auto' ? '⏸ 暂停自动' : '▶ 自动'}
            </button>
          </span>
          <span className="tm-tl-stat mono">
            {totalMs !== undefined && <i className="tm-tl-total">合计 {fmtMs(totalMs)}</i>}
            <i className="tm-tl-count">
              {!stepsLen ? '0/0' : cursor < 0 ? `—/${stepsLen}` : `${cursor + 1}/${stepsLen}`}
            </i>
            <i className={`tm-tl-mode ${active.mode}`}>
              {active.mode === 'auto' ? 'AUTO' : active.mode === 'manual' ? 'MANUAL' : 'LIVE'}
            </i>
          </span>
        </div>
        {stepsLen ? (
          <ol className="tm-tl-steps">
            {steps.map((s, i) => {
              const tone: Tone = !traced ? 'ghost' : (stepTones[i] || 'pending');
              return (
                <li key={`${s.ref}-${i}`} className={`tm-tl-step ${tone}`}>
                  <button
                    type="button"
                    className="tm-tl-chip"
                    onClick={() => jumpTo(i)}
                    disabled={!traced}
                    aria-current={i === cursor ? 'step' : undefined}
                    title={`${circled(i)} ${s.label}${s.detail ? ` · ${s.detail}` : ''}${s.durationMs !== undefined ? ` · ${fmtMs(s.durationMs)}` : ''}`}
                  >
                    <span className="tm-tl-no mono">{circled(i)}</span>
                    <span className="tm-tl-label">{s.label}</span>
                    <span className="tm-tl-meta mono">
                      {s.durationMs !== undefined ? fmtMs(s.durationMs) : (s.detail || '')}
                    </span>
                    {i === cursor && !reduced && active.mode === 'auto' && (
                      <i
                        key={`prog-${cursor}`}
                        className="tm-tl-prog"
                        style={{ animationDuration: `${i >= lastStep ? DWELL_MS : STEP_MS}ms` }}
                      />
                    )}
                  </button>
                  {i < stepsLen - 1 && <i className={`tm-tl-link ${tone}`} />}
                </li>
              );
            })}
          </ol>
        ) : (
          <div className="tm-tl-empty xs muted">该类型暂无任务 · 选择任务后显示执行时序</div>
        )}
      </div>

      <div className="tmap-legend">
        {LEGEND.map((l) => (
          <span className="tm-legend-item" key={l.state}>
            <i className={`tm-legend-line ${l.state}`} />
            {l.text}
          </span>
        ))}
        <span className="tm-legend-note muted xs">
          {!traced
            ? '尚无该类型任务，仅绘制标准路径'
            : curStep
              ? `${active.mode === 'auto' ? '循环演示' : active.mode === 'manual' ? '手动逐步' : '跟随实时阶段'} · ${circled(cursor)} ${curStep.label}${curStep.detail ? ` · ${curStep.detail}` : ''}`
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

/**
 * Test hook (same spirit as the `initialKind` prop): lets a smoke harness assert
 * the derived step timeline, cursor and geometry without a DOM. Every member is
 * used by the component itself, so this costs nothing in the bundle.
 */
export const __internals = {
  nextPlayState,
  iconKindOf,
  buildSteps,
  buildView,
  stageStepIndex,
  failureStepIndex,
  initialPlayState,
  CANON,
};
