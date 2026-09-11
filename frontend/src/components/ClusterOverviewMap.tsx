import { useMemo } from 'react';
import type {
  ClusterStatus, ClusterSummary, EntityLive, HealthMap, NodeInfo, PodInfo,
} from '../types';
import { fmtPct, healthDetail, healthState, loadColor } from '../utils';
import { iconShapes, type IconKind } from './TaskTrajectoryMap';

/* ===========================================================================
   当前集群架构总览 · LIVE CLUSTER OVERVIEW —— 逻辑架构视图

   Bottom-to-top logical groups (Chinese-only vocabulary):

     云数据中心   scheduler · medical-server · dc-services · redis   (node3)
     医院 · 边    hospital-a · hospital-b                             (node1 / node2)
     诊所 · 端    clinic-1 · clinic-2 (+ clinic-N)                    (node1 / node2)
     运维         monitoring · prediction                             (desktop)

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
/** free lanes left of the panels for the 端 ⇄ 云 relation (up / down) */
const LANE_UP = 28;
const LANE_DOWN = 52;
const MEDAL_R = 18;
const TEXT_X = 46;

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
  load?: { cpu: number; mem: number } | null;
  rect: Rect;
}

interface Panel {
  key: 'cloud' | 'edge' | 'terminal' | 'ops';
  title: string;
  summary: string;
  load?: { cpu: number; mem: number } | null;
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

const CLOUD_TILES: { name: string; caption: string; icon: IconKind; keys: string[] }[] = [
  { name: 'scheduler', caption: '调度', icon: 'scheduler', keys: ['scheduler'] },
  { name: 'medical-server', caption: '医疗推理', icon: 'server', keys: ['medical_server', 'medical-server'] },
  {
    name: 'dc-services',
    caption: '患者库 · 协同计算 · 备份',
    icon: 'database',
    keys: ['datacenter(patient-db)', 'datacenter', 'dc-services', 'patient-db'],
  },
  { name: 'redis', caption: '队列 / 记录', icon: 'cache', keys: ['redis'] },
];

const OPS_FALLBACK = ['monitoring', 'prediction'];

const CAPTION_BY_KIND: Record<'hospital' | 'clinic', string> = {
  hospital: '诊断 · 计算 · 通信 · 日常',
  clinic: '转诊 · 计算 · 通信 · 日常',
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
    caption: spec.caption,
    node: 'node3',
    state: healthTone(lookupHealth(health, spec.keys)),
    load: null,
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
    title: '云数据中心',
    summary: `${cloudTiles.length} 个组件 · 就绪 ${cloudOk}`,
    load: loadOf(nodeOf('node3', nodes)),
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
    caption: /predict/i.test(it.name) ? '预测（可选）' : '指标观测',
    node: 'desktop',
    state: it.pods.length
      ? podTone(it.pods)
      : { tone: 'unknown', text: '可选', detail: '可选部署，未上报' },
    load: null,
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
    title: '运维',
    summary: `${opsTiles.length} 个组件 · 就绪 ${opsOk} · desktop`,
    load: null,
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
      label: '指标观测',
    });
  }

  // ---- 医院 · 边 / 诊所 · 端 ---------------------------------------------
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
      name: it.name,
      caption: it.caption,
      node: it.node,
      state: podTone(it.pods),
      load: loadOf(nodeOf(it.node, nodes)),
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
        load: null,
        rect,
        tiles,
      },
      bottom: rect.y + rect.h,
    };
  };

  const edge = placeGroup('edge', '医院 · 边', entitiesOf(status, 'hospital'), cloudBottom + GUTTER);
  panels.push(edge.panel);
  const term = placeGroup('terminal', '诊所 · 端', entitiesOf(status, 'clinic'), edge.bottom + GUTTER);
  panels.push(term.panel);
  const height = term.bottom + BOTTOM_PAD;

  // ---- group relations (all routed through box-free gutters) --------------
  const edgeTopY = edge.panel.rect.y;
  const termTopY = term.panel.rect.y;

  // 边 ⇄ 云: two parallel lines with opposite arrowheads + one label
  const upX = 420;
  const downX = 452;
  links.push({
    id: 'edge-cloud-up',
    d: polyline([{ x: upX, y: edgeTopY }, { x: upX, y: cloudBottom }]),
    arrow: true,
    label: '协同诊断 / 计算 / 同步 / 日常',
  });
  links.push({
    id: 'edge-cloud-down',
    d: polyline([{ x: downX, y: cloudBottom }, { x: downX, y: edgeTopY }]),
    arrow: true,
  });

  // 端 ⇄ 云: routed around 医院 · 边 through the free left lanes
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
    label: '协同计算 / 同步 / 日常',
    // the lane has no room beside it → label it in the 云/边 gutter
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

  // 转诊: 诊所 → 医院 (same host node preferred), dotted
  const hospitals = edge.panel.tiles;
  const used = new Set<string>();
  const pairs: { from: Tile; to: Tile; row: number }[] = [];
  term.panel.tiles.forEach((c) => {
    const sameNode = hospitals.find((t) => t.node && c.node && t.node === c.node && !used.has(t.id));
    const byIndex = hospitals[pairs.length % Math.max(1, hospitals.length)];
    const to = sameNode || byIndex;
    if (to) used.add(to.id);
    if (to) pairs.push({ from: c, to, row: pairs.length });
  });
  const colW2 = (PANEL_W - 2 * PAD - COL_GAP) / 2;
  const gutter2Mid = (edge.bottom + termTopY) / 2;
  let channelled = 0;
  pairs.forEach((p) => {
    const fromX = p.from.rect.x + p.from.rect.w / 2;
    const fromMid = p.from.rect.y + p.from.rect.h / 2;
    const toMid = p.to.rect.y + p.to.rect.h / 2;
    const fromRow = Math.round((p.from.rect.y - (termTopY + HEADER_H)) / (TILE_H + ROW_GAP));
    const toCol = Math.round((p.to.rect.x - (PANEL_X + PAD)) / (colW2 + COL_GAP));
    const direct = Math.abs(fromX - (p.to.rect.x + p.to.rect.w / 2)) < 1 && fromRow === 0;
    let pts: Pt[];
    let hint: Link['labelHint'];
    if (direct) {
      pts = [{ x: fromX, y: p.from.rect.y }, { x: fromX, y: p.to.rect.y + p.to.rect.h }];
    } else {
      const chanX = toCol === 0
        ? PANEL_X + 10 - (p.row % 2) * 4
        : PANEL_X + PANEL_W - 10 - (p.row % 2) * 4;
      const startX = chanX > fromX ? p.from.rect.x + p.from.rect.w : p.from.rect.x;
      const endX = chanX > p.to.rect.x + p.to.rect.w / 2 ? p.to.rect.x + p.to.rect.w : p.to.rect.x;
      pts = [
        { x: startX, y: fromMid },
        { x: chanX, y: fromMid },
        { x: chanX, y: toMid },
        { x: endX, y: toMid },
      ];
      hint = { x: 84 + channelled * 70, y: gutter2Mid - 10 + (channelled % 2) * 15, anchor: 'start' };
      channelled += 1;
    }
    links.push({
      id: `referral-${p.from.id}`,
      d: polyline(pts),
      dotted: true,
      arrow: true,
      label: '转诊',
      labelHint: hint,
    });
  });

  // ---- labels last, against panels + tiles + already placed labels -------
  panels.forEach((p) => {
    avoid.push({ x1: p.rect.x - 2, y1: p.rect.y - 2, x2: p.rect.x + p.rect.w + 2, y2: p.rect.y + p.rect.h + 2 });
    p.tiles.forEach((t) => avoid.push(tileBox(t.rect)));
  });
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

  return { panels, links, height };
}

/* ---------------------------------------------------------------- component */
export default function ClusterOverviewMap({
  status, summary, health, error, updatedAt,
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
}) {
  const layout = useMemo(() => buildLayout(status, summary, health), [status, summary, health]);
  const stamp = updatedAt ? updatedAt.toLocaleTimeString('zh-CN', { hour12: false }) : null;
  const entityCount = layout.panels.reduce((a, p) => a + p.tiles.length, 0);

  return (
    <section className="card lmap">
      <div className="card-headrow">
        <h3 className="card-title">当前集群架构总览 · LIVE CLUSTER OVERVIEW</h3>
        <span className="muted xs mono">
          {status?.version ? `v${status.version}` : '—'}
          <i className="arch-meta-sep">·</i>
          {`${entityCount} 个逻辑实体`}
          <i className="arch-meta-sep">·</i>
          {stamp ? `最后更新 ${stamp}` : '尚未更新'}
        </span>
      </div>

      <p className="eyebrow tmap-caption">
        逻辑架构视图 —— 按 云数据中心 / 医院 · 边 / 诊所 · 端 / 运维 分组，数值与状态点为实时采集
      </p>

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
                {p.load && (() => {
                  const lx = headerLoadX(p.rect);
                  const ly = p.rect.y + 41;
                  return (
                    <g className="lm-panel-load">
                      <text className="lm-load-k" x={lx} y={ly}>node3</text>
                      <text className="lm-load-k" x={lx + 34} y={ly}>CPU</text>
                      <text className="lm-load-v mono" x={lx + 82} y={ly} textAnchor="end">
                        {fmtPct(p.load.cpu)}
                      </text>
                      <rect className="lm-bar-bg" x={lx + 92} y={ly - 5} width="70" height="3" />
                      <rect
                        className="lm-bar-fill"
                        x={lx + 92}
                        y={ly - 5}
                        width={70 * p.load.cpu}
                        height="3"
                        fill={loadColor(p.load.cpu)}
                      />
                      <text className="lm-load-k" x={lx + 172} y={ly}>内存</text>
                      <text className="lm-load-v mono" x={lx + 232} y={ly} textAnchor="end">
                        {fmtPct(p.load.mem)}
                      </text>
                      <rect className="lm-bar-bg" x={lx + 242} y={ly - 5} width="70" height="3" />
                      <rect
                        className="lm-bar-fill"
                        x={lx + 242}
                        y={ly - 5}
                        width={70 * p.load.mem}
                        height="3"
                        fill={loadColor(p.load.mem)}
                      />
                    </g>
                  );
                })()}
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
                  {t.node && (p.key === 'edge' || p.key === 'terminal') && (
                    <text className="lm-node mono" x={right} y={nameY} textAnchor="end">{t.node}</text>
                  )}
                  <text className="lm-cap" x={tx} y={capY}>
                    {clip(t.caption, t.rect.w - (tx - t.rect.x) - 12, 9)}
                  </text>
                  <circle className={`lm-dot ${t.state.tone}`} cx={tx + 3} cy={stateY - 4} r="3.2" />
                  <text className="lm-state" x={tx + 12} y={stateY}>{t.state.text}</text>
                  {t.load && !compact && (
                    <g className="lm-load">
                      <text className="lm-load-k" x={right - 190} y={stateY} textAnchor="end">CPU</text>
                      <text className="lm-load-v mono" x={right - 158} y={stateY} textAnchor="end">
                        {fmtPct(t.load.cpu)}
                      </text>
                      <rect className="lm-bar-bg" x={right - 150} y={stateY - 5} width="34" height="3" />
                      <rect
                        className="lm-bar-fill"
                        x={right - 150}
                        y={stateY - 5}
                        width={34 * t.load.cpu}
                        height="3"
                        fill={loadColor(t.load.cpu)}
                      />
                      <text className="lm-load-k" x={right - 92} y={stateY} textAnchor="end">内存</text>
                      <text className="lm-load-v mono" x={right - 60} y={stateY} textAnchor="end">
                        {fmtPct(t.load.mem)}
                      </text>
                      <rect className="lm-bar-bg" x={right - 52} y={stateY - 5} width="34" height="3" />
                      <rect
                        className="lm-bar-fill"
                        x={right - 52}
                        y={stateY - 5}
                        width={34 * t.load.mem}
                        height="3"
                        fill={loadColor(t.load.mem)}
                      />
                    </g>
                  )}
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
