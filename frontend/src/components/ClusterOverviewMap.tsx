import { useMemo } from 'react';
import type { ClusterStatus, ClusterSummary, NodeInfo, PodInfo } from '../types';
import { NODE_ORDER, READONLY_LABEL, fmtPct, loadColor, nodeRoleLabel } from '../utils';
import { iconShapes, type IconKind } from './TaskTrajectoryMap';

/* ===========================================================================
   当前集群架构总览 · LIVE CLUSTER OVERVIEW

   The real topology of the running cluster (GET /cluster/status, polled by the
   architecture page on the same 5 s ticker): one card per Kubernetes node with
   role badge, readiness, live CPU / memory, and the Pods actually scheduled on
   it — business entities (hospital / clinic) keep their kind colour, platform
   deployments stay neutral grey. Hairline connectors show the control / data
   paths between the edge nodes, the control plane and the cloud data center.

   Everything is inline SVG; no data is fetched here.
   =========================================================================== */

const CV_W = 1240;
const CV_H = 470;
const CARD_W = 300;
const CARD_H = 172;
const MAX_CHIPS = 6;

interface Slot { x: number; y: number }

/** canonical home of each known node; unknown nodes fall back to spare slots */
const SLOTS: Record<string, Slot> = {
  'desktop-jm5iec6': { x: 210, y: 130 },
  'node3': { x: 620, y: 130 },
  'node1': { x: 210, y: 345 },
  'node2': { x: 1030, y: 345 },
};
const SPARE: Slot[] = [{ x: 620, y: 345 }, { x: 1030, y: 130 }, { x: 620, y: 130 }];
const CLOUD_NODE = 'node3';
const CONTROL_NODE = 'desktop-jm5iec6';
const FALLBACK_NODES = ['desktop-jm5iec6', 'node3', 'node1', 'node2'];

type RoleKey = 'cloud' | 'control' | 'edge';

/** cloud / control plane / edge — by node name first, then by reported role */
function roleOf(node: NodeInfo | undefined, name: string): RoleKey {
  const role = String(node?.role || '').toLowerCase();
  if (name === CLOUD_NODE) return 'cloud';
  if (name === CONTROL_NODE || role.includes('control')) return 'control';
  return 'edge';
}

/** platform deployment → icon */
function serviceIcon(name: string): IconKind {
  const n = name.toLowerCase();
  if (n.includes('medical')) return 'server';
  if (n.includes('dc-') || n.includes('datacenter') || n.includes('patient')) return 'database';
  if (n.includes('scheduler')) return 'scheduler';
  if (n.includes('redis')) return 'cache';
  if (n.includes('monitor') || n.includes('predict') || n.includes('grafana') || n.includes('prometheus')) return 'monitor';
  return 'server';
}

interface PodChip {
  key: string;
  kind: 'hospital' | 'clinic' | 'service';
  label: string;
  icon: IconKind;
  count: number;
  readyCount: number;
}

interface NodeCard {
  name: string;
  role: RoleKey;
  roleZh: string;
  roleEn: string;
  ready: boolean;
  cpu: number;
  mem: number;
  chips: PodChip[];
  entityCount: number;
  serviceCount: number;
  slot: Slot;
}

/** clip a mono label line to a pixel budget (same metrics as the CSS font) */
function clipText(text: string, maxPx: number, size = 9): string {
  let w = 0;
  let out = '';
  for (const ch of text) {
    const cw = /[\u3000-\u9fff\uff00-\uffef]/.test(ch) ? size * 1.02 : size * 0.62;
    if (w + cw > maxPx) return `${out}…`;
    w += cw;
    out += ch;
  }
  return out;
}

function num(v: unknown): number {
  const n = Number(v);
  return Number.isFinite(n) ? Math.min(1, Math.max(0, n)) : 0;
}

function groupByNode(pods: PodInfo[] | undefined): Record<string, PodInfo[]> {
  const out: Record<string, PodInfo[]> = {};
  (pods || []).forEach((p) => {
    const n = String(p?.node || '');
    if (!n) return;
    (out[n] = out[n] || []).push(p);
  });
  return out;
}

/** rect anchor: point on the card boundary in the direction of `to` */
function cardAnchor(from: Slot, to: Slot) {
  const dx = to.x - from.x;
  const dy = to.y - from.y;
  const hw = CARD_W / 2;
  const hh = CARD_H / 2;
  if (!dx && !dy) return { x: from.x, y: from.y };
  const sx = dx === 0 ? Infinity : hw / Math.abs(dx);
  const sy = dy === 0 ? Infinity : hh / Math.abs(dy);
  const s = Math.min(sx, sy);
  return { x: from.x + dx * s, y: from.y + dy * s };
}

export default function ClusterOverviewMap({
  status, summary, error, updatedAt,
}: {
  /** GET /cluster/status — nodes + entities + readonly deployments with pods */
  status?: ClusterStatus | null;
  /** GET /cluster/summary — fallback node list when status is unavailable */
  summary?: ClusterSummary | null;
  /** probe error; the previous snapshot stays on screen */
  error?: string | null;
  updatedAt?: Date | null;
}) {
  const cards = useMemo<NodeCard[]>(() => {
    const rawNodes: NodeInfo[] = (status?.nodes?.length ? status.nodes : summary?.nodes) || [];
    const byName: Record<string, NodeInfo> = {};
    rawNodes.forEach((n) => { if (n?.name) byName[n.name] = n; });

    const names = rawNodes.length
      ? [...rawNodes].map((n) => n.name).sort((a, b) => {
        const ra = NODE_ORDER.indexOf(a);
        const rb = NODE_ORDER.indexOf(b);
        return (ra < 0 ? NODE_ORDER.length : ra) - (rb < 0 ? NODE_ORDER.length : rb)
          || String(a).localeCompare(String(b));
      })
      : FALLBACK_NODES;

    // pods per node: business entities first (coloured by kind), then platform
    const podsByNode: Record<string, PodChip[]> = {};
    const push = (node: string, chip: PodChip) => {
      (podsByNode[node] = podsByNode[node] || []).push(chip);
    };
    Object.entries(status?.entities || {}).forEach(([name, ent]) => {
      const kind: PodChip['kind'] = ent?.kind === 'clinic' ? 'clinic' : 'hospital';
      Object.entries(groupByNode(ent?.pods)).forEach(([node, pods]) => {
        push(node, {
          key: `e:${name}`,
          kind,
          label: name,
          icon: kind === 'clinic' ? 'clinic' : 'hospital',
          count: pods.length,
          readyCount: pods.filter((p) => p?.ready).length,
        });
      });
    });
    (status?.readonly || []).forEach((rd) => {
      Object.entries(groupByNode(rd?.pods)).forEach(([node, pods]) => {
        push(node, {
          key: `r:${rd.name}`,
          kind: 'service',
          label: rd.name,
          icon: serviceIcon(rd.name),
          count: pods.length,
          readyCount: pods.filter((p) => p?.ready).length,
        });
      });
    });

    let spare = 0;
    return names.map((name) => {
      const node = byName[name];
      const role = roleOf(node, name);
      const roleLabel = nodeRoleLabel(node?.role, name);
      const chips = (podsByNode[name] || []).sort((a, b) => (a.kind === b.kind
        ? a.label.localeCompare(b.label)
        : (a.kind === 'service' ? 1 : 0) - (b.kind === 'service' ? 1 : 0)));
      const slot = SLOTS[name] || SPARE[spare++] || { x: 620, y: 345 };
      return {
        name,
        role,
        roleZh: roleLabel.zh,
        roleEn: roleLabel.en,
        ready: node ? !!node.ready : false,
        cpu: num(node?.load?.cpu),
        mem: num(node?.load?.memory),
        chips,
        entityCount: chips.filter((c) => c.kind !== 'service').reduce((a, c) => a + c.count, 0),
        serviceCount: chips.filter((c) => c.kind === 'service').reduce((a, c) => a + c.count, 0),
        slot,
      };
    });
  }, [status, summary]);

  const links = useMemo(() => {
    const byName: Record<string, NodeCard> = {};
    cards.forEach((c) => { byName[c.name] = c; });
    const cloud = byName[CLOUD_NODE];
    if (!cloud) return [];
    return cards
      .filter((c) => c.name !== CLOUD_NODE)
      .map((c) => ({ from: c, a: cardAnchor(c.slot, cloud.slot), b: cardAnchor(cloud.slot, c.slot) }));
  }, [cards]);

  const unscheduled = useMemo(() => {
    let n = 0;
    Object.values(status?.entities || {}).forEach((ent) => {
      (ent?.pods || []).forEach((p) => { if (!p?.node) n += 1; });
    });
    (status?.readonly || []).forEach((rd) => {
      (rd?.pods || []).forEach((p) => { if (!p?.node) n += 1; });
    });
    return n;
  }, [status]);

  const totals = useMemo(() => {
    const pods = cards.reduce((a, c) => a + c.entityCount + c.serviceCount, 0);
    const entities = cards.reduce((a, c) => a + c.entityCount, 0);
    const services = cards.reduce((a, c) => a + c.serviceCount, 0);
    const ready = cards.filter((c) => c.ready).length;
    return { pods, entities, services, ready };
  }, [cards]);

  const stamp = updatedAt ? updatedAt.toLocaleTimeString('zh-CN', { hour12: false }) : null;

  return (
    <section className="card cmap">
      <div className="card-headrow">
        <h3 className="card-title">当前集群架构总览 · LIVE CLUSTER OVERVIEW</h3>
        <span className="muted xs mono">
          {status?.version ? `v${status.version}` : '—'}
          <i className="arch-meta-sep">·</i>
          {`${totals.ready}/${cards.length} 节点 Ready`}
          <i className="arch-meta-sep">·</i>
          {stamp ? `最后更新 ${stamp}` : '尚未更新'}
        </span>
      </div>

      <p className="eyebrow tmap-caption">
        集群真实拓扑：控制面 / 边 / 云 节点承载的 Pod 与实时负载 —— 业务实体按类型着色，
        平台组件为中性灰；连线表示当前的调度与数据通路
      </p>

      {error && <div className="cm-inline-err">集群拓扑接口异常：{error}（保留上次成功数据）</div>}

      <div className="tmap-canvas">
        <svg
          className="cmap-svg"
          viewBox={`0 0 ${CV_W} ${CV_H}`}
          width="100%"
          role="img"
          aria-label="当前集群架构总览"
        >
          <title>当前集群架构总览</title>

          <g className="cm-links">
            {links.map((l) => (
              <g key={`ln-${l.from.name}`} className="cm-link">
                <line x1={l.a.x} y1={l.a.y} x2={l.b.x} y2={l.b.y} />
                <circle cx={(l.a.x + l.b.x) / 2} cy={(l.a.y + l.b.y) / 2} r="2.6" className="cm-link-dot" />
              </g>
            ))}
          </g>

          <g className="cm-cards">
            {cards.map((c) => {
              const x0 = c.slot.x - CARD_W / 2;
              const y0 = c.slot.y - CARD_H / 2;
              const x1 = x0 + CARD_W;
              const shown = c.chips.slice(0, MAX_CHIPS);
              const hidden = c.chips.length - shown.length;
              const namesLine = c.chips.length
                ? c.chips.map((ch) => (ch.count > 1 ? `${ch.label} ×${ch.count}` : ch.label)).join(' · ')
                : '该节点暂无业务 Pod';
              const names = clipText(
                hidden > 0 ? `${namesLine} … +${hidden}` : namesLine,
                CARD_W - 34,
              );
              return (
                <g key={c.name} className={`cm-card ${c.role} ${c.ready ? 'ok' : 'bad'}`}>
                  <title>{`${c.name} · ${c.roleZh} ${c.roleEn} · ${c.ready ? 'Ready' : 'NotReady'}`}</title>
                  <rect className="cm-card-bg" x={x0} y={y0} width={CARD_W} height={CARD_H} rx="3" />
                  <rect className="cm-card-bar" x={x0} y={y0} width="3" height={CARD_H} />

                  <text className="cm-node mono" x={x0 + 14} y={y0 + 23}>{c.name}</text>
                  <rect
                    className="cm-role"
                    x={x1 - 14 - (c.roleZh.length * 9 + c.roleEn.length * 5.6 + 18)}
                    y={y0 + 10}
                    width={c.roleZh.length * 9 + c.roleEn.length * 5.6 + 18}
                    height="17"
                    rx="2"
                  />
                  <text className="cm-role-t" x={x1 - 23} y={y0 + 22} textAnchor="end">
                    {`${c.roleZh} ${c.roleEn}`}
                  </text>

                  <circle className={`cm-dot ${c.ready ? 'ok' : 'bad'}`} cx={x0 + 19} cy={y0 + 43} r="3.6" />
                  <text className="cm-ready mono" x={x0 + 29} y={y0 + 47}>
                    {c.ready ? 'Ready' : 'NotReady'}
                  </text>
                  <text className="cm-counts mono" x={x1 - 14} y={y0 + 47} textAnchor="end">
                    {`实体 ${c.entityCount} · 组件 ${c.serviceCount}`}
                  </text>

                  <text className="cm-load-k" x={x0 + 14} y={y0 + 72}>CPU</text>
                  <text className="cm-load-v mono" x={x1 - 14} y={y0 + 72} textAnchor="end">
                    {fmtPct(c.cpu)}
                  </text>
                  <rect className="cm-bar-bg" x={x0 + 14} y={y0 + 78} width={CARD_W - 28} height="3" />
                  <rect
                    className="cm-bar-fill"
                    x={x0 + 14}
                    y={y0 + 78}
                    width={(CARD_W - 28) * c.cpu}
                    height="3"
                    fill={loadColor(c.cpu)}
                  />

                  <text className="cm-load-k" x={x0 + 14} y={y0 + 100}>内存</text>
                  <text className="cm-load-v mono" x={x1 - 14} y={y0 + 100} textAnchor="end">
                    {fmtPct(c.mem)}
                  </text>
                  <rect className="cm-bar-bg" x={x0 + 14} y={y0 + 106} width={CARD_W - 28} height="3" />
                  <rect
                    className="cm-bar-fill"
                    x={x0 + 14}
                    y={y0 + 106}
                    width={(CARD_W - 28) * c.mem}
                    height="3"
                    fill={loadColor(c.mem)}
                  />

                  <line className="cm-rule" x1={x0 + 14} y1={y0 + 122} x2={x1 - 14} y2={y0 + 122} />

                  {shown.map((ch, i) => {
                    const ix = x0 + 18 + i * 27;
                    const iy = y0 + 128;
                    return (
                      <g key={ch.key} className={`cm-chip ${ch.kind}`} transform={`translate(${ix} ${iy})`}>
                        <title>{`${READONLY_LABEL[ch.label]?.zh || ch.label}（${ch.label}）· ${ch.readyCount}/${ch.count} ready`}</title>
                        <g className="cm-ico" transform="scale(0.72)">{iconShapes(ch.icon)}</g>
                        {ch.count > 1 && (
                          <text className="cm-chip-n mono" x={16} y={3}>{ch.count}</text>
                        )}
                      </g>
                    );
                  })}
                  {c.chips.length === 0 && (
                    <text className="cm-empty" x={x0 + 18} y={y0 + 142}>—</text>
                  )}
                  <text className="cm-names mono" x={x0 + 14} y={y0 + 160}>{names}</text>
                </g>
              );
            })}
          </g>
        </svg>
      </div>

      <div className="cm-foot">
        <span className="cm-legend">
          <i className="cm-legend-ico hospital">
            <svg viewBox="0 0 24 24" aria-hidden="true">{iconShapes('hospital')}</svg>
          </i>
          医院实体
        </span>
        <span className="cm-legend">
          <i className="cm-legend-ico clinic">
            <svg viewBox="0 0 24 24" aria-hidden="true">{iconShapes('clinic')}</svg>
          </i>
          诊所实体
        </span>
        <span className="cm-legend">
          <i className="cm-legend-ico service">
            <svg viewBox="0 0 24 24" aria-hidden="true">{iconShapes('server')}</svg>
          </i>
          平台组件
        </span>
        <span className="cm-legend">
          <i className="cm-legend-line" />
          架构通路
        </span>
        <span className="cm-legend-note muted xs mono">
          {`Pod ${totals.pods} · 实体 ${totals.entities} · 组件 ${totals.services}`}
          {unscheduled > 0 ? ` · 未调度 ${unscheduled}` : ''}
          {status?.ok === false ? ' · K8s 不可达' : ''}
        </span>
      </div>
    </section>
  );
}
