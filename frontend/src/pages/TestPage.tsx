import { useEffect, useMemo, useRef, useState } from 'react';
import { api } from '../api';
import Badge from '../components/Badge';
import TaskResultView, { isTerminalStatus } from '../components/TaskResultView';
import { sleep } from '../hooks';
import type {
  HospitalId, ModelKind, Patient, SourceId, TaskItem,
} from '../types';
import { MODEL_COLOR, MODEL_LABEL, MODELS, SOURCE_ROLE } from '../utils';

type Counts = Record<ModelKind, number>;
const DEFAULT_COUNTS: Counts = { diagnosis: 2, compute: 1, sync: 1, routine: 3 };

interface Scenario {
  kind: ModelKind;
  source: SourceId;
  /** diagnosis only: clinic 发起的转诊目标（auto=自动选空闲医院） */
  target?: HospitalId | 'auto';
  patientId?: string;
  seq: number; // 1-based sequence inside its kind
  params: Record<string, unknown>;
}

interface Entry {
  idx: number;
  label: string;
  kind: ModelKind;
  source: SourceId;
  target?: HospitalId | 'auto';
  patientId?: string;
  task_id?: string;
  status: string; // pending | running | finished | failed | error
  error?: string;
  duration_ms?: number;
  item?: TaskItem | null;
}

interface LogLine { kind: 'ok' | 'err' | 'dim'; text: string }

export default function TestPage() {
  const [counts, setCounts] = useState<Counts>(DEFAULT_COUNTS);
  const [patients, setPatients] = useState<Patient[]>([]);
  const [patErr, setPatErr] = useState('');
  const [plan, setPlan] = useState<Scenario[]>([]);
  const [phase, setPhase] = useState<'idle' | 'submitting' | 'polling' | 'done'>('idle');
  const [logs, setLogs] = useState<LogLine[]>([]);
  const entriesRef = useRef<Entry[]>([]);
  const [, setTick] = useState(0);
  const busy = phase === 'submitting' || phase === 'polling';
  const logBoxRef = useRef<HTMLDivElement | null>(null);
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    api.patients().then(setPatients).catch((e) =>
      setPatErr(`测试数据集不可用：${e?.response?.status ?? e?.message}`));
    return () => { mountedRef.current = false; };
  }, []);

  useEffect(() => {
    if (logBoxRef.current) logBoxRef.current.scrollTop = logBoxRef.current.scrollHeight;
  }, [logs]);

  const total = useMemo(
    () => MODELS.reduce((s, m) => s + counts[m], 0),
    [counts],
  );

  const appendLog = (kind: LogLine['kind'], text: string) =>
    setLogs((l) => [...l.slice(-120), { kind, text }]);

  const patchEntry = (idx: number, patch: Partial<Entry>) => {
    entriesRef.current = entriesRef.current.map((e) =>
      (e.idx === idx ? { ...e, ...patch } : e));
    setTick((t) => t + 1);
  };

  // ------------------------------------------------------------------ plan --
  const inc = (k: ModelKind, d: number) =>
    setCounts((c) => ({ ...c, [k]: Math.max(0, Math.min(9, c[k] + d)) }));

  const buildPlan = (patientsNow: Patient[]): Scenario[] => {
    const pat = patientsNow.length > 0
      ? patientsNow
      : [{ patient_id: 'P001', bpCR: 0.3, hospital: 'hospital-a' }];
    const seq = { diagnosis: 0, compute: 0, sync: 0, routine: 0 } as Record<ModelKind, number>;
    const nextSeq = (k: ModelKind) => ++seq[k];
    const out: Scenario[] = [];

    // ---- 诊断：默认最后一条由医院 1 发起、自动转诊；其余由医疗中心直接执行
    for (let i = 0; i < counts.diagnosis; i++) {
      const n = counts.diagnosis;
      const seqN = nextSeq('diagnosis');
      if (n >= 2 && i === n - 1) {
        out.push({
          kind: 'diagnosis', source: 'clinic-1', target: 'auto',
          patientId: pat[i % pat.length].patient_id, seq: seqN, params: {},
        });
      } else {
        out.push({
          kind: 'diagnosis',
          source: (i % 2 === 0 ? 'hospital-a' : 'hospital-b') as SourceId,
          patientId: pat[i % pat.length].patient_id, seq: seqN, params: {},
        });
      }
    }
    // ---- 计算：分区协同（发起方 + 数据中心 + 另一医疗中心）
    for (let i = 0; i < counts.compute; i++) {
      const srcs: SourceId[] = ['hospital-a', 'hospital-b', 'clinic-1'];
      out.push({
        kind: 'compute', source: srcs[i % srcs.length], seq: nextSeq('compute'),
        params: {
          instruments: 1 + ((i + 3) % 8), rows: 64 * (1 + ((i * 3) % 15)),
          intensity: 10 * (1 + ((i * 7 + 4) % 20)), partition_count: 2 + ((i + 1) % 5),
        },
      });
    }
    // ---- 通信：患者库同步（医院侧为主）
    for (let i = 0; i < counts.sync; i++) {
      const srcs: SourceId[] = ['clinic-1', 'clinic-2', 'hospital-a'];
      out.push({
        kind: 'sync', source: srcs[i % srcs.length], seq: nextSeq('sync'),
        params: {
          bandwidth_mbps: 4 + ((i * 3) % 97),
          concurrency: 1 + ((i + 3) % 8),
          chunk_kb: [4, 8, 16][i % 3],
        },
      });
    }
    // ---- 日常：一次性 K8s Job
    for (let i = 0; i < counts.routine; i++) {
      const srcs: SourceId[] = ['clinic-1', 'clinic-2', 'hospital-a'];
      out.push({
        kind: 'routine', source: srcs[i % srcs.length], seq: nextSeq('routine'),
        params: {
          jobs: 1 + ((i + 1) % 5),
          rows: 64 * (1 + ((i + 1) % 12)),
          intensity: 10 * (1 + ((i * 5 + 2) % 20)),
        },
      });
    }
    return out;
  };

  // ------------------------------------------------------------------ run --
  const doSubmit = async (sc: Scenario): Promise<string> => {
    const priority = 4;
    const deadline = '120s';
    if (sc.kind === 'diagnosis') {
      if (!sc.patientId) throw new Error('未选择患者');
      const r = await api.submitDiagnosis({
        source: sc.source, patient_id: sc.patientId,
        target_hospital: sc.source.startsWith('clinic') && sc.target
          ? (sc.target === 'auto' ? undefined : sc.target) : undefined,
        priority, deadline,
      });
      return r.task_id;
    }
    if (sc.kind === 'compute') {
      const p = sc.params as { instruments: number; rows: number; intensity: number; partition_count: number };
      const r = await api.submitCompute({ source: sc.source, ...p, priority, deadline });
      return r.task_id;
    }
    if (sc.kind === 'sync') {
      const p = sc.params as { bandwidth_mbps: number; concurrency: number; chunk_kb: number };
      const r = await api.submitSync({ source: sc.source, ...p, priority, deadline });
      return r.task_id;
    }
    const p = sc.params as { jobs: number; rows: number; intensity: number };
    const r = await api.submitRoutine({ source: sc.source, ...p, priority, deadline });
    return r.task_id;
  };

  const run = async () => {
    if (busy || total === 0) return;
    const planNow = buildPlan(patients);
    setPlan(planNow);
    entriesRef.current = planNow.map((sc, i) => ({
      idx: i, label: scLabel(sc), kind: sc.kind, source: sc.source,
      target: sc.target, patientId: sc.patientId, status: 'pending',
    }));
    setTick((t) => t + 1);
    setLogs([]);
    appendLog('dim', `═══ 综合测试开始 ═══ 共 ${planNow.length} 个任务`
      + MODELS.map((m) => `${MODEL_LABEL[m]}×${counts[m]}`).join(' '));
    setPhase('submitting');

    // concurrency = 3 提交队列
    await runPool(planNow.map((sc, i) => async () => {
      if (!mountedRef.current) return;
      const label = scLabel(sc);
      appendLog('dim', `提交 ${label}（${sc.source}${sc.kind === 'diagnosis' && sc.source.startsWith('clinic')
        ? '，转诊' + (sc.target === 'auto' ? '自动' : sc.target) : ''}）…`);
      try {
        const id = await doSubmit(sc);
        patchEntry(i, { task_id: id, status: 'running' });
        appendLog('ok', `${label} 已入队 → ${id}`);
      } catch (e: any) {
        patchEntry(i, { status: 'error', error: e?.response?.data?.detail ?? e?.message });
        appendLog('err', `${label} 提交失败：${e?.response?.data?.detail ?? e?.message}`);
      }
    }), 3);

    if (!mountedRef.current) return;
    setPhase('polling');
    appendLog('dim', '提交完成，轮询任务直至全部到达终态…');
    await pollUntilDone();
    if (!mountedRef.current) return;
    setPhase('done');
    const failed = entriesRef.current.filter((e) => e.status === 'failed' || e.status === 'error').length;
    appendLog(failed ? 'err' : 'ok',
      `═══ 全部结束 ═══ 成功 ${entriesRef.current.length - failed} / 失败 ${failed}`);
  };

  const pollUntilDone = async () => {
    const began = Date.now();
    const MAX_MS = 8 * 60 * 1000; // 看门狗：8 分钟未全部终态则放弃
    for (;;) {
      const pending = entriesRef.current.filter((e) =>
        e.task_id && !isTerminalStatus(e.status) && e.status !== 'error');
      if (pending.length === 0) break;
      if (Date.now() - began > MAX_MS) {
        pending.forEach((e) => patchEntry(e.idx, { status: 'failed', error: '轮询超时（看门狗停止）' }));
        appendLog('err', '轮询超过 8 分钟，看门狗停止（部分任务可能仍运行在调度器）');
        break;
      }
      await Promise.all(pending.map(async (e) => {
        if (!mountedRef.current) return;
        try {
          const r = await api.taskResult(e.task_id!);
          if (!isTerminalStatus(r.status)) {
            patchEntry(e.idx, { status: r.status });
            return;
          }
          try {
            const detail = await api.taskDetail(e.task_id!);
            patchEntry(e.idx, {
              status: detail.status,
              error: detail.error,
              duration_ms: detail.duration_ms,
              item: detail,
            });
            if (detail.status === 'failed') {
              appendLog('err', `${e.label} 失败：${detail.error || ''}`);
            } else {
              appendLog('dim', `${e.label} 完成（${detail.duration_ms != null ? `${detail.duration_ms.toFixed(0)}ms` : '—'}）`);
            }
          } catch {
            patchEntry(e.idx, { status: r.status, error: r.error });
          }
        } catch { /* 下一次轮询重试 */ }
      }));
      await sleep(1200);
    }
  };

  const entries = entriesRef.current;
  const detailRows = entries.filter((e) =>
    (isTerminalStatus(e.status) && e.item) || e.status === 'error');

  return (
    <div className="page">
      <header className="page-head">
        <h1>综合测试</h1>
        <p>按每类任务的预设参数批量提交（并发 3），自动轮询直至终态并内联渲染每一条完整结果。</p>
      </header>

      {patErr && <div className="errbox">{patErr}</div>}

      <div className="test-layout">
        {/* ---------------- left: config ---------------- */}
        <aside className="card test-config">
          <h3 className="card-title">场景配置</h3>
          {MODELS.map((m) => (
            <div className="tcfg-row" key={m}>
              <span className="tcfg-kind">
                <i style={{ background: MODEL_COLOR[m] }} />
                {MODEL_LABEL[m]} <span className="muted xs">{m}</span>
              </span>
              <span className="counter">
                <button disabled={busy || counts[m] <= 0} onClick={() => inc(m, -1)}>−</button>
                <span>{counts[m]}</span>
                <button disabled={busy || counts[m] >= 9} onClick={() => inc(m, 1)}>＋</button>
              </span>
            </div>
          ))}
          <div className="muted xs" style={{ margin: '4px 0 12px' }}>
            默认：诊断 2（其中医院 1 发起 1 条自动转诊）、计算 1、通信 1、日常 3。
          </div>
          <div className="btnrow">
            <button className="btn primary sm" disabled={busy || total === 0} onClick={run}>
              {busy ? '执行中…' : `⚡ 执行（${total}）`}
            </button>
            <button className="btn ghost sm" disabled={busy || total === 0}
              title="用当前数量重新生成并执行全部场景" onClick={run}>
              全部重跑
            </button>
          </div>
          <button className="btn ghost sm" style={{ marginTop: 8 }}
            disabled={busy} onClick={() => { setCounts(DEFAULT_COUNTS); setPlan([]); entriesRef.current = []; setLogs([]); setTick((t) => t + 1); setPhase('idle'); }}>
            重置配置
          </button>

          {plan.length > 0 && (
            <div className="plan-list">
              <div className="muted xs mb">本轮场景：</div>
              {plan.map((p, i) => (
                <div key={i} className="plan-item">
                  <Badge model={p.kind} />
                  <span className="mono xs">{p.source}</span>
                  {p.kind === 'diagnosis' && (
                    <span className="mono xs muted">
                      {p.source.startsWith('clinic')
                        ? `→ 转诊${p.target === 'auto' ? '自动' : p.target}`
                        : '本医疗中心执行'}
                    </span>
                  )}
                </div>
              ))}
            </div>
          )}

          <h4 className="sub-title" style={{ marginTop: 14 }}>日志</h4>
          <div className="tlog" ref={logBoxRef}>
            {logs.length === 0 ? (
              <div className="dim">（点击「执行」开始）</div>
            ) : logs.map((l, i) => (
              <div key={i} className={l.kind}>{l.text}</div>
            ))}
          </div>
        </aside>

        {/* ---------------- right: run results ---------------- */}
        <section>
          <div className="card">
            <h3 className="card-title">任务汇总</h3>
            {entries.length === 0 ? (
              <div className="empty">尚未执行 — 配置左侧数量后点击「执行」。</div>
            ) : (
              <table className="datatable">
                <thead>
                  <tr><th>任务</th><th>发起方</th><th>转诊目标（诊断）</th><th>状态</th><th>耗时</th></tr>
                </thead>
                <tbody>
                  {entries.map((e) => {
                    const forwarded = e.item?.result?.forwarded_to as string | undefined;
                    return (
                      <tr key={e.idx}>
                        <td><Badge model={e.kind} /><span className="mono xs muted"> · {e.label}</span></td>
                        <td>
                          <span className="mono">{e.source}</span>
                          <span className={`src-role ${SOURCE_ROLE[e.source] === '医疗中心' ? 'edge' : 'term'}`}>
                            {SOURCE_ROLE[e.source]}
                          </span>
                        </td>
                        <td className="muted">
                          {e.kind !== 'diagnosis' ? '—'
                            : e.source.startsWith('clinic')
                              ? (forwarded ? `转 → ${forwarded}` : '自动（待定…）')
                              : '本医疗中心直接执行'}
                        </td>
                        <td>
                          {e.status === 'error'
                            ? <span className="badge s-failed">提交失败</span>
                            : <Badge status={badgeStatus(e.status)} />}
                        </td>
                        <td className="mono">
                          {e.duration_ms != null ? `${e.duration_ms.toFixed(1)}ms`
                            : (e.status === 'pending' ? '排队' : e.status === 'running' ? '运行中' : '—')}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            )}
            {phase === 'done' && (
              <div className="kvrow" style={{ marginTop: 8 }}>
                <span>结束统计</span>
                <span>
                  成功 <b>{entries.filter((e) => e.status === 'finished' || e.status === 'completed').length}</b>
                  {' '}· 失败/异常 <b>{entries.filter((e) => e.status === 'failed' || e.status === 'error').length}</b>
                </span>
              </div>
            )}
          </div>

          {detailRows.map((e) => (
            <div className="card" key={`detail-${e.idx}`}>
              <div className="card-headrow">
                <h3 className="card-title">
                  {e.label} 结果
                  <span className="mono xs muted" style={{ marginLeft: 8 }}>{e.task_id || ''}</span>
                </h3>
                {e.status === 'error'
                  ? <span className="badge s-failed">提交失败</span>
                  : <Badge status={badgeStatus(e.status)} />}
              </div>
              {e.status === 'error' ? (
                <div className="errbox">{e.error || '提交失败'}</div>
              ) : (
                <TaskResultView task={e.item || null} />
              )}
            </div>
          ))}
        </section>
      </div>
    </div>
  );
}

function scLabel(sc: Scenario): string {
  return `${MODEL_LABEL[sc.kind]}-${sc.seq}`;
}

function badgeStatus(s: string): string {
  if (s === 'completed') return 'finished';
  if (s === 'pending') return 'queued';
  return s;
}

async function runPool<T>(fns: Array<() => Promise<T>>, conc: number): Promise<void> {
  let i = 0;
  const workers = Array.from({ length: Math.min(conc, fns.length) }, async () => {
    for (;;) {
      const cur = i++;
      if (cur >= fns.length) return;
      try { await fns[cur](); } catch { /* per-task errors already surfaced by caller */ }
    }
  });
  await Promise.all(workers);
}
