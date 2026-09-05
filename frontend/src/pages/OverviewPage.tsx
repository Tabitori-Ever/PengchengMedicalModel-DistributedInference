import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api';
import { useTicker } from '../hooks';
import StatCard from '../components/StatCard';
import Badge from '../components/Badge';
import { fmtTime, MODEL_COLOR, MODEL_LABEL } from '../utils';
import type { ClusterSummary, TaskItem, TaskStats } from '../types';

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

  return (
    <div className="page">
      <header className="page-head">
        <h1>平台总览</h1>
        <p>医院 / 诊所 Pod 化架构的边云协同医疗推理平台 — 患者原始数据不出医院，仅中间特征上云。</p>
      </header>

      <section className="hero-card">
        <div className="hero-text">
          <h2>数据不出院 · 算力云端化</h2>
          <p>
            每个<b>医院 Pod</b>（node1 / node2 各一）内合并了医疗模型前端（DoubleTower worker）
            与 AlexNet 卷积前端（part1）两种能力；每个医院节点旁部署一个
            <b>诊所 Pod</b>（clinic），提供「查询指定 Pod 内存占用率」任务，默认返回自身。
            ResNet 骨干 + 融合分类（medical-server）与 AlexNet 全连接层（part2）运行于云端 node3，
            由统一调度器编排医疗推理、图像分类与内存监控三类任务。
          </p>
          <div className="hero-chips">
            <span><i style={{ background: '#0ea5e9' }} />hospital · worker + part1</span>
            <span><i style={{ background: '#0891b2' }} />clinic · pod 内存监控</span>
            <span><i style={{ background: '#7c3aed' }} />medical bpCR</span>
            <span><i style={{ background: '#ea580c' }} />alexnet 分类</span>
          </div>
        </div>
        <div className="hero-goto">
          <Link className="btn primary" to="/submit">＋ 提交任务</Link>
          <Link className="btn ghost" to="/cluster">◉ 编辑集群</Link>
        </div>
      </section>

      {err && <div className="errbox">{err}</div>}

      <section className="stats-grid">
        <StatCard label="节点（就绪/总数）" value={`${readyNodes}/${summary?.nodes?.length ?? 0}`}
          tone="default" hint={summary?.ok === false ? 'K8s 状态不可用' : undefined} />
        <StatCard label="业务实体（可编辑）" value={entityCount} tone="blue"
          hint="hospital-a/b + clinic-1/2 等" />
        <StatCard label="运行中 Pod" value={podTotal} tone="cyan" hint="含只读基础设施" />
        <StatCard label="任务总数" value={stats?.total ?? 0} tone="violet" />
        <StatCard label="运行中任务" value={stats?.running ?? 0} tone="amber"
          hint={`队列 ${stats?.queue_length ?? 0}`} />
      </section>

      {stats && stats.by_model && Object.keys(stats.by_model).length > 0 && (
        <section className="card">
          <h3 className="card-title">按模型统计</h3>
          <div className="model-chips">
            {Object.entries(stats.by_model).map(([m, n]) => (
              <span className="model-chip" key={m}>
                <i style={{ background: MODEL_COLOR[m] || '#64748b' }} />
                {MODEL_LABEL[m] || m} <b>{n}</b>
              </span>
            ))}
          </div>
        </section>
      )}

      <section className="card">
        <div className="card-headrow">
          <h3 className="card-title">最近任务</h3>
          <Link className="link" to="/history">全部任务 →</Link>
        </div>
        {tasks.length === 0 ? (
          <div className="empty">暂无任务，去「任务提交」页发起第一个推理吧。</div>
        ) : (
          <table className="datatable">
            <thead>
              <tr><th>ID</th><th>模型</th><th>来源</th><th>状态</th><th>阶段</th><th>耗时</th><th>提交时间</th></tr>
            </thead>
            <tbody>
              {tasks.map((t) => (
                <tr key={t.id}>
                  <td className="mono">{t.id}</td>
                  <td><Badge model={t.model} /></td>
                  <td>{t.source || '—'}</td>
                  <td><Badge status={t.status} /></td>
                  <td className="muted">{t.stage || '—'}</td>
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
