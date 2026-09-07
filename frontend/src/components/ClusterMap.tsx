import { useRef, useState } from 'react';
import type { ClusterStatus, EntityModel } from '../types';
import { AFFINITY_LABEL, KIND_LABEL, loadColor } from '../utils';

const W = 1080;
const H = 760;
const NODE_POS: Record<string, { x: number; y: number }> = {
  node1: { x: 300, y: 250 },
  node2: { x: 780, y: 250 },
  node3: { x: 300, y: 610 },
  'desktop-jm5iec6': { x: 780, y: 610 },
};
const NODE_R = 128;
const EDGE_NODE_R = 148;

const KIND_R: Record<string, number> = { hospital: 34, clinic: 27 };
const KIND_COLOR: Record<string, string> = { hospital: '#0284c7', clinic: '#0e7490' };

interface Props {
  status: ClusterStatus;
  desired: Record<string, EntityModel>;
  selected?: string;
  onPick: (id: string) => void;
  onMove: (id: string, node: string) => void;
}

interface Pt { x: number; y: number }

export default function ClusterMap({ status, desired, selected, onPick, onMove }: Props) {
  const svgRef = useRef<SVGSVGElement | null>(null);
  const [drag, setDrag] = useState<{ id: string; pos: Pt } | null>(null);
  const [hoverNode, setHoverNode] = useState<string | null>(null);

  const nodes = status.nodes || [];
  const edgeNames = nodes
    .filter((n) => n.role === 'edge' || n.name === 'node1' || n.name === 'node2')
    .map((n) => n.name);
  const isEdge = (n: string) => edgeNames.includes(n);

  const nodePos = (name: string): Pt => NODE_POS[name] || { x: 400, y: 400 };
  const nodeR = (name: string) => (isEdge(name) ? EDGE_NODE_R : NODE_R);

  const toLocal = (e: React.PointerEvent): Pt | null => {
    const svg = svgRef.current;
    if (!svg) return null;
    const pt = svg.createSVGPoint();
    pt.x = e.clientX;
    pt.y = e.clientY;
    const m = svg.getScreenCTM();
    if (!m) return null;
    const p = pt.matrixTransform(m.inverse());
    return { x: p.x, y: p.y };
  };

  const entityNode = (id: string, ent: EntityModel, idx: number): string => {
    if (ent.affinity === 'fixed' && ent.node) return ent.node;
    // role-edge / spread-edge pods fan out across edge nodes
    const names = edgeNames.length ? edgeNames : ['node1', 'node2'];
    return names[idx % names.length];
  };

  const startDrag = (e: React.PointerEvent, id: string) => {
    // select immediately (click and drag both pick this pod)
    onPick(id);
    const ent = desired[id];
    if (!ent || ent.affinity !== 'fixed' || ent.replicas !== 1) return;
    e.stopPropagation();
    const p = toLocal(e);
    if (!p) return;
    setDrag({ id, pos: p });
    svgRef.current?.setPointerCapture(e.pointerId);
  };

  const onMoveEvt = (e: React.PointerEvent) => {
    if (!drag) return;
    const p = toLocal(e);
    if (!p) return;
    setDrag((d) => (d ? { ...d, pos: p } : d));
    let best: string | null = null;
    let bestD = 1e9;
    for (const n of nodes) {
      const c = nodePos(n.name);
      const d = Math.hypot(p.x - c.x, p.y - c.y);
      const limit = nodeR(n.name) * 1.6;
      if (d < limit && d < bestD) { bestD = d; best = n.name; }
    }
    setHoverNode(best);
  };

  const endDrag = () => {
    if (drag) {
      const id = drag.id;
      const target = hoverNode && isEdge(hoverNode) ? hoverNode : null;
      if (target && id) onMove(id, target);
    }
    setDrag(null);
    setHoverNode(null);
  };

  const loadPct = (n: any): number => {
    const l = n?.load;
    if (!l) return 0;
    return Math.max(Number(l.cpu || 0), Number(l.memory || 0));
  };

  // ---- slot layout: group editable pods by node, then readonly inside ----
  const editableList = Object.keys(desired)
    .sort()
    .map((id, idx) => ({ id, ent: desired[id], node: entityNode(id, desired[id], idx) }));

  const perNode: Record<string, { editable: typeof editableList; ro: any[] }> = {};
  for (const n of nodes) perNode[n.name] = { editable: [], ro: [] };
  for (const item of editableList) {
    if (perNode[item.node]) perNode[item.node].editable.push(item);
    else perNode[item.node] = { editable: [item], ro: [] };
  }
  for (const d of status.readonly || []) {
    for (const p of d.pods || []) {
      const node = p.node || '';
      if (perNode[node]) perNode[node].ro.push({ name: p.name, ready: p.ready });
    }
  }

  const ringPos = (c: Pt, slot: number, count: number, r: number, rad: number): Pt => {
    const ang = slot === 0 ? -Math.PI / 2 : -Math.PI / 2 + (2 * Math.PI * slot) / Math.max(count, 1);
    return { x: c.x + Math.cos(ang) * rad, y: c.y + Math.sin(ang) * rad };
  };

  return (
    <svg
      ref={svgRef}
      className="cluster-svg"
      viewBox={`0 0 ${W} ${H}`}
      onPointerMove={onMoveEvt}
      onPointerUp={endDrag}
      onPointerCancel={endDrag}
    >
      <defs>
        <pattern id="grid" width="42" height="42" patternUnits="userSpaceOnUse">
          <circle cx="1" cy="1" r="1.2" fill="#cbd5e1" opacity="0.5" />
        </pattern>
      </defs>
      <rect width={W} height={H} fill="#f8fafc" stroke="none" />
      <rect width={W} height={H} fill="url(#grid)" />

      {/* nodes (big circles with load ring) */}
      {nodes.map((n) => {
        const c = nodePos(n.name);
        const r = nodeR(n.name);
        const pct = loadPct(n);
        const C = 2 * Math.PI * r;
        return (
          <g key={n.name} className={`node ${hoverNode === n.name ? 'hot' : ''}`}>
            <circle cx={c.x} cy={c.y} r={r + 16}
              fill={isEdge(n.name) ? '#ecfeff' : '#f1f5f9'}
              stroke={hoverNode === n.name ? '#0284c7' : '#cbd5e1'}
              strokeWidth={hoverNode === n.name ? 2.5 : 1.2} />
            <circle cx={c.x} cy={c.y} r={r + 2} fill="none" stroke="#e2e8f0" strokeWidth="12" />
            <circle cx={c.x} cy={c.y} r={r + 2} fill="none"
              stroke={loadColor(pct)} strokeWidth="12" strokeLinecap="round"
              strokeDasharray={`${Math.max(pct * C, 3)} ${C}`}
              transform={`rotate(-90 ${c.x} ${c.y})`} />
            <circle cx={c.x} cy={c.y} r={r - 22} fill="#ffffff" stroke="#e2e8f0" strokeWidth="1.4" />
            <text x={c.x} y={c.y - 30} textAnchor="middle" className="node-name">{n.name}</text>
            <text x={c.x} y={c.y - 4} textAnchor="middle" className="node-role">
              {n.role || '—'}{isEdge(n.name) ? ' · edge' : ''}{n.ready ? '' : ' · NotReady'}
            </text>
            <text x={c.x} y={c.y + 26} textAnchor="middle"
              className={`node-load ${pct > 0.85 ? 'l-red' : pct > 0.6 ? 'l-amber' : ''}`}>
              {(pct * 100).toFixed(0)}%
            </text>
            <text x={c.x} y={c.y + 52} textAnchor="middle" className="node-cap">
              当前负载 (CPU/内存)
            </text>
            {perNode[n.name]?.editable.length > 0 && (
              <text x={c.x} y={c.y + 74} textAnchor="middle" className="node-cap">
                {perNode[n.name].editable.length} 个业务 Pod
              </text>
            )}
          </g>
        );
      })}

      {/* editable pods (small circles) */}
      {nodes.flatMap((n) => perNode[n.name]?.editable || []).map((item) => {
        const { id, ent } = item;
        const c = nodePos(item.node);
        const r = KIND_R[ent.kind] || 27;
        const count = perNode[item.node]?.editable.length || 1;
        const slot = (perNode[item.node]?.editable || []).findIndex((x) => x.id === id);
        const base = ringPos(c, slot, count, r, nodeR(item.node) + r + 10);
        const live = status.entities?.[id];
        const unready = live && live.pods.length > 0 && live.pods.some((p) => !p.ready);
        const pending = live && live.pods.length === 0;
        const pos = drag?.id === id ? drag.pos : base;
        return (
          <g key={id}
            className={`pod ${ent.kind} ${selected === id ? 'sel' : ''} ${pending ? 'pending' : ''}`}
            transform={`translate(${pos.x},${pos.y})`}
            onPointerDown={(e) => startDrag(e, id)}
            onClick={(e) => { e.stopPropagation(); onPick(id); }}>
            <circle r={r + 8} className="pod-halo" />
            <circle r={r} fill={KIND_COLOR[ent.kind]} stroke="#fff" strokeWidth={3}
              className={unready ? 'unready' : ''} />
            <text y={5} textAnchor="middle" className="pod-ico">
              {ent.kind === 'hospital' ? '院' : '诊'}
            </text>
            {ent.replicas > 1 && (
              <circle cx={r * 0.62} cy={-r * 0.62} r={10} fill="#0f172a" />
            )}
            {ent.replicas > 1 && (
              <text x={r * 0.62} y={-r * 0.62 + 4} textAnchor="middle" className="pod-n">{ent.replicas}</text>
            )}
            <text y={r + 18} textAnchor="middle" className="pod-label">{id}</text>
            <title>{`${id} · ${KIND_LABEL[ent.kind]}\n${AFFINITY_LABEL[ent.affinity]}${ent.affinity === 'fixed' && ent.node ? ' @ ' + ent.node : ''}${pending ? '\n暂无就绪实例' : ''}`}</title>
          </g>
        );
      })}

      {/* readonly pods - drawn inside their node circle, dim */}
      {nodes.map((n) => {
        const list = perNode[n.name]?.ro || [];
        if (list.length === 0) return null;
        const c = nodePos(n.name);
        const rad = nodeR(n.name) - 26;
        return list.map((p: any, i: number) => {
          const r = 9;
          const ang = -Math.PI / 2 + (2 * Math.PI * (i + 0.5)) / Math.max(list.length, 1);
          const pos = { x: c.x + Math.cos(ang) * rad, y: c.y + Math.sin(ang) * rad };
          return (
            <g key={`ro-${p.name}`} className="pod-ro" transform={`translate(${pos.x},${pos.y})`}>
              <circle r={r} fill={p.ready ? '#94a3b8' : '#e2e8f0'} stroke="#fff" strokeWidth={1.5} />
              <title>{p.name}</title>
            </g>
          );
        });
      })}

      {/* legend */}
      <g transform={`translate(20, ${H - 106})`}>
        <rect width={400} height={86} rx={12} fill="#ffffff" stroke="#e2e8f0" strokeWidth={1} />
        <circle cx={48} cy={26} r={11} fill="#0284c7" />
        <text x={66} y={30} className="legend-t">hospital pod（可拖拽）</text>
        <circle cx={218} cy={26} r={8} fill="#0e7490" />
        <text x={232} y={30} className="legend-t">clinic pod</text>
        <circle cx={318} cy={26} r={6} fill="#94a3b8" />
        <text x={330} y={30} className="legend-t">只读 Pod</text>
        <circle cx={42} cy={58} r={14} fill="none" stroke="#22c55e" strokeWidth={5} />
        <circle cx={92} cy={58} r={14} fill="none" stroke="#eab308" strokeWidth={5} />
        <circle cx={142} cy={58} r={14} fill="none" stroke="#dc2626" strokeWidth={5} />
        <text x={164} y={62} className="legend-t">节点负载环：绿 → 黄 → 红（拖动 Pod 到目标节点后点击“应用”）</text>
      </g>
    </svg>
  );
}
