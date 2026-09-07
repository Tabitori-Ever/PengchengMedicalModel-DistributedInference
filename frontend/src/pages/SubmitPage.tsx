import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { api, normalizeTarget } from '../api';
import TaskResultView, { isTerminalStatus } from '../components/TaskResultView';
import SourcePicker from '../components/SourcePicker';
import type { HospitalId, ModelKind, Patient, SourceId, TaskItem, TaskResult } from '../types';
import { MODEL_LABEL, MODELS } from '../utils';

const DEADLINES = ['10s', '30s', '60s', '120s', '300s'] as const;

export default function SubmitPage() {
  const [tab, setTab] = useState<ModelKind>('diagnosis');
  const [running, setRunning] = useState(false);
  const [err, setErr] = useState('');
  const [taskId, setTaskId] = useState('');
  const [poll, setPoll] = useState<TaskResult | null>(null);
  const [item, setItem] = useState<TaskItem | null>(null);
  const fetchedRef = useRef(false);

  // poll the submitted task; once it reaches a terminal state, load the full
  // task record so the rich v3 result payload renders right here.
  useEffect(() => {
    if (!taskId) return undefined;
    fetchedRef.current = false;
    setPoll(null);
    setItem(null);
    let disposed = false;
    const iv = window.setInterval(async () => {
      try {
        const r = await api.taskResult(taskId);
        if (disposed) return;
        setPoll(r);
        if (isTerminalStatus(r.status)) {
          if (!fetchedRef.current) {
            fetchedRef.current = true;
            try {
              const detail = await api.taskDetail(taskId);
              if (!disposed) setItem(detail);
            } catch { /* keep light result */ }
          }
          window.clearInterval(iv);
        }
      } catch { /* keep polling */ }
    }, 1500);
    return () => { disposed = true; window.clearInterval(iv); };
  }, [taskId]);

  const switchTab = (t: ModelKind) => {
    setTab(t); setErr(''); setTaskId(''); setPoll(null); setItem(null);
  };

  return (
    <div className="page narrow">
      <header className="page-head">
        <h1>任务提交</h1>
        <p>四类任务：诊断（bpCR）、计算、通信（患者库同步）、日常（例行 Job）。由医院（边）/ 诊所（端）发起。</p>
      </header>

      <div className="seg">
        {MODELS.map((t) => (
          <button
            key={t}
            className={`seg-btn ${tab === t ? 'on' : ''}`}
            onClick={() => switchTab(t)}
          >
            {MODEL_LABEL[t]}
          </button>
        ))}
      </div>

      <div className="card form-card">
        {tab === 'diagnosis' && <DiagnosisForm onDone={done} busy={running} setBusy={setRunning} onErr={setErr} />}
        {tab === 'compute' && <ComputeForm onDone={done} busy={running} setBusy={setRunning} onErr={setErr} />}
        {tab === 'sync' && <SyncForm onDone={done} busy={running} setBusy={setRunning} onErr={setErr} />}
        {tab === 'routine' && <RoutineForm onDone={done} busy={running} setBusy={setRunning} onErr={setErr} />}
      </div>

      {err && <div className="errbox">{err}</div>}

      {taskId && (
        <div className="card">
          <div className="card-headrow">
            <h3 className="card-title">提交结果 · {MODEL_LABEL[tab]}</h3>
            <Link className="link" to="/history">在任务记录查看 →</Link>
          </div>
          <div className="mono mb">{taskId}</div>
          {poll ? (
            <TaskResultView task={item} live={poll} />
          ) : (
            <div className="empty">等待调度器响应…</div>
          )}
        </div>
      )}

      {running && <div className="hint">正在提交…</div>}
    </div>
  );

  function done(id: string) { setTaskId(id); setErr(''); setPoll(null); }
}

// ---------------------------------------------------------------------------
// small shared form building blocks
// ---------------------------------------------------------------------------
function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="field">
      <label className="field-label">{label}</label>
      {children}
    </div>
  );
}

function RangeRow({
  value, onChange, min, max, step = 1, unit = '',
}: { value: number; onChange: (v: number) => void; min: number; max: number; step?: number; unit?: string }) {
  return (
    <div className="slider-row">
      <input type="range" min={min} max={max} step={step} value={value}
        onChange={(e) => onChange(Number(e.target.value))} />
      <span className="mono">{value}{unit}</span>
    </div>
  );
}

function BtnGroup<T extends string>({
  value, onChange, options, labels,
}: { value: T; onChange: (v: T) => void; options: readonly T[]; labels?: Record<string, string> }) {
  return (
    <div className="btnrow">
      {options.map((o) => (
        <button key={o} className={`mini ${value === o ? 'on' : ''}`}
          onClick={() => onChange(o)}>{labels?.[o] ?? o}</button>
      ))}
    </div>
  );
}

function CommonFields({
  priority, setPriority, deadline, setDeadline,
}: {
  priority: number; setPriority: (n: number) => void;
  deadline: string; setDeadline: (s: string) => void;
}) {
  return (
    <>
      <Field label="优先级">
        <RangeRow value={priority} onChange={setPriority} min={1} max={10} />
      </Field>
      <Field label="截止时间（可选）">
        <BtnGroup value={deadline} onChange={setDeadline} options={DEADLINES} />
      </Field>
    </>
  );
}

function usePatients() {
  const [patients, setPatients] = useState<Patient[]>([]);
  const [patErr, setPatErr] = useState('');
  useEffect(() => {
    api.patients().then(setPatients).catch((e) =>
      setPatErr(`测试数据集不可用：${e?.response?.status ?? e?.message}`));
  }, []);
  return { patients, patErr };
}

interface FormProps {
  onDone: (id: string) => void;
  busy: boolean;
  setBusy: (b: boolean) => void;
  onErr: (e: string) => void;
}

// ---------------------------------------------------------------------------
// 诊断 diagnosis — bpCR DoubleTower
// ---------------------------------------------------------------------------
function DiagnosisForm({ onDone, busy, setBusy, onErr }: FormProps) {
  const { patients, patErr } = usePatients();
  const [source, setSource] = useState<SourceId>('hospital-a');
  const [patient, setPatient] = useState('');
  const [target, setTarget] = useState<HospitalId | 'auto'>('auto');
  const [priority, setPriority] = useState(5);
  const [deadline, setDeadline] = useState('60s');
  const isClinic = source.startsWith('clinic');

  const submit = async () => {
    if (!patient) { onErr('请选择测试患者'); return; }
    setBusy(true); onErr('');
    try {
      const r = await api.submitDiagnosis({
        source, patient_id: patient,
        target_hospital: isClinic ? normalizeTarget(target) : undefined,
        priority, deadline,
      });
      onDone(r.task_id);
    } catch (e: any) { onErr(e?.response?.data?.detail ?? e?.message); }
    finally { setBusy(false); }
  };

  return (
    <>
      <p className="form-desc">
        鹏城医疗 DoubleTower bpCR 预测：医院 Pod（边）提取 DCE/DWI/临床/影像组学特征 →
        云端 medical-server 完成骨干融合分类。诊所（端）发起时将自动转诊到空闲医院。
      </p>
      <Field label="发起端（医院=边直接执行 · 诊所=端转诊）">
        <SourcePicker value={source} onChange={setSource} />
      </Field>
      <Field label="测试患者（必选）">
        {patErr ? <div className="muted">{patErr}</div> : (
          <select value={patient} onChange={(e) => setPatient(e.target.value)}>
            <option value="">选择患者…</option>
            {patients.map((p) => (
              <option key={p.patient_id} value={p.patient_id}>
                {p.patient_id}{p.bpCR != null ? ` · bpCR=${p.bpCR}` : ''}{p.hospital ? ` · ${p.hospital}` : ''}
              </option>
            ))}
          </select>
        )}
      </Field>
      {isClinic ? (
        <Field label="转诊目标医院">
          <BtnGroup value={target} onChange={setTarget}
            options={['auto', 'hospital-a', 'hospital-b'] as const}
            labels={{ auto: '自动（最空闲）' }} />
        </Field>
      ) : (
        <div className="muted xs mb" style={{ marginTop: -6 }}>
          ℹ️ 医院发起时直接在本医院执行（不转诊）；诊所发起时可指定转诊目标。
        </div>
      )}
      <CommonFields priority={priority} setPriority={setPriority}
        deadline={deadline} setDeadline={setDeadline} />
      <button className="btn primary wide" disabled={busy || !patient} onClick={submit}>
        提交诊断任务
      </button>
    </>
  );
}

// ---------------------------------------------------------------------------
// 计算 compute — multi-pod collaborative partition compute
// ---------------------------------------------------------------------------
function ComputeForm({ onDone, busy, setBusy, onErr }: FormProps) {
  const [source, setSource] = useState<SourceId>('hospital-a');
  const [instruments, setInstruments] = useState(4);
  const [rows, setRows] = useState(512);
  const [intensity, setIntensity] = useState(60);
  const [partitionCount, setPartitionCount] = useState(3);
  const [priority, setPriority] = useState(5);
  const [deadline, setDeadline] = useState('60s');

  const submit = async () => {
    setBusy(true); onErr('');
    try {
      const r = await api.submitCompute({
        source, instruments, rows, intensity,
        partition_count: partitionCount, priority, deadline,
      });
      onDone(r.task_id);
    } catch (e: any) { onErr(e?.response?.data?.detail ?? e?.message); }
    finally { setBusy(false); }
  };

  return (
    <>
      <p className="form-desc">
        协同计算：发起端 + 数据中心 + 合作医院按分区并行计算（行 × 仪器），
        每分区独立返回字节 / CPU / checksum，云端汇总校验。
      </p>
      <Field label="发起端">
        <SourcePicker value={source} onChange={setSource} />
      </Field>
      <Field label={`仪器数：${instruments} 台`}>
        <RangeRow value={instruments} onChange={setInstruments} min={1} max={8} />
      </Field>
      <Field label={`数据行数：${rows}`}>
        <RangeRow value={rows} onChange={setRows} min={64} max={1024} step={64} />
      </Field>
      <Field label={`计算强度：${intensity}`}>
        <RangeRow value={intensity} onChange={setIntensity} min={10} max={200} step={10} />
      </Field>
      <Field label={`分区数：${partitionCount}`}>
        <RangeRow value={partitionCount} onChange={setPartitionCount} min={2} max={6} />
      </Field>
      <CommonFields priority={priority} setPriority={setPriority}
        deadline={deadline} setDeadline={setDeadline} />
      <button className="btn primary wide" disabled={busy} onClick={submit}>
        提交计算任务
      </button>
    </>
  );
}

// ---------------------------------------------------------------------------
// 通信 sync — patient-db P2P cloud sync + backup
// ---------------------------------------------------------------------------
function SyncForm({ onDone, busy, setBusy, onErr }: FormProps) {
  const [source, setSource] = useState<SourceId>('clinic-1');
  const [bandwidth, setBandwidth] = useState(20);
  const [concurrency, setConcurrency] = useState(4);
  const [chunkKb, setChunkKb] = useState<'4' | '8' | '16'>('4');
  const [priority, setPriority] = useState(5);
  const [deadline, setDeadline] = useState('60s');

  const submit = async () => {
    setBusy(true); onErr('');
    try {
      const r = await api.submitSync({
        source, bandwidth_mbps: bandwidth, concurrency,
        chunk_kb: Number(chunkKb), priority, deadline,
      });
      onDone(r.task_id);
    } catch (e: any) { onErr(e?.response?.data?.detail ?? e?.message); }
    finally { setBusy(false); }
  };

  return (
    <>
      <p className="form-desc">
        患者库通信同步：端侧从各医院 / 云端 P2P 拉取缺失条目（模拟带宽限速），
        上传本地待同步更新并触发云端全量备份。
      </p>
      <Field label="发起端（本侧副本持有者）">
        <SourcePicker value={source} onChange={setSource} />
      </Field>
      <Field label={`模拟带宽：${bandwidth} Mbps`}>
        <RangeRow value={bandwidth} onChange={setBandwidth} min={2} max={100} step={2} unit="M" />
      </Field>
      <Field label={`并发拉取：${concurrency}`}>
        <RangeRow value={concurrency} onChange={setConcurrency} min={1} max={8} />
      </Field>
      <Field label="分块大小">
        <BtnGroup value={chunkKb} onChange={setChunkKb} options={['4', '8', '16'] as const}
          labels={{ '4': '4 KB', '8': '8 KB', '16': '16 KB' }} />
      </Field>
      <CommonFields priority={priority} setPriority={setPriority}
        deadline={deadline} setDeadline={setDeadline} />
      <button className="btn primary wide" disabled={busy} onClick={submit}>
        提交通信任务
      </button>
    </>
  );
}

// ---------------------------------------------------------------------------
// 日常 routine — ephemeral Kubernetes Jobs
// ---------------------------------------------------------------------------
function RoutineForm({ onDone, busy, setBusy, onErr }: FormProps) {
  const [source, setSource] = useState<SourceId>('clinic-1');
  const [jobs, setJobs] = useState(2);
  const [rows, setRows] = useState(128);
  const [intensity, setIntensity] = useState(30);
  const [priority, setPriority] = useState(5);
  const [deadline, setDeadline] = useState('60s');

  const submit = async () => {
    setBusy(true); onErr('');
    try {
      const r = await api.submitRoutine({
        source, jobs, rows, intensity, priority, deadline,
      });
      onDone(r.task_id);
    } catch (e: any) { onErr(e?.response?.data?.detail ?? e?.message); }
    finally { setBusy(false); }
  };

  return (
    <>
      <p className="form-desc">
        日常例行任务：调度器按空闲边缘节点生成一批一次性 K8s Job（含行列计算），
        捕获 JSON 输出后自动删除，不常驻集群。
      </p>
      <Field label="发起端">
        <SourcePicker value={source} onChange={setSource} />
      </Field>
      <Field label={`Job 数量：${jobs} 个`}>
        <RangeRow value={jobs} onChange={setJobs} min={1} max={5} />
      </Field>
      <Field label={`数据行数：${rows}`}>
        <RangeRow value={rows} onChange={setRows} min={64} max={1024} step={64} />
      </Field>
      <Field label={`计算强度：${intensity}`}>
        <RangeRow value={intensity} onChange={setIntensity} min={10} max={200} step={10} />
      </Field>
      <CommonFields priority={priority} setPriority={setPriority}
        deadline={deadline} setDeadline={setDeadline} />
      <button className="btn primary wide" disabled={busy} onClick={submit}>
        提交日常任务
      </button>
    </>
  );
}
