import { useMemo } from 'react';
import type {
  ClusterStatus, ClusterSummary, EntityLive, HealthMap, NodeInfo, PodInfo,
} from '../types';
import { fmtPct, healthDetail, healthState, loadColor } from '../utils';
import {
  CLOUD_CAPTION, FLOW, TIER_LABEL, entityCaption, entityName, entityTier, nodeRoleText,
} from '../terms';
import { iconShapes, type IconKind } from './TaskTrajectoryMap';

/* ===========================================================================
   当前集群架构总览 —— 逻辑架构视图

   Bottom-to-top logical groups (Chinese-only vocabulary):

     数据中心   scheduler · medical-server · dc-services · redis   (node3)
     医疗中心   hospital-a · hospital-b                             (node1 / node2)
     医院       clinic-1 · clinic-2 (+ clinic-N)                    (node1 / node2)
     运维       monitoring                                   (desktop)

   Entities are borderless icon medallions + name + one short Chinese caption +
   live state (health probe / pod readiness / node CPU·内存). Group relations
   are hairline connectors drawn in box-free gutters, labelled on their longest
   segment. Live data: { status, summary, health } — polled by the page.
   =========================================================================== */

const CV_W = 1240;
const PANEL_X = 60;
const PANEL_W = 800;
const OPS_X = 968;
const OPS_W = 242;
const PAD = 16;
const COL_GAP = 20;
const ROW_GAP = 12;
const TILE_H = 76;
const TILE_H_SM = 56;
const HEADER_H = 50;
const BUS_GAP = 16;
const GUTTER = 68;
const TOP_Y = 26;
const BOTTOM_PAD = 22;
/** free lanes left of the panels for the 医院 ⇄ 数据中心 relation (up / down) */
const LANE_UP = 28;
const LANE_DOWN = 52;
const MEDAL_R = 18;
const TEXT_X = 46;
/** 节点资源 小框（右侧栏，运维面板下方） */
const RES_ROW_H = 42;
const RES_ORDER = ['desktop-jm5iec6', 'node1', 'node2', 'node3'];

interface Box { x1: number; y1: number; x2: number; y2: number }
interface Rect { x: number; y: number; w: number; h: number }
interface Pt { x: number; y: number }

type Tone = 'ok' | 'degraded' | 'down' | 'unknown';

interface TileState {
  tone: Tone;
  text: string;
  detail: string;
}

interface Tile {
  id: string;
  icon: IconKind;
  name: string;
  caption: string;
  node?: string | null;
  state: TileState;
  rect: Rect;
}

/** one line of the 节点资源 box */
interface ResRow {
  name: string;
  role: string;
  cpu: number;
  mem: number;
  known: boolean;
  y: number;
}

interface ResBox {
  rect: Rect;
  title: string;
  summary: string;
  rows: ResRow[];
}

interface Panel {
  key: 'cloud' | 'edge' | 'terminal' | 'ops';
  title: string;
  summary: string;
  rect: Rect;
  tiles: Tile[];
}

interface Link {
  id: string;
  d: string;
  /** deliberate label anchor (bypasses the automatic search) */
  labelHint?: { x: number; y: number; anchor: 'start' | 'middle' | 'end' };
  label?: string;
  dotted?: boolean;
  arrow?: boolean;
  labelAt?: { x: number; y: number; anchor: 'start' | 'middle' | 'end' };
}

interface Layout {
  panels: Panel[];
  /** 节点资源小框：仅“集群负载”页展示，可视化页传 false 隐藏 */
  resources: ResBox | null;
  links: Link[];
  height: number;
}

/* -------------------------------------------------------------- primitives */
function textWidth(text: string, size: number): number {
  let w = 0;
  for (const ch of text) w += /[\u3000-\u9fff\uff00-\uffef]/.test(ch) ? size * 1.04 : size * 0.62;
  return w;
}

function clip(text: string, maxPx: number, size: number): string {
  let w = 0;
  let out = '';
  for (const ch of text) {
    const cw = /[\u3000-\u9fff\uff00-\uffef]/.test(ch) ? size * 1.04 : size * 0.62;
    if (w + cw > maxPx) return `${out}…`;
    w += cw;
    out += ch;
  }
  return out;
}

function frac(v: unknown): number {
  const n = Number(v);
  return Number.isFinite(n) ? Math.min(1, Math.max(0, n)) : 0;
}

function polyline(pts: Pt[]): string {
  if (!pts.length) return '';
  return pts.slice(1).reduce((acc, p) => `${acc} L ${p.x} ${p.y}`, `M ${pts[0].x} ${pts[0].y}`);
}

/** x of the 云数据中心 header load block — always right of the summary text */
function headerLoadX(r: Rect): number {
  return r.x + Math.max(330, r.w * 0.52);
}

/**
 * Label placement: walk the longest segments and take the first anchor whose
 * text box avoids every panel / tile / earlier label and stays in the canvas.
 */
function placeLabel(
  pts: Pt[],
  text: string,
  avoid: Box[],
  size = 9.5,
  bounds: { w: number; h: number } = { w: CV_W, h: 100000 },
): { x: number; y: number; anchor: 'start' | 'middle' | 'end'; box: Box } {
  const segs: { i: number; len: number; horizontal: boolean }[] = [];
  for (let i = 1; i < pts.length; i += 1) {
    const dx = pts[i].x - pts[i - 1].x;
    const dy = pts[i].y - pts[i - 1].y;
    segs.push({ i, len: Math.hypot(dx, dy), horizontal: Math.abs(dx) >= Math.abs(dy) });
  }
  segs.sort((a, b) => b.len - a.len);
  const w = textWidth(text, size);
  const boxOf = (x: number, y: number, anchor: string): Box => {
    const left = anchor === 'middle' ? x - w / 2 : anchor === 'end' ? x - w : x;
    return { x1: left, y1: y - size, x2: left + w, y2: y + 2.5 };
  };
  const hits = (b: Box) => b.x1 < 2 || b.x2 > bounds.w - 2 || b.y1 < 2 || b.y2 > bounds.h - 2
    || avoid.some((r) => b.x1 < r.x2 && r.x1 < b.x2 && b.y1 < r.y2 && r.y1 < b.y2);

  for (const seg of segs.slice(0, 4)) {
    const a = pts[seg.i - 1];
    const b = pts[seg.i];
    for (const t of [0.5, 0.35, 0.66]) {
      const px = a.x + (b.x - a.x) * t;
      const py = a.y + (b.y - a.y) * t;
      const cands: { x: number; y: number; anchor: 'start' | 'middle' | 'end' }[] = seg.horizontal
        ? [{ x: px, y: py - 6, anchor: 'middle' }, { x: px, y: py + 11, anchor: 'middle' }]
        : [
          { x: px + 8, y: py + 3.5, anchor: 'start' },
          { x: px - 8, y: py + 3.5, anchor: 'end' },
        ];
      for (const c of cands) {
        const box = boxOf(c.x, c.y, c.anchor);
        if (!hits(box)) return { ...c, box };
      }
    }
  }
  const best = segs[0];
  if (!best) {
    return { x: pts[0]?.x || 0, y: pts[0]?.y || 0, anchor: 'middle', box: { x1: 0, y1: 0, x2: 0, y2: 0 } };
  }
  const a = pts[best.i - 1];
  const b = pts[best.i];
  const x = (a.x + b.x) / 2;
  const y = (a.y + b.y) / 2;
  const anchor: 'middle' | 'start' = best.horizontal ? 'middle' : 'start';
  const cand = best.horizontal ? { x, y: y - 6 } : { x: x + 8, y: y + 3.5 };
  const box = boxOf(cand.x, cand.y, anchor);
  const dx = box.x1 < 2 ? 2 - box.x1 : box.x2 > bounds.w - 2 ? bounds.w - 2 - box.x2 : 0;
  const dy = box.y2 > bounds.h - 2 ? bounds.h - 2 - box.y2 : box.y1 < 2 ? 2 - box.y1 : 0;
  return {
    x: cand.x + dx,
    y: cand.y + dy,
    anchor,
    box: { x1: box.x1 + dx, y1: box.y1 + dy, x2: box.x2 + dx, y2: box.y2 + dy },
  };
}

/* ------------------------------------------------------------ entity inputs */
interface EntityItem {
  name: string;
  kind: 'hospital' | 'clinic';
  pods: PodInfo[];
  node: string | null;
  caption: string;
}

const CLOUD_TILES: { name: string; icon: IconKind; keys: string[] }[] = [
  { name: 'scheduler', icon: 'scheduler', keys: ['scheduler'] },
  { name: 'medical-server', icon: 'server', keys: ['medical_server', 'medical-server'] },
  {
    name: 'dc-services',
    icon: 'database',
    keys: ['datacenter(patient-db)', 'datacenter', 'dc-services', 'patient-db'],
  },
  { name: 'redis', icon: 'cache', keys: ['redis'] },
];

const OPS_FALLBACK = ['monitoring'];

const CAPTION_BY_KIND: Record<'hospital' | 'clinic', string> = {
  hospital: entityCaption('hospital-a'),
  clinic: entityCaption('clinic-1'),
};

const NODE_BY_ENTITY: Record<string, string> = {
  'hospital-a': 'node1',
  'hospital-b': 'node2',
  'clinic-1': 'node1',
  'clinic-2': 'node2',
};

function lookupHealth(health: HealthMap | null | undefined, keys: string[]): unknown {
  if (!health) return undefined;
  for (const k of keys) {
    if (k in health) return health[k];
  }
  return undefined;
}

function healthTone(v: unknown): TileState {
  const st = healthState(v);
  const label = st === 'ok' ? '运行正常' : st === 'degraded' ? '降级' : st === 'down' ? '不可达' : '未知';
  return { tone: st === 'ok' ? 'ok' : st, text: label, detail: `探针 ${healthDetail(v)}` };
}

function podTone(pods: PodInfo[]): TileState {
  const total = pods.length;
  const ready = pods.filter((p) => p?.ready).length;
  if (!total) return { tone: 'unknown', text: '无 Pod', detail: '尚未上报 Pod' };
  const tone: Tone = ready === total ? 'ok' : ready === 0 ? 'down' : 'degraded';
  return { tone, text: `${ready}/${total} 就绪`, detail: `${total} 个 Pod 中 ${ready} 个就绪` };
}

function nodeOf(nodeName: string | null | undefined, nodes: NodeInfo[]): NodeInfo | undefined {
  if (!nodeName) return undefined;
  return nodes.find((n) => n?.name === nodeName);
}

function loadOf(node: NodeInfo | undefined): { cpu: number; mem: number } | null {
  if (!node) return null;
  return { cpu: frac(node.load?.cpu), mem: frac(node.load?.memory) };
}

/** entities of one kind, sorted, with a canonical fallback when none is reported */
function entitiesOf(status: ClusterStatus | null | undefined, kind: 'hospital' | 'clinic'): EntityItem[] {
  const out: EntityItem[] = [];
  Object.entries(status?.entities || {}).forEach(([name, ent]: [string, EntityLive]) => {
    const k: 'hospital' | 'clinic' = ent?.kind === 'clinic' ? 'clinic' : 'hospital';
    if (k !== kind) return;
    const pods = Array.isArray(ent?.pods) ? ent.pods : [];
    const hosts = pods.map((p) => String(p?.node || '')).filter(Boolean);
    const node = String(ent?.node || '') || (hosts.length ? hosts[0] : '') || NODE_BY_ENTITY[name] || null;
    out.push({ name, kind: k, pods, node, caption: CAPTION_BY_KIND[k] });
  });
  if (!out.length) {
    const fallback = kind === 'hospital' ? ['hospital-a', 'hospital-b'] : ['clinic-1', 'clinic-2'];
    fallback.forEach((name) => out.push({
      name, kind, pods: [], node: NODE_BY_ENTITY[name] || null, caption: CAPTION_BY_KIND[kind],
    }));
  }
  return out.sort((a, b) => a.name.localeCompare(b.name));
}

/* ------------------------------------------------------------------- layout */
function buildLayout(
  status: ClusterStatus | null | undefined,
  summary: ClusterSummary | null | undefined,
  health: HealthMap | null | undefined,
  withResources = true,
): Layout {
  const nodes = (status?.nodes?.length ? status.nodes : summary?.nodes) || [];
  const panels: Panel[] = [];
  const links: Link[] = [];
  const avoid: Box[] = [];
  const tileBox = (r: Rect): Box => ({ x1: r.x - 1, y1: r.y - 1, x2: r.x + r.w + 1, y2: r.y + r.h + 1 });

  // ---- 云数据中心 ---------------------------------------------------------
  const cloudRowY = TOP_Y + HEADER_H;
  const cloudBusY = cloudRowY + TILE_H + BUS_GAP;
  const cloudRect: Rect = {
    x: PANEL_X, y: TOP_Y, w: PANEL_W, h: HEADER_H + TILE_H + BUS_GAP + 10 + PAD,
  };
  const cloudW = (PANEL_W - 2 * PAD - 3 * COL_GAP) / 4;
  const cloudTiles: Tile[] = CLOUD_TILES.map((spec, i) => ({
    id: spec.name,
    icon: spec.icon,
    name: spec.name,
    caption: CLOUD_CAPTION[spec.name] || '',
    node: 'node3',
    state: healthTone(lookupHealth(health, spec.keys)),
    rect: {
      x: PANEL_X + PAD + i * (cloudW + COL_GAP),
      y: cloudRowY,
      w: cloudW,
      h: TILE_H,
    },
  }));
  const cloudOk = cloudTiles.filter((t) => t.state.tone === 'ok').length;
  panels.push({
    key: 'cloud',
    title: TIER_LABEL.cloud,
    summary: `${cloudTiles.length} 个组件 · 就绪 ${cloudOk}`,
    rect: cloudRect,
    tiles: cloudTiles,
  });
  const cloudBottom = cloudRect.y + cloudRect.h;

  // internal relations: scheduler ↔ medical-server directly + a dispatch bus
  // under the row feeding dc-services and redis (every line stays box-free)
  const sched = cloudTiles[0].rect;
  const med = cloudTiles[1].rect;
  const dbc = cloudTiles[2].rect;
  const rds = cloudTiles[3].rect;
  const rowMid = sched.y + sched.h / 2;
  const cOf = (r: Rect) => r.x + r.w / 2;
  links.push({
    id: 'cloud-sched-med',
    d: polyline([{ x: sched.x + sched.w, y: rowMid }, { x: med.x, y: rowMid }]),
  });
  links.push({
    id: 'cloud-bus',
    d: polyline([{ x: cOf(sched), y: cloudBusY }, { x: cOf(rds), y: cloudBusY }]),
  });
  links.push({
    id: 'cloud-sched-dc',
    d: polyline([{ x: cOf(sched), y: sched.y + sched.h }, { x: cOf(sched), y: cloudBusY }]),
  });
  links.push({
    id: 'cloud-bus-dc',
    d: polyline([{ x: cOf(dbc), y: cloudBusY }, { x: cOf(dbc), y: dbc.y + dbc.h }]),
  });
  links.push({
    id: 'cloud-bus-redis',
    d: polyline([{ x: cOf(rds), y: cloudBusY }, { x: cOf(rds), y: rds.y + rds.h }]),
  });

  // ---- 运维 (secondary, right of 云数据中心) -------------------------------
  const opsDefs = (status?.readonly || []).filter((rd) => /monitor|predict/i.test(String(rd?.name || '')));
  const opsItems = opsDefs.length
    ? opsDefs.map((rd) => ({ name: String(rd.name), pods: (Array.isArray(rd.pods) ? rd.pods : []) as PodInfo[] }))
    : OPS_FALLBACK.map((name) => ({ name, pods: [] as PodInfo[] }));
  const opsRect: Rect = {
    x: OPS_X,
    y: TOP_Y,
    w: OPS_W,
    h: HEADER_H + opsItems.length * TILE_H_SM + (opsItems.length - 1) * 8 + PAD,
  };
  const opsTiles: Tile[] = opsItems.map((it, i) => ({
    id: `ops:${it.name}`,
    icon: 'monitor',
    name: it.name,
    caption: /predict/i.test(it.name) ? '预测服务（可选）' : '指标观测',
    node: 'desktop',
    state: it.pods.length
      ? podTone(it.pods)
      : { tone: 'unknown', text: '可选', detail: '可选部署，未上报' },
    rect: {
      x: opsRect.x + PAD,
      y: opsRect.y + HEADER_H + i * (TILE_H_SM + 8),
      w: OPS_W - 2 * PAD,
      h: TILE_H_SM,
    },
  }));
  const opsOk = opsTiles.filter((t) => t.state.tone === 'ok').length;
  panels.push({
    key: 'ops',
    title: TIER_LABEL.ops,
    summary: `${opsTiles.length} 个组件 · 就绪 ${opsOk}`,
    rect: opsRect,
    tiles: opsTiles,
  });
  {
    const y = opsRect.y + HEADER_H + TILE_H_SM * 0.5;
    links.push({
      id: 'ops-cloud',
      d: polyline([{ x: opsRect.x, y }, { x: PANEL_X + PANEL_W, y }]),
      dotted: true,
      arrow: true,
      label: FLOW.opsCloud,
    });
  }

  // ---- 医疗中心 / 医院 ---------------------------------------------------
  const placeGroup = (
    key: 'edge' | 'terminal',
    title: string,
    items: EntityItem[],
    top: number,
  ): { panel: Panel; bottom: number } => {
    const rows = Math.max(1, Math.ceil(items.length / 2));
    const rect: Rect = {
      x: PANEL_X, y: top, w: PANEL_W, h: HEADER_H + rows * TILE_H + (rows - 1) * ROW_GAP + PAD,
    };
    const colW = (PANEL_W - 2 * PAD - COL_GAP) / 2;
    const tiles: Tile[] = items.map((it, i) => ({
      id: it.name,
      icon: it.kind === 'clinic' ? 'clinic' : 'hospital',
      name: entityName(it.name),
      caption: it.caption,
      node: it.node,
      state: podTone(it.pods),
      rect: {
        x: rect.x + PAD + (i % 2) * (colW + COL_GAP),
        y: top + HEADER_H + Math.floor(i / 2) * (TILE_H + ROW_GAP),
        w: colW,
        h: TILE_H,
      },
    }));
    const total = items.reduce((a, it) => a + it.pods.length, 0);
    const ready = items.reduce((a, it) => a + it.pods.filter((p) => p?.ready).length, 0);
    return {
      panel: {
        key,
        title,
        summary: `${items.length} 个实体 · 就绪 ${ready}/${total}`,
        rect,
        tiles,
      },
      bottom: rect.y + rect.h,
    };
  };

  // ---- 节点资源（集中展示，避免在每个实体上重复） -------------------------
  const nodeByName: Record<string, NodeInfo> = {};
  nodes.forEach((n) => { if (n?.name) nodeByName[n.name] = n; });
  const resNames = [
    ...RES_ORDER.filter((n) => !!nodeByName[n]),
    ...nodes.map((n) => n.name).filter((n) => !RES_ORDER.includes(n)),
  ];
  const shownNames = resNames.length ? resNames : RES_ORDER;
  const resRect: Rect = {
    x: OPS_X,
    y: opsRect.y + opsRect.h + 18,
    w: OPS_W,
    h: HEADER_H + shownNames.length * RES_ROW_H + PAD,
  };
  const resRows: ResRow[] = shownNames.map((name, i) => {
    const n = nodeByName[name];
    const role = nodeRoleText(n?.role, name);
    const load = loadOf(n);
    return {
      name,
      role,
      cpu: load ? load.cpu : 0,
      mem: load ? load.mem : 0,
      known: !!load,
      y: resRect.y + HEADER_H + i * RES_ROW_H,
    };
  });
  const resources: ResBox | null = withResources ? {
    rect: resRect,
    title: '节点资源',
    summary: `${shownNames.length} 个节点 · 就绪 ${resRows.filter((r) => nodeByName[r.name]?.ready).length}`,
    rows: resRows,
  } : null;

  const edge = placeGroup('edge', TIER_LABEL.medical, entitiesOf(status, 'hospital'), cloudBottom + GUTTER);
  panels.push(edge.panel);
  const term = placeGroup('terminal', TIER_LABEL.hospital, entitiesOf(status, 'clinic'), edge.bottom + GUTTER);
  panels.push(term.panel);
  const height = (withResources ? Math.max(term.bottom, resRect.y + resRect.h) : term.bottom) + BOTTOM_PAD;

  // ---- group relations (all routed through box-free gutters) --------------
  const edgeTopY = edge.panel.rect.y;
  const termTopY = term.panel.rect.y;

  // 医疗中心 ⇄ 数据中心: two parallel lines with opposite arrowheads + one label
  const upX = 420;
  const downX = 452;
  links.push({
    id: 'edge-cloud-up',
    d: polyline([{ x: upX, y: edgeTopY }, { x: upX, y: cloudBottom }]),
    arrow: true,
    label: FLOW.medicalCloud,
  });
  links.push({
    id: 'edge-cloud-down',
    d: polyline([{ x: downX, y: cloudBottom }, { x: downX, y: edgeTopY }]),
    arrow: true,
  });

  // 医院 ⇄ 数据中心: routed around 医疗中心 through the free left lanes
  const termMid = term.panel.tiles.length ? term.panel.tiles[0].rect.y + 24 : termTopY + HEADER_H;
  const cloudMid = cloudTiles[2].rect.y + 20;
  links.push({
    id: 'term-cloud-up',
    d: polyline([
      { x: PANEL_X, y: termMid },
      { x: LANE_UP, y: termMid },
      { x: LANE_UP, y: cloudMid },
      { x: PANEL_X, y: cloudMid },
    ]),
    arrow: true,
    label: FLOW.hospitalCloud,
    // the lane has no room beside it → label it in the 数据中心/医疗中心 gutter
    labelHint: { x: 76, y: cloudBottom + GUTTER / 2 + 4, anchor: 'start' },
  });
  links.push({
    id: 'term-cloud-down',
    d: polyline([
      { x: PANEL_X, y: cloudMid + 26 },
      { x: LANE_DOWN, y: cloudMid + 26 },
      { x: LANE_DOWN, y: termMid + 26 },
      { x: PANEL_X, y: termMid + 26 },
    ]),
    arrow: true,
  });

  // 转诊: one group-level dotted indicator between 医院 and 医疗中心
  // (added clinic-N entities never add another line)
  {
    const refX = PANEL_X + PANEL_W / 2;
    links.push({
      id: 'referral',
      d: polyline([{ x: refX, y: termTopY }, { x: refX, y: edge.bottom }]),
      dotted: true,
      arrow: true,
      label: FLOW.referral,
    });
  }

  // ---- labels last, against panels + tiles + already placed labels -------
  panels.forEach((p) => {
    avoid.push({ x1: p.rect.x - 2, y1: p.rect.y - 2, x2: p.rect.x + p.rect.w + 2, y2: p.rect.y + p.rect.h + 2 });
    p.tiles.forEach((t) => avoid.push(tileBox(t.rect)));
  });
  if (resources) {
    avoid.push({
      x1: resources.rect.x - 2, y1: resources.rect.y - 2,
      x2: resources.rect.x + resources.rect.w + 2, y2: resources.rect.y + resources.rect.h + 2,
    });
  }
  links.forEach((l) => {
    if (!l.label) return;
    const placed = l.labelHint
      ? (() => {
        const w = textWidth(l.label as string, 9.5);
        const left = l.labelHint!.anchor === 'middle' ? l.labelHint!.x - w / 2
          : l.labelHint!.anchor === 'end' ? l.labelHint!.x - w : l.labelHint!.x;
        return {
          ...l.labelHint!,
          box: { x1: left, y1: l.labelHint!.y - 9.5, x2: left + w, y2: l.labelHint!.y + 2.5 },
        };
      })()
      : (() => {
        const nums = (l.d.match(/-?[\d.]+/g) || []).map(Number);
        const pts: Pt[] = [];
        for (let i = 0; i + 1 < nums.length; i += 2) pts.push({ x: nums[i], y: nums[i + 1] });
        return placeLabel(pts, l.label as string, avoid, 9.5, { w: CV_W, h: height });
      })();
    l.labelAt = { x: placed.x, y: placed.y, anchor: placed.anchor };
    avoid.push(placed.box);
  });

  return { panels, resources, links, height };
}

/* ---------------------------------------------------------------- component */
export default function ClusterOverviewMap({
  status, summary, health, error, updatedAt, showResources = true,
}: {
  /** GET /cluster/status — entities + readonly deployments with pods */
  status?: ClusterStatus | null;
  /** GET /cluster/summary — fallback node list */
  summary?: ClusterSummary | null;
  /** GET /health — cloud service probe map */
  health?: HealthMap | null;
  /** probe error; the previous snapshot stays on screen */
  error?: string | null;
  updatedAt?: Date | null;
  /** 是否展示“节点资源”小框（可视化页传 false，负载页保留） */
  showResources?: boolean;
}) {
  const layout = useMemo(
    () => buildLayout(status, summary, health, showResources),
    [status, summary, health, showResources],
  );
  const res = layout.resources;
  const stamp = updatedAt ? updatedAt.toLocaleTimeString('zh-CN', { hour12: false }) : null;
  const entityCount = layout.panels.reduce((a, p) => a + p.tiles.length, 0);

  return (
    <section className="card lmap">
      <div className="card-headrow">
        <h3 className="card-title">当前集群架构总览</h3>
      </div>

      {error && <div className="cm-inline-err">集群拓扑接口异常：{error}（保留上次成功数据）</div>}

      <div className="tmap-canvas">
        <svg
          className="lmap-svg"
          viewBox={`0 0 ${CV_W} ${layout.height}`}
          width="100%"
          role="img"
          aria-label="当前集群架构逻辑视图"
        >
          <title>当前集群架构逻辑视图</title>
          <defs>
            <marker
              id="lm-arrow"
              viewBox="0 0 8 8"
              refX="7"
              refY="4"
              markerWidth="7"
              markerHeight="7"
              markerUnits="userSpaceOnUse"
              orient="auto"
            >
              <path className="lm-arrow-p" d="M0,0.7 L7.6,4 L0,7.3 Z" />
            </marker>
            <marker
              id="lm-arrow-dot"
              viewBox="0 0 8 8"
              refX="7"
              refY="4"
              markerWidth="7"
              markerHeight="7"
              markerUnits="userSpaceOnUse"
              orient="auto"
            >
              <path className="lm-arrow-p dot" d="M0,0.7 L7.6,4 L0,7.3 Z" />
            </marker>
          </defs>

          <g className="lm-panels">
            {layout.panels.map((p) => (
              <g key={p.key} className={`lm-panel ${p.key}`}>
                <rect
                  className="lm-panel-bg"
                  x={p.rect.x}
                  y={p.rect.y}
                  width={p.rect.w}
                  height={p.rect.h}
                  rx="3"
                />
                <rect className="lm-panel-bar" x={p.rect.x} y={p.rect.y} width="3" height={p.rect.h} />
                <text className="lm-panel-title" x={p.rect.x + 16} y={p.rect.y + 23}>{p.title}</text>
                <text className="lm-panel-sum mono" x={p.rect.x + 16} y={p.rect.y + 41}>{p.summary}</text>
                <line
                  className="lm-panel-rule"
                  x1={p.rect.x + 14}
                  y1={p.rect.y + HEADER_H - 3}
                  x2={p.rect.x + p.rect.w - 14}
                  y2={p.rect.y + HEADER_H - 3}
                />
              </g>
            ))}
          </g>

          {res && (
          <g className="lm-panel resource">
            <rect
              className="lm-panel-bg"
              x={res.rect.x}
              y={res.rect.y}
              width={res.rect.w}
              height={res.rect.h}
              rx="3"
            />
            <rect
              className="lm-panel-bar"
              x={res.rect.x}
              y={res.rect.y}
              width="3"
              height={res.rect.h}
            />
            <text
              className="lm-panel-title"
              x={res.rect.x + 16}
              y={res.rect.y + 23}
            >
              {res.title}
            </text>
            <text
              className="lm-panel-sum mono"
              x={res.rect.x + 16}
              y={res.rect.y + 41}
            >
              {res.summary}
            </text>
            <line
              className="lm-panel-rule"
              x1={res.rect.x + 14}
              y1={res.rect.y + HEADER_H - 3}
              x2={res.rect.x + res.rect.w - 14}
              y2={res.rect.y + HEADER_H - 3}
            />
            {(res?.rows || []).map((row) => {
              const x0 = res.rect.x + 16;
              return (
                <g className="lm-res-row" key={row.name}>
                  <text className="lm-res-name" x={x0} y={row.y + 15}>{row.name}</text>
                  <text
                    className="lm-res-role"
                    x={res.rect.x + res.rect.w - 16}
                    y={row.y + 15}
                    textAnchor="end"
                  >
                    {row.role}
                  </text>
                  <text className="lm-load-k" x={x0} y={row.y + 32}>CPU</text>
                  <text className="lm-load-v mono" x={x0 + 58} y={row.y + 32} textAnchor="end">
                    {row.known ? fmtPct(row.cpu) : '—'}
                  </text>
                  <rect className="lm-bar-bg" x={x0 + 64} y={row.y + 27} width="30" height="3" />
                  <rect
                    className="lm-bar-fill"
                    x={x0 + 64}
                    y={row.y + 27}
                    width={30 * row.cpu}
                    height="3"
                    fill={loadColor(row.cpu)}
                  />
                  <text className="lm-load-k" x={x0 + 104} y={row.y + 32}>内存</text>
                  <text className="lm-load-v mono" x={x0 + 164} y={row.y + 32} textAnchor="end">
                    {row.known ? fmtPct(row.mem) : '—'}
                  </text>
                  <rect className="lm-bar-bg" x={x0 + 170} y={row.y + 27} width="28" height="3" />
                  <rect
                    className="lm-bar-fill"
                    x={x0 + 170}
                    y={row.y + 27}
                    width={28 * row.mem}
                    height="3"
                    fill={loadColor(row.mem)}
                  />
                </g>
              );
            })}
          </g>
          )}

          <g className="lm-links">
            {layout.links.map((l) => (
              <g key={l.id} className={`lm-link ${l.dotted ? 'dotted' : ''}`}>
                <path
                  className="lm-line"
                  d={l.d}
                  markerEnd={l.arrow ? `url(#${l.dotted ? 'lm-arrow-dot' : 'lm-arrow'})` : undefined}
                />
              </g>
            ))}
          </g>

          <g className="lm-tiles">
            {layout.panels.flatMap((p) => p.tiles.map((t) => {
              const compact = t.rect.h < 60;
              const cx = t.rect.x + (compact ? 24 : 26);
              const cy = t.rect.y + t.rect.h / 2;
              const tx = t.rect.x + (compact ? 46 : TEXT_X);
              const nameY = compact ? t.rect.y + 22 : t.rect.y + 26;
              const capY = compact ? t.rect.y + 37 : t.rect.y + 41;
              const stateY = compact ? t.rect.y + 50 : t.rect.y + 61;
              const right = t.rect.x + t.rect.w - 6;
              return (
                <g key={`${p.key}:${t.id}`} className={`lm-tile ${t.state.tone}`}>
                  <title>{`${t.name} · ${t.caption} · ${t.state.text}（${t.state.detail}）`}</title>
                  <circle className="lm-medal" cx={cx} cy={cy} r={compact ? 15 : MEDAL_R} />
                  <g
                    className="lm-ico"
                    transform={`translate(${cx - (compact ? 9 : 11)} ${cy - (compact ? 9 : 11)}) scale(${compact ? 0.76 : 0.92})`}
                  >
                    {iconShapes(t.icon)}
                  </g>
                  <text className="lm-name" x={tx} y={nameY}>{t.name}</text>
                  {t.node && (
                    <text className="lm-node mono" x={right} y={nameY} textAnchor="end">{t.node}</text>
                  )}
                  <text className="lm-cap" x={tx} y={capY}>
                    {clip(t.caption, t.rect.w - (tx - t.rect.x) - 12, 9)}
                  </text>
                  <circle className={`lm-dot ${t.state.tone}`} cx={tx + 3} cy={stateY - 4} r="3.2" />
                  <text className="lm-state" x={tx + 12} y={stateY}>{t.state.text}</text>
                </g>
              );
            }))}
          </g>

          <g className="lm-labels">
            {layout.links.filter((l) => l.labelAt && l.label).map((l) => (
              <text
                key={`lb-${l.id}`}
                className="lm-label"
                x={l.labelAt!.x}
                y={l.labelAt!.y}
                textAnchor={l.labelAt!.anchor}
              >
                {l.label}
              </text>
            ))}
          </g>
        </svg>
      </div>

      <div className="lm-foot">
        <span className="lm-legend"><i className="lm-legend-dot ok" />正常</span>
        <span className="lm-legend"><i className="lm-legend-dot degraded" />降级</span>
        <span className="lm-legend"><i className="lm-legend-dot down" />不可达</span>
        <span className="lm-legend"><i className="lm-legend-dot unknown" />未知 / 可选</span>
        <span className="lm-legend"><i className="lm-legend-line solid" />协同链路</span>
        <span className="lm-legend"><i className="lm-legend-line dotted" />转诊 / 观测</span>
        <span className="lm-legend-note muted xs mono">
          {`逻辑分组 ${layout.panels.length} · 实体 ${entityCount}`}
          {status?.ok === false ? ' · K8s 不可达' : ''}
        </span>
      </div>
    </section>
  );
}

/** test hook: the layout builder is pure, so a harness can assert its geometry */
export const __overviewInternals = {
  buildLayout, CV_W, HEADER_H, TILE_H, TILE_H_SM, PANEL_X, PANEL_W, OPS_X, OPS_W,
};
