import { useMemo } from 'react';
import type {
  ClusterStatus, ClusterSummary, EntityLive, HealthMap, NodeInfo, PodInfo,
} from '../types';
import { fmtPct, healthDetail, healthState, loadColor } from '../utils';
import { iconShapes, type IconKind } from './TaskTrajectoryMap';

/* ===========================================================================
   当前集群架构总览 · LIVE CLUSTER OVERVIEW —— 逻辑架构视图 (LOGICAL VIEW)

   Bottom-to-top logical subgraphs mirroring the platform's flow chart:

     01 CLOUD     云 Data Center   scheduler · medical-server · dc-services · redis
     02 EDGE      边 hospital      hospital-a @node1 · hospital-b @node2
     03 TERMINAL  端 clinic        clinic-1 @node1 · clinic-2 @node2 (+ clinic-N)
     04 OPS       运维 desktop     monitoring · prediction

   Entities are borderless icon medallions + name + capability caption + live
   state (health probe / pod readiness / node CPU·memory). Group-to-group
   relations are hairline connectors laid out in box-free gutters, labelled on
   their longest segment. Live data comes from { status, summary, health } —
   the architecture page polls all three on its 5 s ticker.
   =========================================================================== */

const CV_W = 1240;
const PANEL_X = 60;
const PANEL_W = 800;
const OPS_X = 1010;
const OPS_W = 200;
const PAD = 16;
const COL_GAP = 32;
const ROW_GAP = 12;
const TILE_H = 86;
const TILE_H_SM = 48;
const HEADER_H = 50;
const GUTTER = 68;
const TOP_Y = 26;
const BOTTOM_PAD = 22;
/** left lanes for the TERMINAL ⇄ CLOUD relation (up / down) */
const LANE_UP = 28;
const LANE_DOWN = 52;
const MEDAL_R = 21;

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
  no: string;
  zh: string;
  en: string;
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
  labelLines?: string[];
  dotted?: boolean;
  arrow?: boolean;
  labelAt?: { x: number; y: number; anchor: 'start' | 'middle' | 'end'; lines?: boolean };
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

/**
 * Label placement: walk the longest segments and take the first anchor whose
 * text box avoids every panel / tile / earlier label (same approach as the
 * trajectory map, kept local so that file stays untouched).
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
  { name: 'scheduler', caption: '任务队列 · 调度选点 · 任务记录', icon: 'scheduler', keys: ['scheduler'] },
  { name: 'medical-server', caption: '医疗模型后端推理', icon: 'server', keys: ['medical_server', 'medical-server'] },
  {
    name: 'dc-services',
    caption: '患者数据库 patient-db · 协同计算 · 备份',
    icon: 'database',
    keys: ['datacenter(patient-db)', 'datacenter', 'dc-services', 'patient-db'],
  },
  { name: 'redis', caption: '任务队列 / 任务记录', icon: 'cache', keys: ['redis'] },
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
  return { tone: st === 'ok' ? 'ok' : st, text: label, detail: healthDetail(v) };
}

function podTone(pods: PodInfo[]): TileState {
  const total = pods.length;
  const ready = pods.filter((p) => p?.ready).length;
  if (!total) return { tone: 'unknown', text: '无 Pod 上报', detail: 'no pods reported' };
  const tone: Tone = ready === total ? 'ok' : ready === 0 ? 'down' : 'degraded';
  return { tone, text: `${ready}/${total} Ready`, detail: `${ready} of ${total} pods ready` };
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

  const colW = (PANEL_W - 2 * PAD - COL_GAP) / 2;
  const colX = (col: number) => PANEL_X + PAD + col * (colW + COL_GAP);

  // ---- 01 CLOUD -----------------------------------------------------------
  const cloudRect: Rect = {
    x: PANEL_X, y: TOP_Y, w: PANEL_W, h: HEADER_H + 2 * TILE_H + ROW_GAP + PAD,
  };
  const cloudTiles: Tile[] = CLOUD_TILES.map((spec, i) => ({
    id: spec.name,
    icon: spec.icon,
    name: spec.name,
    caption: spec.caption,
    node: 'node3',
    state: healthTone(lookupHealth(health, spec.keys)),
    load: null,
    rect: {
      x: colX(i % 2),
      y: TOP_Y + HEADER_H + Math.floor(i / 2) * (TILE_H + ROW_GAP),
      w: colW,
      h: TILE_H,
    },
  }));
  const cloudOk = cloudTiles.filter((t) => t.state.tone === 'ok').length;
  panels.push({
    key: 'cloud',
    no: '01',
    zh: 'CLOUD · 云 Data Center',
    en: 'node3',
    summary: `组件 ${cloudTiles.length} · Ready ${cloudOk}/${cloudTiles.length}`,
    load: loadOf(nodeOf('node3', nodes)),
    rect: cloudRect,
    tiles: cloudTiles,
  });
  const cloudBottom = cloudRect.y + cloudRect.h;

  // internal cloud relations (scheduler is tile 0, top-left)
  const sched = cloudTiles[0].rect;
  const med = cloudTiles[1].rect;
  const dbc = cloudTiles[2].rect;
  const rds = cloudTiles[3].rect;
  links.push({
    id: 'cloud-sched-med',
    d: polyline([
      { x: sched.x + sched.w, y: sched.y + sched.h * 0.5 },
      { x: med.x, y: sched.y + sched.h * 0.5 },
    ]),
  });
  links.push({
    id: 'cloud-sched-dc',
    d: polyline([
      { x: sched.x + sched.w * 0.5, y: sched.y + sched.h },
      { x: dbc.x + dbc.w * 0.5, y: dbc.y },
    ]),
  });
  {
    const midX = sched.x + sched.w + COL_GAP / 2;
    links.push({
      id: 'cloud-sched-redis',
      d: polyline([
        { x: sched.x + sched.w, y: sched.y + sched.h * 0.78 },
        { x: midX, y: sched.y + sched.h * 0.78 },
        { x: midX, y: rds.y + rds.h * 0.5 },
        { x: rds.x, y: rds.y + rds.h * 0.5 },
      ]),
    });
  }

  // ---- 04 OPS (right of CLOUD, dotted 观测 link) ---------------------------
  const opsDefs = (status?.readonly || []).filter((rd) => /monitor|predict/i.test(String(rd?.name || '')));
  const opsItems = opsDefs.length
    ? opsDefs.map((rd) => ({ name: String(rd.name), pods: (Array.isArray(rd.pods) ? rd.pods : []) as PodInfo[] }))
    : OPS_FALLBACK.map((name) => ({ name, pods: [] as PodInfo[] }));
  const opsRect: Rect = {
    x: OPS_X,
    y: TOP_Y,
    w: OPS_W,
    h: HEADER_H + opsItems.length * TILE_H_SM + (opsItems.length - 1) * 6 + PAD,
  };
  const opsTiles: Tile[] = opsItems.map((it, i) => ({
    id: `ops:${it.name}`,
    icon: 'monitor',
    name: it.name,
    caption: /predict/i.test(it.name) ? '可选预测服务' : 'Prometheus 指标观测',
    node: 'desktop',
    state: it.pods.length
      ? podTone(it.pods)
      : { tone: 'unknown', text: '可选 / 未部署', detail: 'optional deployment' },
    load: null,
    rect: {
      x: opsRect.x + PAD,
      y: opsRect.y + HEADER_H + i * (TILE_H_SM + 6),
      w: OPS_W - 2 * PAD,
      h: TILE_H_SM,
    },
  }));
  const opsOk = opsTiles.filter((t) => t.state.tone === 'ok').length;
  panels.push({
    key: 'ops',
    no: '04',
    zh: 'OPS · 运维',
    en: '',
    summary: `组件 ${opsTiles.length} · Ready ${opsOk}/${opsTiles.length} · desktop`,
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
      labelLines: ['Prometheus/metrics-server', '观测 · 指标采集'],
    });
  }

  // ---- 02 EDGE / 03 TERMINAL ----------------------------------------------
  const placeGroup = (
    key: 'edge' | 'terminal',
    no: string,
    title: string,
    items: EntityItem[],
    top: number,
  ): { panel: Panel; bottom: number } => {
    const rows = Math.max(1, Math.ceil(items.length / 2));
    const rect: Rect = {
      x: PANEL_X, y: top, w: PANEL_W, h: HEADER_H + rows * TILE_H + (rows - 1) * ROW_GAP + PAD,
    };
    const tiles: Tile[] = items.map((it, i) => ({
      id: it.name,
      icon: it.kind === 'clinic' ? 'clinic' : 'hospital',
      name: it.name,
      caption: it.caption,
      node: it.node,
      state: podTone(it.pods),
      load: loadOf(nodeOf(it.node, nodes)),
      rect: {
        x: colX(i % 2),
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
        no,
        zh: title,
        en: key === 'edge' ? 'node1 / node2' : 'node1 / node2',
        summary: `实体 ${items.length} · Pod ${ready}/${total} Ready`,
        load: null,
        rect,
        tiles,
      },
      bottom: rect.y + rect.h,
    };
  };

  const edge = placeGroup('edge', '02', 'EDGE · 边 hospital', entitiesOf(status, 'hospital'), cloudBottom + GUTTER);
  panels.push(edge.panel);
  const term = placeGroup('terminal', '03', 'TERMINAL · 端 clinic', entitiesOf(status, 'clinic'), edge.bottom + GUTTER);
  panels.push(term.panel);
  const height = term.bottom + BOTTOM_PAD;

  // ---- group relations (all routed through box-free gutters) --------------
  const edgeTopY = edge.panel.rect.y;
  const termTopY = term.panel.rect.y;

  // EDGE ⇄ CLOUD: two parallel lines with opposite arrowheads + one label
  const upX = 420;
  const downX = 452;
  links.push({
    id: 'edge-cloud-up',
    d: polyline([{ x: upX, y: edgeTopY }, { x: upX, y: cloudBottom }]),
    arrow: true,
    label: '协同诊断 / 协同计算 / 数据同步 / 日常任务',
  });
  links.push({
    id: 'edge-cloud-down',
    d: polyline([{ x: downX, y: cloudBottom }, { x: downX, y: edgeTopY }]),
    arrow: true,
  });

  // TERMINAL ⇄ CLOUD: routed around the EDGE panel through the left lanes
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
    label: '协同计算 / 数据同步 / 日常任务',
    // the left lane has no room beside it, so the label sits in the CLOUD/EDGE gutter
    labelHint: { x: 76, y: 318, anchor: 'start' },
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

  // 转诊: clinic-N ⇢ hospital-N (same host node preferred), dotted
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
  let channelled = 0;
  pairs.forEach((p) => {
    const fromX = p.from.rect.x + p.from.rect.w / 2;
    const fromMid = p.from.rect.y + p.from.rect.h / 2;
    const toMid = p.to.rect.y + p.to.rect.h / 2;
    const fromRow = Math.round((p.from.rect.y - (termTopY + HEADER_H)) / (TILE_H + ROW_GAP));
    const toCol = Math.round((p.to.rect.x - (PANEL_X + PAD)) / (colW + COL_GAP));
    const direct = Math.abs(fromX - (p.to.rect.x + p.to.rect.w / 2)) < 1 && fromRow === 0;
    let pts: Pt[];
    let hint: Link['labelHint'];
    if (direct) {
      // straight shot: clinic tile top → hospital tile bottom
      pts = [{ x: fromX, y: p.from.rect.y }, { x: fromX, y: p.to.rect.y + p.to.rect.h }];
    } else {
      // route through the free channel beside the tiles (left for column 0,
      // right for column 1) so no other tile is crossed
      const chanX = toCol === 0
        ? 70 - (p.row % 2) * 4
        : PANEL_X + PANEL_W - 10 - (p.row % 2) * 4;
      const startX = chanX > fromX ? p.from.rect.x + p.from.rect.w : p.from.rect.x;
      const endX = chanX > p.to.rect.x + p.to.rect.w / 2 ? p.to.rect.x + p.to.rect.w : p.to.rect.x;
      pts = [
        { x: startX, y: fromMid },
        { x: chanX, y: fromMid },
        { x: chanX, y: toMid },
        { x: endX, y: toMid },
      ];
      // the channel leaves no room beside the line → label it in the gutter
      hint = { x: 84 + channelled * 120, y: termTopY - 46 + (channelled % 2) * 14, anchor: 'start' };
      channelled += 1;
    }
    links.push({
      id: `referral-${p.from.id}`,
      d: polyline(pts),
      dotted: true,
      arrow: true,
      label: '医疗推理转诊',
      labelHint: hint,
    });
  });

  // ---- labels last, against panels + tiles + already placed labels -------
  panels.forEach((p) => {
    avoid.push({ x1: p.rect.x - 2, y1: p.rect.y - 2, x2: p.rect.x + p.rect.w + 2, y2: p.rect.y + p.rect.h + 2 });
    p.tiles.forEach((t) => avoid.push(tileBox(t.rect)));
  });
  links.forEach((l) => {
    if (!l.label && !l.labelLines) return;
    const nums = (l.d.match(/-?[\d.]+/g) || []).map(Number);
    const pts: Pt[] = [];
    for (let i = 0; i + 1 < nums.length; i += 2) pts.push({ x: nums[i], y: nums[i + 1] });
    const widest = l.label || (l.labelLines || [])
      .slice()
      .sort((a, b) => textWidth(b, 9.5) - textWidth(a, 9.5))[0] || '';
    const placed = l.labelHint
      ? (() => {
        const w = textWidth(widest, 9.5);
        const left = l.labelHint!.anchor === 'middle' ? l.labelHint!.x - w / 2
          : l.labelHint!.anchor === 'end' ? l.labelHint!.x - w : l.labelHint!.x;
        return {
          ...l.labelHint!,
          box: { x1: left, y1: l.labelHint!.y - 9.5, x2: left + w, y2: l.labelHint!.y + 2.5 },
        };
      })()
      : placeLabel(pts, widest, avoid, 9.5, { w: CV_W, h: height });
    l.labelAt = { x: placed.x, y: placed.y, anchor: placed.anchor, lines: !!l.labelLines };
    if (l.labelLines) placed.box.y2 += 11; // reserve the second tspan line
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
  const readyPanels = layout.panels.filter((p) => p.tiles.some((t) => t.state.tone === 'ok')).length;

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
        逻辑架构视图 · LOGICAL VIEW —— 云 / 边 / 端 / 运维 四个逻辑分组（实体以 @node 标注物理归属），
        连线为协同关系，圆点与百分比为实时探针状态与节点负载
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
                <text className="lm-panel-no mono" x={p.rect.x + 16} y={p.rect.y + 23}>{p.no}</text>
                <text className="lm-panel-title" x={p.rect.x + 46} y={p.rect.y + 23}>{p.zh}</text>
                {p.rect.w < 420 ? (
                  <text className="lm-panel-sum mono" x={p.rect.x + 46} y={p.rect.y + 41}>
                    {p.summary}
                  </text>
                ) : (
                  <text
                    className="lm-panel-sum mono"
                    x={p.rect.x + p.rect.w - 16}
                    y={p.rect.y + 23}
                    textAnchor="end"
                  >
                    {p.en ? `${p.summary} · ${p.en}` : p.summary}
                  </text>
                )}
                {p.load && (
                  <g className="lm-panel-load">
                    <text className="lm-load-k" x={p.rect.x + 46} y={p.rect.y + 41}>node3</text>
                    <text className="lm-load-k" x={p.rect.x + 96} y={p.rect.y + 41}>CPU</text>
                    <text className="lm-load-v mono" x={p.rect.x + 126} y={p.rect.y + 41}>{fmtPct(p.load.cpu)}</text>
                    <rect className="lm-bar-bg" x={p.rect.x + 160} y={p.rect.y + 36} width="70" height="3" />
                    <rect
                      className="lm-bar-fill"
                      x={p.rect.x + 160}
                      y={p.rect.y + 36}
                      width={70 * p.load.cpu}
                      height="3"
                      fill={loadColor(p.load.cpu)}
                    />
                    <text className="lm-load-k" x={p.rect.x + 248} y={p.rect.y + 41}>内存</text>
                    <text className="lm-load-v mono" x={p.rect.x + 284} y={p.rect.y + 41}>{fmtPct(p.load.mem)}</text>
                    <rect className="lm-bar-bg" x={p.rect.x + 318} y={p.rect.y + 36} width="70" height="3" />
                    <rect
                      className="lm-bar-fill"
                      x={p.rect.x + 318}
                      y={p.rect.y + 36}
                      width={70 * p.load.mem}
                      height="3"
                      fill={loadColor(p.load.mem)}
                    />
                  </g>
                )}
                <line
                  className="lm-panel-rule"
                  x1={p.rect.x + 14}
                  y1={p.rect.y + HEADER_H - 6}
                  x2={p.rect.x + p.rect.w - 14}
                  y2={p.rect.y + HEADER_H - 6}
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
              const cx = t.rect.x + 30;
              const cy = t.rect.y + 43;
              const tx = t.rect.x + 62;
              const compact = t.rect.h < 60;
              return (
                <g key={`${p.key}:${t.id}`} className={`lm-tile ${t.state.tone}`}>
                  <title>{`${t.name}${t.node ? ` @${t.node}` : ''} · ${t.state.text} · ${t.state.detail}`}</title>
                  <circle className="lm-medal" cx={cx} cy={compact ? t.rect.y + t.rect.h / 2 : cy} r={MEDAL_R} />
                  <g
                    className="lm-ico"
                    transform={`translate(${cx - 11} ${(compact ? t.rect.y + t.rect.h / 2 : cy) - 11}) scale(0.92)`}
                  >
                    {iconShapes(t.icon)}
                  </g>
                  <text className="lm-name" x={tx} y={compact ? t.rect.y + 21 : cy - 13}>{t.name}</text>
                  {t.node && (
                    <text className="lm-node mono" x={tx + textWidth(t.name, 13) + 10} y={compact ? t.rect.y + 21 : cy - 13}>
                      {`@${t.node}`}
                    </text>
                  )}
                  <text className="lm-cap" x={tx} y={compact ? t.rect.y + 37 : cy + 4}>
                    {clip(t.caption, t.rect.w - 62 - 12, 9)}
                  </text>
                  <circle className={`lm-dot ${t.state.tone}`} cx={tx + 3} cy={compact ? t.rect.y + t.rect.h / 2 : cy + 15} r="3.4" />
                  <text className="lm-state" x={tx + 12} y={compact ? t.rect.y + t.rect.h / 2 + 4 : cy + 19}>
                    {t.state.text}
                  </text>
                  {t.load && !compact && (
                    <g className="lm-load">
                      <text className="lm-load-k" x={t.rect.x + t.rect.w - 216} y={cy + 19}>CPU</text>
                      <text className="lm-load-v mono" x={t.rect.x + t.rect.w - 192} y={cy + 19}>
                        {fmtPct(t.load.cpu)}
                      </text>
                      <rect className="lm-bar-bg" x={t.rect.x + t.rect.w - 158} y={cy + 14} width="46" height="3" />
                      <rect
                        className="lm-bar-fill"
                        x={t.rect.x + t.rect.w - 158}
                        y={cy + 14}
                        width={46 * t.load.cpu}
                        height="3"
                        fill={loadColor(t.load.cpu)}
                      />
                      <text className="lm-load-k" x={t.rect.x + t.rect.w - 100} y={cy + 19}>内存</text>
                      <text className="lm-load-v mono" x={t.rect.x + t.rect.w - 76} y={cy + 19}>
                        {fmtPct(t.load.mem)}
                      </text>
                      <rect className="lm-bar-bg" x={t.rect.x + t.rect.w - 42} y={cy + 14} width="26" height="3" />
                      <rect
                        className="lm-bar-fill"
                        x={t.rect.x + t.rect.w - 42}
                        y={cy + 14}
                        width={26 * t.load.mem}
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
            {layout.links.filter((l) => l.labelAt).map((l) => {
              const at = l.labelAt!;
              if (at.lines && l.labelLines) {
                return (
                  <text key={`lb-${l.id}`} className="lm-label" x={at.x} y={at.y} textAnchor={at.anchor}>
                    <tspan x={at.x} dy="0">{l.labelLines[0]}</tspan>
                    <tspan x={at.x} dy="11">{l.labelLines[1]}</tspan>
                  </text>
                );
              }
              return (
                <text key={`lb-${l.id}`} className="lm-label" x={at.x} y={at.y} textAnchor={at.anchor}>
                  {l.label}
                </text>
              );
            })}
          </g>
        </svg>
      </div>

      <div className="lm-foot">
        <span className="lm-legend"><i className="lm-legend-dot ok" />正常</span>
        <span className="lm-legend"><i className="lm-legend-dot degraded" />降级</span>
        <span className="lm-legend"><i className="lm-legend-dot down" />不可达</span>
        <span className="lm-legend"><i className="lm-legend-dot unknown" />未知 / 可选</span>
        <span className="lm-legend"><i className="lm-legend-line solid" />协同关系</span>
        <span className="lm-legend"><i className="lm-legend-line dotted" />转诊 / 观测</span>
        <span className="lm-legend-note muted xs mono">
          {`逻辑分组 ${layout.panels.length} · 实体 ${entityCount} · 分组就绪 ${readyPanels}/${layout.panels.length}`}
          {status?.ok === false ? ' · K8s 不可达' : ''}
        </span>
      </div>
    </section>
  );
}

/** test hook: the layout builder is pure, so a harness can assert its geometry */
export const __overviewInternals = {
  buildLayout, CV_W, HEADER_H, TILE_H, PANEL_X, PANEL_W, OPS_X, OPS_W,
};
