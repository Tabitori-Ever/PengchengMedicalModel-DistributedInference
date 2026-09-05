import { fmtMemBytes, fmtMs, fmtPct, MODEL_LABEL } from '../utils';
import type { TaskItem, TaskResult } from '../types';

interface Props {
  task: TaskItem | null;
  live?: TaskResult | null;
}

const STATUS_TEXT: Record<string, string> = {
  running: '运行中', queued: '排队中', finished: '已完成',
  completed: '已完成', failed: '失败', not_found: '未找到',
};

// waterfall row order per model
const WATERFALL: Record<string, Array<[string, string]>> = {
  medical: [
    ['队列等待', 'queue_wait_ms'],
    ['Worker（医院 Pod）', 'worker_total_ms'],
    ['　· 计算', 'worker_compute_ms'],
    ['　· 网络', 'worker_network_ms'],
    ['阶段间开销', 'inter_stage_ms'],
    ['Server（云端）', 'server_total_ms'],
    ['　· 计算', 'server_compute_ms'],
    ['　· 网络', 'server_network_ms'],
    ['流水线总计', 'pipeline_total_ms'],
    ['端到端总计', 'e2e_total_ms'],
  ],
  alexnet: [
    ['队列等待', 'queue_wait_ms'],
    ['Part1（医院 Pod）', 'part1_total_ms'],
    ['　· 计算', 'part1_compute_ms'],
    ['　· 网络', 'part1_network_ms'],
    ['阶段间开销', 'inter_stage_ms'],
    ['Part2（FC）', 'part2_total_ms'],
    ['　· 计算', 'part2_compute_ms'],
    ['　· 网络', 'part2_network_ms'],
    ['流水线总计', 'pipeline_total_ms'],
    ['端到端总计', 'e2e_total_ms'],
  ],
  clinic: [
    ['队列等待', 'queue_wait_ms'],
    ['Clinic 查询', 'clinic_total_ms'],
    ['流水线总计', 'pipeline_total_ms'],
    ['端到端总计', 'e2e_total_ms'],
  ],
};

export default function TaskResultView({ task, live }: Props) {
  const meta = task ?? null;
  const status: string = live?.status || meta?.status || 'unknown';
  const result: any = live?.result || meta?.result;
  const model: string = meta?.model
    || (result && (result.usage_percent != null || result.pod ? 'clinic'
        : result.bpCR_probability != null || result.predictions ? 'medical'
          : result.class_name ? 'alexnet' : '')) || '';
  const error = live?.error || meta?.error;
  const progress = live?.progress ?? meta?.progress;

  if (status === 'not_found') {
    return <div className="empty">任务不存在或已删除</div>;
  }
  if (status === 'running' || status === 'queued') {
    return (
      <div className="result-running">
        <div className="progressbar">
          <div className="progressfill" style={{ width: `${progress ?? 10}%` }} />
        </div>
        <div className="muted">
          阶段：{live?.stage || meta?.stage || '排队'} · 位置：{live?.node || meta?.node || '—'}
          {progress != null ? ` · ${progress}%` : ''}
        </div>
      </div>
    );
  }
  if (status === 'failed') {
    return <div className="errbox">任务失败：{error || '未知错误'}</div>;
  }
  if (!result) {
    return <div className="empty">暂无结果数据</div>;
  }

  return (
    <div className="result-wrap">
      {/* ---- medical ---- */}
      {model === 'medical' && (
        <>
          {result.bpCR_probability != null && (
            <div className="result-hero violet">
              <div className="hero-value">
                {(result.bpCR_probability * 100).toFixed(1)}%
              </div>
              <div className="hero-label">
                {result.bpCR_probability >= 0.5 ? 'pCR · 完全缓解（预测）' : 'non-pCR'}
                <span className="muted">bpCR 概率</span>
              </div>
            </div>
          )}
          {Array.isArray(result.predictions) && result.predictions.length > 0 && (
            <table className="datatable">
              <thead><tr><th>患者</th><th>bpCR 概率</th><th>预测</th></tr></thead>
              <tbody>
                {result.predictions.map((p: any, i: number) => (
                  <tr key={i}>
                    <td className="mono">{p.patient_id ?? i + 1}</td>
                    <td>{(p.bpCR_probability * 100).toFixed(1)}%</td>
                    <td>{p.prediction === 1 ? 'pCR' : 'non-pCR'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </>
      )}

      {/* ---- alexnet ---- */}
      {model === 'alexnet' && result.class_name && (
        <div className="result-hero orange">
          <div className="hero-value sm">{result.class_name}</div>
          <div className="hero-label">
            置信度 {(Number(result.score || 0) * 100).toFixed(1)}%
            {result.class_id != null && <span className="muted"> 类别 #{result.class_id}</span>}
          </div>
        </div>
      )}

      {/* ---- clinic (memory monitor) ---- */}
      {model === 'clinic' && (
        <div className="mem-card">
          <div className="mem-top">
            <div className="mem-ring" style={{
              background: `conic-gradient(#0891b2 ${(result.usage_percent ?? 0) * 3.6}deg, #e2e8f0 0deg)`,
            }}>
              <div className="mem-ring-in">
                <b>{result.usage_percent != null ? `${result.usage_percent}%` : '—'}</b>
              </div>
            </div>
            <div className="mem-detail">
              <div className="mem-pod mono">{result.pod ?? '—'}</div>
              <div className="muted">
                命名空间 {result.namespace ?? 'default'} · 数据来源：
                {result.source === 'cgroup' ? '本容器 cgroup（默认自身）' : 'metrics-server'}
              </div>
              <div className="mem-bytes">
                <span>已用 <b>{fmtMemBytes(result.usage_bytes)}</b></span>
                <span>上限 {fmtMemBytes(result.limit_bytes)}</span>
              </div>
              {result.measured_at && (
                <div className="muted xs">采样时间 {new Date(result.measured_at).toLocaleString('zh-CN', { hour12: false })}</div>
              )}
            </div>
          </div>
          {Array.isArray(result.containers) && result.containers.length > 0 && (
            <table className="datatable">
              <thead><tr><th>容器</th><th>已用</th><th>上限</th></tr></thead>
              <tbody>
                {result.containers.map((c: any, i: number) => (
                  <tr key={i}>
                    <td className="mono">{c.name}</td>
                    <td>{fmtMemBytes(c.usage_bytes)}</td>
                    <td>{fmtMemBytes(c.limit_bytes)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}

      {/* ---- latency waterfall ---- */}
      {waterfall(model)}
    </div>
  );

  function waterfall(m: string) {
    const metrics = result?.metrics || {};
    const keys = WATERFALL[m] || [];
    if (!metrics || Object.keys(metrics).length === 0) return null;
    const rows = keys.filter(([, k]) => metrics[k] != null);
    if (rows.length === 0) return null;
    const max = Math.max(...rows.map(([, k]) => Number(metrics[k]) || 0), 1);
    return (
      <div className="wf">
        {rows.map(([label, k], i) => (
          <div className="wf-row" key={k}>
            <span className={`wf-lbl ${label.startsWith('　') ? 'sub' : ''}`}>{label.trim()}</span>
            <div className="wf-bar">
              <div
                className="wf-fill"
                style={{
                  width: `${Math.max((Number(metrics[k]) / max) * 100, 0.5)}%`,
                  background: wfColor(m, i),
                }}
              />
            </div>
            <span className="wf-val mono">{fmtMs(Number(metrics[k]))}</span>
          </div>
        ))}
      </div>
    );
  }
}

function wfColor(model: string, idx: number): string {
  if (model === 'medical') {
    const palette = ['#94a3b8', '#6366f1', '#a5b4fc', '#c7d2fe', '#fbbf24', '#10b981', '#6ee7b7', '#a7f3d0', '#0f172a', '#334155'];
    return palette[idx % palette.length];
  }
  if (model === 'clinic') return '#94a3b8';
  const palette = ['#94a3b8', '#ea580c', '#fdba74', '#fed7aa', '#fbbf24', '#0d9488', '#5eead4', '#99f6e4', '#0f172a', '#334155'];
  return palette[idx % palette.length];
}

export { MODEL_LABEL, STATUS_TEXT, fmtPct };
