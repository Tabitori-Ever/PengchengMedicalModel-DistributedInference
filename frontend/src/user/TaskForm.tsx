import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { ModelKind, Patient } from '../types';
import { MODEL_LABEL } from '../utils';
import { KIND_HINT, entityName } from '../terms';

/** 表单草稿：用户平台只暴露少量参数，其余收进「高级」 */
export interface Draft {
  kind: ModelKind;
  source: string;
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
  /** 计算规模预设 / 通信方式预设（'custom' 表示高级区手改过） */
  sizePreset?: string;
  modePreset?: string;
  /** 已选数据集（仅用于说明与预填，文件内容不会上传） */
  dataset?: { file: string; name: string };
  advanced: boolean;
}

const SIZES: Record<string, { instruments: number; rows: number; intensity: number; partitions: number }> = {
  small: { instruments: 2, rows: 128, intensity: 20, partitions: 2 },
  medium: { instruments: 4, rows: 256, intensity: 40, partitions: 3 },
  large: { instruments: 8, rows: 1024, intensity: 120, partitions: 6 },
};

const MODES: Record<string, { bandwidth: number; concurrency: number; chunkKb: number }> = {
  normal: { bandwidth: 20, concurrency: 4, chunkKb: 4 },
  slow: { bandwidth: 8, concurrency: 2, chunkKb: 4 },
  fast: { bandwidth: 60, concurrency: 8, chunkKb: 16 },
};

/** 诊断固定由「医院 1」发起（用于展示转诊链路） */
const DIAG_SOURCE = 'clinic-1';

export function draftFor(kind: ModelKind): Draft {
  const mid = SIZES.medium;
  const mod = MODES.normal;
  return {
    kind,
    source: kind === 'diagnosis' || kind === 'routine' ? DIAG_SOURCE : 'hospital-a',
    patientId: '',
    target: 'auto',
    instruments: mid.instruments,
    rows: kind === 'routine' ? 128 : mid.rows,
    intensity: kind === 'routine' ? 30 : mid.intensity,
    partitions: mid.partitions,
    bandwidth: mod.bandwidth,
    concurrency: mod.concurrency,
    chunkKb: mod.chunkKb,
    jobs: 3,
    sizePreset: 'medium',
    modePreset: 'normal',
    advanced: false,
  };
}

/** 用户气泡里的一句话摘要（不含优先级等管理信息） */
export function draftSummary(d: Draft): string {
  const ds = d.dataset ? ` · 数据集 ${d.dataset.name}` : '';
  if (d.kind === 'diagnosis') return `诊断 · 医院 1 发起 · 患者 ${d.patientId || '—'}${ds}`;
  if (d.kind === 'compute') {
    const label = d.sizePreset === 'small' ? '小' : d.sizePreset === 'large' ? '大'
      : d.sizePreset === 'custom' ? '自定义' : '中';
    return `计算 · 规模 ${label}（${d.instruments} 仪器 / ${d.rows} 行 / ${d.partitions} 分区）${ds}`;
  }
  if (d.kind === 'sync') {
    const label = d.modePreset === 'slow' ? '省流' : d.modePreset === 'fast' ? '高速'
      : d.modePreset === 'custom' ? '自定义' : '普通';
    return `通信 · 方式 ${label}（${d.bandwidth} Mbps / 并发 ${d.concurrency} / ${d.chunkKb} KB）${ds}`;
  }
  return `日常 · ${d.jobs} 个任务${ds}`;
}

function fmtSize(bytes?: number): string {
  if (!bytes && bytes !== 0) return '';
  if (bytes >= 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(2)} MB`;
  if (bytes >= 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${bytes} B`;
}

interface DatasetEntry {
  file: string;
  kind: string;
  name: string;
  description: string;
  bytes?: number;
  content?: any;
}

const num = (v: unknown): number | undefined => {
  const n = Number(v);
  return Number.isFinite(n) ? n : undefined;
};
const clampInt = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, Math.round(v)));

/** 数据集内容 → 草稿补丁（字段名与后端请求体一致；内容不会上传） */
function prefill(kind: ModelKind, data: any, draft: Draft): Partial<Draft> {
  if (!data || typeof data !== 'object') return {};
  const out: Partial<Draft> = {};
  if (kind === 'compute') {
    const i = num(data.instruments);
    const r = num(data.rows);
    const it = num(data.intensity);
    const p = num(data.partition_count) ?? num(data.partitions);
    if (i !== undefined) out.instruments = clampInt(i, 1, 8);
    if (r !== undefined) out.rows = clampInt(r, 64, 1024);
    if (it !== undefined) out.intensity = clampInt(it, 10, 200);
    if (p !== undefined) out.partitions = clampInt(p, 2, 6);
    const hit = Object.entries(SIZES).find(([, s]) => s.instruments === (out.instruments ?? draft.instruments)
      && s.rows === (out.rows ?? draft.rows) && s.intensity === (out.intensity ?? draft.intensity)
      && s.partitions === (out.partitions ?? draft.partitions));
    out.sizePreset = hit ? hit[0] : 'custom';
  } else if (kind === 'sync') {
    const b = num(data.bandwidth_mbps);
    const c = num(data.concurrency);
    const k = num(data.chunk_kb);
    if (b !== undefined) out.bandwidth = clampInt(b, 2, 100);
    if (c !== undefined) out.concurrency = clampInt(c, 1, 8);
    if (k !== undefined) out.chunkKb = [4, 8, 16].includes(Math.round(k)) ? Math.round(k) : 4;
    const hit = Object.entries(MODES).find(([, m]) => m.bandwidth === (out.bandwidth ?? draft.bandwidth)
      && m.concurrency === (out.concurrency ?? draft.concurrency) && m.chunkKb === (out.chunkKb ?? draft.chunkKb));
    out.modePreset = hit ? hit[0] : 'custom';
  } else if (kind === 'routine') {
    const j = num(data.jobs);
    const r = num(data.rows);
    const it = num(data.intensity);
    if (j !== undefined) out.jobs = clampInt(j, 1, 5);
    if (r !== undefined) out.rows = clampInt(r, 64, 1024);
    if (it !== undefined) out.intensity = clampInt(it, 10, 200);
  } else {
    const ids: string[] = Array.isArray(data.patient_ids) ? data.patient_ids.map(String) : [];
    if (ids.length && !ids.includes(draft.patientId)) out.patientId = ids[0];
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
  const kind = draft.kind;
  const set = <K extends keyof Draft>(k: K, v: Draft[K]) => onChange({ ...draft, [k]: v });
  const [showData, setShowData] = useState(false);
  const [list, setList] = useState<DatasetEntry[] | null>(null);
  const [dataErr, setDataErr] = useState('');
  const [fileHint, setFileHint] = useState('');
  const fileRef = useRef<HTMLInputElement | null>(null);

  // 数据集索引（/app/datasets/index.json）与各文件内容（体积小，一次加载）
  const loadDatasets = useCallback(async () => {
    if (list) return;
    setDataErr('');
    try {
      const base = import.meta.env.BASE_URL || '/';
      const res = await fetch(`${base}datasets/index.json`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const idx: DatasetEntry[] = await res.json();
      const withContent = await Promise.all((Array.isArray(idx) ? idx : []).map(async (d) => {
        try {
          const r = await fetch(`${base}datasets/${d.file}`);
          const text = await r.text();
          return { ...d, bytes: text.length, content: JSON.parse(text) };
        } catch {
          return d;
        }
      }));
      setList(withContent);
    } catch (e: any) {
      setDataErr(`数据集不可用：${e?.message || e}`);
      setList([]);
    }
  }, [list]);

  useEffect(() => { if (showData) loadDatasets(); }, [showData, loadDatasets]);

  const available = useMemo(() => (list || []).filter((d) => d.kind === kind), [list, kind]);
  const others = useMemo(() => (list || []).filter((d) => d.kind !== kind), [list, kind]);

  const choose = (d: DatasetEntry) => {
    onChange({
      ...draft,
      ...prefill(kind, d.content, draft),
      dataset: { file: d.file, name: d.name },
    });
    setFileHint(`已按「${d.name}」预填参数`);
  };

  const onLocalFile = async (f: File | undefined) => {
    if (!f) return;
    setFileHint('');
    if (/\.json$/i.test(f.name)) {
      try {
        const data = JSON.parse(await f.text());
        onChange({ ...draft, ...prefill(kind, data, draft), dataset: { file: f.name, name: f.name } });
        setFileHint(`已按本机文件 ${f.name}（${fmtSize(f.size)}）预填参数`);
      } catch {
        setFileHint(`${f.name} 解析失败，仅记录文件名`);
        onChange({ ...draft, dataset: { file: f.name, name: f.name } });
      }
    } else {
      setFileHint(`${f.name}（${fmtSize(f.size)}）仅作为数据来源说明`);
      onChange({ ...draft, dataset: { file: f.name, name: f.name } });
    }
  };

  const disabled = busy || (kind === 'diagnosis' && !draft.patientId);

  return (
    <div className="uf">
      <div className="uf-desc">{KIND_HINT[kind]}</div>

      {kind === 'diagnosis' && (
        <>
          <div className="uf-field">
            <label className="uf-label">发起方</label>
            <div className="uf-fixed">{`${entityName(DIAG_SOURCE)}（${DIAG_SOURCE}）· 自动转诊到医疗中心`}</div>
          </div>
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
        </>
      )}

      {kind === 'compute' && (
        <div className="uf-field">
          <label className="uf-label">规模</label>
          <div className="uf-seg">
            {([['small', '小'], ['medium', '中'], ['large', '大']] as const).map(([k, label]) => (
              <button
                key={k}
                type="button"
                className={`uf-seg-btn ${draft.sizePreset === k ? 'on' : ''}`}
                onClick={() => onChange({ ...draft, sizePreset: k, ...SIZES[k] })}
              >
                {label}
              </button>
            ))}
          </div>
        </div>
      )}

      {kind === 'sync' && (
        <div className="uf-field">
          <label className="uf-label">方式</label>
          <div className="uf-seg">
            {([['normal', '普通'], ['slow', '省流'], ['fast', '高速']] as const).map(([k, label]) => (
              <button
                key={k}
                type="button"
                className={`uf-seg-btn ${draft.modePreset === k ? 'on' : ''}`}
                onClick={() => onChange({ ...draft, modePreset: k, ...MODES[k] })}
              >
                {label}
              </button>
            ))}
          </div>
        </div>
      )}

      {kind === 'routine' && (
        <div className="uf-field">
          <label className="uf-label">任务数<span className="uf-hint">{`（${draft.jobs}）`}</span></label>
          <input
            type="range"
            min={1}
            max={5}
            value={draft.jobs}
            onChange={(e) => set('jobs', Number(e.target.value))}
          />
        </div>
      )}

      {/* 数据：数据集选择（按类型筛选）+ 本机文件补充 */}
      <div className="uf-field">
        <label className="uf-label">数据</label>
        <div className="uf-data-row">
          <button type="button" className="mini" onClick={() => setShowData((v) => !v)}>
            {showData ? '收起数据集' : '选择数据集'}
          </button>
          {draft.dataset && (
            <>
              <span className="uf-hint">{`已选：${draft.dataset.name}`}</span>
              <button
                type="button"
                className="mini danger"
                onClick={() => onChange({ ...draft, dataset: undefined })}
              >
                清除
              </button>
            </>
          )}
        </div>

        {showData && (
          <div className="uf-data">
            {dataErr && <div className="uf-hint">{dataErr}</div>}
            {!dataErr && !list && <div className="uf-hint">加载数据集…</div>}
            {!dataErr && list && available.length === 0 && (
              <div className="uf-hint">暂无该类型数据集（可选择本机文件）</div>
            )}
            {available.map((d) => (
              <button
                key={d.file}
                type="button"
                className={`uf-data-item ${draft.dataset?.file === d.file ? 'on' : ''}`}
                onClick={() => choose(d)}
              >
                <b>{d.name}</b>
                <i>{d.description}</i>
                <span className="mono">{fmtSize(d.bytes)}</span>
              </button>
            ))}
            {others.length > 0 && (
              <div className="uf-hint">{`另有 ${others.length} 个其它类型数据集，切换任务类型后可选`}</div>
            )}
            <div className="uf-data-foot">
              <button type="button" className="mini" onClick={() => fileRef.current?.click()}>从本机选择文件</button>
              <input
                ref={fileRef}
                type="file"
                accept=".json,.csv,.txt"
                style={{ display: 'none' }}
                onChange={(e) => onLocalFile(e.target.files?.[0])}
              />
              <span className="uf-hint">文件仅用于预填参数，不会上传</span>
            </div>
          </div>
        )}
        {fileHint && <div className="uf-hint">{fileHint}</div>}
      </div>

      {/* 高级参数（默认收起） */}
      <div className="uf-field">
        <button type="button" className="uf-adv-toggle" onClick={() => set('advanced', !draft.advanced)}>
          {draft.advanced ? '收起高级参数' : '高级参数'}
        </button>
        {draft.advanced && (
          <div className="uf-adv">
            {kind === 'diagnosis' && (
              <div className="uf-adv-row">
                <span>转诊目标</span>
                <select value={draft.target} onChange={(e) => set('target', e.target.value as Draft['target'])}>
                  <option value="auto">自动（最空闲）</option>
                  <option value="hospital-a">医疗中心 A</option>
                  <option value="hospital-b">医疗中心 B</option>
                </select>
              </div>
            )}
            {kind === 'compute' && (
              <>
                <AdvNum label="仪器数" value={draft.instruments} min={1} max={8}
                  onChange={(v) => onChange({ ...draft, instruments: v, sizePreset: 'custom' })} />
                <AdvNum label="数据行数" value={draft.rows} min={64} max={1024} step={64}
                  onChange={(v) => onChange({ ...draft, rows: v, sizePreset: 'custom' })} />
                <AdvNum label="计算强度" value={draft.intensity} min={10} max={200} step={10}
                  onChange={(v) => onChange({ ...draft, intensity: v, sizePreset: 'custom' })} />
                <AdvNum label="分区数" value={draft.partitions} min={2} max={6}
                  onChange={(v) => onChange({ ...draft, partitions: v, sizePreset: 'custom' })} />
              </>
            )}
            {kind === 'sync' && (
              <>
                <AdvNum label="模拟带宽" unit="Mbps" value={draft.bandwidth} min={2} max={100} step={2}
                  onChange={(v) => onChange({ ...draft, bandwidth: v, modePreset: 'custom' })} />
                <AdvNum label="并发拉取" value={draft.concurrency} min={1} max={8}
                  onChange={(v) => onChange({ ...draft, concurrency: v, modePreset: 'custom' })} />
                <div className="uf-adv-row">
                  <span>分块大小</span>
                  <select
                    value={draft.chunkKb}
                    onChange={(e) => onChange({ ...draft, chunkKb: Number(e.target.value), modePreset: 'custom' })}
                  >
                    {[4, 8, 16].map((k) => <option key={k} value={k}>{`${k} KB`}</option>)}
                  </select>
                </div>
              </>
            )}
            {kind === 'routine' && (
              <>
                <AdvNum label="数据行数" value={draft.rows} min={64} max={1024} step={64}
                  onChange={(v) => set('rows', v)} />
                <AdvNum label="计算强度" value={draft.intensity} min={10} max={200} step={10}
                  onChange={(v) => set('intensity', v)} />
              </>
            )}
          </div>
        )}
      </div>

      {error && <div className="errbox">{error}</div>}

      <div className="uf-actions">
        <button className="btn primary" disabled={disabled} onClick={onSubmit}>
          {busy ? '提交中…' : `提交${MODEL_LABEL[kind]}任务`}
        </button>
      </div>
    </div>
  );
}

function AdvNum({
  label, value, min, max, step = 1, unit, onChange,
}: {
  label: string; value: number; min: number; max: number; step?: number;
  unit?: string; onChange: (v: number) => void;
}) {
  return (
    <div className="uf-adv-row">
      <span>{label}{unit ? `（${unit}）` : ''}</span>
      <b className="mono">{value}</b>
      <input type="range" min={min} max={max} step={step} value={value}
        onChange={(e) => onChange(Number(e.target.value))} />
    </div>
  );
}
