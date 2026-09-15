import { useMemo, useRef, useState } from 'react';
import type { ModelKind, Patient, SourceId } from '../types';
import SourcePicker from '../components/SourcePicker';
import { MODEL_LABEL } from '../utils';
import { KIND_HINT, SOURCE_HINT, entityName } from '../terms';

/** 表单草稿：一次任务的全部可调参数（文件只用于预填，不随请求发送） */
export interface Draft {
  kind: ModelKind;
  source: SourceId;
  patientId: string;
  target: 'auto' | 'hospital-a' | 'hospital-b';
  instruments: number;
  rows: number;
  intensity: number;
  partitions: number;
  bandwidth: number;
  concurrency: number;
  chunkKb: number;
  jobs: number;
  fileName?: string;
  fileSize?: number;
  fileNote?: string;
}

export function draftFor(kind: ModelKind): Draft {
  return {
    kind,
    source: kind === 'compute' || kind === 'diagnosis' ? 'hospital-a' : 'clinic-1',
    patientId: '',
    target: 'auto',
    instruments: 4,
    rows: kind === 'routine' ? 128 : 512,
    intensity: kind === 'routine' ? 30 : 60,
    partitions: 3,
    bandwidth: 20,
    concurrency: 4,
    chunkKb: 4,
    jobs: 2,
  };
}

/** 用户气泡里的一句话摘要 */
export function draftSummary(d: Draft): string {
  const src = `${entityName(d.source)}（${d.source}）`;
  if (d.kind === 'diagnosis') return `${src} 发起诊断 · 患者 ${d.patientId || '—'}`;
  if (d.kind === 'compute') return `${src} 发起计算 · ${d.instruments} 台仪器 / ${d.rows} 行 / 分区 ${d.partitions}`;
  if (d.kind === 'sync') return `${src} 发起通信 · 带宽 ${d.bandwidth} Mbps / 并发 ${d.concurrency} / 分块 ${d.chunkKb} KB`;
  return `${src} 发起日常 · ${d.jobs} 个任务 / ${d.rows} 行`;
}

function fmtSize(bytes?: number): string {
  if (!bytes && bytes !== 0) return '';
  if (bytes >= 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(2)} MB`;
  if (bytes >= 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${bytes} B`;
}

/** 从 JSON 文件中取可用参数做预填（不发送文件内容） */
function prefillFromJson(kind: ModelKind, data: any): Partial<Draft> {
  if (!data || typeof data !== 'object') return {};
  const num = (v: unknown): number | undefined => {
    const n = Number(v);
    return Number.isFinite(n) ? n : undefined;
  };
  const out: Partial<Draft> = {};
  if (kind === 'compute') {
    const i = num(data.instruments) ?? num(data.instrument_count);
    const r = num(data.rows) ?? num(data.row_count);
    const it = num(data.intensity);
    const p = num(data.partition_count) ?? num(data.partitions);
    if (i !== undefined) out.instruments = Math.min(8, Math.max(1, Math.round(i)));
    if (r !== undefined) out.rows = Math.min(1024, Math.max(64, Math.round(r)));
    if (it !== undefined) out.intensity = Math.min(200, Math.max(10, Math.round(it)));
    if (p !== undefined) out.partitions = Math.min(6, Math.max(2, Math.round(p)));
  } else if (kind === 'sync') {
    const b = num(data.bandwidth_mbps) ?? num(data.bandwidth);
    const c = num(data.concurrency);
    const k = num(data.chunk_kb) ?? num(data.chunkKb);
    if (b !== undefined) out.bandwidth = Math.min(100, Math.max(2, Math.round(b)));
    if (c !== undefined) out.concurrency = Math.min(8, Math.max(1, Math.round(c)));
    if (k !== undefined) out.chunkKb = [4, 8, 16].includes(Math.round(k)) ? Math.round(k) : 4;
  } else if (kind === 'routine') {
    const j = num(data.jobs);
    const r = num(data.rows);
    const it = num(data.intensity);
    if (j !== undefined) out.jobs = Math.min(5, Math.max(1, Math.round(j)));
    if (r !== undefined) out.rows = Math.min(1024, Math.max(64, Math.round(r)));
    if (it !== undefined) out.intensity = Math.min(200, Math.max(10, Math.round(it)));
  } else {
    const p = data.patient_id ?? data.patientId;
    if (typeof p === 'string' && p) out.patientId = p;
  }
  return out;
}

export default function TaskForm({
  draft, onChange, patients, busy, error, onSubmit,
}: {
  draft: Draft;
  onChange: (d: Draft) => void;
  patients: Patient[];
  busy: boolean;
  error?: string;
  onSubmit: () => void;
}) {
  const fileRef = useRef<HTMLInputElement | null>(null);
  const [fileMsg, setFileMsg] = useState('');
  const set = <K extends keyof Draft>(k: K, v: Draft[K]) => onChange({ ...draft, [k]: v });
  const kind = draft.kind;
  const isClinic = draft.source.startsWith('clinic');
  const canUpload = kind === 'compute' || kind === 'sync';
  const requirePatient = kind === 'diagnosis';

  const fileHint = useMemo(() => {
    if (kind === 'sync') return '可选：上传患者记录 .json 可自动预填带宽 / 并发 / 分块';
    return '可选：上传 .json 可自动预填仪器数 / 行数 / 强度 / 分区数';
  }, [kind]);

  const onFile = async (f: File | undefined) => {
    if (!f) return;
    setFileMsg('');
    const patch: Partial<Draft> = { fileName: f.name, fileSize: f.size };
    if (/\.json$/i.test(f.name)) {
      try {
        const text = await f.text();
        const parsed = JSON.parse(text);
        Object.assign(patch, prefillFromJson(kind, parsed));
        patch.fileNote = '已按文件内容预填参数';
        setFileMsg('已从 JSON 预填参数（文件内容不会上传）');
      } catch {
        patch.fileNote = '文件无法解析，仅作为数据来源备注';
        setFileMsg('JSON 解析失败，仅作为数据来源备注');
      }
    } else {
      patch.fileNote = '仅作为数据来源备注（非 JSON）';
      setFileMsg('已记录文件名与大小（仅作为数据来源备注）');
    }
    onChange({ ...draft, ...patch });
  };

  const submitDisabled = busy || (requirePatient && !draft.patientId);

  return (
    <div className="uf">
      <div className="uf-desc">{KIND_HINT[kind]}</div>

      <div className="uf-field">
        <label className="uf-label">发起方<span className="uf-hint">（{SOURCE_HINT}）</span></label>
        <SourcePicker value={draft.source} onChange={(s) => set('source', s)} />
      </div>

      {kind === 'diagnosis' && (
        <>
          <div className="uf-field">
            <label className="uf-label">患者</label>
            <select value={draft.patientId} onChange={(e) => set('patientId', e.target.value)}>
              <option value="">选择患者…</option>
              {patients.map((p) => (
                <option key={p.patient_id} value={p.patient_id}>
                  {p.patient_id}{p.bpCR != null ? ` · bpCR=${p.bpCR}` : ''}
                </option>
              ))}
            </select>
          </div>
          {isClinic && (
            <div className="uf-field">
              <label className="uf-label">转诊目标</label>
              <select value={draft.target} onChange={(e) => set('target', e.target.value as Draft['target'])}>
                <option value="auto">自动（最空闲）</option>
                <option value="hospital-a">医疗中心 A</option>
                <option value="hospital-b">医疗中心 B</option>
              </select>
            </div>
          )}
        </>
      )}

      {kind === 'compute' && (
        <>
          <Num label="仪器数" value={draft.instruments} min={1} max={8} onChange={(v) => set('instruments', v)} />
          <Num label="数据行数" value={draft.rows} min={64} max={1024} step={64} onChange={(v) => set('rows', v)} />
          <Num label="计算强度" value={draft.intensity} min={10} max={200} step={10} onChange={(v) => set('intensity', v)} />
          <Num label="分区数" value={draft.partitions} min={2} max={6} onChange={(v) => set('partitions', v)} />
        </>
      )}

      {kind === 'sync' && (
        <>
          <Num label="模拟带宽" unit="Mbps" value={draft.bandwidth} min={2} max={100} step={2} onChange={(v) => set('bandwidth', v)} />
          <Num label="并发拉取" value={draft.concurrency} min={1} max={8} onChange={(v) => set('concurrency', v)} />
          <div className="uf-field">
            <label className="uf-label">分块大小</label>
            <div className="uf-seg">
              {[4, 8, 16].map((k) => (
                <button
                  key={k}
                  type="button"
                  className={`uf-seg-btn ${draft.chunkKb === k ? 'on' : ''}`}
                  onClick={() => set('chunkKb', k)}
                >
                  {k} KB
                </button>
              ))}
            </div>
          </div>
        </>
      )}

      {kind === 'routine' && (
        <>
          <Num label="任务数" value={draft.jobs} min={1} max={5} onChange={(v) => set('jobs', v)} />
          <Num label="数据行数" value={draft.rows} min={64} max={1024} step={64} onChange={(v) => set('rows', v)} />
          <Num label="计算强度" value={draft.intensity} min={10} max={200} step={10} onChange={(v) => set('intensity', v)} />
        </>
      )}

      {canUpload && (
        <div className="uf-field">
          <label className="uf-label">数据文件<span className="uf-hint">（{fileHint}）</span></label>
          <div className="uf-file">
            <button type="button" className="mini" onClick={() => fileRef.current?.click()}>
              选择文件
            </button>
            <input
              ref={fileRef}
              type="file"
              accept=".json,.csv,.txt,.xlsx,.xls"
              style={{ display: 'none' }}
              onChange={(e) => onFile(e.target.files?.[0])}
            />
            {draft.fileName ? (
              <span className="uf-file-name">
                <b className="mono">{draft.fileName}</b>
                <i className="mono">{fmtSize(draft.fileSize)}</i>
                {draft.fileNote && <i className="uf-file-note">{draft.fileNote}</i>}
                <button type="button" className="mini danger" onClick={() => onChange({
                  ...draft, fileName: undefined, fileSize: undefined, fileNote: undefined,
                })}
                >
                  移除
                </button>
              </span>
            ) : (
              <span className="uf-hint">未选择文件（不会向后端上传文件内容）</span>
            )}
          </div>
          {fileMsg && <div className="uf-hint">{fileMsg}</div>}
        </div>
      )}

      {error && <div className="errbox">{error}</div>}

      <div className="uf-actions">
        <button className="btn primary" disabled={submitDisabled} onClick={onSubmit}>
          {busy ? '提交中…' : `提交${MODEL_LABEL[kind]}任务`}
        </button>
      </div>
    </div>
  );
}

function Num({
  label, value, min, max, step = 1, unit, onChange,
}: {
  label: string; value: number; min: number; max: number; step?: number;
  unit?: string; onChange: (v: number) => void;
}) {
  return (
    <div className="uf-field uf-num">
      <label className="uf-label">{label}<span className="uf-hint">{`（${value}${unit ? ` ${unit}` : ''}）`}</span></label>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
      />
    </div>
  );
}
