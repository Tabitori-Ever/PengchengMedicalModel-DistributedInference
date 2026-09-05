import { useEffect, useMemo, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api';
import TaskResultView from '../components/TaskResultView';
import type { Patient, TaskItem, TaskResult } from '../types';
import { MODEL_LABEL } from '../utils';

type Tab = 'medical' | 'alexnet' | 'clinic';

const DEADLINES = ['10s', '30s', '60s', '120s', '300s'];

export default function SubmitPage() {
  const [tab, setTab] = useState<Tab>('medical');
  const [running, setRunning] = useState(false);
  const [err, setErr] = useState('');
  const [taskId, setTaskId] = useState('');
  const [poll, setPoll] = useState<TaskResult | null>(null);
  const [item, setItem] = useState<TaskItem | null>(null);
  const fetchedRef = useRef(false);

  // poll the submitted task; once it reaches a terminal state, also load the
  // full task record so the rich result (bpCR / class / memory / waterfall)
  // is rendered right here on the submit page.
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
        if (['finished', 'completed', 'failed'].includes(r.status)) {
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

  return (
    <div className="page narrow">
      <header className="page-head">
        <h1>任务提交</h1>
        <p>三类任务：医疗 bpCR 预测、AlexNet 图像分类、Pod 内存监控（clinic）。</p>
      </header>

      <div className="seg">
        {(['medical', 'alexnet', 'clinic'] as Tab[]).map((t) => (
          <button
            key={t}
            className={`seg-btn ${tab === t ? 'on' : ''}`}
            onClick={() => { setTab(t); setErr(''); setTaskId(''); setPoll(null); setItem(null); }}
          >
            {MODEL_LABEL[t]}
          </button>
        ))}
      </div>

      <div className="card form-card">
        {tab === 'medical' && <MedicalForm onDone={done} busy={running} setBusy={setRunning} onErr={setErr} />}
        {tab === 'alexnet' && <AlexForm onDone={done} busy={running} setBusy={setRunning} onErr={setErr} />}
        {tab === 'clinic' && <ClinicForm onDone={done} busy={running} setBusy={setRunning} onErr={setErr} />}
      </div>

      {err && <div className="errbox">{err}</div>}

      {taskId && (
        <div className="card">
          <div className="card-headrow">
            <h3 className="card-title">提交结果 · {MODEL_LABEL[tab]}</h3>
            <Link className="link" to="/history">在任务记录中查看 →</Link>
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
function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="field">
      <label className="field-label">{label}</label>
      {children}
    </div>
  );
}

function Slider({
  value, onChange,
}: { value: number; onChange: (v: number) => void }) {
  return (
    <div className="slider-row">
      <input type="range" min={1} max={10} value={value}
        onChange={(e) => onChange(Number(e.target.value))} />
      <span className="mono">P{value}</span>
    </div>
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

// --------------------------- medical bpCR ---------------------------
function MedicalForm({ onDone, busy, setBusy, onErr }: FormProps) {
  const { patients, patErr } = usePatients();
  const [hospital, setHospital] = useState('hospital-a');
  const [patient, setPatient] = useState('');
  const [priority, setPriority] = useState(5);
  const [deadline, setDeadline] = useState('30s');

  const submit = async () => {
    if (!patient) { onErr('请选择测试患者'); return; }
    setBusy(true); onErr('');
    try {
      const r = await api.submitPreprocessed({ patient_id: patient, hospital, priority, deadline });
      onDone(r.task_id);
    } catch (e: any) { onErr(e?.response?.data?.detail ?? e?.message); }
    finally { setBusy(false); }
  };

  return (
    <>
      <p className="form-desc">
        鹏城医疗模型（DoubleTower）bpCR 预测：医院 Pod 处理 DCE/DWI 前端特征，
        云端 medical-server 完成骨干网络与融合分类。患者原始数据不离开所选医院。
      </p>
      <Field label="患者来源医院（worker 前端所在 Hospital Pod）">
        <div className="btnrow">
          {['hospital-a', 'hospital-b'].map((h) => (
            <button key={h} className={`mini ${hospital === h ? 'on' : ''}`}
              onClick={() => setHospital(h)}>{h}</button>
          ))}
        </div>
      </Field>
      <Field label="测试患者">
        {patErr ? <div className="muted">{patErr}</div> : (
          <select value={patient} onChange={(e) => setPatient(e.target.value)}>
            <option value="">选择患者…</option>
            {patients.map((p) => (
              <option key={p.patient_id} value={p.patient_id}>
                {p.patient_id}{p.bpCR != null ? ` · bpCR=${p.bpCR}` : ''}
              </option>
            ))}
          </select>
        )}
      </Field>
      <Field label="优先级">
        <Slider value={priority} onChange={setPriority} />
      </Field>
      <Field label="截止时间">
        <div className="btnrow">
          {DEADLINES.map((d) => (
            <button key={d} className={`mini ${deadline === d ? 'on' : ''}`}
              onClick={() => setDeadline(d)}>{d}</button>
          ))}
        </div>
      </Field>
      <button className="btn primary wide" disabled={busy || !patient} onClick={submit}>
        提交医疗推理任务
      </button>
    </>
  );
}

// --------------------------- alexnet ---------------------------
function AlexForm({ onDone, busy, setBusy, onErr }: FormProps) {
  const [hospital, setHospital] = useState('hospital-a');
  const [priority, setPriority] = useState(5);
  const [deadline, setDeadline] = useState('10s');
  const [mode, setMode] = useState<'random' | 'upload'>('random');
  const [fileInfo, setFileInfo] = useState('');

  const submit = async () => {
    setBusy(true); onErr('');
    try {
      const image = mode === 'random'
        ? randomImage()
        : await fileToImage();
      const r = await api.submitAlexNet({
        hospital, priority, deadline, image,
      });
      onDone(r.task_id);
    } catch (e: any) { onErr(e?.response?.data?.detail ?? e?.message); }
    finally { setBusy(false); }
  };

  const fileToImage = async (): Promise<number[][][]> => {
    const input = document.getElementById('imgfile') as HTMLInputElement;
    if (!input?.files?.[0]) throw new Error('请选择图片文件');
    const bmp = await createImageBitmap(input.files[0]);
    const w = 224, h = 224;
    const canvas = document.createElement('canvas');
    canvas.width = w; canvas.height = h;
    const ctx = canvas.getContext('2d')!;
    ctx.drawImage(bmp, 0, 0, w, h);
    const { data } = ctx.getImageData(0, 0, w, h);
    const out: number[][][] = [];
    for (let c = 0; c < 3; c++) {
      const ch: number[][] = [];
      for (let y = 0; y < h; y++) {
        const row: number[] = [];
        for (let x = 0; x < w; x++) {
          row.push(data[(y * w + x) * 4 + c] / 255);
        }
        ch.push(row);
      }
      out.push(ch);
    }
    setFileInfo(`已载入并缩放至 224×224`);
    return out;
  };

  return (
    <>
      <p className="form-desc">
        AlexNet 分布式图像分类：所选医院 Pod 内执行 part1（Conv1-5 + avgpool），
        part2 全连接层由云端 part2 Pod 完成，返回 ImageNet 类别与置信度。
      </p>
      <Field label="图像来源（part1 执行所在 Hospital Pod）">
        <div className="btnrow">
          {['hospital-a', 'hospital-b'].map((h) => (
            <button key={h} className={`mini ${hospital === h ? 'on' : ''}`}
              onClick={() => setHospital(h)}>{h}</button>
          ))}
        </div>
      </Field>
      <Field label="输入图像">
        <div className="btnrow">
          <button className={`mini ${mode === 'random' ? 'on' : ''}`}
            onClick={() => setMode('random')}>随机噪声图</button>
          <button className={`mini ${mode === 'upload' ? 'on' : ''}`}
            onClick={() => setMode('upload')}>上传图片</button>
        </div>
        {mode === 'upload' && (
          <>
            <input id="imgfile" type="file" accept="image/*"
              onChange={(e) => setFileInfo(e.target.files?.[0] ? '已选择文件' : '')} />
            <div className="muted xs">{fileInfo}</div>
          </>
        )}
      </Field>
      <Field label="优先级">
        <Slider value={priority} onChange={setPriority} />
      </Field>
      <Field label="截止时间">
        <div className="btnrow">
          {DEADLINES.map((d) => (
            <button key={d} className={`mini ${deadline === d ? 'on' : ''}`}
              onClick={() => setDeadline(d)}>{d}</button>
          ))}
        </div>
      </Field>
      <button className="btn primary wide" disabled={busy} onClick={submit}>
        提交图像分类任务
      </button>
    </>
  );
}

function randomImage(): number[][][] {
  const size = 224;
  const out: number[][][] = [];
  for (let c = 0; c < 3; c++) {
    const ch: number[][] = [];
    for (let y = 0; y < size; y++) {
      const row: number[] = new Array(size);
      for (let x = 0; x < size; x++) row[x] = Math.random();
      ch.push(row);
    }
    out.push(ch);
  }
  return out;
}

// --------------------------- clinic (memory monitor) ---------------------------
function ClinicForm({ onDone, busy, setBusy, onErr }: FormProps) {
  const [clinic, setClinic] = useState('clinic-1');
  const [target, setTarget] = useState('');
  const [priority, setPriority] = useState(5);
  const [podNames, setPodNames] = useState<string[]>([]);

  useEffect(() => {
    api.clusterStatus().then((s) => {
      const names = Object.values(s.entities || {}).flatMap((e) =>
        (e.pods || []).map((p) => p.name));
      const ro = (s.readonly || []).flatMap((d) =>
        (d.pods || []).map((p) => p.name));
      setPodNames([...new Set([...names, ...ro])]);
    }).catch(() => setPodNames([]));
  }, []);

  const submit = async () => {
    setBusy(true); onErr('');
    try {
      const r = await api.submitClinic({
        clinic,
        target_pod: target.trim() || undefined,
        priority,
      });
      onDone(r.task_id);
    } catch (e: any) { onErr(e?.response?.data?.detail ?? e?.message); }
    finally { setBusy(false); }
  };

  return (
    <>
      <p className="form-desc">
        Clinic 任务：查询指定 Pod 的内存占用率（usage / limit）。
        目标留空时默认查询所选 Clinic Pod <b>自身</b>（读取容器 cgroup，恒可用）；
        填写目标 Pod 时由 Clinic 经 metrics-server 查询。
      </p>
      <Field label="执行查询的 Clinic Pod">
        <div className="btnrow">
          {['clinic-1', 'clinic-2'].map((c) => (
            <button key={c} className={`mini ${clinic === c ? 'on' : ''}`}
              onClick={() => setClinic(c)}>{c}</button>
          ))}
        </div>
      </Field>
      <Field label="目标 Pod（留空 = 默认查询自身）">
        <input list="podlist" value={target} onChange={(e) => setTarget(e.target.value)}
          placeholder="例如 hospital-a-xxxxxxxxxx-xxxxx" />
        <datalist id="podlist">
          {podNames.map((p) => <option key={p} value={p} />)}
        </datalist>
        {podNames.length > 0 && (
          <div className="pod-sug">
            {podNames.slice(0, 12).map((p) => (
              <button key={p} className="mini" onClick={() => setTarget(p)}>{p}</button>
            ))}
          </div>
        )}
      </Field>
      <Field label="优先级">
        <Slider value={priority} onChange={setPriority} />
      </Field>
      <button className="btn primary wide" disabled={busy} onClick={submit}>
        提交内存监控任务
      </button>
    </>
  );
}

// (results now rendered inline on this page via TaskResultView)

