import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  api, asAttemptList, attemptChecksums, attemptMetrics, attemptPrediction, attemptStages, compareGroups,
  errText, groupStageMeans, isSuccess, mean, modeBucket, percentile, runSpecLoose,
} from '../api';
import type { Attempt, CompareGroup, CompareResp, CompareRow, GroupCorrectness, MatrixRow, Run } from '../api';
import type { Stage } from '../types';
import { KIND_ZH, KINDS, MODE_ZH, modeColor } from '../types';
import { RequestedModeLabel } from '../components/Badge';
import GroupedBars, { StackedBreakdown } from '../components/Charts';
import SuiteCompare from '../components/SuiteCompare';
import { checksumsEqual, fmtMs, fmtProb, nLabel, probEq } from '../components/format';
import { usePolling } from '../hooks';

type View = 'suite' | 'run' | 'global' | 'matrix';

/**
 * 性能对比（核心页）：
 *  - 套件对比（默认，首屏）：GET /api/compare/suite 的总体结论 + 分档位对比 + 分组柱状图；
 *  - 本次实验内对比：读取该 run 的 attempts，按 mode_used 分桶后本地聚合；
 *  - 累计对比：GET /api/compare?kind=&source=；
 *  - 全量矩阵：GET /api/compare/matrix。
 * 只渲染 API 返回的数据，缺数据即空状态。
 */
export default function ComparePage({ focusRun, refreshKey }: { focusRun: string; refreshKey: number }) {
  const [view, setView] = useState<View>('suite');
  const [kindF, setKindF] = useState('');
  const [sourceF, setSourceF] = useState('');

  const [runs, setRuns] = useState<Run[]>([]);
  const [selRun, setSelRun] = useState(focusRun || '');
  const [runAttempts, setRunAttempts] = useState<Attempt[]>([]);
  const [runErr, setRunErr] = useState('');

  const [compare, setCompare] = useState<CompareResp | null>(null);
  const [cmpErr, setCmpErr] = useState('');
  const [fallbackRows, setFallbackRows] = useState<Attempt[]>([]);

  const [matrix, setMatrix] = useState<MatrixRow[]>([]);
  const [matrixErr, setMatrixErr] = useState('');

  useEffect(() => {
    if (focusRun) setSelRun(focusRun);
  }, [focusRun, refreshKey]);

  // 实验列表（用于「本次实验内对比」的选择器与筛选）
  usePolling(async () => {
    try { setRuns(await api.listRuns(50)); } catch { /* 列表失败不阻塞对比 */ }
  }, 10000, true);

  // 选中实验的 attempt 明细
  const loadRun = useCallback(async (id: string) => {
    if (!id) { setRunAttempts([]); return; }
    try {
      const d = await api.runDetail(id);
      setRunAttempts(asAttemptList(d.attempts));
      setRunErr('');
    } catch (e) {
      setRunAttempts([]);
      setRunErr(errText(e));
    }
  }, []);

  useEffect(() => { void loadRun(selRun); }, [selRun, loadRun, refreshKey]);

  // 运行中时继续刷新当前实验
  usePolling(async () => { if (selRun) await loadRun(selRun); }, 3000, Boolean(selRun));

  // 累计对比
  const loadCompare = useCallback(async () => {
    try {
      const q: Record<string, string> = {};
      if (kindF) q.kind = kindF;
      if (sourceF) q.source = sourceF;
      const resp = await api.compare(q);
      setCompare(resp);
      setCmpErr('');
      if (compareGroups(resp).length === 0) {
        // 后端可能尚未实现聚合：回退到 /api/results 本地聚合（仍只渲染真实数据）
        const rs = await api.results(100);
        setFallbackRows(rs);
      } else {
        setFallbackRows([]);
      }
    } catch (e) {
      setCompare(null);
      setCmpErr(errText(e));
      try { setFallbackRows(await api.results(100)); } catch { setFallbackRows([]); }
    }
  }, [kindF, sourceF]);

  useEffect(() => { if (view === 'global') void loadCompare(); }, [view, loadCompare]);

  const loadMatrix = useCallback(async () => {
    try {
      setMatrix(await api.compareMatrix());
      setMatrixErr('');
    } catch (e) {
      setMatrix([]);
      setMatrixErr(errText(e));
    }
  }, []);

  useEffect(() => { if (view === 'matrix') void loadMatrix(); }, [view, loadMatrix]);

  /* ---------------------------------------------------- 分桶聚合（本地） */
  const perRunRows = useMemo(
    () => aggregateAttempts(runAttempts),
    [runAttempts],
  );

  const fallbackAgg = useMemo(() => {
    const filtered = fallbackRows.filter((r) => {
      if (kindF && r.kind !== kindF) return false;
      if (sourceF && r.source !== sourceF) return false;
      return true;
    });
    return aggregateAttempts(filtered);
  }, [fallbackRows, kindF, sourceF]);

  const globalGroups = compareGroups(compare);
  const globalRows = useMemo(
    () => (globalGroups.length > 0 ? groupsToRows(globalGroups) : fallbackAgg),
    [globalGroups, fallbackAgg],
  );

  const rows = view === 'run' ? perRunRows : globalRows;
  const selRunObj = runs.find((r) => r.run_id === selRun);

  return (
    <>
      <div className="segbar">
        <div className="seg">
          <button className={`seg-btn ${view === 'suite' ? 'on' : ''}`} onClick={() => setView('suite')}>
            套件对比
          </button>
          <button className={`seg-btn ${view === 'run' ? 'on' : ''}`} onClick={() => setView('run')}>
            本次实验内对比
          </button>
          <button className={`seg-btn ${view === 'global' ? 'on' : ''}`} onClick={() => setView('global')}>
            累计对比
          </button>
          <button className={`seg-btn ${view === 'matrix' ? 'on' : ''}`} onClick={() => setView('matrix')}>
            全量矩阵
          </button>
        </div>

        {view === 'suite' ? null : (
          <div className="filterbar inline">
            <span className="filter-label">筛选</span>
            <select value={kindF} onChange={(e) => setKindF(e.target.value)}>
              <option value="">全部任务类型</option>
              {KINDS.map((k) => <option key={k} value={k}>{KIND_ZH[k]}</option>)}
            </select>
            <input className="src-input" placeholder="发起方，如 hospital-a / clinic-1"
              value={sourceF} onChange={(e) => setSourceF(e.target.value)} />
            {view === 'run' ? (
              <>
                <span className="filter-label">实验</span>
                <select value={selRun} onChange={(e) => setSelRun(e.target.value)}>
                  <option value="">（未选择实验）</option>
                  {runs.map((r) => (
                    <option key={r.run_id} value={r.run_id}>
                      {r.run_id} · {String(runSpecLoose(r).kind || '?')} · {runSpecLoose(r).source || '?'} · {MODE_ZH[String(runSpecLoose(r).mode)] || runSpecLoose(r).mode || '?'}
                    </option>
                  ))}
                </select>
                <button className="mini" disabled={!selRun} onClick={() => void loadRun(selRun)}>刷新</button>
              </>
            ) : null}
          </div>
        )}
      </div>

      {view === 'suite' ? <SuiteCompare refreshKey={refreshKey} /> : null}

      {view === 'run' && !selRun ? (
        <div className="card"><div className="empty">暂无数据，请先下发测试任务</div></div>
      ) : null}
      {view === 'run' && selRun && runErr ? (
        <div className="card"><div className="errbox">读取实验 {selRun} 失败：{runErr}</div></div>
      ) : null}

      {view === 'suite' ? null : view === 'matrix' ? (
        <MatrixView rows={matrix} err={matrixErr} />
      ) : (
        <>
          <section className="card">
            <div className="card-headrow">
              <div className="card-title">
                分组柱状 · client_total_ms（mean，含 p50 / p95 标记）
              </div>
              <span className="hint">
                {view === 'run'
                  ? (selRunObj
                    ? `${runSpecLoose(selRunObj).kind || '—'} · ${runSpecLoose(selRunObj).source || '—'} · 实验 ${selRun}`
                    : `实验 ${selRun}`)
                  : `累计 · ${kindF ? KIND_ZH[kindF as keyof typeof KIND_ZH] : '全部类型'} · ${sourceF || '全部实体'}`}
              </span>
            </div>
            {rows.length === 0
              ? <div className="empty">暂无数据，请先下发测试任务</div>
              : <GroupedBars rows={rows} />}
          </section>

          <section className="card">
            <div className="card-headrow">
              <div className="card-title">分阶段堆叠 · 计算 / 网络 / 排队</div>
              {view === 'global' && globalGroups.length === 0 && fallbackAgg.length > 0
                ? <span className="hint">/api/compare 无聚合返回，已按 /api/results 本地聚合</span> : null}
            </div>
            {rows.length === 0
              ? <div className="empty">暂无数据，请先下发测试任务</div>
              : <StackedBreakdown rows={rows} />}
          </section>

          <section className="card">
            <div className="card-headrow">
              <div className="card-title">分阶段与正确性明细</div>
              <span className="hint">每行一个模式分组 · 所有时延单位 ms · 括号内为样本量</span>
            </div>
            {rows.length === 0
              ? <div className="empty">暂无数据，请先下发测试任务</div>
              : (
                <div className="tbl-scroll-x">
                  <table className="dt">
                    <thead>
                      <tr>
                        <th>模式</th>
                        <th>请求模式</th>
                        <th>执行者</th>
                        <th>n</th>
                        <th>成功</th>
                        <th>mean</th>
                        <th>p50</th>
                        <th>p95</th>
                        <th>min</th>
                        <th>max</th>
                        <th>计算 mean</th>
                        <th>网络 mean</th>
                        <th>排队 mean</th>
                        <th>正确性</th>
                      </tr>
                    </thead>
                    <tbody>
                      {rows.map((r) => (
                        <tr key={r.key} className={r.bucket === 'degraded' ? 'row-warn' : ''}>
                          <td>
                            <span className="modecell">
                              <i className="sw" style={{ background: modeColor(r.bucket) }} />
                              <b>{MODE_ZH[r.bucket]}</b>
                            </span>
                          </td>
                          <td><RequestedModeLabel mode={r.modeRequested} /></td>
                          <td className="mono">{r.executor || '—'}</td>
                          <td className="mono num">{r.n}</td>
                          <td className="mono num">{r.success}</td>
                          <td className="mono num strong">{fmtMs(r.mean, 0)}</td>
                          <td className="mono num">{fmtMs(r.p50, 0)}</td>
                          <td className="mono num">{fmtMs(r.p95, 0)}</td>
                          <td className="mono num">{fmtMs(r.min, 0)}</td>
                          <td className="mono num">{fmtMs(r.max, 0)}</td>
                          <td className="mono num">{fmtMs(r.compute, 0)}</td>
                          <td className="mono num">{fmtMs(r.network, 0)}</td>
                          <td className="mono num">{fmtMs(r.queue, 0)}</td>
                          <td><RowCorrectness rows={rows} row={r} /></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}

            <StageTable rows={rows} />

            <div className="footnote">
              协同含排队等待（queue_wait_ms），本地执行恒为 0；pipeline_total_ms 仅用于内部分解。
            </div>
          </section>
        </>
      )}

      <div className="footnote">
        {cmpErr && view === 'global' ? <>提示：/api/compare 不可用（{cmpErr}），已按 /api/results 本地聚合。<br /></> : null}
        数据来源：套件对比 = GET /api/compare/suite；本次实验内对比 = GET /api/runs/{'{run_id}'}；
        累计对比 = GET /api/compare；全量矩阵 = GET /api/compare/matrix。
      </div>
    </>
  );
}

/* ------------------------------------------------------------- 聚合逻辑 */

function aggregateAttempts(attempts: Attempt[]): CompareRow[] {
  const buckets = new Map<string, Attempt[]>();
  for (const a of attempts) {
    const b = modeBucket(a);
    const arr = buckets.get(b) || [];
    arr.push(a);
    buckets.set(b, arr);
  }
  const order = ['collaborative', 'local', 'degraded'];
  const out: CompareRow[] = [];
  for (const b of order) {
    const group = buckets.get(b);
    if (!group || group.length === 0) continue;
    out.push(buildRow(b as CompareRow['bucket'], group));
  }
  return out;
}

function buildRow(bucket: CompareRow['bucket'], group: Attempt[]): CompareRow {
  const totals = group.map((a) => attemptMetrics(a).client_total_ms).filter((v): v is number => v !== null);
  const sorted = [...totals].sort((a, b) => a - b);
  const last = group[group.length - 1];
  const stages: Stage[] = group.flatMap((a) => attemptStages(a));
  const stageMean = (pick: (s: Stage) => number | null): number | null => {
    const vals = stages.map(pick).filter((v): v is number => v !== null);
    return mean(vals);
  };
  const mAll = group.map((a) => attemptMetrics(a));
  const meanOf = (pick: (m: ReturnType<typeof attemptMetrics>) => number | null): number | null =>
    mean(mAll.map(pick).filter((v): v is number => v !== null));

  const executors = Array.from(new Set(group.map((a) => a.executor).filter(Boolean))) as string[];
  const reqModes = Array.from(new Set(group.map((a) => a.mode_requested).filter(Boolean))) as string[];

  return {
    key: `${bucket}-${group.length}`,
    bucket,
    modeRequested: reqModes.join('/') || '—',
    modeUsed: (last.mode_used as string) || bucket,
    degraded: bucket === 'degraded',
    n: group.length,
    success: group.filter(isSuccess).length,
    mean: mean(totals),
    p50: percentile(sorted, 0.5),
    p95: percentile(sorted, 0.95),
    min: sorted.length ? sorted[0] : null,
    max: sorted.length ? sorted[sorted.length - 1] : null,
    compute: stageMean((s) => (typeof s.compute_ms === 'number' ? s.compute_ms : null)) ?? meanOf((m) => m.compute_ms_total),
    network: stageMean((s) => (typeof s.network_ms === 'number' ? s.network_ms : null)) ?? meanOf((m) => m.network_ms_total),
    queue: meanOf((m) => m.queue_wait_ms),
    executor: executors.join('/') || null,
    stages,
    bpcrExpected: firstDefined(group.map((a) => a.bpcr_expected ?? null)),
    bpcrActual: firstDefined(group.map((a) => attemptPrediction(a))),
    checksums: firstDefinedArr(group.map((a) => attemptChecksums(a))),
  };
}

function groupsToRows(groups: CompareGroup[]): CompareRow[] {
  return groups.map((g, i) => {
    const mode = strOf(g.mode) || strOf(g.mode_used) || `group-${i}`;
    const lower = mode.toLowerCase();
    const bucket: CompareRow['bucket'] =
      lower === 'degraded' ? 'degraded' : lower === 'local' ? 'local' : 'collaborative';
    const st = groupStageMeans(g);
    // 站点后端把聚合嵌在 client_total_ms 对象里，同时也可能平铺 mean/p50/p95
    const agg = aggOf(g.client_total_ms);
    return {
      key: `${bucket}-${i}`,
      bucket,
      modeRequested: strOf(g.mode_requested) || mode || '—',
      modeUsed: strOf(g.mode_used) || mode || bucket,
      degraded: bucket === 'degraded' || g.degraded === true,
      n: g.n ?? 0,
      success: g.success ?? 0,
      mean: numOr(agg?.mean) ?? numOr(g.mean),
      p50: numOr(agg?.p50) ?? numOr(g.p50),
      p95: numOr(agg?.p95) ?? numOr(g.p95),
      min: numOr(agg?.min) ?? numOr(g.min),
      max: numOr(agg?.max) ?? numOr(g.max),
      compute: st.compute,
      network: st.network,
      queue: st.queue,
      executor: strOf(g.executor) || null,
      stages: [],
      bpcrExpected: numOr(g.bpcr_expected),
      bpcrActual: numOr(g.bpcr_actual ?? g.bpcr_mean),
      checksums: null,
      serverCorrectness: (g.correctness as GroupCorrectness | null) ?? null,
    };
  });
}

type AggRecord = { mean?: unknown; p50?: unknown; p95?: unknown; min?: unknown; max?: unknown };
function aggOf(v: unknown): AggRecord | null {
  return v && typeof v === 'object' ? (v as AggRecord) : null;
}

function numOr(v: unknown): number | null {
  if (typeof v === 'number' && Number.isFinite(v)) return v;
  if (typeof v === 'string' && v !== '' && Number.isFinite(Number(v))) return Number(v);
  return null;
}
function strOf(v: unknown): string {
  return typeof v === 'string' ? v : '';
}
function firstDefined<T>(vals: (T | null)[]): T | null {
  for (const v of vals) if (v !== null && v !== undefined) return v;
  return null;
}
function firstDefinedArr(vals: (string[] | null)[]): string[] | null {
  for (const v of vals) if (v) return v;
  return null;
}

/* ------------------------------------------------------------- 子组件 */

function RowCorrectness({ rows, row }: { rows: CompareRow[]; row: CompareRow }) {
  // 诊断：与协同分组的 bpCR 比对（协同为基线）
  const base = rows.find((r) => r.bucket === 'collaborative');
  const baseProb = base?.bpcrActual ?? null;
  const baseCk = base?.checksums ?? null;

  // 站点后端直接给判定时优先采用（含 checked/match/mismatch/skipped 计数）
  const sc = row.serverCorrectness;
  if (sc && typeof sc.status === 'string') {
    return (
      <div className="ok-cell">
        <ServerVerdict status={sc.status} />
        <div className="mono muted">
          校验 {sc.checked ?? 0} · 一致 {sc.match ?? 0} · 不一致 {sc.mismatch ?? 0} · 跳过 {sc.skipped ?? 0}
        </div>
        {row.bpcrActual !== null ? <div className="mono">bpCR {fmtProb(row.bpcrActual)}</div> : null}
      </div>
    );
  }

  if (row.bpcrActual !== null) {
    const eq = baseProb === null ? null : probEq(baseProb, row.bpcrActual, 1e-6);
    return (
      <div className="ok-cell">
        <div className="mono">bpCR {fmtProb(row.bpcrActual)}</div>
        <div className="mono muted">协同基线 {fmtProb(baseProb)}</div>
        {row.bucket === 'collaborative' ? <span className="muted">基线</span>
          : eq === null ? <span className="muted">无基线</span>
            : eq ? <span className="ok-tag">与协同一致</span>
              : <span className="bad-tag">与协同不一致</span>}
      </div>
    );
  }
  if (row.checksums) {
    const eq = baseCk === null ? null : checksumsEqual(baseCk, row.checksums);
    return (
      <div className="ok-cell">
        <div className="mono">checksums ×{row.checksums.length}</div>
        {row.bucket === 'collaborative' ? <span className="muted">基线</span>
          : eq === null ? <span className="muted">无基线</span>
            : eq ? <span className="ok-tag">与协同一致</span>
              : <span className="bad-tag">与协同不一致</span>}
      </div>
    );
  }
  if (row.n > 0) {
    return <span className="muted">无校验数据（{nLabel(row.n, row.success)}）</span>;
  }
  return <span className="muted">—</span>;
}

/** 站点后端 correctness.status 的中文呈现 */
function ServerVerdict({ status }: { status: string }) {
  const s = status.toLowerCase();
  if (s === 'match') return <span className="ok-tag">校验一致</span>;
  if (s === 'mismatch') return <span className="bad-tag">校验不一致</span>;
  if (s === 'partial') return <span className="warn-tag">部分一致</span>;
  if (s === 'skipped' || s === 'unknown' || s === 'none') return <span className="muted">未校验</span>;
  return <span className="muted">{status}</span>;
}

function StageTable({ rows }: { rows: CompareRow[] }) {  const withStages = rows.filter((r) => r.stages.length > 0);
  if (withStages.length === 0) return null;
  return (
    <div className="stage-block">
      <div className="card-title mt">逐阶段均值（来自 stages[]）</div>
      {withStages.map((r) => {
        const names = Array.from(new Set(r.stages.map((s) => s.name || '未命名')));
        return (
          <div className="stage-group" key={r.key}>
            <div className="stage-group-head">
              <i className="sw" style={{ background: modeColor(r.bucket) }} />
              {MODE_ZH[r.bucket]} <i className="muted">{nLabel(r.n, r.success)} · {r.stages.length} 条阶段记录</i>
            </div>
            <table className="dt small">
              <thead>
                <tr><th>阶段</th><th>样本</th><th>均值 ms</th><th>最小 ms</th><th>最大 ms</th><th>执行者</th></tr>
              </thead>
              <tbody>
                {names.map((n) => {
                  const ss = r.stages.filter((s) => (s.name || '未命名') === n);
                  const ms = ss.map((s) => s.ms).filter((v): v is number => typeof v === 'number');
                  const actors = Array.from(new Set(ss.map((s) => s.actor).filter(Boolean))).join('/');
                  return (
                    <tr key={n}>
                      <td className="mono">{n}</td>
                      <td className="mono num">{ms.length}</td>
                      <td className="mono num">{fmtMs(mean(ms), 1)}</td>
                      <td className="mono num">{fmtMs(ms.length ? Math.min(...ms) : null, 1)}</td>
                      <td className="mono num">{fmtMs(ms.length ? Math.max(...ms) : null, 1)}</td>
                      <td className="mono">{actors || '—'}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        );
      })}
    </div>
  );
}

function MatrixView({ rows, err }: { rows: MatrixRow[]; err: string }) {
  const [kindF, setKindF] = useState('');
  const view = useMemo(
    () => rows.filter((r) => {
      if (kindF && r.kind !== kindF) return false;
      return true;
    }),
    [rows, kindF],
  );

  // 以 (kind, source) 分组，行内按 mode 展开
  const grouped = useMemo(() => {
    const m = new Map<string, MatrixRow[]>();
    for (const r of view) {
      const key = `${r.kind || '?'} | ${r.source || '?'}`;
      const arr = m.get(key) || [];
      arr.push(r);
      m.set(key, arr);
    }
    return Array.from(m.entries()).sort((a, b) => a[0].localeCompare(b[0]));
  }, [view]);

  return (
    <section className="card">
      <div className="card-headrow">
        <div className="card-title">全量聚合矩阵</div>
        <select value={kindF} onChange={(e) => setKindF(e.target.value)} className="src-input">
          <option value="">全部任务类型</option>
          {KINDS.map((k) => <option key={k} value={k}>{KIND_ZH[k]}</option>)}
        </select>
      </div>

      {err ? <div className="errbox">/api/compare/matrix 不可用：{err}</div> : null}
      {rows.length === 0 && !err ? <div className="empty">暂无数据，请先下发测试任务</div> : null}
      {rows.length > 0 && grouped.length === 0 ? <div className="empty">当前筛选无数据</div> : null}

      {grouped.map(([key, list]) => (
        <div className="matrix-group" key={key}>
          <div className="matrix-head mono">{key}</div>
          <table className="dt small">
            <thead>
              <tr>
                <th>模式</th><th>n</th><th>成功</th><th>mean</th><th>p50</th><th>p95</th><th>max</th><th>min</th>
                <th>计算 mean</th><th>网络 mean</th><th>排队 mean</th>
              </tr>
            </thead>
            <tbody>
              {list.map((r, i) => {
                const st = groupStageMeans(r);
                const mode = (strOf(r.mode) || strOf(r.mode_used) || '—').toLowerCase();
                return (
                  <tr key={i} className={mode === 'degraded' ? 'row-warn' : ''}>
                    <td>
                      <span className="modecell">
                        <i className="sw" style={{ background: modeColor(mode) }} />
                        {MODE_ZH[mode] || mode}
                      </span>
                    </td>
                    <td className="mono num">{r.n ?? '—'}</td>
                    <td className="mono num">{r.success ?? '—'}</td>
                    <td className="mono num strong">{fmtMs(numOr(r.mean), 0)}</td>
                    <td className="mono num">{fmtMs(numOr(r.p50), 0)}</td>
                    <td className="mono num">{fmtMs(numOr(r.p95), 0)}</td>
                    <td className="mono num">{fmtMs(numOr(r.max), 0)}</td>
                    <td className="mono num">{fmtMs(numOr(r.min), 0)}</td>
                    <td className="mono num">{fmtMs(st.compute, 0)}</td>
                    <td className="mono num">{fmtMs(st.network, 0)}</td>
                    <td className="mono num">{fmtMs(st.queue, 0)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ))}
    </section>
  );
}
