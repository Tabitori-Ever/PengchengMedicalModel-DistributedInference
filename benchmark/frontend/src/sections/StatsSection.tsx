import type { PlanRun } from '../types';
import { MODE_ZH } from '../types';
import { planKindLabel } from '../planDraft';
import { fmtInt, fmtMs } from '../components/format';

/** ⑤ 统计信息：只呈现数据（表头即口径），不写说明性文字。 */
export default function StatsSection({ run }: { run: PlanRun | null }) {
  const kinds = run?.comparison?.per_kind ?? [];
  const phases = run?.config?.strategies ?? run?.strategies ?? [];

  return (
    <div className="card">
      <div className="card-title">⑤ 统计信息</div>

      {!run || kinds.length === 0 ? (
        <div className="empty">无数据</div>
      ) : (
        <div className="tbl-scroll-x">
          <table className="dt">
            <thead>
              <tr>
                <th rowSpan={2}>任务类型</th>
                <th colSpan={8} className="grp-h">本地执行（未调度）</th>
                <th colSpan={8} className="grp-h">云边端协同</th>
                <th rowSpan={2}>差值</th>
              </tr>
              <tr>
                <th>n</th><th>完成</th><th>处理总用时</th><th>均值</th><th>p50</th>
                <th>p95</th><th>最慢</th><th>重传</th>
                <th>n</th><th>完成</th><th>处理总用时</th><th>均值</th><th>p50</th>
                <th>p95</th><th>最慢</th><th>重传</th>
              </tr>
            </thead>
            <tbody>
              {kinds.map((e) => {
                const l = e.modes?.local;
                const c = e.modes?.collaborative;
                return (
                  <tr key={e.kind}>
                    <td><b>{planKindLabel(run.config, e.kind)}</b>
                      <div className="hint mono">{e.kind}</div>
                    </td>
                    <td className="mono num">{fmtInt(l?.n)}</td>
                    <td className="mono num">{fmtInt(l?.completed)}</td>
                    <td className="mono num">{fmtMs(l?.processing_ms, 0)}</td>
                    <td className="mono num">{fmtMs(l?.mean)}</td>
                    <td className="mono num">{fmtMs(l?.p50)}</td>
                    <td className="mono num">{fmtMs(l?.p95)}</td>
                    <td className="mono num">{fmtMs(l?.max)}</td>
                    <td className="mono num">{fmtInt(l?.retries)}</td>
                    <td className="mono num">{fmtInt(c?.n)}</td>
                    <td className="mono num">{fmtInt(c?.completed)}</td>
                    <td className="mono num">{fmtMs(c?.processing_ms, 0)}</td>
                    <td className="mono num">{fmtMs(c?.mean)}</td>
                    <td className="mono num">{fmtMs(c?.p50)}</td>
                    <td className="mono num">{fmtMs(c?.p95)}</td>
                    <td className="mono num">{fmtMs(c?.max)}</td>
                    <td className="mono num">{fmtInt(c?.retries)}</td>
                    <td className="mono num">
                      {e.gain_pct === null || e.gain_pct === undefined ? '—' : (
                        <span className={e.gain_pct > 0 ? 'gd-up' : 'gd-down'}>
                          {e.gain_pct > 0 ? '+' : ''}{e.gain_pct.toFixed(2)}%
                        </span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {run?.by_kind?.length ? (
        <>
          <div className="card-title mt">按阶段汇总</div>
          <div className="tbl-scroll-x">
            <table className="dt small">
              <thead>
                <tr>
                  <th>阶段</th><th>任务类型</th><th>总数</th><th>完成</th><th>失败</th>
                  <th>重传</th><th>重传耗时</th>
                  <th>均值</th><th>p50</th><th>p95</th>
                </tr>
              </thead>
              <tbody>
                {run.by_kind.map((b) => (
                  <tr key={b.unit_id}>
                    <td>{MODE_ZH[b.phase] || b.phase}</td>
                    <td><b>{planKindLabel(run.config, b.kind)}</b></td>
                    <td className="mono num">{fmtInt(b.total)}</td>
                    <td className="mono num">{fmtInt(b.ok)}</td>
                    <td className="mono num">{fmtInt(b.fail)}</td>
                    <td className="mono num">{fmtInt(b.retries)}</td>
                    <td className="mono num">{fmtMs(b.retry_ms_total, 0)}</td>
                    <td className="mono num">{fmtMs(b.latency?.mean)}</td>
                    <td className="mono num">{fmtMs(b.latency?.p50)}</td>
                    <td className="mono num">{fmtMs(b.latency?.p95)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      ) : null}

      {run ? (
        <div className="hint">
          阶段 {phases.length} · 每阶段任务 {fmtInt(run.total_tasks_per_phase)}
        </div>
      ) : null}
    </div>
  );
}
