import { useEffect, useMemo, useState } from 'react';
import { api, asAttemptList, errText, progressCounts, runLive, runProgressDone } from '../api';
import type { Attempt, CreateRunBody, Run } from '../api';
import type { Kind, Patient, ReqMode, Source, SuiteInfo } from '../types';
import { CLINICS, HOSPITALS, KIND_ZH, KINDS, MODE_ZH, SOURCES, STRATEGIES } from '../types';
import { StatusBadge } from '../components/Badge';
import { AttemptStats, AttemptTable, ProgressBar } from '../components/AttemptTable';
import { fmtTime } from '../components/format';
import { usePolling } from '../hooks';

/* ------------------------------------------------- 单次任务参数（与 /schedule/* 同构） */
interface DiagParams { patient_id: string }
interface ComputeParams { instruments: number; rows: number; intensity: number; partition_count: number }
interface SyncParams { bandwidth_mbps: number; concurrency: number; chunk_kb: number }
interface RoutineParams { jobs: number; rows: number; intensity: number }

const COMPUTE_PRESETS: { name: string; v: ComputeParams }[] = [
  { name: '规模小', v: { instruments: 2, rows: 128, intensity: 20, partition_count: 2 } },
  { name: '中等', v: { instruments: 4, rows: 256, intensity: 40, partition_count: 3 } },
  { name: '规模大', v: { instruments: 8, rows: 1024, intensity: 120, partition_count: 3 } },
];

const SYNC_PRESETS: { name: string; v: SyncParams }[] = [
  { name: '省流', v: { bandwidth_mbps: 8, concurrency: 2, chunk_kb: 4 } },
  { name: '普通', v: { bandwidth_mbps: 20, concurrency: 4, chunk_kb: 4 } },
  { name: '高速', v: { bandwidth_mbps: 60, concurrency: 8, chunk_kb: 16 } },
];

const ROUTINE_PRESETS: { name: string; v: RoutineParams }[] = [
  { name: '3 个作业', v: { jobs: 3, rows: 256, intensity: 40 } },
  { name: '5 个作业', v: { jobs: 5, rows: 512, intensity: 60 } },
];

type SendMode = 'suite' | 'single';

export default function DispatchPage({
  onCreated, onGoto,
}: { onCreated: (runId: string) => void; onGoto: (t: 'results' | 'compare') => void }) {
  const [send, setSend] = useState<SendMode>('suite');

  /* ------------------------------------------------------------ 固定套件 */
  const [suites, setSuites] = useState<SuiteInfo[]>([]);
  const [suiteErr, setSuiteErr] = useState('');
  const [suiteId, setSuiteId] = useState('');
  const [suiteSource, setSuiteSource] = useState('clinic-1');
  const [suiteConcurrency, setSuiteConcurrency] = useState(1);

  /* ------------------------------------------------------------ 单次任务 */
  const [kind, setKind] = useState<Kind>('compute');
  const [source, setSource] = useState<Source>('hospital-a');
  const [repeats, setRepeats] = useState(3);
  const [concurrency, setConcurrency] = useState(1);
  const [diag, setDiag] = useState<DiagParams>({ patient_id: '' });
  const [compute, setCompute] = useState<ComputeParams>(COMPUTE_PRESETS[1].v);
  const [sync, setSync] = useState<SyncParams>(SYNC_PRESETS[1].v);
  const [routine, setRoutine] = useState<RoutineParams>(ROUTINE_PRESETS[0].v);

  /* ------------------------------------------------------ 两种模式共用 */
  const [strategy, setStrategy] = useState<ReqMode>('collaborative');
  const [label, setLabel] = useState('');
  const [patients, setPatients] = useState<Patient[]>([]);
  const [patErr, setPatErr] = useState('');
  const [busy, setBusy] = useState(false);
  const [submitErr, setSubmitErr] = useState('');
  const [runId, setRunId] = useState('');
  const [run, setRun] = useState<Run | null>(null);
  const [createNote, setCreateNote] = useState('');
  const [sentKind, setSentKind] = useState<SendMode>('single');
  /** 下发响应里的任务数：第一次轮询回来之前用它兜底显示「完成 0 / N」 */
  const [sentTotal, setSentTotal] = useState(0);

  useEffect(() => {
    api.suites()
      .then((list) => {
        setSuites(list);
        setSuiteErr('');
        if (list.length > 0) {
          setSuiteId((cur) => cur || list[0].suite_id);
          setSuiteSource((cur) => cur || list[0].default_source);
          setSuiteConcurrency((cur) => cur || list[0].default_concurrency || 1);
        }
      })
      .catch((e) => setSuiteErr(errText(e)));
  }, []);

  useEffect(() => {
    api.patients()
      .then((ps) => {
        setPatients(ps);
        if (ps.length > 0) setDiag((d) => ({ patient_id: d.patient_id || ps[0].patient_id }));
      })
      .catch((e) => setPatErr(errText(e)));
  }, []);

  const suite = useMemo(
    () => suites.find((s) => s.suite_id === suiteId) || null,
    [suites, suiteId],
  );

  const attempts: Attempt[] = asAttemptList(run?.attempts);
  const counts = progressCounts(run, attempts);
  const total = counts.total || sentTotal;
  const running = Boolean(runId) && (run === null || runLive(run) || !runProgressDone(run.progress));

  // 套件一次跑 20 个任务（本地执行约 1–3 分钟），2.5 s 轮询直到 pending/running 归零
  usePolling(async () => {
    if (!runId) return;
    try {
      setRun(await api.runDetail(runId));
    } catch {
      /* 轮询失败静默，下一轮再试 */
    }
  }, 2500, running);

  const params: Record<string, unknown> = useMemo(() => {
    switch (kind) {
      case 'diagnosis': return { patient_id: diag.patient_id || undefined };
      case 'compute': return { ...compute };
      case 'sync': return { ...sync };
      case 'routine': return { ...routine };
      default: return {};
    }
  }, [kind, diag, compute, sync, routine]);

  const problems: string[] = [];
  if (send === 'suite') {
    // 套件清单还没到手时不提示，避免刚进页面就报「请选择固定套件」
    if ((suites.length > 0 || suiteErr) && !suiteId) problems.push('请选择固定套件');
    if (suiteSource && !SOURCES.includes(suiteSource as Source)) problems.push('请选择发起方');
  } else {
    if (kind === 'diagnosis' && !diag.patient_id) problems.push('请选择患者（诊断任务的 patient_id）');
    if (repeats < 1) problems.push('重复次数 repeats 至少为 1');
  }
  if ((send === 'suite' ? suiteConcurrency : concurrency) < 1) problems.push('并发 concurrency 至少为 1');

  const startRun = (id: string, status: string, attemptCount: number) => {
    setRunId(id);
    setSentTotal(attemptCount);
    setRun({ run_id: id, status, attempts: attemptCount });
    onCreated(id);
  };

  const submitSuite = async () => {
    if (!suite) return;
    setBusy(true);
    setSubmitErr('');
    setCreateNote('');
    setRun(null);
    setRunId('');
    try {
      const resp = await api.createSuiteRun({
        suite_id: suite.suite_id,
        mode: strategy,
        source: suiteSource,
        concurrency: suiteConcurrency,
        label: label.trim() || undefined,
      });
      startRun(resp.run_id, resp.status || 'queued', resp.attempts ?? suite.task_count);
      setSentKind('suite');
      setCreateNote(`已下发套件：${suite.name} · ${MODE_ZH[strategy]} · ${resp.attempts ?? suite.task_count} 个任务`);
    } catch (e) {
      setSubmitErr(errText(e));
    } finally {
      setBusy(false);
    }
  };

  const submitSingle = async () => {
    setBusy(true);
    setSubmitErr('');
    setCreateNote('');
    setRun(null);
    setRunId('');
    const body: CreateRunBody = {
      kind,
      source,
      params,
      mode: strategy,
      repeats,
      concurrency,
      label: label.trim() || undefined,
    };
    try {
      const resp = await api.createRun(body);
      startRun(resp.run_id, resp.status || 'queued', resp.attempts ?? repeats);
      setSentKind('single');
      setCreateNote(`已下发单次任务：${KIND_ZH[kind]} · ${MODE_ZH[strategy]} · ${resp.attempts ?? repeats} 次`);
    } catch (e) {
      setSubmitErr(errText(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <div className="segbar">
        <div className="seg">
          <button className={`seg-btn ${send === 'suite' ? 'on' : ''}`} onClick={() => setSend('suite')}>
            固定套件
          </button>
          <button className={`seg-btn ${send === 'single' ? 'on' : ''}`} onClick={() => setSend('single')}>
            单次任务
          </button>
        </div>
        <span className="hint">
          {send === 'suite'
            ? '一次点击下发整套任务：同一套件在两种调度策略下测同一批工作'
            : '自定义任务类型与参数，适合临时试探'}
        </span>
      </div>

      <div className="dispatch-grid">
        {send === 'suite' ? (
          <section className="card cfg-card">
            <div className="card-headrow">
              <div className="card-title">固定套件</div>
              {suite ? <span className="hint">{suite.description || ''}</span> : null}
            </div>

            {suiteErr ? <div className="errbox">/api/suites 不可用：{suiteErr}</div> : null}
            {!suiteErr && suites.length === 0 ? <div className="hint">正在读取套件清单…</div> : null}

            {suites.length > 0 ? (
              <div className="field">
                <label className="field-label">套件</label>
                <select
                  value={suiteId}
                  onChange={(e) => {
                    const next = suites.find((s) => s.suite_id === e.target.value) || null;
                    setSuiteId(e.target.value);
                    if (next) {
                      setSuiteSource(next.default_source);
                      setSuiteConcurrency(next.default_concurrency || 1);
                    }
                  }}
                >
                  {suites.map((s) => (
                    <option key={s.suite_id} value={s.suite_id}>
                      {s.name}（{s.task_count} 个任务）
                    </option>
                  ))}
                </select>
              </div>
            ) : null}

            <div className="card-title mt">调度策略</div>
            <StrategyPicker value={strategy} onChange={setStrategy} />
            <div className="note-line">
              {strategy === 'collaborative'
                ? '云边端协同：3 个分区并行派发给发起端、数据中心与伙伴边缘端。'
                : '本地执行：3 个分区在发起端串行执行，不经调度器编排。'}
            </div>

            <div className="card-title mt">发起方</div>
            <SourcePicker value={suiteSource} onChange={setSuiteSource} />

            <div className="card-title mt">下发设置</div>
            <div className="field-2col">
              <div className="field">
                <label className="field-label">并发 concurrency</label>
                <input type="number" min={1} max={64} value={suiteConcurrency}
                  onChange={(e) => setSuiteConcurrency(Number(e.target.value))} />
              </div>
              <div className="field">
                <label className="field-label">标签 label（可选）</label>
                <input value={label} placeholder="例如 套件-协同-A" onChange={(e) => setLabel(e.target.value)} />
              </div>
            </div>

            {suite ? (
              <>
                <div className="card-title mt">档位清单（{suite.task_count} 个任务）</div>
                <SuiteSpecTable suite={suite} />
                <div className="hint">
                  共 {suite.task_count} 个任务 = {suite.specs.length} 档 × 每档重复，同一档位各次重复用同一 seed，
                  两种策略跑的是同一批工作。
                </div>
              </>
            ) : null}

            {submitErr ? <div className="errbox">下发失败：{submitErr}</div> : null}
            {problems.length > 0 ? <div className="hint">待修正：{problems.join('；')}</div> : null}

            <div className="btnrow end">
              <button className="btn primary" disabled={busy || problems.length > 0 || !suite}
                onClick={() => void submitSuite()}>
                {busy ? '下发中…' : `下发套件（${suite ? suite.task_count : 0} 个任务 · ${MODE_ZH[strategy]}）`}
              </button>
            </div>
          </section>
        ) : (
          <section className="card cfg-card">
            <div className="card-title">任务类型</div>
            <div className="kinds">
              {KINDS.map((k) => (
                <button key={k} className={`kindbtn k-${k} ${kind === k ? 'on' : ''}`} onClick={() => setKind(k)}>
                  <b>{KIND_ZH[k]}</b>
                </button>
              ))}
            </div>

            <div className="card-title mt">调度策略</div>
            <StrategyPicker value={strategy} onChange={setStrategy} />
            <div className="note-line">
              {strategy === 'collaborative'
                ? '云边端协同：由调度器编排边 + 云 + 伙伴端多角色协作。'
                : '本地执行：发起端自己完成全部分区，不经调度器编排。'}
            </div>

            <div className="card-title mt">发起方</div>
            <SourcePicker value={source} onChange={(v) => setSource(v as Source)} />
            {source.startsWith('clinic') && kind === 'diagnosis' ? (
              <div className="note-line">
                诊所无本地模型：本地执行表现为自助转诊到最近医院后就地推理，不是诊所自己推理。
              </div>
            ) : null}

            <div className="card-title mt">任务参数</div>
            {kind === 'diagnosis' && (
              <div className="field">
                <label className="field-label">患者 patient_id</label>
                <input
                  list="patient-list"
                  value={diag.patient_id}
                  placeholder="选择或输入患者编号"
                  onChange={(e) => setDiag({ patient_id: e.target.value })}
                />
                <datalist id="patient-list">
                  {patients.map((p) => (
                    <option key={p.patient_id} value={p.patient_id}>
                      {p.bpCR !== undefined || p.bpcr !== undefined
                        ? `bpCR=${(p.bpCR ?? p.bpcr ?? 0).toFixed(4)}` : ''}
                    </option>
                  ))}
                </datalist>
                {patErr
                  ? <div className="hint">/api/patients 不可用（{patErr}），请手工输入患者编号。</div>
                  : <div className="hint">数据集内置 {patients.length} 位患者。</div>}
              </div>
            )}

            {kind === 'compute' && (
              <NumFields
                rows={[
                  ['instruments', '器械数 instruments', compute.instruments, 1, 64, (v) => setCompute({ ...compute, instruments: v })],
                  ['rows', '行数 rows', compute.rows, 1, 65536, (v) => setCompute({ ...compute, rows: v })],
                  ['intensity', '强度 intensity', compute.intensity, 1, 1000, (v) => setCompute({ ...compute, intensity: v })],
                  ['partition_count', '分区数 partition_count', compute.partition_count, 1, 16, (v) => setCompute({ ...compute, partition_count: v })],
                ]}
                presets={COMPUTE_PRESETS.map((p) => ({
                  name: `计算_${p.name}`,
                  apply: () => setCompute(p.v),
                  hint: `${p.v.instruments}×${p.v.rows}×${p.v.intensity}×${p.v.partition_count}`,
                }))}
              />
            )}

            {kind === 'sync' && (
              <NumFields
                rows={[
                  ['bandwidth_mbps', '带宽 bandwidth_mbps', sync.bandwidth_mbps, 1, 10000, (v) => setSync({ ...sync, bandwidth_mbps: v })],
                  ['concurrency', '并发 concurrency', sync.concurrency, 1, 64, (v) => setSync({ ...sync, concurrency: v })],
                  ['chunk_kb', '分块 chunk_kb', sync.chunk_kb, 1, 4096, (v) => setSync({ ...sync, chunk_kb: v })],
                ]}
                presets={SYNC_PRESETS.map((p) => ({
                  name: `通信_${p.name}`,
                  apply: () => setSync(p.v),
                  hint: `${p.v.bandwidth_mbps} Mbps · ${p.v.concurrency} 并发 · ${p.v.chunk_kb} KB`,
                }))}
              />
            )}

            {kind === 'routine' && (
              <NumFields
                rows={[
                  ['jobs', '作业数 jobs', routine.jobs, 1, 64, (v) => setRoutine({ ...routine, jobs: v })],
                  ['rows', '行数 rows', routine.rows, 1, 65536, (v) => setRoutine({ ...routine, rows: v })],
                  ['intensity', '强度 intensity', routine.intensity, 1, 1000, (v) => setRoutine({ ...routine, intensity: v })],
                ]}
                presets={ROUTINE_PRESETS.map((p) => ({
                  name: `日常_${p.name}`,
                  apply: () => setRoutine(p.v),
                  hint: `${p.v.jobs} 个作业`,
                }))}
              />
            )}

            <div className="card-title mt">实验设置</div>
            <div className="field-2col">
              <div className="field">
                <label className="field-label">重复次数 repeats</label>
                <input type="number" min={1} max={200} value={repeats}
                  onChange={(e) => setRepeats(Number(e.target.value))} />
              </div>
              <div className="field">
                <label className="field-label">并发 concurrency</label>
                <input type="number" min={1} max={64} value={concurrency}
                  onChange={(e) => setConcurrency(Number(e.target.value))} />
              </div>
            </div>
            <div className="field">
              <label className="field-label">标签 label（可选）</label>
              <input value={label} placeholder="例如 计算-协同-基线A" onChange={(e) => setLabel(e.target.value)} />
            </div>

            {submitErr ? <div className="errbox">下发失败：{submitErr}</div> : null}
            {problems.length > 0 ? <div className="hint">待修正：{problems.join('；')}</div> : null}

            <div className="btnrow end">
              <button className="btn primary" disabled={busy || problems.length > 0} onClick={() => void submitSingle()}>
                {busy ? '下发中…' : `下发实验（${repeats} 次）`}
              </button>
            </div>

            <div className="submit-summary">
              <span className="ss-item"><i>任务</i><b>{KIND_ZH[kind]}</b></span>
              <span className="ss-item"><i>发起</i><b>{source}</b></span>
              <span className="ss-item"><i>策略</i><b>{MODE_ZH[strategy]}</b></span>
              <span className="ss-item"><i>参数</i><b>{JSON.stringify(params)}</b></span>
            </div>
          </section>
        )}

        {/* ------------------------------------------------------- 下发结果 / 进度 */}
        <section className="card">
          <div className="card-headrow">
            <div className="card-title">本次下发</div>
            {runId ? (
              <div className="btnrow">
                <button className="mini" onClick={() => onGoto('results')}>结果明细 →</button>
                <button className="mini" onClick={() => onGoto('compare')}>性能对比 →</button>
              </div>
            ) : null}
          </div>

          {!runId && !busy ? (
            <div className="empty">暂无数据，请先下发测试任务</div>
          ) : null}
          {busy ? <div className="hint">正在提交 POST /api/runs…</div> : null}

          {runId ? (
            <>
              {createNote ? <div className="msg ok">{createNote}</div> : null}
              <div className="kvgrid">
                <div className="kv"><span>run_id</span><b className="mono wrap">{runId}</b></div>
                <div className="kv"><span>状态 status</span><b><StatusBadge status={run?.status} /></b></div>
                <div className="kv"><span>任务数</span><b>{total || '—'}</b></div>
                <div className="kv"><span>创建时间</span><b>{fmtTime(run?.created_at)}</b></div>
              </div>

              <ProgressBar finished={counts.finished} total={total} />
              {running ? (
                <div className="hint">
                  每 2.5 s 刷新 · 待执行 {counts.pending} · 执行中 {counts.running}
                  {sentKind === 'suite' ? ' · 本地执行整套约 20 s 起' : ''}
                </div>
              ) : (
                <div className="hint">已完成（完成 {counts.finished} / {total}）</div>
              )}

              <AttemptStats attempts={attempts} />
              <AttemptTable attempts={attempts} />
            </>
          ) : null}
        </section>
      </div>
    </>
  );
}

/* ---------------------------------------------------------------- 小组件 */

/** 套件的档位清单：完全来自 GET /api/suites 的 specs[]，前端不写死任何档位 */
export function SuiteSpecTable({ suite }: { suite: SuiteInfo }) {
  return (
    <div className="tbl-scroll-x">
      <table className="dt small">
        <thead>
          <tr>
            <th>档位</th><th>仪器</th><th>行数</th><th>强度</th><th>分区</th><th>重复次数</th>
          </tr>
        </thead>
        <tbody>
          {suite.specs.map((sp) => (
            <tr key={sp.label}>
              <td><b>{sp.label}</b></td>
              <td className="mono num">{String(sp.params.instruments ?? '—')}</td>
              <td className="mono num">{String(sp.params.rows ?? '—')}</td>
              <td className="mono num">{String(sp.params.intensity ?? '—')}</td>
              <td className="mono num">{String(sp.params.partition_count ?? '—')}</td>
              <td className="mono num">{sp.repeats}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function StrategyPicker({ value, onChange }: { value: ReqMode; onChange: (m: ReqMode) => void }) {
  return (
    <div className="modes">
      {STRATEGIES.map((m) => (
        <button key={m} className={`modebtn m-${m} ${value === m ? 'on' : ''}`} onClick={() => onChange(m)}>
          <b>{MODE_ZH[m]}</b>
          <i className="mono">{m}</i>
        </button>
      ))}
    </div>
  );
}

function SourcePicker({ value, onChange }: { value: string; onChange: (s: string) => void }) {
  return (
    <div className="src-groups">
      <div className="src-group">
        <div className="src-group-t">医院</div>
        <div className="src-opts">
          {HOSPITALS.map((s) => (
            <button key={s} className={`src-opt ${value === s ? 'on' : ''}`} onClick={() => onChange(s)}>
              <span className="src-name">{s}</span>
            </button>
          ))}
        </div>
      </div>
      <div className="src-group">
        <div className="src-group-t">诊所</div>
        <div className="src-opts cols2">
          {CLINICS.map((s) => (
            <button key={s} className={`src-opt ${value === s ? 'on' : ''}`} onClick={() => onChange(s)}>
              <span className="src-name">{s}</span>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}

function NumFields({
  rows, presets,
}: {
  rows: [string, string, number, number, number, (v: number) => void][];
  presets: { name: string; apply: () => void; hint: string }[];
}) {
  return (
    <>
      <div className="field-2col">
        {rows.map(([key, lab, val, min, max, set]) => (
          <div className="field" key={key}>
            <label className="field-label">{lab}</label>
            <input
              type="number" min={min} max={max} value={val}
              onChange={(e) => set(Number(e.target.value))}
            />
          </div>
        ))}
      </div>
      <div className="presets">
        <span className="presets-lbl">预设</span>
        {presets.map((p) => (
          <button key={p.name} className="mini" onClick={p.apply} title={p.hint}>{p.name}</button>
        ))}
      </div>
    </>
  );
}
