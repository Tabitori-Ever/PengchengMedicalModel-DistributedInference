import { useEffect, useMemo, useRef, useState } from 'react';
import { api } from '../api';
import { useTicker } from '../hooks';
import Badge from '../components/Badge';
import TaskResultView from '../components/TaskResultView';
import { fmtTime, MODEL_LABEL } from '../utils';
import type { TaskItem, TaskResult } from '../types';

const STATUS_FILTERS = ['all', 'running', 'finished', 'failed'] as const;
const MODEL_FILTERS = ['all', 'medical', 'alexnet', 'clinic'] as const;

export default function HistoryPage() {
  const [tasks, setTasks] = useState<TaskItem[]>([]);
  const [status, setStatus] = useState<string>('all');
  const [model, setModel] = useState<string>('all');
  const [selId, setSelId] = useState('');
  const [err, setErr] = useState('');

  const refresh = async () => {
    try {
      const t = await api.listTasks();
      setTasks(t);
    } catch (e: any) { setErr(e?.message || '获取任务列表失败'); }
  };
  useEffect(() => { refresh(); }, []);
  useTicker(refresh, 5000);

  const filtered = useMemo(
    () => tasks.filter((t) =>
      (status === 'all' || t.status === status) &&
      (model === 'all' || t.model === model)),
    [tasks, status, model],
  );

  const clearAll = async () => {
    if (!window.confirm('清除全部任务记录？')) return;
    await api.clearTasks();
    refresh();
    setSelId('');
  };

  return (
    <div className="page">
      <header className="page-head">
        <h1>任务记录</h1>
        <p>全部提交记录：医疗推理 / 图像分类 / 内存监控（clinic）。</p>
      </header>

      <div className="filterbar">
        <span className="filter-label">状态</span>
        {STATUS_FILTERS.map((f) => (
          <button key={f} className={`mini ${status === f ? 'on' : ''}`}
            onClick={() => setStatus(f)}>{f === 'all' ? '全部' : f}</button>
        ))}
        <span className="filter-label">模型</span>
        {MODEL_FILTERS.map((f) => (
          <button key={f} className={`mini ${model === f ? 'on' : ''}`}
            onClick={() => setModel(f)}>{f === 'all' ? '全部' : MODEL_LABEL[f]}</button>
        ))}
        <span className="spacer" />
        <span className="muted">{filtered.length} 条</span>
        <button className="mini danger" onClick={clearAll}>清空</button>
      </div>

      {err && <div className="errbox">{err}</div>}

      <div className="history-grid">
        <section className="card hist-list">
          {filtered.length === 0 ? (
            <div className="empty">暂无符合条件的任务</div>
          ) : (
            <table className="datatable">
              <thead>
                <tr><th>模型</th><th>状态</th><th>来源</th><th>阶段</th>
                  <th>耗时</th><th>时间</th></tr>
              </thead>
              <tbody>
                {filtered.map((t) => (
                  <tr key={t.id} className={selId === t.id ? 'sel' : ''}
                    onClick={() => setSelId(t.id)}>
                    <td><Badge model={t.model} /></td>
                    <td><Badge status={t.status} /></td>
                    <td className="muted">{t.source || '—'}</td>
                    <td className="muted">{t.stage || '—'}</td>
                    <td className="mono">{t.duration_ms != null ? `${t.duration_ms.toFixed(1)}ms` : '—'}</td>
                    <td className="muted">{fmtTime(t.start_time)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
        <DetailPane id={selId} />
      </div>
    </div>
  );
}

function DetailPane({ id }: { id: string }) {
  const [item, setItem] = useState<TaskItem | null>(null);
  const [live, setLive] = useState<TaskResult | null>(null);
  const [err, setErr] = useState('');
  const finishedRef = useRef(false);

  useEffect(() => {
    setItem(null); setLive(null); setErr(''); finishedRef.current = false;
    if (!id) return;
    api.taskDetail(id).then(setItem).catch(() => setItem(null));
  }, [id]);

  useTicker(async () => {
    if (!id || finishedRef.current) return;
    try {
      const r = await api.taskResult(id);
      setLive(r);
      if (r.status === 'finished' || r.status === 'completed' || r.status === 'failed') {
        finishedRef.current = true;
      }
    } catch { /* poll until available */ }
  }, 2000, !!id);

  if (!id) return <aside className="card detail-pane empty">选择左侧任务查看详情</aside>;

  const del = async () => {
    if (!window.confirm('删除该任务？')) return;
    try {
      await api.deleteTask(id);
    } catch { /* ignore */ }
    window.location.reload();
  };

  return (
    <aside className="card detail-pane">
      <div className="card-headrow">
        <h3 className="card-title">详情</h3>
        <span className="mono xs">{id.slice(-12)}</span>
      </div>
      {err && <div className="errbox">{err}</div>}
      {item ? (
        <>
          <div className="kvrow">
            <span>模型</span><Badge model={item.model} />
            <span>状态</span><Badge status={live?.status || item.status} />
          </div>
          <div className="kvrow">
            <span>来源</span><b>{item.source || '—'}</b>
            <span>优先级</span><span className="mono">P{item.priority ?? '—'}</span>
          </div>
          <TaskResultView task={item} live={live} />
          <div className="btnrow end">
            <button className="mini danger" onClick={del}>删除</button>
          </div>
        </>
      ) : <div className="muted">任务不存在</div>}
    </aside>
  );
}
