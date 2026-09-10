import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api';
import { useTicker } from '../hooks';
import StatCard from '../components/StatCard';
import Badge from '../components/Badge';
import { fmtTime, MODEL_COLOR, MODEL_LABEL, MODELS } from '../utils';
import type { ClusterSummary, TaskItem, TaskStats } from '../types';

const KIND_DESC: Record<string, string> = {
  diagnosis: '双塔 bpCR 诊断：医院前端提取特征 → 云端 server 融合分类',
  compute: '多 Pod 协同计算：分区并行，云端聚合 checksum',
  sync: '患者库 P2P 云同步：断点补齐 + 自动备份',
  routine: '日常例行：边缘一键生成一次性 K8s Job 并回收',
};

export default function OverviewPage() {
  const [stats, setStats] = useState<TaskStats | null>(null);
  const [tasks, setTasks] = useState<TaskItem[]>([]);
  const [summary, setSummary] = useState<ClusterSummary | null>(null);
  const [err, setErr] = useState('');

  const refresh = async () => {
    try {
      const [s, t, c] = await Promise.all([
        api.taskStats(), api.listTasks(), api.clusterSummary(),
      ]);
      setStats(s); setTasks(t.slice(0, 8)); setSummary(c); setErr('');
    } catch (e: any) {
      setErr(e?.message || '加载失败');
    }
  };
  useEffect(() => { refresh(); }, []);
  useTicker(refresh, 8000);

  const readyNodes = (summary?.nodes || []).filter((n) => n.ready).length;
  const entityCount = summary?.editable ?? 0;
  const podTotal = (summary?.editable_pods ?? 0) + (summary?.readonly_pods ?? 0);

  const byModel = stats?.by_model || {};

  return (
    <div className="page">
      <header className="page-head">
        <h1>平台总览 <span className="ver-tag">v3.0 · {summary?.version || '3.0'}</span></h1>
        <p>云 / 边 / 端三层架构：患者数据不出院，仅中间特征上云；四类业务任务由统一调度器编排。</p>
      </header>

      <section className="hero-card">
        <div className="hero-text">
          <h2>医院为边 · 诊所为端 · 数据在云</h2>
          <p>
            每台<b>医院 Pod（边 edge，hospital-a / hospital-b）</b>承载医疗诊断前端与计算分区能力，
            每个<b>诊所 Pod（端 terminal，clinic-1 / clinic-2）</b>负责转诊发起与本地业务；
            <b>数据中心（云，node3）</b>运行 medical-server / dc-services / 调度器。
            v3.0 将任务收敛为四类：<b>诊断（bpCR）</b>、<b>计算</b>、<b>通信（患者库同步）</b>与<b>日常（例行 Job）</b>。
          </p>
          <div className="hero-chips">
            {MODELS.map((m) => (
              <span key={m}>
                <i style={{ background: MODEL_COLOR[m] }} />
                {MODEL_LABEL[m]} {m}
              </span>
            ))}
            <span><i style={{ background: '#334155' }} />医院 边 edge</span>
            <span><i style={{ background: '#64748b' }} />诊所 端 terminal</span>
            <span><i style={{ background: '#0284c7' }} />数据中心 云 cloud</span>
          </div>
        </div>
        <div className="hero-goto">
          <Link className="btn primary" to="/architecture">◈ 实时架构</Link>
          <Link className="btn ghost" to="/submit">＋ 提交任务</Link>
          <Link className="btn ghost" to="/test">⚡ 综合测试</Link>
          <Link className="btn ghost" to="/cluster">◉ 编辑集群</Link>
        </div>
      </section>

      {err && <div className="errbox">{err}</div>}

      <section className="stats-grid">
        <StatCard label="节点（就绪/总数）" value={`${readyNodes}/${summary?.nodes?.length ?? 0}`}
          tone="default" hint={summary?.ok === false ? 'K8s 状态不可用' : undefined} />
        <StatCard label="业务实体（可编辑）" value={entityCount} tone="blue"
          hint="hospital-a/b + clinic-1/2" />
        <StatCard label="运行中 Pod" value={podTotal} tone="cyan" hint="含只读基础设施" />
        <StatCard label="任务总数" value={stats?.total ?? 0} tone="violet" />
        <StatCard label="运行中任务" value={stats?.running ?? 0} tone="amber"
          hint={`队列 ${stats?.queue_length ?? 0}`} />
      </section>

      {Object.keys(byModel).length > 0 && (
        <section className="card">
          <h3 className="card-title">按任务类型统计</h3>
          <div className="model-chips">
            {MODELS.map((m) => (
              <span className="model-chip" key={m}>
                <i style={{ background: MODEL_COLOR[m] }} />
                {MODEL_LABEL[m]} <b>{byModel[m] ?? 0}</b>
              </span>
            ))}
          </div>
        </section>
      )}

      <section className="card">
        <h3 className="card-title">四类任务说明</h3>
        <div className="kind-cards">
          {MODELS.map((m) => (
            <div className="kind-card" key={m}>
              <span className="kind-dot" style={{ background: MODEL_COLOR[m] }} />
              <b>{MODEL_LABEL[m]}</b>
              <span className="muted xs">{KIND_DESC[m]}</span>
            </div>
          ))}
        </div>
      </section>

      <section className="card">
        <div className="card-headrow">
          <h3 className="card-title">最近任务</h3>
          <Link className="link" to="/history">全部任务 →</Link>
        </div>
        {tasks.length === 0 ? (
          <div className="empty">暂无任务，去「任务提交」页发起第一个任务吧。</div>
        ) : (
          <table className="datatable">
            <thead>
              <tr><th>ID</th><th>类型</th><th>来源</th><th>状态</th><th>阶段</th><th>耗时</th><th>提交时间</th></tr>
            </thead>
            <tbody>
              {tasks.map((t) => (
                <tr key={t.id}>
                  <td className="mono">{t.id}</td>
                  <td><Badge model={t.model} /></td>
                  <td className="muted">{t.source || '—'}</td>
                  <td><Badge status={t.status} /></td>
                  <td className="muted">
                    {t.status === 'failed' ? (t.error || '—').slice(0, 40) : t.stage || '—'}
                  </td>
                  <td className="mono">{t.duration_ms != null ? `${t.duration_ms.toFixed(1)}ms` : '—'}</td>
                  <td className="muted">{fmtTime(t.start_time)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
}
