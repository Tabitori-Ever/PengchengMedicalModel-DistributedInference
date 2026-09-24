import type { PlanRun, SuiteAgg, SuiteConfigRow } from '../types';
import { MODE_ZH } from '../types';
import { planKindLabel } from '../planDraft';
import { SuiteBars } from '../components/Charts';
import { fmtInt, fmtMs } from '../components/format';

/** ⑥ 性能对比：只呈现数据。 */
export default function CompareSection({ run }: { run: PlanRun | null }) {
  const kinds = run?.comparison?.per_kind ?? [];
  const phases = run?.config?.strategies ?? run?.strategies ?? [];

  const chartRows: SuiteConfigRow[] = kinds.map((e) => ({
    spec_key: e.kind,
    label: planKindLabel(run?.config, e.kind),
    params: (run?.config?.kinds?.find((k) => k.kind === e.kind)?.params ?? {}) as Record<string, unknown>,
    modes: {
      collaborative: toAgg(e.modes?.collaborative),
      local: toAgg(e.modes?.local),
    },
  })).filter((r) => (r.modes?.collaborative?.n ?? 0) > 0 || (r.modes?.local?.n ?? 0) > 0);

  return (
    <div className="card">
      <div className="card-title">⑥ 性能对比</div>

      {!run || chartRows.length === 0 ? (
        <div className="empty">无数据</div>
      ) : (
        <>
          <SuiteBars configs={chartRows} ariaLabel="按任务类型对比协同与本地执行的时延均值" />

          <div className="tbl-scroll-x">
            <table className="dt">
              <thead>
                <tr>
                  <th>任务类型</th>
                  <th>本地处理总用时</th><th>协同处理总用时</th>
                  <th>本地均值</th><th>协同均值</th>
                  <th>本地 p95</th><th>协同 p95</th>
                  <th>差值</th><th>比值</th>
                  <th>本地重传</th><th>协同重传</th>
                </tr>
              </thead>
              <tbody>
                {kinds.map((e) => {
                  const l = e.modes?.local;
                  const c = e.modes?.collaborative;
                  const ratio = (l?.processing_ms && c?.processing_ms)
                    ? l.processing_ms / c.processing_ms : null;
                  return (
                    <tr key={e.kind}>
                      <td><b>{planKindLabel(run.config, e.kind)}</b>
                        <div className="hint">n={fmtInt(l?.n)}/{fmtInt(c?.n)}</div>
                      </td>
                      <td className="mono num">{fmtMs(l?.processing_ms, 0)}</td>
                      <td className="mono num">{fmtMs(c?.processing_ms, 0)}</td>
                      <td className="mono num">{fmtMs(l?.mean)}</td>
                      <td className="mono num">{fmtMs(c?.mean)}</td>
                      <td className="mono num">{fmtMs(l?.p95)}</td>
                      <td className="mono num">{fmtMs(c?.p95)}</td>
                      <td className="mono num">
                        {e.gain_pct === null || e.gain_pct === undefined ? '—' : (
                          <span className={e.gain_pct > 0 ? 'gd-up' : 'gd-down'}>
                            {e.gain_pct > 0 ? '+' : ''}{e.gain_pct.toFixed(2)}%
                          </span>
                        )}
                      </td>
                      <td className="mono num">{ratio === null ? '—' : `${ratio.toFixed(2)}×`}</td>
                      <td className="mono num">{fmtInt(l?.retries)}</td>
                      <td className="mono num">{fmtInt(c?.retries)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          <div className="hint">阶段顺序 {phases.map((p) => MODE_ZH[p] || p).join(' → ') || '—'}</div>
        </>
      )}
    </div>
  );
}

function toAgg(m: {
  n?: number; completed?: number; mean?: number | null; p50?: number | null;
  p95?: number | null; min?: number | null; max?: number | null;
} | undefined): SuiteAgg {
  return {
    n: m?.n ?? 0, success: m?.completed ?? 0, fail: (m?.n ?? 0) - (m?.completed ?? 0),
    mean: m?.mean ?? null, p50: m?.p50 ?? null, p95: m?.p95 ?? null,
    min: m?.min ?? null, max: m?.max ?? null,
  };
}
