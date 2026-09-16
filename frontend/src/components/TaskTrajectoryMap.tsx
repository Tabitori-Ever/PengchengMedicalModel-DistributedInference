import { useEffect, useMemo, useState } from 'react';
// 本组件专用样式表（多任务 tab / 同屏紧凑轨迹）。管理平台入口只引入 styles/index.css，
// 因此在组件内引入，保证样式表随组件一起进入管理平台的产物（重复引入会被去重）。
import '../styles/traj.css';
import type { ModelKind, TaskItem } from '../types';
import {
  MODEL_COLOR, MODEL_LABEL, MODELS, SOURCE_ROLE, STATUS_LABEL, fmtMs,
} from '../utils';
import { TIER_LABEL as TIER_TEXT, entityName } from '../terms';

/* ===========================================================================
   任务执行轨迹（逻辑架构视图的配套地图）

   数据中心 / 医疗中心 / 医院 三层示意图：先以极淡的“无关”线画出同类任务的
   标准执行路径，再按时间顺序走一遍某个真实任务的具体执行链路 —— 由任务数据推导出的
   有序步骤时间轴驱动游标，每个节点/连线只处于四种状态之一：

     已执行 – 中性灰实线
     当前   – 橙色高亮（脉冲光圈 + 流动光点）
     待执行 – 更淡的灰色虚线
     无关   – 最淡灰，与本任务无关

   游标跟随运行中任务的实时阶段，停在失败任务的出错步骤上，或对已完成任务自动循环播放
   （每步 2.5 s，末步停留 3 s 后回到第一步）。

   全部为内联 SVG + CSS（虚线偏移关键帧、SMIL animateMotion 流动光点）；组件内部不取数，
   任务由 ArchitecturePage 轮询 api.listTasks() 后以 props 传入。
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
 * 日常任务专用伪节点：每个真正跑过一次性 Job 的业务节点一个虚线圆
 * （调度器只会在 node1 / node2 上创建 Job）。
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

const NODE_META: Record<string, { name: string; id: string; caption: string; tier: Tier }> = {
  'scheduler': { name: 'scheduler', id: 'scheduler', caption: '调度', tier: 'cloud' },
  'medical-server': { name: 'medical-server', id: 'medical-server', caption: '医疗推理', tier: 'cloud' },
  'dc-services': { name: 'dc-services', id: 'dc-services', caption: '患者库 · 协同计算', tier: 'cloud' },
  'redis': { name: 'redis', id: 'redis', caption: '队列 · 记录', tier: 'cloud' },
  // 医疗中心 = 原 hospital-*（node1 / node2）；医院 = 原 clinic-*（node1 / node2）
  'hospital-a': { name: entityName('hospital-a'), id: 'hospital-a', caption: '诊断 · 计算 · 通信 · 日常', tier: 'edge' },
  'hospital-b': { name: entityName('hospital-b'), id: 'hospital-b', caption: '诊断 · 计算 · 通信 · 日常', tier: 'edge' },
  'clinic-1': { name: entityName('clinic-1'), id: 'clinic-1', caption: '转诊 · 计算 · 通信 · 日常', tier: 'terminal' },
  'clinic-2': { name: entityName('clinic-2'), id: 'clinic-2', caption: '转诊 · 计算 · 通信 · 日常', tier: 'terminal' },
};

/** inline 24×24 icon per node — no icon library, no external assets */
export type IconKind = 'scheduler' | 'server' | 'database' | 'cache' | 'hospital' | 'clinic' | 'job' | 'monitor';

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

/** 数据中心容器：云侧四个组件作为一个整体展示 */
const DC_PANEL = { x: 280, y: 14, w: 894, h: 106 };
const DC_NODES = ['scheduler', 'medical-server', 'dc-services', 'redis'];
const DC_NODE_SET = new Set(DC_NODES);

/** Icon geometry authored in a 0..24 box; stroked/filled via the .tm-icon CSS. */
export function iconShapes(kind: IconKind) {
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
    case 'monitor': // 监控 / 预测：仪表盘
      return (
        <>
          <path d="M4.6 17.4 A8 8 0 0 1 19.4 17.4" />
          <path d="M12 17.4 L16.2 11.6" />
          <circle className="tm-icon-dot" cx="12" cy="17.4" r="1.1" />
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

/** 自下而上的三层标签（命名取自 terms.ts 单一来源） */
const TIER_LABEL: { text: string; y: number }[] = [
  { text: TIER_TEXT.cloud, y: 106 },
  { text: TIER_TEXT.medical, y: 316 },
  { text: TIER_TEXT.hospital, y: 536 },
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
    if (hospital) out.push(semEdge(hospital, 'medical-server', '推理'));
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
  worker: '医院推理前端',
  server: '数据中心融合推理',
  compute: '分区协同计算',
  sync: '并行拉取 / 数据中心备份',
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
/** One member of a parallel step (a partition / a peer / an execution node). */
interface StepItem {
  label: string;
  detail?: string;
  nodes: string[];
  edges: string[];
  durationMs?: number;
  ref: string;
}

interface Step {
  /** chip text, e.g. 提交 / 分区计算 */
  label: string;
  /** chip tooltip / secondary text */
  detail?: string;
  /**
   * measured duration when the payload exposes one. For a parallel step this is
   * the MAX of its items (concurrent work does not add up).
   */
  durationMs?: number;
  /** nodes that take part in this step */
  nodes: string[];
  /** semantic edges that carry this step */
  edges: string[];
  /** stable key used to locate the failing step */
  ref: string;
  /** present when the step is a parallel group — all of it lights up together */
  items?: StepItem[];
  /** sub-item refs, so stage / failure lookup can resolve into the group */
  refs?: string[];
}

/** Collapse concurrent work into ONE step: union of nodes/edges, MAX duration. */
function parallelStep(label: string, ref: string, items: StepItem[], detail?: string): Step {
  let durationMs: number | undefined;
  items.forEach((it) => {
    if (it.durationMs === undefined) return;
    durationMs = durationMs === undefined ? it.durationMs : Math.max(durationMs, it.durationMs);
  });
  return {
    label,
    ref,
    detail,
    durationMs,
    nodes: Array.from(new Set(items.flatMap((i) => i.nodes))),
    edges: Array.from(new Set(items.flatMap((i) => i.edges))),
    items,
    refs: items.map((i) => i.ref),
  };
}

/** Does a step answer to `ref` (own ref or one of its parallel sub-items)? */
function stepHasRef(step: Step, ref: string): boolean {
  return step.ref === ref || !!step.refs && step.refs.includes(ref);
}

function refStepIndex(steps: Step[], ref: string, fallback: number): number {
  const i = steps.findIndex((s) => stepHasRef(s, ref));
  return i < 0 ? fallback : i;
}

function prefixStepIndex(steps: Step[], prefix: string, fallback: number): number {
  const i = steps.findIndex((s) => stepHasRef(s, prefix)
    || !!s.refs && s.refs.some((r) => r.startsWith(prefix)));
  return i < 0 ? fallback : i;
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
      label: '医院推理',
      detail: `${hospital} 医院前端提特征`,
      nodes: [hospital],
      edges: [edgeKey(hospital, 'medical-server', '推理')],
      durationMs: stageMs('worker') ?? num(detail.worker_latency_ms),
      ref: 'worker',
    });
  }
  steps.push({
    label: '数据中心融合',
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
      : '发起方本地生成仪器流数据',
    nodes: [src],
    edges: [],
    ref: 'produce',
  });

  // ④ ∥ 分区计算 — all partitions run concurrently and light up together
  const partItems: StepItem[] = parts.length
    ? parts.map((p, i) => {
      const actor = nodeForEntity(p?.actor);
      const idx = num(p?.partition) ?? i;
      const ok = !p?.failed;
      return {
        label: `分区 ${idx + 1}`,
        detail: `${p?.actor ?? actor ?? '未知执行体'}${ok ? '' : ' · 失败'}${p?.rows ? ` · ${p.rows} 行` : ''}`,
        nodes: actor ? [actor] : [],
        edges: actor ? [edgeKey('scheduler', actor, '分区')] : [],
        durationMs: num(p?.ms),
        ref: `p:${idx}`,
      };
    })
    : [{
      label: '分区计算',
      detail: '等待各分区结果（payload 尚未回报分区）',
      nodes: ['dc-services'],
      edges: [edgeKey('scheduler', 'dc-services', '分区')],
      ref: 'p:0',
    }];
  steps.push(parallelStep('分区计算', 'parallel', partItems,
    `${partItems.length} 个执行体并行计算，取最慢分区耗时`));

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
 * sync — ①提交 ②取清单/计算缺失 ③并行拉取 ④数据中心上传 ⑤数据中心备份 ⑥结果回传
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
    label: '取清单',
    detail: detail?.missing !== undefined
      ? `数据中心 ${detail?.cloud_items ?? '—'} 项 · 缺失 ${detail.missing} 项`
      : 'dc-services 清单 → 计算缺失分块',
    nodes: ['dc-services', src],
    edges: [],
    ref: 'manifest',
  });

  // ③ ∥ P2P 拉取 — one sub item per peer, all pulling at the same time
  const peerItems: StepItem[] = peers.length
    ? peers.map((peer) => {
      const mine = chunks.filter((c) => nodeForEntity(c?.peer) === peer);
      const okN = mine.filter((c) => c?.ok).length;
      const ms = mine.reduce((acc, c) => acc + (num(c?.ms) || 0), 0);
      return {
        label: peer,
        detail: mine.length ? `${okN}/${mine.length} 分块` : '并行拉取缺失分块',
        nodes: [peer],
        edges: [edgeKey(peer, src, '拉取')],
        durationMs: mine.length ? Math.round(ms * 100) / 100 : undefined,
        ref: `peer:${peer}`,
      };
    })
    : [{
      label: '并行拉取',
      detail: '等待对端回报分块',
      nodes: [],
      edges: [],
      ref: 'peer:none',
    }];
  steps.push(parallelStep('并行拉取', 'parallel', peerItems,
    `${peerItems.length} 个对端并行拉取，取最慢耗时`));

  steps.push({
    label: '数据中心上传',
    detail: detail?.uploaded !== undefined
      ? `上传 ${detail.uploaded} 项本地更新`
      : `${src} → dc-services 上传本地更新`,
    nodes: [src, 'dc-services'],
    edges: [edgeKey(src, 'dc-services', '上传/备份')],
    durationMs: num(detail?.upload_ms),
    ref: 'upload',
  });
  steps.push({
    label: '数据中心备份',
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
    detail: nodesUsed.length ? `空闲业务节点 ${nodesUsed.join(' / ')}` : 'scheduler 读取节点空闲度',
    nodes: ['scheduler'],
    edges: [],
    ref: 'nodes',
  });

  // ③ ∥ Job 执行 — one sub item per execution node (create → run → output)
  const jobItems: StepItem[] = jobs.map((j) => {
    const mine = rawJobs.filter((x) => String(x?.node) === j.node);
    const okN = mine.filter((x) => String(x?.state) === 'succeeded').length;
    const wall = mine.reduce((acc, x) => acc + (num(x?.wall_ms) || 0), 0);
    const count = j.count || mine.length || 1;
    return {
      label: j.node,
      detail: `${count} 个 Job${mine.length ? ` · 成功 ${okN}` : ''}`,
      nodes: [j.id],
      edges: [edgeKey('scheduler', j.id, 'Job创建')],
      durationMs: wall ? Math.round(wall * 100) / 100 : undefined,
      ref: `job:${j.id}`,
    };
  });
  steps.push(parallelStep('Job 执行', 'parallel', jobItems,
    `${jobItems.length} 个执行节点并行跑一次性 Job，取最慢节点耗时`));

  steps.push({
    label: '读取日志',
    detail: rawJobs.length
      ? `scheduler 读取 ${rawJobs.length} 个 Job 日志 · 成功 ${detail?.succeeded ?? '—'}`
      : 'scheduler 读取 Job 日志并解析 JSON',
    nodes: jobIds,
    edges: jobIds.map((id) => edgeKey(id, 'scheduler', '日志')),
    ref: 'logs',
  });
  steps.push({
    label: '删除 Job',
    detail: '回收一次性 Job',
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
  // sub-item refs resolve into their parallel group
  const at = (ref: string, fallback = 0) => refStepIndex(steps, ref, fallback);
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
    if (stage === 'sync') return prefixStepIndex(steps, 'peer:', at('manifest'));
    return at('manifest');
  }
  if (stage === 'routine') return at('parallel', at('nodes'));
  return at('nodes');
}

/** Which step blew up — per-partition / per-job detail when available. */
function failureStepIndex(kind: ModelKind, task: TaskItem, steps: Step[]): number {
  const last = Math.max(0, steps.length - 1);
  const res: any = task.result || null;
  const detail = detailOf(task);
  const at = (ref: string, fallback: number = last) => refStepIndex(steps, ref, fallback);
  if (kind === 'compute') {
    const parts: any[] = Array.isArray(res?.partitions) ? res.partitions : [];
    const bad = parts.find((p) => p?.failed);
    if (bad) return at(`p:${num(bad?.partition) ?? 0}`, at('p:0'));
  }
  if (kind === 'routine') {
    const jobs: any[] = Array.isArray(detail?.jobs) ? detail.jobs : [];
    if (jobs.some((j) => String(j?.state) === 'failed' || j?.timeout)) return at('parallel', last);
  }
  const stage = String(task.stage || '').toLowerCase();
  if (kind === 'diagnosis') {
    if (stage === 'server') return at('server', last);
    if (stage === 'worker') return at('worker', last);
    return at('submit');
  }
  if (kind === 'compute') return at('p:0', at('dispatch'));
  if (kind === 'sync') return prefixStepIndex(steps, 'peer:', at('manifest'));
  return at('parallel', last);
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

/**
 * Task ids are timestamp-prefixed (`YYYYMMDDHHMMSS` + uuid tail), so the PREFIX
 * carries the useful information and is what the picker shows.
 */
function idPrefix(id: unknown, n = 13): string {
  const v = String(id || '');
  if (!v) return '—';
  return v.length > n ? `${v.slice(0, n)}…` : v;
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

/** `2026091002150… · 运行中 · clinic-1 · 运行 4.20 s` — prefix first. */
function taskOptionLabel(t: TaskItem): string {
  const parts = [idPrefix(t.id, 13), statusLabel(t.status), t.source || '—'];
  const dur = durationText(t);
  if (dur && dur !== '—') parts.push(dur);
  return parts.join(' · ');
}

/** facts[0] is the headline result of the kind (shown as primary info). */
function factsFor(kind: ModelKind, task: TaskItem): { k: string; v: string }[] {
  const res: any = task.result || null;
  const detail: any = res?.result_detail || null;
  const out: { k: string; v: string }[] = [];

  if (kind === 'diagnosis') {
    const forwarded = res?.forwarded_to;
    const src = nodeForEntity(task.source);
    const p = detail?.bpCR_probability;
    out.push({ k: 'bpCR 概率', v: typeof p === 'number' ? p.toFixed(4) : '—' });
    out.push({ k: '转诊', v: forwarded ? `→ ${forwarded}` : isClinicNode(src) ? '未转诊' : '本院执行' });
    out.push({ k: '推理 / 融合', v: `${fmtMs(detail?.worker_latency_ms)} / ${fmtMs(detail?.server_latency_ms)}` });
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
    out.push({ k: '数据中心备份', v: backup?.backup_id ? String(backup.backup_id) : '—' });
    out.push({ k: '对端', v: peers.length ? peers.join(' · ') : '—' });
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

const STEP_MS = 2000;
const DWELL_MS = 2400;
const MIN_STEP_MS = 1000;
const MAX_STEP_MS = 3500;

/**
 * Autoplay dwell for one step, and the duration of its progress bar: a step
 * whose real cost is known (parallel steps use the MAX of their items) slows the
 * demo down / speeds it up within sane bounds; unknown steps use STEP_MS.
 */
function autoplayDelay(step: Step | undefined, isLast: boolean): number {
  if (isLast) return DWELL_MS;
  const d = step?.durationMs;
  if (d === undefined) return STEP_MS;
  return Math.min(MAX_STEP_MS, Math.max(MIN_STEP_MS, Math.round(d)));
}

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
// 多任务并发（调度态势）：同一份 tasks prop，不新增接口
// ---------------------------------------------------------------------------
/** 后端 scheduler MAX_CONCURRENT 默认值 */
const CONCURRENCY_MAX = 4;
const WINDOW_MAX_MS = 10 * 60 * 1000;
const LANE_ROWS_MAX = 12;
/** 窗口尾部留白（占窗口比例，避免最长条贴边） */
const WINDOW_TAIL_RATIO = 0.04;

type LaneTier = 'medical' | 'hospital' | 'cloud';

interface LaneRow {
  id: string;
  kind: ModelKind;
  source: string;
  sourceName: string;
  tier: LaneTier;
  status: string;
  stateClass: 'running' | 'done' | 'failed';
  priority: number;
  left: number;
  width: number;
  startMs: number;
  endMs: number;
}

interface LaneModel {
  rows: LaneRow[];
  queued: LaneRow[];
  spanMs: number;
  windowStart: number;
  total: number;
  running: number;
  /** 全部任务都在 10 分钟之前且没有进行中任务 → 窗口退化为“最近任务区间” */
  recentOnly: boolean;
}

function tierOfSource(source: string): LaneTier {
  if (source.startsWith('clinic')) return 'hospital';
  if (source.startsWith('hospital')) return 'medical';
  return 'cloud';
}

function hhmm(ms: number): string {
  return new Date(ms).toLocaleTimeString('zh-CN', { hour12: false, hour: '2-digit', minute: '2-digit' });
}

/**
 * 并发窗口：始终展示最近任务（最多 12 条）。
 * 起点 = min(now - 10min, 最早一条 start_time)；终点 = max(now（若有运行中）, 最晚一条 end) + 留白。
 * 若全部任务都在 10 分钟之前且无进行中任务，则退化为“最近任务区间”。
 */
function buildLanes(list: TaskItem[], nowMs: number): LaneModel {
  const all = (Array.isArray(list) ? list : []).filter((t) => t && t.id);
  const parsed = all
    .map((t) => {
      const startMs = Date.parse(String(t.start_time || ''));
      const endRaw = Date.parse(String(t.end_time || ''));
      return {
        t,
        startMs: Number.isFinite(startMs) ? startMs : nowMs,
        endMs: Number.isFinite(endRaw) ? endRaw : NaN,
      };
    })
    .filter((x) => Number.isFinite(x.startMs))
    .sort((a, b) => b.startMs - a.startMs);      // 最近优先

  const anyRunning = all.some((t) => String(t.status || '') === 'running');
  const recent = parsed.slice(0, LANE_ROWS_MAX);
  if (!recent.length) {
    return { rows: [], queued: [], spanMs: WINDOW_MAX_MS, windowStart: nowMs - WINDOW_MAX_MS, total: 0, running: 0, recentOnly: false };
  }

  const earliest = Math.min(...recent.map((x) => x.startMs));
  const latestEnd = Math.max(
    ...recent.map((x) => (Number.isFinite(x.endMs) ? x.endMs : String(x.t.status || '') === 'running' ? nowMs : x.startMs)),
    anyRunning ? nowMs : 0,
  );
  const windowStart = Math.min(nowMs - WINDOW_MAX_MS, earliest);
  const rawSpan = Math.max(30000, latestEnd - windowStart);
  const spanMs = Math.round(rawSpan * (1 + WINDOW_TAIL_RATIO));

  const toRow = (x: { t: TaskItem; startMs: number; endMs: number }): LaneRow => {
    const status = String(x.t.status || '');
    const failed = status === 'failed';
    const endMs = status === 'running' || !Number.isFinite(x.endMs) ? nowMs : x.endMs;
    const clampedStart = Math.max(x.startMs, windowStart);
    const clampedEnd = Math.min(Math.max(endMs, clampedStart + 1000), windowStart + spanMs);
    const left = ((clampedStart - windowStart) / spanMs) * 100;
    const width = Math.max(1.6, ((clampedEnd - clampedStart) / spanMs) * 100);
    const src = String(x.t.source || '');
    return {
      id: String(x.t.id),
      kind: ((x.t.model as ModelKind) || 'diagnosis'),
      source: src,
      sourceName: src ? entityName(src) : '—',
      tier: tierOfSource(src),
      status,
      stateClass: failed ? 'failed' : status === 'running' ? 'running' : 'done',
      priority: Number.isFinite(Number(x.t.priority)) ? Number(x.t.priority) : 5,
      left: Math.max(0, Math.min(100, left)),
      width: Math.min(100 - Math.max(0, Math.min(100, left)), width),
      startMs: clampedStart,
      endMs: clampedEnd,
    };
  };

  const rows = recent.slice().sort((a, b) => a.startMs - b.startMs).map(toRow);

  const queued = all
    .filter((t) => String(t.status || '') === 'queued')
    .map((t) => {
      const src = String(t.source || '');
      return {
        id: String(t.id),
        kind: ((t.model as ModelKind) || 'diagnosis'),
        source: src,
        sourceName: src ? entityName(src) : '—',
        tier: tierOfSource(src),
        status: 'queued',
        stateClass: 'running' as const,
        priority: Number.isFinite(Number(t.priority)) ? Number(t.priority) : 5,
        left: 0,
        width: 0,
        startMs: Date.parse(String(t.start_time || '')) || nowMs,
        endMs: nowMs,
      };
    })
    .sort((a, b) => b.priority - a.priority || a.startMs - b.startMs);

  return {
    rows,
    queued,
    spanMs,
    windowStart,
    total: all.length,
    running: all.filter((t) => String(t.status || '') === 'running').length,
    recentOnly: !anyRunning && latestEnd < nowMs - WINDOW_MAX_MS,
  };
}

// ---------------------------------------------------------------------------
// 多任务同屏对比（多任务 tab）
// ---------------------------------------------------------------------------
/** Tab 状态：'multi' 与四种任务类型共用一套 seg 控件（只影响渲染分支） */
type TabKind = 'multi' | ModelKind;
const MULTI_TAB = 'multi';
/** 同屏对比的任务条数上限 */
const MULTI_MAX = 4;
/** 任务选择芯片行的候选条数上限 */
const MULTI_PICK_MAX = 6;
/**
 * 紧凑行：几何（NODE_BOX / 折行 / 端口 / 标签避让 / 标签避让盒）完全复用，行高 =
 * VB_H × 比例，由父级 viewBox 等比缩小整行 —— 节点标签 12px、副标签 8.5px 属于
 * 用户单位，缩小后仍是「按比例」的，因此比例只取决于可读性：
 * 0.75 ≈ 节点标签 9px / 副标签 6.4px；MULTI_ROW_MIN_H 是不可读下限（硬兜底）。
 * 多行叠放时整块可纵向滚动（.tmap-canvas.tmap-multi）。
 */
const MULTI_ROW_SCALE = 0.75;
const MULTI_ROW_MIN_H = 440;
const MULTI_ROW_H = clamp(Math.round(VB_H * MULTI_ROW_SCALE), MULTI_ROW_MIN_H, VB_H);

/** 任务 payload 的 model → 四种类型之一（未知值退化为诊断，避免索引越界）。 */
function taskKindOf(task: TaskItem): ModelKind {
  const m = String(task?.model || '');
  return (MODELS as string[]).includes(m) ? m as ModelKind : 'diagnosis';
}

/** 并发视图候选排序权重：运行中 → 排队 → 最近完成。 */
function multiRank(task: TaskItem): number {
  const s = String(task?.status || '').toLowerCase();
  if (s === 'running') return 0;
  if (s === 'queued') return 1;
  return 2;
}

/**
 * 多任务候选：运行中 / 排队优先，其次最近完成（sortTasks 已按开始时间倒序，
 * 稳定排序保证同一权重内仍是最近优先）。
 */
function multiCandidates(list: TaskItem[]): TaskItem[] {
  const all = (Array.isArray(list) ? list : []).filter((t) => t && t.id);
  return sortTasks(all)
    .sort((a, b) => multiRank(a) - multiRank(b))
    .slice(0, MULTI_PICK_MAX);
}

/** 默认同屏选择：运行中 / 排队优先，取前 MULTI_MAX 条。 */
function defaultMultiIds(list: TaskItem[]): string[] {
  return multiCandidates(list).slice(0, MULTI_MAX).map((t) => String(t.id));
}

/** 共享步进索引 → 归一化进度 0..1（单步任务恒为 0）。 */
function sharedProgress(index: number, span: number): number {
  return span > 1 ? clamp(index / (span - 1), 0, 1) : 0;
}

/** 归一化进度 → 某条任务自己的步骤游标（步数少的任务只会提前停在自己的末步）。 */
function sharedCursor(progress: number, stepsLen: number): number {
  return Math.round(clamp(progress, 0, 1) * Math.max(0, stepsLen - 1));
}

/** 多任务共用的播放状态：一个归一化进度驱动全部轨迹。 */
interface SharedPlay {
  index: number;
  mode: PlayMode;
}

/** 共享进度的 ⏮ / ◀ / ▶ / ▶自动 状态机（与单任务同义，只是操作共享刻度）。 */
function nextSharedState(
  state: SharedPlay,
  action: StepAction,
  last: number,
  canAuto: boolean,
): SharedPlay {
  const lastAt = Math.max(0, last);
  const at = clamp(state.index, 0, lastAt);
  switch (action) {
    case 'reset':
      return { index: 0, mode: 'manual' };
    case 'prev':
      return { index: Math.max(0, at - 1), mode: 'manual' };
    case 'next':
      return { index: Math.min(lastAt, at + 1), mode: 'manual' };
    default:
      if (!canAuto) return state;
      return state.mode === 'auto' ? { ...state, mode: 'manual' } : { index: 0, mode: 'auto' };
  }
}

// ---------------------------------------------------------------------------
// component
// ---------------------------------------------------------------------------
const TONES: Tone[] = ['ghost', 'past', 'current', 'pending', 'unrelated', 'failed'];

const LEGEND: { state: Tone; text: string }[] = [
  { state: 'past', text: '已执行' },
  { state: 'current', text: '当前' },
  { state: 'pending', text: '待执行' },
  { state: 'unrelated', text: '无关' },
  { state: 'failed', text: '失败' },
  { state: 'ghost', text: '无任务 · 标准路径' },
];

/** chip secondary text: parallel groups show both item count and MAX duration */
function stepMeta(s: Step): string {
  const n = s.items && s.items.length > 1 ? `${s.items.length} 项` : '';
  const d = s.durationMs !== undefined ? fmtMs(s.durationMs) : '';
  if (n && d) return `${n} · ${d}`;
  return n || d || s.detail || '';
}

const CIRCLED = ['①', '②', '③', '④', '⑤', '⑥', '⑦', '⑧', '⑨', '⑩',
  '⑪', '⑫', '⑬', '⑭', '⑮', '⑯', '⑰', '⑱', '⑲', '⑳'];

function circled(i: number): string {
  return CIRCLED[i] || `(${i + 1})`;
}

/**
 * ONE trajectory = one SVG. 单任务模式与多任务同屏对比共用同一段 SVG：
 * 几何、层序、四态着色、标签避让全部按 kind+task 现算（与游标无关，播放不重排）。
 *
 * - task   : 该行的真实任务（null → 只画标准路径）
 * - cursor : 该行自己的步骤游标（多任务时由共享进度换算而来），-1 表示未定位
 * - compact: 紧凑行（多任务）——省略流动光点，并交由父级 viewBox 等比缩放
 */
function TrajectoryGraph({
  task, kind, cursor, compact = false, reduced = false, running = 0, queued = 0,
}: {
  task: TaskItem | null;
  kind: ModelKind;
  cursor: number;
  compact?: boolean;
  reduced?: boolean;
  /** 数据中心面板上的全局并发计数 */
  running?: number;
  queued?: number;
}) {
  const view = useMemo(() => buildView(kind, task), [kind, task]);
  const steps = useMemo(
    () => buildSteps(kind, task, view.ctx || CANON[kind]),
    [kind, task, view],
  );
  const lastStep = Math.max(0, steps.length - 1);
  const at = task ? Math.min(Math.max(-1, cursor), lastStep) : -1;
  const failed = String(task?.status || '').toLowerCase() === 'failed';

  const stepTones = useMemo<Tone[]>(() => {
    if (!task || !steps.length) return [];
    return steps.map((_, i) => {
      if (i === at) return failed ? 'failed' : 'current';
      return i < at ? 'past' : 'pending';
    });
  }, [steps, at, task, failed]);

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
      if (!task) { m.set(e.key, 'ghost'); return; }
      const i = edgeStep.get(e.key);
      if (i === undefined) { m.set(e.key, e.live ? 'pending' : 'unrelated'); return; }
      m.set(e.key, stepTones[i] || 'pending');
    });
    return m;
  }, [view, task, edgeStep, stepTones]);

  const nodeTone = useMemo(() => {
    const m = new Map<string, NodeState>();
    const curTone: NodeState = stepTones[at] || 'current';
    view.nodeIds.forEach((id) => {
      if (!task) { m.set(id, 'ghost'); return; }
      let past = false;
      let cur = false;
      let pend = false;
      steps.forEach((s, i) => {
        if (!s.nodes.includes(id)) return;
        if (i === at) cur = true;
        else if (i < at) past = true;
        else pend = true;
      });
      if (cur) m.set(id, curTone);
      else if (past) m.set(id, 'past');
      else if (pend) m.set(id, 'pending');
      else m.set(id, 'unrelated');
    });
    return m;
  }, [view, steps, at, stepTones, task]);

  const toneClass = (t: Tone) => `tm-edge ${t}`;
  const labelOf = (id: string): { name: string; id?: string; caption: string } => {
    if (isJob(id)) {
      const node = jobNodeOf(id);
      const job = view.jobs.find((j) => j.id === id);
      const extra = job && (job.count || 1) > 1 ? ` ×${job.count}` : '';
      return { name: job?.name ? `…${job.name}` : 'Job', caption: `${node}${extra}` };
    }
    return NODE_META[id] || { name: id, caption: '' };
  };

  // only the current step's edges carry the travelling dot (dropped in compact rows)
  const dotEdges = useMemo(() => {
    if (compact || reduced || !task || at < 0) return [];
    const cur = steps[at];
    if (!cur) return [];
    return view.edges.filter((e) => cur.edges.includes(e.key)).slice(0, 3);
  }, [compact, reduced, view, steps, at, task]);

  const curStep = at >= 0 ? steps[at] : null;
  const dcLive = !!curStep && curStep.nodes.some((n) => DC_NODE_SET.has(n));

  return (
    <svg
      className={compact ? 'tmap-svg tmg-svg' : 'tmap-svg'}
      viewBox={`0 0 ${VB_W} ${VB_H}`}
      width="100%"
      role="img"
      aria-label={`${MODEL_LABEL[kind]} 任务执行轨迹图`}
    >
      <title>{`${MODEL_LABEL[kind]} 任务执行轨迹`}</title>
      {/* 多任务同屏时每个 SVG 都会定义同名 marker：定义完全一致（用户单位、几何都取自
          同一套常量），文档级 id 解析到任意一份都是同一个箭头。 */}
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
        {/* 数据中心容器：包含 scheduler + medical-server + dc-services + redis；
            连线垂直穿越其发丝边框，所有折行都落在容器之外。 */}
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
            {TIER_TEXT.cloud}
          </text>
          <text
            className="tm-dc-meta mono"
            x={DC_PANEL.x + DC_PANEL.w - 16}
            y={DC_PANEL.y + 21}
            textAnchor="end"
          >
            {`组件 ${DC_NODES.length} · 运行中 ${running} · 排队 ${queued}`}
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
            key={t.text}
            className="tm-tier mono"
            x="44"
            y={t.y}
            textAnchor="middle"
            transform={`rotate(-90 44 ${t.y})`}
          >
            {t.text}
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
                <title>{`日常任务 Job · ${meta.name}（${meta.caption}）`}</title>
                {ringed && <circle className="tm-ring" cx={b.x} cy={b.y} r={r + 5} />}
                <circle className="tm-box job-box" cx={b.x} cy={b.y} r={r} />
                <g className="tm-icon tm-icon-sm" transform={`translate(${b.x - 6} ${b.y - 17}) scale(0.5)`}>
                  {iconShapes('job')}
                </g>
                <text className="tm-node-name" x={b.x} y={b.y + 8} textAnchor="middle">{meta.name}</text>
                <text className="tm-node-cap" x={b.x} y={b.y + 20} textAnchor="middle">{meta.caption}</text>
              </g>
            );
          }

          const mx = b.x - b.w / 2 + MEDAL_INSET;
          const tx = b.x - b.w / 2 + TEXT_INSET;
          return (
            <g key={id} className={['tm-node', focused ? 'active' : '', state].filter(Boolean).join(' ')}>
              <title>{`${meta.name}${meta.id && meta.id !== meta.name ? `（${meta.id}）` : ''} · ${meta.caption}`}</title>
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
  );
}

export default function TaskTrajectoryMap({
  tasks, initialKind = 'diagnosis',
}: {
  /** latest tasks from GET /tasks (ArchitecturePage polls it on its ticker) */
  tasks?: TaskItem[];
  /** kind selected on first paint (deep-link / test hook) */
  initialKind?: ModelKind;
}) {
  const [kind, setKind] = useState<TabKind>(initialKind);
  const [selId, setSelId] = useState<string | null>(null);
  const [autoFollow, setAutoFollow] = useState(true);
  const [play, setPlay] = useState<PlayState>({ id: '', index: 0, mode: 'auto' });
  // 多任务：null = 默认选择（运行 / 排队优先）；[] = 用户手动清空
  const [multiPicked, setMultiPicked] = useState<string[] | null>(null);
  const [multiPlay, setMultiPlay] = useState<SharedPlay>({ index: 0, mode: 'auto' });
  const [multiHint, setMultiHint] = useState('');
  const reduced = usePrefersReducedMotion();

  const multi = kind === MULTI_TAB;
  // 单任务轨道的类型：多任务模式下不渲染它，退化为默认类型以避免索引越界
  const singleKind: ModelKind = kind === MULTI_TAB ? 'diagnosis' : kind;

  const kindTasks = useMemo(() => {
    const list = (Array.isArray(tasks) ? tasks : []).filter((t) => t && t.model === singleKind);
    return sortTasks(list).slice(0, 6);
  }, [tasks, singleKind]);

  // 同类型的完整列表（下拉只展示最近 6 条，但已选任务可能更早）
  const kindAll = useMemo(() => {
    const list = (Array.isArray(tasks) ? tasks : []).filter((t) => t && t.model === singleKind);
    return sortTasks(list);
  }, [tasks, singleKind]);

  const traced = useMemo(() => {
    if (autoFollow) return kindTasks[0] || null;
    if (selId) {
      const found = kindAll.find((t) => t.id === selId)
        || (Array.isArray(tasks) ? tasks : []).find((t) => t && t.id === selId);
      if (found) return found;
    }
    return kindTasks[0] || null;
  }, [kindTasks, kindAll, autoFollow, selId, tasks]);

  // 下拉选项：最近 6 条；若当前追踪的任务更早，则额外附上一条
  const options = useMemo(() => {
    const list = [...kindTasks];
    if (traced && !list.some((t) => t.id === traced.id)) list.push(traced);
    return list;
  }, [kindTasks, traced]);

  // 单任务轨道：上下文只依赖类型 + 任务；几何由 TrajectoryGraph 现算（与游标无关）
  const singleCtx = useMemo(
    () => (traced ? ctxFromTask(singleKind, traced) : null),
    [singleKind, traced],
  );

  // ordered execution timeline — real task when available, canonical otherwise
  const steps = useMemo(
    () => buildSteps(singleKind, traced, singleCtx || CANON[singleKind]),
    [singleKind, traced, singleCtx],
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
    () => (traced && stepsLen ? stageStepIndex(singleKind, traced, steps) : 0),
    [singleKind, traced, steps, stepsLen],
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
  // （多任务模式由共享进度的定时器驱动，这里不参与）
  useEffect(() => {
    if (multi || active.mode !== 'auto' || stepsLen < 2 || reduced) return undefined;
    const delay = autoplayDelay(steps[active.index], active.index >= lastStep);
    const id = window.setTimeout(() => {
      setPlay((s) => {
        if (s.mode !== 'auto') return s;
        return { ...s, index: s.index >= lastStep ? 0 : s.index + 1 };
      });
    }, delay);
    return () => window.clearTimeout(id);
  }, [multi, active, stepsLen, lastStep, reduced, steps]);

  // ---- step state map（时序条与 TrajectoryGraph 共用同一套四态语义） ----
  const stepTones = useMemo<Tone[]>(() => {
    if (!traced || !stepsLen) return [];
    const failed = String(traced.status || '').toLowerCase() === 'failed';
    return steps.map((_, i) => {
      if (i === cursor) return failed ? 'failed' : 'current';
      return i < cursor ? 'past' : 'pending';
    });
  }, [steps, stepsLen, cursor, traced]);

  // ---- 多任务同屏对比 ----
  const allTasks = useMemo(
    () => (Array.isArray(tasks) ? tasks : []).filter((t) => t && t.id),
    [tasks],
  );
  const multiCand = useMemo(() => multiCandidates(allTasks), [allTasks]);
  // 已选任务：默认取「运行 / 排队优先」，用户点选后完全跟随点选顺序
  const multiSel = useMemo(() => {
    if (multiPicked === null) return multiCand.slice(0, MULTI_MAX);
    const byId = new Map(allTasks.map((t) => [String(t.id), t]));
    return multiPicked
      .map((id) => byId.get(id))
      .filter((t): t is TaskItem => !!t)
      .slice(0, MULTI_MAX);
  }, [multiPicked, multiCand, allTasks]);
  // 每条轨迹自己的步骤（共享进度的刻度 = 其中最长的一条）
  const multiRows = useMemo(() => multiSel.map((task) => {
    const k = taskKindOf(task);
    return { task, kind: k, steps: buildSteps(k, task, ctxFromTask(k, task) || CANON[k]) };
  }), [multiSel]);
  const multiSpan = useMemo(
    () => Math.max(1, ...multiRows.map((r) => r.steps.length)),
    [multiRows],
  );
  const multiLast = Math.max(0, multiSpan - 1);
  const multiIndex = clamp(multiPlay.index, 0, multiLast);
  const multiProg = sharedProgress(multiIndex, multiSpan);
  /** 共享进度 → 某条轨迹自己的游标 */
  const multiCursorAt = (len: number) => sharedCursor(multiProg, len);
  // 基准轨迹：步数最多的一条（它的每一步就是共享刻度）
  const multiRef = useMemo(
    () => multiRows.reduce<typeof multiRows[number] | null>(
      (best, r) => (!best || r.steps.length > best.steps.length ? r : best), null,
    ),
    [multiRows],
  );
  const multiRefSteps = multiRef?.steps || [];
  const multiRefFailed = String(multiRef?.task.status || '').toLowerCase() === 'failed';
  const multiTotalMs = useMemo(() => {
    const known = multiRefSteps.map((s) => s.durationMs).filter((d): d is number => typeof d === 'number');
    return known.length ? known.reduce((a, b) => a + b, 0) : undefined;
  }, [multiRefSteps]);
  /** 每个共享刻度上停着几条轨迹（时序条上体现共享进度） */
  const multiHits = useMemo(() => {
    const arr = new Array(multiSpan).fill(0);
    multiRows.forEach((r) => { arr[sharedCursor(multiProg, r.steps.length)] += 1; });
    return arr;
  }, [multiRows, multiSpan, multiProg]);
  const multiDelay = autoplayDelay(multiRefSteps[multiIndex], multiIndex >= multiLast);

  // 多任务的自动播放：与单任务同一节奏（每步 2s / 末步 2.4s，末步后回到 ①）
  useEffect(() => {
    if (!multi || multiPlay.mode !== 'auto' || multiSpan < 2 || reduced) return undefined;
    const t = window.setTimeout(() => {
      setMultiPlay((s) => (s.mode !== 'auto' ? s : { ...s, index: s.index >= multiLast ? 0 : s.index + 1 }));
    }, multiDelay);
    return () => window.clearTimeout(t);
  }, [multi, multiPlay.mode, multiIndex, multiLast, multiSpan, multiDelay, reduced]);

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

  // ---- 多任务交互：选择 / 共享播放 / 放大到单任务 ----
  const toggleMulti = (id: string) => {
    const base = multiPicked === null ? defaultMultiIds(allTasks) : multiPicked;
    if (base.includes(id)) {
      setMultiPicked(base.filter((x) => x !== id));
      setMultiHint('');
      return;
    }
    if (base.length >= MULTI_MAX) {
      setMultiHint(`同屏上限 ${MULTI_MAX} 条 · 先取消一条再加入 ${idPrefix(id, 10)}`);
      return;
    }
    setMultiPicked([...base, id]);
    setMultiHint('');
  };
  const multiCanStep = multiRows.length > 0 && multiSpan > 0;
  const multiCanAuto = multiCanStep && multiSpan >= 2 && !reduced;
  const applyMulti = (action: StepAction) => {
    if (!multiCanStep) return;
    setMultiPlay((s) => nextSharedState(s, action, multiLast, multiCanAuto));
  };
  const jumpMulti = (i: number) => {
    if (!multiCanStep) return;
    setMultiPlay({ index: clamp(i, 0, multiLast), mode: 'manual' });
  };
  /** 放大到单任务：切到该任务的类型页并选中它（多任务 tab 仍可切回） */
  const focusTask = (id: string, k: ModelKind) => {
    setKind(k);
    setSelId(id);
    setAutoFollow(false);
  };

  // 多任务并发：最近 5–10 分钟窗口 + 调度队列
  const lanes = useMemo(() => buildLanes(Array.isArray(tasks) ? tasks : [], Date.now()), [tasks]);
  const ticks = useMemo(() => [0, 0.25, 0.5, 0.75, 1].map((p) => ({
    p,
    label: hhmm(lanes.windowStart + lanes.spanMs * p),
  })), [lanes.windowStart, lanes.spanMs]);
  /** 态势条点击：多任务模式下改为「加入 / 移出」同屏对比 */
  const pickTask = (row: LaneRow) => {
    if (multi) { toggleMulti(row.id); return; }
    focusTask(row.id, row.kind);
  };

  const totalMs = useMemo(() => {
    const known = steps.map((s) => s.durationMs).filter((d): d is number => typeof d === 'number');
    return known.length ? known.reduce((a, b) => a + b, 0) : undefined;
  }, [steps]);

  const curStep = cursor >= 0 ? steps[cursor] : null;
  const hasParallel = useMemo(() => steps.some((st) => !!st.items && st.items.length > 1), [steps]);
  const allFacts = useMemo(() => (traced ? factsFor(singleKind, traced) : []), [singleKind, traced]);
  const leadFact = allFacts[0];
  const restFacts = allFacts.slice(1);

  // ---- 底部时序条的取值：多任务时以基准轨迹为刻度，游标即共享进度 ----
  const tlSteps = multi ? multiRefSteps : steps;
  const tlCursor = multi ? multiIndex : cursor;
  const tlMode: PlayMode = multi ? multiPlay.mode : active.mode;
  const tlCanStep = multi ? multiCanStep : canStep;
  const tlCanAuto = multi ? multiCanAuto : canAuto;
  const tlCurStep = multi ? (multiRefSteps[multiIndex] || null) : curStep;
  const tlHasParallel = multi
    ? multiRefSteps.some((st) => !!st.items && st.items.length > 1)
    : hasParallel;
  const tlTones = useMemo<Tone[]>(() => {
    if (!multi) return stepTones;
    return multiRefSteps.map((_, i) => {
      if (i === tlCursor) return multiRefFailed ? 'failed' : 'current';
      return i < tlCursor ? 'past' : 'pending';
    });
  }, [multi, stepTones, multiRefSteps, tlCursor, multiRefFailed]);
  const tlTotalMs = multi ? multiTotalMs : totalMs;
  const onTlReset = () => (multi ? applyMulti('reset') : onReset());
  const onTlPrev = () => (multi ? applyMulti('prev') : onPrev());
  const onTlNext = () => (multi ? applyMulti('next') : onNext());
  const onTlAuto = () => (multi ? applyMulti('auto') : onAuto());
  const onTlJump = (i: number) => (multi ? jumpMulti(i) : jumpTo(i));

  const dcSummary = useMemo(() => {
    let running = 0;
    let queued = 0;
    allTasks.forEach((t) => {
      const st = String(t?.status || '').toLowerCase();
      if (st === 'running') running += 1;
      else if (st === 'queued') queued += 1;
    });
    return { running, queued };
  }, [allTasks]);
  const multiSelectedIds = useMemo(
    () => new Set(multiRows.map((r) => String(r.task.id))),
    [multiRows],
  );
  const multiSummary = `${multiRows.length} 条 · 运行中 ${multiRows.filter((r) => String(r.task.status || '') === 'running').length} · 排队 ${multiRows.filter((r) => String(r.task.status || '') === 'queued').length}`;


  return (
    <section className="card tmap">
      <div className="card-headrow">
        <h3 className="card-title">任务执行轨迹</h3>
        <span className="muted xs mono">
          {multi
            ? `多任务 · 已选 ${multiRows.length} 条`
            : traced
              ? `${MODEL_LABEL[singleKind]} · ${shortId(traced.id)} · ${statusLabel(traced.status)}`
              : `${MODEL_LABEL[singleKind]} · 标准路径`}
        </span>
      </div>

      <p className="eyebrow tmap-caption">
        数据中心 / 医疗中心 / 医院 三层拓扑按时间顺序回放 —— 可用「上一步 / 下一步」手动逐步，
        或「▶ 自动」循环演示；只有当前步骤为橙色高亮，已执行为灰色、待执行为淡灰虚线、无关链路最淡
      </p>

      <div className="tmap-controls">
        <div className="seg tmap-tabs" role="tablist" aria-label="任务类型">
          <button
            type="button"
            role="tab"
            aria-selected={multi}
            className={`seg-btn ${multi ? 'on' : ''}`}
            onClick={() => setKind(MULTI_TAB)}
            title="多任务：在同一块画布上同屏对比多条任务的执行轨迹"
          >
            <svg className="tmg-glyph" viewBox="0 0 12 12" aria-hidden="true">
              <rect x="1.4" y="1.4" width="6.4" height="6.4" rx="1" />
              <rect x="4.2" y="4.2" width="6.4" height="6.4" rx="1" />
            </svg>
            多任务
          </button>
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

        <div className="tmap-pickrow">
          <span className="eyebrow">任务选择</span>
          {multi ? (
            <>
              <span className="tmg-chips" role="group" aria-label="选择要同屏对比的任务">
                {multiCand.length === 0 && <i className="tmg-hint">暂无任务可选</i>}
                {multiCand.map((t) => {
                  const k = taskKindOf(t);
                  const on = multiSelectedIds.has(String(t.id));
                  const full = multiRows.length >= MULTI_MAX;
                  return (
                    <button
                      key={t.id}
                      type="button"
                      className={`tmg-chip ${on ? 'on' : ''} ${!on && full ? 'dim' : ''}`}
                      aria-pressed={on}
                      onClick={() => toggleMulti(String(t.id))}
                      title={`${t.id} · ${MODEL_LABEL[k]} · ${entityName(t.source)} · ${statusLabel(t.status)}`}
                    >
                      <i className="tmg-kind" style={{ background: MODEL_COLOR[k] }} />
                      <b className="tmg-chip-kind">{MODEL_LABEL[k]}</b>
                      <i className="tmg-chip-id mono">{idPrefix(t.id, 10)}</i>
                      <span className="tmg-chip-st">{statusLabel(t.status)}</span>
                    </button>
                  );
                })}
              </span>
              <span className="tmg-count mono">{`已选 ${multiRows.length}/${MULTI_MAX}`}</span>
              {multiHint && <i className="tmg-hint">{multiHint}</i>}
              <button
                type="button"
                className="mini"
                onClick={() => { setMultiPicked(null); setMultiHint(''); }}
                title={`恢复默认选择：运行中 / 排队优先，最多 ${MULTI_MAX} 条`}
              >
                恢复默认
              </button>
            </>
          ) : (
            <>
              <button
                type="button"
                className={`mini ${autoFollow ? 'on' : ''}`}
                aria-pressed={autoFollow}
                onClick={() => setAutoFollow((v) => !v)}
                title="新提交的任务自动成为跟踪目标"
              >
                自动跟随最新
              </button>
              <select
                className="tmap-select mono"
                aria-label="选择要跟踪的任务"
                value={traced?.id || ''}
                disabled={!options.length}
                onChange={(e) => { setSelId(e.target.value); setAutoFollow(false); }}
              >
                {options.length === 0 && <option value="">该类型暂无任务记录</option>}
                {options.map((t) => (
                  <option key={t.id} value={t.id} title={t.id}>{taskOptionLabel(t)}</option>
                ))}
              </select>
            </>
          )}
        </div>
      </div>

      <div className="tmap-info">
        {multi ? (
          allTasks.length === 0 ? (
            <div className="tmap-none">
              <b>暂无任务</b>
              <span>· 显示标准执行路径</span>
            </div>
          ) : (
            <>
              <div className="tmi-lead">
                <span className="badge s-multi">多任务</span>
                <b className="tmi-step">
                  {!multiRows.length
                    ? '未选择任务'
                    : `${circled(multiIndex)} ${multiRefSteps[multiIndex]?.label || '未定位到步骤'}`}
                </b>
                <span className="tmi-dur mono">{`已选 ${multiSummary}`}</span>
                <span className="tmi-lead-fact">
                  <i className="tmi-k">共享进度</i>
                  <b className="tmi-v mono">{`${Math.round(multiProg * 100)}%`}</b>
                </span>
              </div>
              <div className="tmi-meta">
                <span className="tmi-cell">
                  <i className="tmi-k">同屏任务</i>
                  <b className="tmi-v mono">{`${multiRows.length} / ${MULTI_MAX} 条`}</b>
                </span>
                <span className="tmi-cell">
                  <i className="tmi-k">共享步进</i>
                  <b className="tmi-v mono">{tlSteps.length ? `${multiIndex + 1} / ${multiSpan}` : '0 / 0'}</b>
                </span>
                <span className="tmi-cell" title="步数最多的任务，作为共享进度的刻度">
                  <i className="tmi-k">基准任务</i>
                  <b className="tmi-v mono">{multiRef ? `${idPrefix(multiRef.task.id, 13)} · ${MODEL_LABEL[multiRef.kind]}` : '—'}</b>
                </span>
                <span className="tmi-cell">
                  <i className="tmi-k">任务总数</i>
                  <b className="tmi-v mono">{`${allTasks.length} 条`}</b>
                </span>
              </div>
            </>
          )
        ) : traced ? (
          <>
            <div className="tmi-lead">
              <span className={`badge s-${String(traced.status || '')}`}>{statusLabel(traced.status)}</span>
              <b className="tmi-step">
                {curStep ? `${circled(cursor)} ${curStep.label}` : '未定位到步骤'}
              </b>
              <span className="tmi-dur mono">{durationText(traced)}</span>
              {leadFact && (
                <span className="tmi-lead-fact">
                  <i className="tmi-k">{leadFact.k}</i>
                  <b className="tmi-v mono">{leadFact.v}</b>
                </span>
              )}
            </div>
            <div className="tmi-meta">
              <span className="tmi-cell" title={traced.id}>
                <i className="tmi-k">任务ID</i>
                <b className="tmi-v mono">{idPrefix(traced.id, 16)}</b>
              </span>
              <span className="tmi-cell">
                <i className="tmi-k">发起方</i>
                <b className="tmi-v">{traced.source || '—'}</b>
                <i className="tmi-sub">{SOURCE_ROLE[String(traced.source || '')] || '?'}</i>
              </span>
              <span className="tmi-cell">
                <i className="tmi-k">阶段</i>
                <b className="tmi-v">{STAGE_TEXT[String(traced.stage || '')] || String(traced.stage || '—')}</b>
              </span>
              {restFacts.map((f) => (
                <span className="tmi-cell" key={f.k} title={`${f.k} ${f.v}`}>
                  <i className="tmi-k">{f.k}</i>
                  <b className="tmi-v mono">{f.v}</b>
                </span>
              ))}
            </div>
          </>
        ) : (
          <div className="tmap-none">
            <b>暂无任务</b>
            <span>· 显示标准执行路径</span>
          </div>
        )}
      </div>

      <div className="tmap-conc">
        <div className="tmc-head">
          <span className="eyebrow">多任务并发 · 调度态势</span>
          {lanes.recentOnly && <i className="tmc-flag">最近任务（无进行中任务）</i>}
          <span className="tmc-meta mono">
            {lanes.total > LANE_ROWS_MAX ? `最近 ${LANE_ROWS_MAX} 条 / 共 ${lanes.total} 条` : `任务 ${lanes.total} 条`}
            {` · 运行中 ${lanes.running}/${CONCURRENCY_MAX}`}
          </span>
        </div>

        <div className="tmc-body">
          <div className="tmc-lanes">
            {lanes.rows.length === 0 ? (
              <div className="tmc-empty xs muted">暂无任务记录</div>
            ) : (
              <>

            <div className="tmc-axis" aria-hidden="true">
              {ticks.map((tk) => (
                <i key={tk.p} className="tmc-tick" style={{ left: `${tk.p * 100}%` }}>
                  {tk.label}
                </i>
              ))}
            </div>
            {lanes.rows.map((r) => (
              <button
                key={r.id}
                type="button"
                className={`tmc-row ${(multi ? multiSelectedIds.has(String(r.id)) : r.id === tracedId) ? 'on' : ''}`}
                onClick={() => pickTask(r)}
                title={`${r.id} · ${MODEL_LABEL[r.kind]} · ${r.sourceName}（${r.source}）· ${statusLabel(r.status)} · P${r.priority}`}
              >
                <span className="tmc-label">
                  <i className="tmc-kind" style={{ background: MODEL_COLOR[r.kind] }} />
                  <b>{MODEL_LABEL[r.kind]}</b>
                  <span className="tmc-src">{r.sourceName}</span>
                  <i className="tmc-id mono">{idPrefix(r.id, 11)}</i>
                </span>
                <span className="tmc-track">
                  <i
                    className={`tmc-bar ${r.stateClass}`}
                    style={{ left: `${r.left}%`, width: `${r.width}%`, background: MODEL_COLOR[r.kind] }}
                  />
                  <i className={`tmc-mark tier-${r.tier} ${r.stateClass}`} style={{ left: `${r.left}%` }} />
                </span>
                <span className={`tmc-p mono ${r.priority <= 2 ? 'high' : ''}`}>{`P${r.priority}`}</span>
              </button>
            ))}
            <div className="tmc-shapes xs muted">
              <i className="tmc-mark tier-medical" />医疗中心
              <i className="tmc-mark tier-hospital" />医院
              <i className="tmc-mark tier-cloud" />其它
              <i className="tmc-bar state-running" />运行中
              <i className="tmc-bar state-done" />已完成
              <i className="tmc-bar state-failed" />失败
            </div>
              </>
            )}
          </div>

<div className="tmc-queue">
            <div className="tmc-queue-head">
              <span className="eyebrow">调度队列</span>
              <span className="mono xs">并发上限 {CONCURRENCY_MAX}</span>
            </div>
            {lanes.queued.length === 0 ? (
              <div className="tmc-empty xs muted">队列为空 · 可立即调度</div>
            ) : (
              lanes.queued.slice(0, 8).map((q, i) => (
                <button
                  key={q.id}
                  type="button"
                  className={`tmc-q ${(multi ? multiSelectedIds.has(String(q.id)) : q.id === tracedId) ? 'on' : ''}`}
                  onClick={() => pickTask(q)}
                  title={`${q.id} · ${MODEL_LABEL[q.kind]} · ${q.sourceName}（${q.source}）· P${q.priority}`}
                >
                  <i className="tmc-q-no mono">{i + 1}</i>
                  <i className="tmc-kind" style={{ background: MODEL_COLOR[q.kind] }} />
                  <b>{MODEL_LABEL[q.kind]}</b>
                  <span className="tmc-src">{q.sourceName}</span>
                  <i className="tmc-id mono">{idPrefix(q.id, 9)}</i>
                  <i className={`tmc-p mono ${q.priority <= 2 ? 'high' : ''}`}>{`P${q.priority}`}</i>
                </button>
              ))
            )}
            <div className="tmc-note xs muted">
              优先级降序调度（同级先到先服务）· 并发上限 {CONCURRENCY_MAX}，运行中 {lanes.running}
            </div>
          </div>
        </div>
      </div>

      <div className={multi ? 'tmap-canvas tmap-multi' : 'tmap-canvas'}>
        {multi ? (
          multiRows.length ? (
            multiRows.map((r) => {
              const at = multiCursorAt(r.steps.length);
              const step = r.steps[at];
              return (
                <div className="tmg-row" key={r.task.id}>
                  <button
                    type="button"
                    className="tmg-head"
                    onClick={() => focusTask(String(r.task.id), r.kind)}
                    title={`放大到「${idPrefix(r.task.id, 13)}」的单任务轨迹（${MODEL_LABEL[r.kind]}）`}
                  >
                    <i className="tmg-kind" style={{ background: MODEL_COLOR[r.kind] }} />
                    <b className="tmg-kindlab">{MODEL_LABEL[r.kind]}</b>
                    <i className="tmg-id mono">{idPrefix(r.task.id, 13)}</i>
                    <span className="tmg-src">{entityName(r.task.source)}</span>
                    <span className={`badge s-${String(r.task.status || '')}`}>{statusLabel(r.task.status)}</span>
                    <i className="tmg-dur mono">{durationText(r.task)}</i>
                    <b className="tmg-cur">{step ? `${circled(at)} ${step.label}` : '未定位到步骤'}</b>
                  </button>
                  {/* 每行一个 SVG：几何与单任务完全同源，整体由 viewBox 等比缩小 */}
                  <div className="tmg-figure" style={{ height: MULTI_ROW_H }}>
                    <TrajectoryGraph
                      task={r.task}
                      kind={r.kind}
                      cursor={at}
                      compact
                      reduced={reduced}
                      running={dcSummary.running}
                      queued={dcSummary.queued}
                    />
                  </div>
                </div>
              );
            })
          ) : (
            <div className="tmg-empty xs muted">
              暂无任务 · 提交任务后可在同一块画布上对比多条执行轨迹
            </div>
          )
        ) : (
          <TrajectoryGraph
            task={traced}
            kind={singleKind}
            cursor={cursor}
            reduced={reduced}
            running={dcSummary.running}
            queued={dcSummary.queued}
          />
        )}
      </div>

      <div className="tmap-timeline">
        <div className="tm-tl-head">
          <span className="tm-tl-title">
            <span className="eyebrow">执行时序</span>
            {tlMode === 'auto' && !reduced && (
              <i className="tm-loop mono">循环演示</i>
            )}
            {multi && multiRef && (
              <i className="tm-tl-base mono" title="步数最多的任务，作为共享进度的刻度">
                {`基准 ${idPrefix(multiRef.task.id, 13)} · ${multiSpan} 步`}
              </i>
            )}
          </span>
          <span className="tm-tl-ctl">
            <button
              type="button"
              className="mini"
              onClick={onTlReset}
              disabled={!tlCanStep}
              title="回到第 ① 步"
            >
              ⏮ 重置
            </button>
            <button
              type="button"
              className="mini"
              onClick={onTlPrev}
              disabled={!tlCanStep || tlCursor <= 0}
              title="上一步（切换到手动）"
            >
              ◀ 上一步
            </button>
            <button
              type="button"
              className="mini"
              onClick={onTlNext}
              disabled={!tlCanStep || tlCursor >= tlSteps.length - 1}
              title="下一步（切换到手动）"
            >
              下一步 ▶
            </button>
            <button
              type="button"
              className={`mini ${tlMode === 'auto' ? 'on' : ''}`}
              onClick={onTlAuto}
              disabled={!tlCanStep || tlSteps.length < 2 || reduced}
              aria-pressed={tlMode === 'auto'}
              title={reduced ? '已启用「减少动态效果」，自动循环关闭' : '自动循环演示（每步 2.5s，末步停留 3s 后回到 ①）'}
            >
              {tlMode === 'auto' ? '⏸ 暂停自动' : '▶ 自动'}
            </button>
          </span>
          <span className="tm-tl-stat mono">
            {tlTotalMs !== undefined && (
              <i className="tm-tl-total">{`${multi ? '基准合计' : '合计'} ${fmtMs(tlTotalMs)}`}</i>
            )}
            <i className="tm-tl-count">
              {!tlSteps.length ? '0/0' : tlCursor < 0 ? `—/${tlSteps.length}` : `${tlCursor + 1}/${tlSteps.length}`}
            </i>
            <i className={`tm-tl-mode ${tlMode}`}>
              {tlMode === 'auto' ? '自动' : tlMode === 'manual' ? '手动' : '实时'}
            </i>
          </span>
        </div>
        {tlSteps.length ? (
          <ol className="tm-tl-steps">
            {tlSteps.map((s, i) => {
              const tone: Tone = multi
                ? (tlTones[i] || 'pending')
                : (!traced ? 'ghost' : (stepTones[i] || 'pending'));
              return (
                <li key={`${s.ref}-${i}`} className={`tm-tl-step ${tone}`}>
                  <button
                    type="button"
                    className="tm-tl-chip"
                    onClick={() => onTlJump(i)}
                    disabled={multi ? false : !traced}
                    aria-current={i === tlCursor ? 'step' : undefined}
                    title={`${circled(i)} ${s.label}${s.detail ? ` · ${s.detail}` : ''}${s.durationMs !== undefined ? ` · ${fmtMs(s.durationMs)}` : ''}`}
                  >
                    <span className="tm-tl-no mono">{circled(i)}</span>
                    <span className="tm-tl-label">{s.label}</span>
                    {s.items && s.items.length > 1 && (
                      <span className="tm-tl-para mono" title={`并行 ${s.items.length} 项，取最大值耗时`}>
                        ∥{s.items.length}
                      </span>
                    )}
                    {multi && !!multiHits[i] && (
                      <span className="tm-tl-hit mono" title={`${multiHits[i]} 条轨迹停在此步`}>
                        {`${multiHits[i]} 条`}
                      </span>
                    )}
                    <span className="tm-tl-meta mono">{stepMeta(s)}</span>
                    {i === tlCursor && !reduced && tlMode === 'auto' && (
                      <i
                        key={`prog-${tlCursor}`}
                        className="tm-tl-prog"
                        style={{ animationDuration: `${autoplayDelay(s, i >= tlSteps.length - 1)}ms` }}
                      />
                    )}
                  </button>
                  {i < tlSteps.length - 1 && <i className={`tm-tl-link ${tone}`} />}
                </li>
              );
            })}
          </ol>
        ) : (
          <div className="tm-tl-empty xs muted">
            {multi ? '暂无同屏任务 · 请在上方选择要对比的任务' : '该类型暂无任务 · 选择任务后显示执行时序'}
          </div>
        )}
        {tlHasParallel && (
          <div className="tm-tl-par">
            {tlCurStep?.items && tlCurStep.items.length > 1 ? (
              <>
                <i className="tm-tl-par-tag mono">
                  {`∥ ${tlCurStep.label} · ${tlCurStep.items.length} 项并行${multi ? ' · 基准任务' : ''}`}
                </i>
                {tlCurStep.items.map((it) => (
                  <span className="tm-tl-par-item" key={it.ref}>
                    <b>{it.label}</b>
                    {it.detail && <i className="tm-tl-par-detail">{it.detail}</i>}
                    {it.durationMs !== undefined && <i className="tm-tl-par-ms mono">{fmtMs(it.durationMs)}</i>}
                  </span>
                ))}
              </>
            ) : (
              <i className="tm-tl-par-tag idle mono">并行步骤激活时展开各子项</i>
            )}
          </div>
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
          {multi
            ? (multiRows.length
              ? `${tlMode === 'auto' ? '循环演示' : tlMode === 'manual' ? '手动逐步' : '跟随实时阶段'} · 同屏 ${multiRows.length} 条 · 共享进度 ${Math.round(multiProg * 100)}% · ${circled(multiIndex)} ${multiRefSteps[multiIndex]?.label || '—'}`
              : '尚无同屏任务，未绘制任何轨迹')
            : !traced
              ? '尚无该类型任务，仅绘制标准路径'
              : curStep
                ? `${active.mode === 'auto' ? '循环演示' : active.mode === 'manual' ? '手动逐步' : '跟随实时阶段'} · ${circled(cursor)} ${curStep.label}${curStep.detail ? ` · ${curStep.detail}` : ''}`
                : '该任务发起方未知，未绘制实际连线'}
        </span>
      </div>
    </section>
  );
}

/**
 * Test hook (same spirit as the `initialKind` prop): lets a smoke harness assert
 * the derived step timeline, cursor and geometry without a DOM. Every member is
 * used by the component itself, so this costs nothing in the bundle.
 */
export const __internals = {
  nextPlayState,
  nextSharedState,
  iconKindOf,
  buildLanes,
  autoplayDelay,
  refStepIndex,
  prefixStepIndex,
  buildSteps,
  buildView,
  stageStepIndex,
  failureStepIndex,
  initialPlayState,
  CANON,
  // 多任务同屏对比：候选排序 / 默认选择 / 共享进度换算
  MULTI_MAX,
  MULTI_PICK_MAX,
  taskKindOf,
  multiCandidates,
  defaultMultiIds,
  sharedProgress,
  sharedCursor,
};
