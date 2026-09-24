import type { PlanRun, ReqMode } from '../types';
import { MODE_ZH } from '../types';
import { StatusBadge } from '../components/Badge';
import { ProgressBar } from '../components/AttemptTable';
import { fmtInt, fmtTime } from '../components/format';

/** ③ 运行进度：只呈现状态与计数。 */
export default function PlanRunSection({ run }: { run: PlanRun | null }) {
  const phases = run?.config?.strategies ?? run?.strategies ?? [];

  return (
    <div className="card">
      <div className="card-headrow">
        <div className="card-title">③ 运行进度</div>
        {run ? <span className="hint mono">{run.plan_run_id}</span> : null}
      </div>

      {!run ? (
        <div className="empty">无数据</div>
      ) : (
        <>
          <div className="kvgrid">
            <div className="kv"><span>方案</span><b>{run.plan_name}</b></div>
            <div className="kv"><span>标签</span><b>{run.label || '—'}</b></div>
            <div className="kv"><span>状态</span><b><StatusBadge status={run.status} /></b></div>
            <div className="kv"><span>当前阶段</span>
              <b>{run.current_phase ? (MODE_ZH[run.current_phase] || run.current_phase) : '—'}</b>
            </div>
            <div className="kv"><span>任务/阶段</span><b>{fmtInt(run.total_tasks_per_phase)}</b></div>
            <div className="kv"><span>阶段数</span><b>{phases.length}</b></div>
            <div className="kv"><span>重传额度</span><b>{fmtInt(run.config?.retries)}</b></div>
            <div className="kv"><span>创建时间</span><b>{fmtTime(run.created_at)}</b></div>
            <div className="kv"><span>开始时间</span><b>{fmtTime(run.started_at)}</b></div>
            <div className="kv"><span>结束时间</span><b>{fmtTime(run.finished_at)}</b></div>
          </div>

          {run.error ? <div className="errbox">{run.error}</div> : null}

          <div className="phase-progress">
            {phases.map((ph) => {
              const p = run.progress?.[ph];
              const total = p?.total ?? 0;
              const finished = (p?.completed ?? 0) + (p?.failed ?? 0);
              const state = !run.live ? 'done'
                : (run.current_phase === ph ? 'live' : 'wait');
              return (
                <div key={ph} className={`phase-block ${state}`}>
                  <div className="phase-head">
                    <b>{MODE_ZH[ph] || ph}</b>
                    {run.live ? (
                      <span className="hint">
                        {state === 'live' ? '执行中' : '等待中'}
                      </span>
                    ) : <StatusBadge status={run.status} />}
                  </div>
                  <ProgressBar finished={finished} total={total} />
                  <div className="tbl-scroll-x">
                    <table className="dt small">
                      <thead>
                        <tr>
                          <th>总数</th><th>完成</th><th>失败</th><th>执行中</th>
                          <th>待执行</th><th>重传</th>
                        </tr>
                      </thead>
                      <tbody>
                        <tr>
                          <td className="mono num">{fmtInt(p?.total)}</td>
                          <td className="mono num">{fmtInt(p?.completed)}</td>
                          <td className="mono num">{fmtInt(p?.failed)}</td>
                          <td className="mono num">{fmtInt(p?.running)}</td>
                          <td className="mono num">{fmtInt(p?.pending)}</td>
                          <td className="mono num">
                            {fmtInt(run.phases?.find((x) => x.phase === ph)?.retries)}
                          </td>
                        </tr>
                      </tbody>
                    </table>
                  </div>
                </div>
              );
            })}
          </div>

          {run.runs?.length ? (
            <>
              <div className="card-title mt">子运行</div>
              <div className="tbl-scroll-x">
                <table className="dt small">
                  <thead>
                    <tr>
                      <th>阶段</th><th>执行单元</th><th>任务类型</th><th>发起方</th>
                      <th>任务数</th><th>状态</th><th>run_id</th>
                    </tr>
                  </thead>
                  <tbody>
                    {run.runs.map((r) => (
                      <tr key={r.run_id}>
                        <td>{MODE_ZH[r.phase as ReqMode] || r.phase}</td>
                        <td><b>{r.unit}</b></td>
                        <td>{r.kind}</td>
                        <td className="mono">{r.source}</td>
                        <td className="mono num">{fmtInt(r.repeats)}</td>
                        <td><StatusBadge status={r.status} /></td>
                        <td className="mono wrap" style={{ fontSize: 11 }}>{r.run_id}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          ) : null}
        </>
      )}
    </div>
  );
}
