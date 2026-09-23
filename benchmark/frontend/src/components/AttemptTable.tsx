import type { Attempt } from '../api';
import { attemptMetrics, isSuccess } from '../api';
import { ModeBadge, StatusBadge } from './Badge';
import { fmtMs, nLabel } from './format';

/**
 * 逐次 attempt 明细表：运行中的实验与历史实验共用同一张表。
 * 列全部来自 /api/runs/{run_id} 的 attempts[]，缺失显示「—」。
 */
export function AttemptTable({ attempts, showOrchestrator = true }: { attempts: Attempt[]; showOrchestrator?: boolean }) {
  if (attempts.length === 0) return <div className="empty">等待第一次 attempt 记录…</div>;
  return (
    <div className="tbl-scroll-x">
      <table className="dt">
        <thead>
          <tr>
            <th>#</th>
            <th>模式</th>
            {showOrchestrator ? <th>编排者</th> : null}
            <th>执行者</th>
            <th>client_total_ms</th>
            <th>排队</th>
            <th>计算</th>
            <th>网络</th>
            <th>状态</th>
          </tr>
        </thead>
        <tbody>
          {attempts.map((a, i) => {
            const m = attemptMetrics(a);
            return (
              <tr key={a.id ?? i} className={a.degraded ? 'row-warn' : ''}>
                <td className="mono">{a.attempt_index ?? i + 1}</td>
                <td><ModeBadge modeUsed={a.mode_used} degraded={a.degraded} modeRequested={a.mode_requested} /></td>
                {showOrchestrator ? <td className="mono">{a.orchestrator || '—'}</td> : null}
                <td className="mono">{a.executor || '—'}</td>
                <td className="mono num strong">{fmtMs(m.client_total_ms, 0)}</td>
                <td className="mono num">{fmtMs(m.queue_wait_ms, 0)}</td>
                <td className="mono num">{fmtMs(m.compute_ms_total, 0)}</td>
                <td className="mono num">{fmtMs(m.network_ms_total, 0)}</td>
                <td>
                  <StatusBadge status={a.status} />
                  {a.error ? <div className="err-inline">{a.error}</div> : null}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/** 明细表上方的样本量/统计小条（只用成功样本的 client_total_ms） */
export function AttemptStats({ attempts }: { attempts: Attempt[] }) {
  const ok = attempts
    .filter(isSuccess)
    .map((a) => attemptMetrics(a).client_total_ms)
    .filter((v): v is number => v !== null);
  const failed = attempts.filter((a) => (a.status || '').toLowerCase() === 'failed').length;
  if (attempts.length === 0) return null;
  const sorted = [...ok].sort((a, b) => a - b);
  const mean = sorted.length ? sorted.reduce((s, v) => s + v, 0) / sorted.length : null;
  return (
    <div className="mini-chips">
      <span className="mini-chip">attempt <b>{attempts.length}</b></span>
      <span className="mini-chip">成功样本 <b>{nLabel(ok.length)}</b></span>
      <span className="mini-chip">mean <b>{fmtMs(mean, 0)}</b></span>
      <span className="mini-chip">失败 <b>{failed}</b></span>
    </div>
  );
}

/** 进度条 + 完成/总数 */
export function ProgressBar({ finished, total }: { finished: number; total: number }) {
  const pct = total > 0 ? (finished / total) * 100 : 0;
  return (
    <>
      <div className="progressbar">
        <div className="progressfill" style={{ width: `${total > 0 ? pct : 4}%` }} />
      </div>
      <div className="prog-line mono">
        完成 {finished} / {total}
        {total > 0 ? <i className="muted">（{pct.toFixed(0)}%）</i> : null}
      </div>
    </>
  );
}
