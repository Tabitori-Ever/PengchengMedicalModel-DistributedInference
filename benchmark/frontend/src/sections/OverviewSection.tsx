import type { PlanCriterionResult, PlanRun } from '../types';
import { MODE_ZH } from '../types';
import { fmtInt, fmtMs, fmtNum } from '../components/format';
import { ModeBadge } from '../components/Badge';

/**
 * ④ 测试结果总览：只呈现数据与判定结果（通过/不通过），
 * 不解释判定口径、不给结论性描述——结果由第三方测试判读。
 */
export default function OverviewSection({ run }: { run: PlanRun | null }) {
  const ov = run?.comparison?.overall;
  const criteria = run?.comparison?.criteria ?? [];

  return (
    <div className="card">
      <div className="card-headrow">
        <div className="card-title">④ 测试结果总览</div>
        {run ? <span className="hint mono">{run.plan_run_id}</span> : null}
      </div>

      {!run ? (
        <div className="empty">无数据</div>
      ) : (
        <>
          <div className="stat-cards">
            {(run.phases ?? []).map((p) => (
              <div key={p.phase} className={`stat-card ph-${p.phase}`}>
                <div className="sc-head">
                  <ModeBadge modeUsed={p.phase} />
                  <span className="hint">{MODE_ZH[p.phase] || p.phase}</span>
                </div>
                <div className="sc-grid">
                  <div><i>完成/总数</i><b>{fmtInt(p.completed)} / {fmtInt(p.tasks_total)}</b></div>
                  <div><i>完成率</i><b>{fmtNum(p.completion_pct, 2)}%</b></div>
                  <div><i>失败</i><b>{fmtInt(p.failed)}</b></div>
                  <div><i>重传</i><b>{fmtInt(p.retries)}</b></div>
                  <div><i>处理总用时</i><b>{fmtMs(p.processing_ms, 0)}</b></div>
                  <div><i>执行总用时</i><b>{fmtMs(p.exec_ms, 0)}</b></div>
                  <div><i>重传耗时</i><b>{fmtMs(p.retry_ms_total, 0)}</b></div>
                  <div><i>阶段墙钟</i><b>{fmtMs(p.total_wall_ms, 0)}</b></div>
                  <div><i>均值</i><b>{fmtMs(p.latency?.mean)}</b></div>
                  <div><i>p50</i><b>{fmtMs(p.latency?.p50)}</b></div>
                  <div><i>p95</i><b>{fmtMs(p.latency?.p95)}</b></div>
                  <div><i>最慢</i><b>{fmtMs(p.latency?.max)}</b></div>
                </div>
              </div>
            ))}
          </div>

          <table className="dt small overall-tbl">
            <thead>
              <tr>
                <th>对比项</th><th>本地执行</th><th>云边端协同</th><th>差值</th><th>比值</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td>处理总用时（含重传）</td>
                <td className="mono num">{fmtMs(ov?.local_processing_ms, 0)}</td>
                <td className="mono num">{fmtMs(ov?.collaborative_processing_ms, 0)}</td>
                <td className="mono num"><Pct v={ov?.processing_gain_pct} /></td>
                <td className="mono num"><Ratio a={ov?.local_processing_ms} b={ov?.collaborative_processing_ms} /></td>
              </tr>
              <tr>
                <td>执行总用时</td>
                <td className="mono num">{fmtMs(ov?.local_exec_ms, 0)}</td>
                <td className="mono num">{fmtMs(ov?.collaborative_exec_ms, 0)}</td>
                <td className="mono num">—</td>
                <td className="mono num"><Ratio a={ov?.local_exec_ms} b={ov?.collaborative_exec_ms} /></td>
              </tr>
              <tr>
                <td>阶段墙钟</td>
                <td className="mono num">{fmtMs(ov?.local_wall_ms, 0)}</td>
                <td className="mono num">{fmtMs(ov?.collaborative_wall_ms, 0)}</td>
                <td className="mono num"><Pct v={ov?.wall_gain_pct} /></td>
                <td className="mono num"><Ratio a={ov?.local_wall_ms} b={ov?.collaborative_wall_ms} /></td>
              </tr>
              <tr>
                <td>重传次数</td>
                <td className="mono num">{fmtInt(ov?.retries_local)}</td>
                <td className="mono num">{fmtInt(ov?.retries_collaborative)}</td>
                <td className="mono num">—</td>
                <td className="mono num">—</td>
              </tr>
            </tbody>
          </table>

          <div className="card-title mt">判定</div>
          <div className="tbl-scroll-x">
            <table className="dt small">
              <thead>
                <tr>
                  <th>判定项</th><th>要求</th><th>实测</th><th>结论</th>
                </tr>
              </thead>
              <tbody>
                {criteria.map((c) => (
                  <tr key={c.id}>
                    <td className="wrapcell">{c.label}</td>
                    <td className="mono num">{requireText(c)}</td>
                    <td className="mono num">{measureText(c)}</td>
                    <td><VerdictBadge v={c.verdict} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}

function Pct({ v }: { v: number | null | undefined }) {
  if (v === null || v === undefined) return <>—</>;
  return (
    <span className={v > 0 ? 'gd-up' : v < 0 ? 'gd-down' : ''}>
      {v > 0 ? '+' : ''}{v.toFixed(2)}%
    </span>
  );
}

function Ratio({ a, b }: { a: number | null | undefined; b: number | null | undefined }) {
  if (!a || !b) return <>—</>;
  return <>{`${(a / b).toFixed(2)}×`}</>;
}

/** 要求：只给数值与比较符，不写口径解释 */
function requireText(c: PlanCriterionResult): string {
  if (c.type === 'gain_gt') return `> ${c.value}${c.unit}`;
  if (c.type === 'complete_gt') return `> ${c.value}${c.unit}`;
  if (c.type === 'complete_gte') return `≥ ${c.value}${c.unit}`;
  return `≤ ${c.value}${c.unit}`;
}

function measureText(c: PlanCriterionResult): string {
  if (c.measured === null || c.measured === undefined) return '—';
  return `${c.measured}${c.unit}`;
}

function VerdictBadge({ v }: { v: PlanCriterionResult['verdict'] }) {
  const label = v === 'pass' ? '通过' : v === 'fail' ? '不通过' : '待实测';
  return <span className={`vr-badge ${v === 'pass' ? 'ok' : v === 'fail' ? 'bad' : 'warn'}`}>{label}</span>;
}
