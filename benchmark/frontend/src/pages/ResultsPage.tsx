import { Fragment, useMemo, useState } from 'react';
import {
  api, asAttemptList, attemptChecksums, attemptMetrics, attemptPrediction, attemptStages, errText,
  isSuccess, progressCounts, runLive, runProgressDone, runSpecLoose,
} from '../api';
import type { Attempt, Run, RunSummary } from '../api';
import type { SiteHealth } from '../types';
import { KIND_ZH, KINDS, MODE_ZH, statusZh } from '../types';
import { DegradeReason, KindBadge, ModeBadge, StatusBadge } from '../components/Badge';
import { AttemptStats, AttemptTable, ProgressBar } from '../components/AttemptTable';
import { podEntries, podReachable, schedulerState } from '../components/StatusBanner';
import { fmtMs, fmtProb, fmtTime, nLabel, probEq } from '../components/format';
import { usePolling } from '../hooks';

/**
 * 结果明细：
 *  - 上半部分：实验运行进度与逐次 attempt 明细（GET /api/runs、GET /api/runs/{id}）；
 *  - 下半部分：跨实验的最近单次结果（GET /api/results?limit=100）。
 * 正确性：诊断看 bpcr_actual vs bpcr_expected；计算看 checksums 是否一致。
 */
export default function ResultsPage({ focusRun }: { focusRun: string }) {
  /* ---------------------------------------------------- 运行进度与明细 */
  const [health, setHealth] = useState<SiteHealth | null>(null);
  const [runs, setRuns] = useState<Run[]>([]);
  const [details, setDetails] = useState<Record<string, Run>>({});
  const [runErr, setRunErr] = useState('');
  const [selected, setSelected] = useState<string>(focusRun || '');

  const sel = selected || focusRun;

  const anyLive = useMemo(
    () => runs.some((r) => runLive(r) && !runProgressDone(r.progress))
      || Boolean(sel && details[sel] && runLive(details[sel])),
    [runs, sel, details],
  );

  usePolling(async () => {
    try {
      const h = await api.health();
      setHealth(h);
    } catch {
      setHealth(null);
    }
    try {
      const list = await api.listRuns(50);
      setRuns(list);
      setRunErr('');
      const targets = list
        .filter((r) => runLive(r) && !runProgressDone(r.progress))
        .slice(0, 6)
        .map((r) => r.run_id);
      if (sel && !targets.includes(sel)) targets.unshift(sel);
      const pairs = await Promise.all(
        targets.filter(Boolean).map(async (id) => {
          try { return [id, await api.runDetail(id)] as const; } catch { return null; }
        }),
      );
      setDetails((prev) => {
        const next = { ...prev };
        for (const p of pairs) if (p) next[p[0]] = p[1];
        return next;
      });
    } catch (e) {
      setRunErr(errText(e));
    }
  }, anyLive ? 2500 : 8000, true);

  const shownId = sel && (details[sel] || runs.find((r) => r.run_id === sel)) ? sel : (runs[0]?.run_id || '');
  const shown = shownId ? (details[shownId] || runs.find((r) => r.run_id === shownId) || null) : null;
  const shownSpec = runSpecLoose(shown);
  const shownAttempts: Attempt[] = asAttemptList(shown?.attempts);
  const shownCounts = progressCounts(shown, shownAttempts);
  const sch = schedulerState(health);
  const pods = podEntries(health);
  const podsBad = pods.filter(([, v]) => podReachable(v) === false).length;
  const localReady = pods.filter(([, v]) => v?.local_mode_available === true).length;

  /* ------------------------------------------------- 最近单次结果明细 */
  const [rows, setRows] = useState<Attempt[]>([]);
  const [err, setErr] = useState('');
  const [kindF, setKindF] = useState('');
  const [sourceF, setSourceF] = useState('');
  const [onlyFail, setOnlyFail] = useState(false);
  const [onlyRun, setOnlyRun] = useState(false);
  const [open, setOpen] = useState<string | null>(null);

  usePolling(async () => {
    try {
      const r = await api.results(100);
      setRows(r);
      setErr('');
    } catch (e) {
      setErr(errText(e));
    }
  }, 5000, true);

  const sources = useMemo(
    () => Array.from(new Set(rows.map((r) => r.source).filter(Boolean))).sort() as string[],
    [rows],
  );

  // 计算类的 checksums 参照：同一 (run_id) 内第一次出现的 checksums
  const checksumRef = useMemo(() => {
    const m = new Map<string, string[]>();
    for (const r of rows) {
      const c = attemptChecksums(r);
      if (!c) continue;
      const key = `${r.run_id || ''}|${r.kind}|${r.source}`;
      if (!m.has(key)) m.set(key, c);
    }
    return m;
  }, [rows]);

  const view = useMemo(() => rows.filter((r) => {
    if (kindF && r.kind !== kindF) return false;
    if (sourceF && r.source !== sourceF) return false;
    if (onlyFail && (r.status || '').toLowerCase() !== 'failed') return false;
    if (onlyRun && focusRun && r.run_id !== focusRun) return false;
    return true;
  }), [rows, kindF, sourceF, onlyFail, onlyRun, focusRun]);

  return (
    <>
      <div className="statusline">
        <span className={`sl-item ${sch.known ? (sch.ok ? 'ok' : 'bad') : 'idle'}`}>
          调度器：{sch.known ? (sch.ok ? '可达' : '不可达') : '未知'}
        </span>
        <span className={`sl-item ${pods.length === 0 ? 'idle' : podsBad ? 'bad' : 'ok'}`}>
          站点：{pods.length === 0 ? '未知' : `${pods.length - podsBad}/${pods.length} 可达`}
        </span>
        <span className={`sl-item ${pods.length === 0 ? 'idle' : localReady === pods.length ? 'ok' : 'warn'}`}>
          本地执行能力：{pods.length === 0 ? '未知' : `${localReady}/${pods.length} 可用`}
        </span>
        <span className="sl-item idle">
          轮询：{anyLive ? '2.5 s' : '8 s'}
        </span>
      </div>

      <div className="live-grid">
        <section className="card">
          <div className="card-headrow">
            <div className="card-title">实验列表</div>
            <span className="hint">共 {runs.length} 个</span>
          </div>

          {runErr ? <div className="errbox">/api/runs 不可用：{runErr}</div> : null}
          {runs.length === 0 && !runErr ? <div className="empty">暂无数据，请先下发测试任务</div> : null}

          {runs.length > 0 && (
            <div className="runlist">
              {runs.map((r) => {
                const d = details[r.run_id];
                const ats = asAttemptList(d?.attempts);
                const c = progressCounts(d || r, ats);
                const live = runLive(d || r) && !runProgressDone((d || r).progress);
                return (
                  <button
                    key={r.run_id}
                    className={`runrow ${shownId === r.run_id ? 'on' : ''}`}
                    onClick={() => setSelected(r.run_id)}
                  >
                    <div className="rr-top">
                      <span className="rr-id mono">{r.run_id}</span>
                      <StatusBadge status={(d || r).status} />
                      {live ? <span className="rr-live">轮询中</span> : null}
                    </div>
                    <div className="rr-mid">
                      <span className="mono">{runSpecLoose(d || r).kind || '—'}</span>
                      <span className="mono muted">{runSpecLoose(d || r).source || '—'}</span>
                      <span className="muted">
                        {MODE_ZH[String(runSpecLoose(d || r).mode)] || runSpecLoose(d || r).mode || '—'}
                      </span>
                      {runSpecLoose(d || r).suite_id ? <span className="rr-suite">套件</span> : null}
                      <span className="rr-prog mono">{c.total ? `${c.finished}/${c.total}` : '—'}</span>
                    </div>
                    <div className="rr-bot">
                      <span>{runSpecLoose(d || r).label || '(无标签)'}</span>
                      <i className="mono">{fmtTime(r.created_at || r.created)}</i>
                    </div>
                  </button>
                );
              })}
            </div>
          )}
        </section>

        <section className="card">
          <div className="card-headrow">
            <div className="card-title">实验进度与逐次明细</div>
            {shownId ? <span className="hint mono">{shownId}</span> : null}
          </div>

          {!shownId ? <div className="empty">暂无数据，请先下发测试任务</div> : null}

          {shown ? (
            <>
              <div className="kvgrid">
                <div className="kv"><span>任务类型</span><b>{shownSpec.kind || '—'}</b></div>
                <div className="kv"><span>发起方</span><b>{shownSpec.source || '—'}</b></div>
                <div className="kv"><span>调度策略</span>
                  <b>{MODE_ZH[String(shownSpec.mode)] || shownSpec.mode || '—'}</b></div>
                <div className="kv"><span>套件</span>
                  <b className="mono">{shownSpec.suite_id || '—'}</b></div>
                <div className="kv"><span>任务数 / 并发</span>
                  <b>{shownSpec.repeats ?? '—'} / {shownSpec.concurrency ?? '—'}</b></div>
                <div className="kv"><span>创建 / 结束</span>
                  <b>{fmtTime(shown.created_at)}{shown.finished_at ? ` → ${fmtTime(shown.finished_at)}` : ''}</b></div>
                <div className="kv"><span>参数</span>
                  <b className="wrap mono">{shownSpec.params ? JSON.stringify(shownSpec.params) : '—'}</b></div>
                <div className="kv"><span>摘要</span><b>{summaryText(shown.summary)}</b></div>
              </div>

              <ProgressBar finished={shownCounts.finished} total={shownCounts.total} />

              <AttemptStats attempts={shownAttempts} />
              <AttemptTable attempts={shownAttempts} />
            </>
          ) : null}
        </section>
      </div>

      <div className="filterbar">
        <span className="filter-label">筛选</span>
        <select value={kindF} onChange={(e) => setKindF(e.target.value)}>
          <option value="">全部任务类型</option>
          {KINDS.map((k) => <option key={k} value={k}>{KIND_ZH[k]}</option>)}
        </select>
        <select value={sourceF} onChange={(e) => setSourceF(e.target.value)}>
          <option value="">全部发起方</option>
          {sources.map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
        <button className={`mini ${onlyFail ? 'on' : ''}`} onClick={() => setOnlyFail((v) => !v)}>仅失败</button>
        {focusRun ? (
          <button className={`mini ${onlyRun ? 'on' : ''}`} onClick={() => setOnlyRun((v) => !v)}
            title={`只看本次下发的实验 ${focusRun}`}>仅本次实验</button>
        ) : null}
        <span className="spacer" />
        <span className="hint">显示 {nLabel(view.length)} / 共 {nLabel(rows.length)}</span>
      </div>

      {err ? <div className="errbox">/api/results 不可用：{err}</div> : null}

      <div className="card">
        <div className="card-title">最近单次结果</div>

        {view.length === 0 && !err
          ? <div className="empty">暂无数据，请先下发测试任务</div>
          : (
            <div className="tbl-scroll-x">
              <table className="dt">
                <thead>
                  <tr>
                    <th>时间</th>
                    <th>类型</th>
                    <th>发起</th>
                    <th>调度策略</th>
                    <th>实际模式</th>
                    <th>降级</th>
                    <th>执行者</th>
                    <th>client_total_ms</th>
                    <th>排队</th>
                    <th>计算</th>
                    <th>网络</th>
                    <th>状态</th>
                    <th>错误</th>
                    <th>正确性</th>
                    <th>阶段</th>
                  </tr>
                </thead>
                <tbody>
                  {view.map((a, i) => {
                    const m = attemptMetrics(a);
                    const key = `${a.run_id || ''}-${a.id ?? i}`;
                    const stages = attemptStages(a);
                    const correctness = checkCorrectness(a, checksumRef);
                    return (
                      <Fragment key={key}>
                        <tr className={a.degraded ? 'row-warn' : ''}
                          onClick={() => setOpen(open === key ? null : key)}>
                          <td className="mono nowrap">{fmtTime(a.finished_at || a.created_at)}</td>
                          <td><KindBadge kind={a.kind} /></td>
                          <td className="mono">{a.source || '—'}</td>
                          <td><span className="muted">{MODE_ZH[String(a.mode_requested)] || a.mode_requested || '—'}</span></td>
                          <td><ModeBadge modeUsed={a.mode_used} degraded={a.degraded} /></td>
                          <td>
                            {a.degraded
                              ? <span className="b-deg"><DegradeReason reason={a.degrade_reason} /></span>
                              : <span className="muted">否</span>}
                          </td>
                          <td className="mono">{a.executor || '—'}</td>
                          <td className="mono num strong">{fmtMs(m.client_total_ms, 0)}</td>
                          <td className="mono num">{fmtMs(m.queue_wait_ms, 0)}</td>
                          <td className="mono num">{fmtMs(m.compute_ms_total, 0)}</td>
                          <td className="mono num">{fmtMs(m.network_ms_total, 0)}</td>
                          <td><StatusBadge status={a.status} /></td>
                          <td className="err-cell">{a.error || '—'}</td>
                          <td><CorrectnessCell r={correctness} a={a} /></td>
                          <td className="mono">{stages.length ? `${stages.length} 段` : '—'}</td>
                        </tr>
                        {open === key && (
                          <tr className="expandrow">
                            <td colSpan={15}>
                              <StagesTable a={a} />
                            </td>
                          </tr>
                        )}
                      </Fragment>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}

        <div className="footnote">
          正确性列：诊断比较 <b>bpcr_actual</b> 与 <b>bpcr_expected</b>；
          计算比较同一实验内各 attempt 的 <b>checksums</b> 是否一致。
          缺少校验数据时显示「无校验数据」，不做推断；点击行可展开分阶段明细。
        </div>
      </div>
    </>
  );
}

type Correctness =
  | { type: 'diag'; expected: number | null; actual: number | null; eq: boolean | null }
  | { type: 'checksum'; same: boolean | null; n: number }
  | { type: 'none' };

function checkCorrectness(a: Attempt, ref: Map<string, string[]>): Correctness {
  if (a.kind === 'diagnosis') {
    const expected = a.bpcr_expected ?? null;
    const actual = attemptPrediction(a);
    if (expected === null && actual === null) return { type: 'none' };
    // 概率逐位一致；数值浮点误差给 1e-6 容差
    return { type: 'diag', expected, actual, eq: probEq(expected, actual, 1e-6) };
  }
  if (a.kind === 'compute') {
    const c = attemptChecksums(a);
    if (!c) return { type: 'none' };
    const key = `${a.run_id || ''}|${a.kind}|${a.source}`;
    const base = ref.get(key);
    if (!base) return { type: 'checksum', same: null, n: c.length };
    const same = base.length === c.length && base.every((x, idx) => x === c[idx]);
    return { type: 'checksum', same, n: c.length };
  }
  return { type: 'none' };
}

function CorrectnessCell({ r, a }: { r: Correctness; a: Attempt }) {
  if (r.type === 'none') return <span className="muted">无校验数据</span>;
  if (r.type === 'diag') {
    return (
      <div className="ok-cell">
        <div className="mono">
          真值 {fmtProb(r.expected)} · 实际 {fmtProb(r.actual)}
        </div>
        {r.eq === null ? <span className="muted">缺一侧数据</span>
          : r.eq ? <span className="ok-tag">一致</span>
            : <span className="bad-tag">不一致</span>}
      </div>
    );
  }
  return (
    <div className="ok-cell">
      <div className="mono">checksums ×{r.n}</div>
      {r.same === null ? <span className="muted">无同组参照</span>
        : r.same ? <span className="ok-tag">一致</span>
          : <span className="bad-tag">不一致</span>}
      {a.error ? null : null}
    </div>
  );
}

function StagesTable({ a }: { a: Attempt }) {
  const stages = attemptStages(a);
  const m = attemptMetrics(a);
  return (
    <div className="expand">
      <div className="expand-head">
        <span className="mono">task_id {a.task_id || '—'}</span>
        <span className="mono">run_id {a.run_id || '—'}</span>
        <span className="mono">attempt #{a.attempt_index ?? '—'}</span>
        <span className="mono">orchestrator {a.orchestrator || '—'}</span>
        <span className="mono">pipeline_total_ms {fmtMs(m.pipeline_total_ms, 1)}</span>
        {a.metrics_source ? <span className="mono">指标来源 {String(a.metrics_source)}</span> : null}
        {a.detail && typeof a.detail === 'object'
          ? <span className="mono">
            站点路由 {String((a.detail as Record<string, unknown>).route || '—')}
            {Array.isArray((a.detail as Record<string, unknown>).switch_notes)
              ? ` · ${((a.detail as Record<string, unknown>).switch_notes as unknown[]).join(' / ')}`
              : ''}
          </span>
          : null}
      </div>
      {stages.length === 0
        ? <div className="empty">该 attempt 未返回 stages 明细</div>
        : (
          <table className="dt small">
            <thead>
              <tr><th>阶段</th><th>执行者</th><th>节点</th><th>耗时 ms</th><th>计算 ms</th><th>网络 ms</th><th>说明</th></tr>
            </thead>
            <tbody>
              {stages.map((s, i) => (
                <tr key={i}>
                  <td className="mono">{s.name || '—'}</td>
                  <td className="mono">{s.actor || '—'}</td>
                  <td className="mono">{s.node || '—'}</td>
                  <td className="mono num">{fmtMs(s.ms ?? null, 1)}</td>
                  <td className="mono num">{fmtMs(s.compute_ms ?? null, 1)}</td>
                  <td className="mono num">{fmtMs(s.network_ms ?? null, 1)}</td>
                  <td>{s.detail || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      {(() => {
        const d = a.result_detail;
        if (!d) return null;
        const ok = a.status ? statusZh(a.status) : '';
        return (
          <div className="expand-detail">
            <span className="expand-lbl">result_detail</span>
            <pre className="json">{JSON.stringify(d, null, 2)}</pre>
            {ok ? <span className="hint">状态：{ok}</span> : null}
          </div>
        );
      })()}
      {isSuccess(a) ? null : <span className="hint">该 attempt 未成功，指标可能不完整。</span>}
    </div>
  );
}

function summaryText(s: RunSummary | RunSummary[] | null | undefined): string {
  if (!s) return '—';
  const rows = Array.isArray(s) ? s : [s];
  if (rows.length === 0) return '—';
  return rows
    .map((r) => `${MODE_ZH[String(r.mode || r.mode_used)] || r.mode || r.mode_used || '?'} mean ${fmtMs(r.mean, 0)}（${nLabel(r.n, r.success)}）`)
    .join(' · ');
}
