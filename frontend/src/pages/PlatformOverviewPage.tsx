import { useEffect, useState } from 'react';
import { NavLink } from 'react-router-dom';
import { api } from '../api';
import { useTicker } from '../hooks';
import { MODEL_COLOR, MODEL_LABEL } from '../utils';
import { TIER_LABEL } from '../terms';
import type { ClusterSummary, HealthMap, TaskStats } from '../types';

const MODELS = ['diagnosis', 'compute', 'sync', 'routine'] as const;

const ENTRIES: Array<{ to: string; title: string; desc: string }> = [
  { to: '/visual', title: '可视化', desc: '逻辑架构总览 · 任务执行轨迹 · 多任务并发' },
  { to: '/load', title: '集群负载', desc: '节点资源 · 实例就绪 · 组件健康 · 任务概览' },
  { to: '/test', title: '综合测试', desc: '四类任务混合批量执行与结果查看' },
  { to: '/cluster', title: '集群编排', desc: '地图 / 列表双视图编辑与下发' },
];

function healthOk(v: unknown): boolean {
  if (typeof v === 'boolean') return v;
  if (typeof v === 'string') return v === 'ok' || v === 'true';
  if (v && typeof v === 'object') {
    const o = v as Record<string, unknown>;
    const s = o.status ?? o.state ?? o.health ?? o.ok ?? o.ready;
    if (typeof s === 'boolean') return s;
    if (typeof s === 'string') return s === 'ok' || s === 'true' || s === 'running';
  }
  return false;
}

/** 管理平台 · 平台总览：只展示平台级概况与入口，不重复负载/结果细节。 */
export default function PlatformOverviewPage() {
  const [summary, setSummary] = useState<ClusterSummary | null>(null);
  const [stats, setStats] = useState<TaskStats | null>(null);
  const [health, setHealth] = useState<HealthMap | null>(null);
  const [err, setErr] = useState('');

  const refresh = async () => {
    try {
      const [c, t, h] = await Promise.all([
        api.clusterSummary(),
        api.taskStats(),
        api.health(),
      ]);
      setSummary(c); setStats(t); setHealth(h); setErr('');
    } catch (e: any) {
      setErr(e?.message || '部分数据获取失败（保留上次成功数据）');
    }
  };
  useEffect(() => { refresh(); }, []);
  useTicker(refresh, 10000);

  const nodes = summary?.nodes || [];
  const ready = nodes.filter((n) => n.ready).length;
  const byModel = stats?.by_model || {};
  const probes = Object.values(health || {});
  const okProbes = probes.filter(healthOk).length;

  return (
    <div className="page">
      <header className="page-head">
        <h1>平台总览</h1>
        <p>
          三级架构：{TIER_LABEL.cloud} · {TIER_LABEL.medical} · {TIER_LABEL.hospital}；
          四类任务：诊断 / 计算 / 通信 / 日常，由医疗中心与医院发起、数据中心统一调度。
        </p>
      </header>

      {err && <div className="errbox">{err}</div>}

      <section className="stats-grid">
        <div className="stat-card"><div className="stat-value">{ready}/{nodes.length}</div><div className="stat-label">节点就绪</div></div>
        <div className="stat-card"><div className="stat-value">{summary?.editable ?? '—'}</div><div className="stat-label">业务实体</div></div>
        <div className="stat-card"><div className="stat-value">{((summary?.editable_pods ?? 0) + (summary?.readonly_pods ?? 0)) || '—'}</div><div className="stat-label">运行实例</div></div>
        <div className="stat-card"><div className="stat-value">{stats?.total ?? '—'}</div><div className="stat-label">任务总数</div></div>
        <div className="stat-card"><div className="stat-value">{stats?.running ?? 0}</div><div className="stat-label">运行中 · 队列 {stats?.queue_length ?? 0}</div></div>
        <div className="stat-card"><div className="stat-value">{probes.length ? `${okProbes}/${probes.length}` : '—'}</div><div className="stat-label">组件健康</div></div>
      </section>

      <section className="card">
        <h3 className="card-title">任务类型分布</h3>
        <div className="model-chips">
          {MODELS.map((m) => (
            <span className="model-chip" key={m}>
              <i style={{ background: MODEL_COLOR[m] }} />
              {MODEL_LABEL[m]} <b>{byModel[m] ?? 0}</b>
            </span>
          ))}
        </div>
      </section>

      <section className="card">
        <h3 className="card-title">功能入口</h3>
        <div className="kvgrid">
          {ENTRIES.map((e) => (
            <div className="kv" key={e.to}>
              <span>
                <NavLink className="link" to={e.to}>{e.title}</NavLink>
              </span>
              <b className="muted" style={{ fontWeight: 400 }}>{e.desc}</b>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}
