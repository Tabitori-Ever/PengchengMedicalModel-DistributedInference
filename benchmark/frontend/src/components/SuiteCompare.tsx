import { useCallback, useEffect, useState } from 'react';
import { aggNum, api, errText, suiteDeltaPct } from '../api';
import type { SuiteAgg, SuiteCompareResp, SuiteConfigRow, SuiteInfo } from '../api';
import { MODE_ZH, SOURCES, modeColor } from '../types';
import { SuiteBars } from './Charts';
import { fmtMs, nLabel } from './format';
import { usePolling } from '../hooks';

/**
 * 套件对比：GET /api/compare/suite?suite_id=&source=
 * 结论第一行给出「协同平均 vs 本地平均，协同快 N%」；再按档位逐行对比并画分组柱状图。
 * 数据只来自该接口，speedup 为 null 时明确提示数据不足。
 */
export default function SuiteCompare({ refreshKey }: { refreshKey: number }) {
  const [suites, setSuites] = useState<SuiteInfo[]>([]);
  const [suitesErr, setSuitesErr] = useState('');
  const [suiteId, setSuiteId] = useState('');
  const [source, setSource] = useState('clinic-1');
  const [data, setData] = useState<SuiteCompareResp | null>(null);
  const [err, setErr] = useState('');
  const [tick, setTick] = useState(0);

  useEffect(() => {
    api.suites()
      .then((list) => {
        setSuites(list);
        setSuitesErr('');
        if (list.length > 0) setSuiteId((cur) => cur || list[0].suite_id);
      })
      .catch((e) => setSuitesErr(errText(e)));
  }, []);

  const load = useCallback(async (id: string, src: string) => {
    if (!id) { setData(null); return; }
    try {
      const resp = await api.compareSuite(id, src || undefined);
      setData(resp);
      setErr('');
    } catch (e) {
      setData(null);
      setErr(errText(e));
    }
  }, []);

  useEffect(() => { void load(suiteId, source); }, [suiteId, source, refreshKey, load]);
  usePolling(async () => { await load(suiteId, source); }, 10000, Boolean(suiteId));

  return (
    <SuiteCompareView
      data={data}
      suites={suites}
      suitesErr={suitesErr}
      err={err}
      suiteId={suiteId}
      source={source}
      tick={tick}
      onSuiteChange={setSuiteId}
      onSourceChange={setSource}
      onRefresh={() => { setTick((t) => t + 1); void load(suiteId, source); }}
    />
  );
}

export interface SuiteCompareViewProps {
  data: SuiteCompareResp | null;
  suites: SuiteInfo[];
  suiteId: string;
  source: string;
  err?: string;
  suitesErr?: string;
  tick?: number;
  onSuiteChange?: (id: string) => void;
  onSourceChange?: (src: string) => void;
  onRefresh?: () => void;
}

/** 纯展示层：给定 /api/compare/suite 的返回即可渲染（便于离线核对渲染结果） */
export function SuiteCompareView({
  data, suites, suiteId, source, err = '', suitesErr = '', tick = 0,
  onSuiteChange, onSourceChange, onRefresh,
}: SuiteCompareViewProps) {
  const overall = data?.overall || {};
  const collab = (overall.collaborative as SuiteAgg | undefined) || null;
  const local = (overall.local as SuiteAgg | undefined) || null;
  const speedup = data?.speedup || null;
  const configs: SuiteConfigRow[] = data?.configs || [];
  const suite = suites.find((s) => s.suite_id === suiteId) || null;

  const collabMean = aggNum(speedup?.collaborative_mean_ms) ?? aggNum(collab?.mean);
  const localMean = aggNum(speedup?.local_mean_ms) ?? aggNum(local?.mean);
  const fasterPct = aggNum(speedup?.collaborative_faster_pct);
  const ratio = aggNum(speedup?.ratio);

  return (
    <>
      <div className="segbar">
        <div className="filterbar inline">
          <span className="filter-label">套件</span>
          <select value={suiteId} onChange={(e) => onSuiteChange?.(e.target.value)}>
            {suites.map((s) => (
              <option key={s.suite_id} value={s.suite_id}>{s.name}（{s.task_count} 个任务）</option>
            ))}
            {suites.length === 0 ? <option value="">（无可用套件）</option> : null}
          </select>
          <span className="filter-label">发起方</span>
          <select value={source} onChange={(e) => onSourceChange?.(e.target.value)}>
            <option value="">全部</option>
            {SOURCES.map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
          <button className="mini" disabled={!suiteId} onClick={() => onRefresh?.()}>刷新</button>
          <span className="hint">第 {tick + 1} 次查看 · 每 10 s 自动刷新</span>
        </div>
      </div>

      {suitesErr ? <div className="errbox">/api/suites 不可用：{suitesErr}</div> : null}
      {err ? <div className="errbox">/api/compare/suite 不可用：{err}</div> : null}

      {/* ------------------------------------------------ 总体结论（最醒目的第一行） */}
      <section className="conclusion">
        {speedup && collabMean !== null && localMean !== null && fasterPct !== null ? (
          <>
            <div className="cc-main">
              协同平均 <b>{fmtMs(collabMean, 1)}</b> vs 本地平均 <b>{fmtMs(localMean, 1)}</b>
              ，协同快 <b className="cc-big">{fasterPct.toFixed(1)}%</b>
            </div>
            <div className="cc-sub">
              {data?.suite_name || suite?.name || suiteId} · 发起方 {data?.source || source || '全部'}
              {ratio !== null ? <> · 本地 / 协同 = {ratio.toFixed(2)} 倍</> : null}
            </div>
          </>
        ) : (
          <>
            <div className="cc-main">数据不足，需两种策略各跑一次套件</div>
            <div className="cc-sub">
              {data?.suite_name || suite?.name || suiteId} · 发起方 {data?.source || source || '全部'}
              {' · '}当前协同 {nLabel(collab?.n ?? 0)}，本地 {nLabel(local?.n ?? 0)}
            </div>
          </>
        )}

        <div className="cc-modes">
          <span className="cc-mode">
            <i className="sw" style={{ background: modeColor('collaborative') }} />
            云边端协同
            <b>mean {fmtMs(aggNum(collab?.mean), 1)}</b>
            <i className="muted">p50 {fmtMs(aggNum(collab?.p50), 1)} · p95 {fmtMs(aggNum(collab?.p95), 1)} · {nLabel(aggNum(collab?.n) ?? 0, aggNum(collab?.success))}</i>
          </span>
          <span className="cc-mode">
            <i className="sw" style={{ background: modeColor('local') }} />
            本地执行
            <b>mean {fmtMs(aggNum(local?.mean), 1)}</b>
            <i className="muted">p50 {fmtMs(aggNum(local?.p50), 1)} · p95 {fmtMs(aggNum(local?.p95), 1)} · {nLabel(aggNum(local?.n) ?? 0, aggNum(local?.success))}</i>
          </span>
        </div>
      </section>

      {/* ------------------------------------------------------------ 分组柱状图 */}
      <section className="card">
        <div className="card-headrow">
          <div className="card-title">分档位柱状 · client_total_ms（mean，带 p95 标记）</div>
          <span className="hint">{MODE_ZH.collaborative} / {MODE_ZH.local}</span>
        </div>
        <SuiteBars configs={configs} />
      </section>

      {/* ------------------------------------------------------------ 分档位对比表 */}
      <section className="card">
        <div className="card-headrow">
          <div className="card-title">分档位对比</div>
          <span className="hint">每档位 4 次重复 · 单位 ms · 括号内为样本量</span>
        </div>

        {configs.length === 0 ? (
          <div className="empty">暂无数据，请先各跑一次套件</div>
        ) : (
          <div className="tbl-scroll-x">
            <table className="dt">
              <thead>
                <tr>
                  <th>档位</th>
                  <th>参数</th>
                  <th>协同 n</th>
                  <th>协同 mean</th>
                  <th>协同 p50</th>
                  <th>本地 n</th>
                  <th>本地 mean</th>
                  <th>本地 p50</th>
                  <th>差值</th>
                </tr>
              </thead>
              <tbody>
                {configs.map((c) => {
                  const cm = (c.modes?.collaborative as SuiteAgg | undefined) || null;
                  const lm = (c.modes?.local as SuiteAgg | undefined) || null;
                  const cmMean = aggNum(cm?.mean);
                  const lmMean = aggNum(lm?.mean);
                  const pct = suiteDeltaPct(cmMean, lmMean);
                  return (
                    <tr key={String(c.spec_key || c.label)}>
                      <td><b>{c.label || '—'}</b></td>
                      <td className="mono">{paramsText(c.params)}</td>
                      <td className="mono num">{nLabel(aggNum(cm?.n) ?? 0, aggNum(cm?.success))}</td>
                      <td className="mono num strong">{fmtMs(cmMean, 1)}</td>
                      <td className="mono num">{fmtMs(aggNum(cm?.p50), 1)}</td>
                      <td className="mono num">{nLabel(aggNum(lm?.n) ?? 0, aggNum(lm?.success))}</td>
                      <td className="mono num strong">{fmtMs(lmMean, 1)}</td>
                      <td className="mono num">{fmtMs(aggNum(lm?.p50), 1)}</td>
                      <td>
                        {pct === null ? <span className="muted">—</span>
                          : pct >= 0
                            ? <span className="ok-tag">协同快 {pct.toFixed(1)}%</span>
                            : <span className="warn-tag">协同慢 {Math.abs(pct).toFixed(1)}%</span>}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}

        <div className="hint mt">
          差值 =（本地 mean − 协同 mean）/ 本地 mean。协同把 3 个分区并行派发给发起端、
          数据中心与伙伴边缘端；本地执行在发起端串行跑完 3 个分区。
        </div>
      </section>
    </>
  );
}

function paramsText(p: Record<string, unknown> | undefined): string {
  if (!p) return '—';
  const parts: string[] = [];
  if (p.instruments !== undefined) parts.push(`仪器 ${p.instruments}`);
  if (p.rows !== undefined) parts.push(`行数 ${p.rows}`);
  if (p.intensity !== undefined) parts.push(`强度 ${p.intensity}`);
  if (p.partition_count !== undefined) parts.push(`分区 ${p.partition_count}`);
  return parts.length ? parts.join(' · ') : '—';
}
