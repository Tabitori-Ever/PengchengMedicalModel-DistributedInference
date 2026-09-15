import { useCallback, useEffect, useMemo, useState } from 'react';
import { api } from '../api';
import { useTicker } from '../hooks';
import type { ClusterStatus, HealthMap, TaskStats } from '../types';
import { healthDetail, healthState, loadColor, sortNodes } from '../utils';
import { PLATFORM_NAME, entityName } from '../terms';

const REFRESH_MS = 5000;
const NODES = ['desktop-jm5iec6', 'node1', 'node2', 'node3'];

function errText(e: any): string {
  if (e?.response?.status) return `HTTP ${e.response.status}`;
  return e?.message || '请求失败';
}

interface Row {
  name: string;
  role: string;
  ready: boolean;
  cpu: number;
  mem: number;
  known: boolean;
  pods: { label: string; kind: 'medical' | 'hospital' | 'service'; ready: number; total: number }[];
}

/**
 * 集群负载（管理平台）：集中展示节点资源、Pod 就绪、组件健康与任务概览。
 * 可视化页不再重复这些数字。
 */
export default function LoadPage() {
  const [cluster, setCluster] = useState<ClusterStatus | null>(null);
  const [health, setHealth] = useState<HealthMap | null>(null);
  const [stats, setStats] = useState<TaskStats | null>(null);
  const [errs, setErrs] = useState<string[]>([]);
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null);
  const [countdown, setCountdown] = useState(REFRESH_MS / 1000);

  const refresh = useCallback(async () => {
    const problems: string[] = [];
    const [c, h, t] = await Promise.all([
      api.clusterStatus().catch((e) => { problems.push(`集群状态 /cluster/status 不可用：${errText(e)}`); return null; }),
      api.health().catch((e) => { problems.push(`健康探针 /health 不可用：${errText(e)}`); return null; }),
      api.taskStats().catch((e) => { problems.push(`任务统计 /tasks/stats 不可用：${errText(e)}`); return null; }),
    ]);
    if (c) setCluster(c);
    if (h) setHealth(h);
    if (t) setStats(t);
    setErrs(problems);
    setUpdatedAt(new Date());
    setCountdown(REFRESH_MS / 1000);
  }, []);

  useEffect(() => { refresh(); }, [refresh]);
  useTicker(refresh, REFRESH_MS);
  useTicker(() => setCountdown((c) => (c <= 1 ? REFRESH_MS / 1000 : c - 1)), 1000);

  const nodes = useMemo(() => sortNodes(cluster?.nodes || []), [cluster]);

  const rows = useMemo<Row[]>(() => {
    const byName: Record<string, Row> = {};
    nodes.forEach((n) => {
      const cpu = Number(n?.load?.cpu);
      const mem = Number(n?.load?.memory);
      byName[n.name] = {
        name: n.name,
        role: n.name === 'node3' ? '数据中心'
          : n.name === 'desktop-jm5iec6' ? '控制面'
            : n.role === 'edge' || n.name === 'node1' || n.name === 'node2' ? '医疗中心 · 医院' : '—',
        ready: !!n.ready,
        cpu: Number.isFinite(cpu) ? Math.min(1, Math.max(0, cpu)) : 0,
        mem: Number.isFinite(mem) ? Math.min(1, Math.max(0, mem)) : 0,
        known: Number.isFinite(cpu) || Number.isFinite(mem),
        pods: [],
      };
    });

    Object.entries(cluster?.entities || {}).forEach(([name, ent]) => {
      const kind = ent?.kind === 'clinic' ? 'hospital' : 'medical';
      const byNode: Record<string, { r: number; t: number }> = {};
      (ent?.pods || []).forEach((p) => {
        const key = String(p?.node || '');
        if (!key) return;
        byNode[key] = byNode[key] || { r: 0, t: 0 };
        byNode[key].t += 1;
        if (p?.ready) byNode[key].r += 1;
      });
      Object.entries(byNode).forEach(([node, v]) => {
        if (!byName[node]) return;
        byName[node].pods.push({ label: entityName(name), kind, ready: v.r, total: v.t });
      });
    });
    (cluster?.readonly || []).forEach((rd) => {
      const byNode: Record<string, { r: number; t: number }> = {};
      (rd?.pods || []).forEach((p) => {
        const key = String(p?.node || '');
        if (!key) return;
        byNode[key] = byNode[key] || { r: 0, t: 0 };
        byNode[key].t += 1;
        if (p?.ready) byNode[key].r += 1;
      });
      Object.entries(byNode).forEach(([node, v]) => {
        if (!byName[node]) return;
        byName[node].pods.push({ label: rd.name, kind: 'service', ready: v.r, total: v.t });
      });
    });

    const order = (a: Row, b: Row) => NODES.indexOf(a.name) - NODES.indexOf(b.name);
    const list = Object.values(byName).sort(order);
    return list.length ? list : NODES.map((name) => ({
      name, role: '—', ready: false, cpu: 0, mem: 0, known: false, pods: [],
    }));
  }, [nodes, cluster]);

  const healthKeys = useMemo(() => Object.keys(health || {}), [health]);
  const readyNodes = rows.filter((r) => r.ready).length;
  const totalPods = rows.reduce((a, r) => a + r.pods.reduce((b, p) => b + p.total, 0), 0);
  const readyPods = rows.reduce((a, r) => a + r.pods.reduce((b, p) => b + p.ready, 0), 0);

  return (
    <div className="page wide">
      <header className="page-head row">
        <div>
          <h1>集群负载 <span className="ver-tag">{REFRESH_MS / 1000}s 自动刷新</span></h1>
          <p>
            {PLATFORM_NAME} 的运行视图：4 个节点的 CPU / 内存、业务实例与平台实例的就绪情况、
            组件探针结果，以及任务队列概览。
          </p>
        </div>
        <div className="toolbar">
          <span className="arch-meta mono">
            {updatedAt ? `最后更新 ${updatedAt.toLocaleTimeString('zh-CN', { hour12: false })}` : '尚未更新'}
            <i className="arch-meta-sep">·</i>
            {countdown}s 后刷新
          </span>
          <button className="btn primary sm" onClick={refresh}>刷新</button>
        </div>
      </header>

      {cluster?.ok === false && cluster?.error && (
        <div className="errbox">Kubernetes 不可达：{cluster.error}（负载数据可能过期）</div>
      )}
      {errs.map((e, i) => <div className="errbox" key={i}>{e}</div>)}

      <section className="load-kpis">
        <Kpi label="节点就绪" value={`${readyNodes}/${rows.length}`} accent />
        <Kpi label="实例就绪" value={`${readyPods}/${totalPods}`} accent />
        <Kpi label="队列长度" value={stats?.queue_length ?? 0} />
        <Kpi label="运行中任务" value={stats?.running ?? 0} />
        <Kpi label="任务总数" value={stats?.total ?? 0} />
        <Kpi label="组件探针" value={`${healthKeys.filter((k) => healthState(health?.[k]) === 'ok').length}/${healthKeys.length}`} />
      </section>

      <section className="card">
        <div className="card-headrow">
          <h3 className="card-title">节点资源</h3>
          <span className="muted xs mono">{cluster?.version ? `v${cluster.version}` : '—'}</span>
        </div>
        <div className="load-nodes">
          {rows.map((r) => (
            <div className="load-node" key={r.name}>
              <div className="load-node-head">
                <span className={`st-dot ${r.ready ? 'ok' : 'bad'}`} />
                <b className="mono">{r.name}</b>
                <span className="muted xs">{r.role}</span>
                <span className={`load-ready ${r.ready ? 'ok' : 'bad'}`}>{r.ready ? '就绪' : '未就绪'}</span>
              </div>
              <div className="load-row">
                <span className="load-k">CPU</span>
                <b className="load-v mono">{r.known ? `${Math.round(r.cpu * 100)}%` : '—'}</b>
                <span className="load-bar">
                  <i style={{ width: `${(r.known ? r.cpu : 0) * 100}%`, background: loadColor(r.cpu) }} />
                </span>
              </div>
              <div className="load-row">
                <span className="load-k">内存</span>
                <b className="load-v mono">{r.known ? `${Math.round(r.mem * 100)}%` : '—'}</b>
                <span className="load-bar">
                  <i style={{ width: `${(r.known ? r.mem : 0) * 100}%`, background: loadColor(r.mem) }} />
                </span>
              </div>
              <div className="load-pods">
                {r.pods.length === 0 && <span className="muted xs">该节点暂无业务实例</span>}
                {r.pods.map((p) => (
                  <span className={`load-pod ${p.kind}`} key={`${r.name}-${p.label}`}>
                    <b>{p.label}</b>
                    <i className="mono">{`${p.ready}/${p.total}`}</i>
                  </span>
                ))}
              </div>
            </div>
          ))}
        </div>
      </section>

      <section className="card">
        <div className="card-headrow">
          <h3 className="card-title">组件健康</h3>
          <span className="muted xs mono">{healthKeys.length} 项探针</span>
        </div>
        <div className="arch-chips">
          {healthKeys.length === 0 && <div className="muted xs">探针数据暂不可用</div>}
          {healthKeys.map((k) => {
            const state = healthState(health?.[k]);
            return (
              <div className={`arch-chip ${state}`} key={k} title={`${k} · ${healthDetail(health?.[k])}`}>
                <i className="chip-dot" />
                <span className="chip-body">
                  <b className="chip-name mono">{k}</b>
                </span>
                <span className={`chip-state mono ${state}`}>{state}</span>
              </div>
            );
          })}
        </div>
      </section>

      <section className="card">
        <div className="card-headrow">
          <h3 className="card-title">任务概览</h3>
          <span className="muted xs mono">四类任务累计</span>
        </div>
        <div className="load-kinds">
          {Object.entries(stats?.by_model || {}).map(([k, v]) => (
            <span className="load-kind" key={k}>
              <b>{k === 'diagnosis' ? '诊断' : k === 'compute' ? '计算' : k === 'sync' ? '通信' : k === 'routine' ? '日常' : k}</b>
              <i className="mono">{v}</i>
            </span>
          ))}
          {!stats && <div className="muted xs">任务统计暂不可用</div>}
        </div>
      </section>
    </div>
  );
}

function Kpi({ label, value, accent }: { label: string; value: string | number; accent?: boolean }) {
  return (
    <div className={`load-kpi ${accent ? 'accent' : ''}`}>
      <div className="lk-value mono">{value}</div>
      <div className="lk-label">{label}</div>
    </div>
  );
}
