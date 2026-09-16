import { useState } from 'react';
import type { SourceId } from '../types';
import { MODEL_LABEL, SOURCES } from '../utils';
import { entityName } from '../terms';
import type { DatasetEntry } from './dataset';
import DatasetBrowser from './DatasetBrowser';

/**
 * 表单草稿：只保留「发起方」与「所选数据集文件」。
 * 任务类型与全部参数都写在文件名/文件内容里，用户不再做任何参数选择。
 */
export interface Draft {
  source: SourceId;
  file: DatasetEntry | null;
}

export const DEFAULT_SOURCE: SourceId = 'clinic-1';

export function draftFor(source?: string): Draft {
  const s = SOURCES.includes(source as SourceId) ? (source as SourceId) : DEFAULT_SOURCE;
  return { source: s, file: null };
}

/** 用户气泡里的一句话摘要（不含管理侧信息） */
export function draftSummary(d: Draft): string {
  const who = entityName(d.source);
  if (!d.file) return `${who} 发起`;
  return `${who} 发起 · 数据集 ${d.file.name}`;
}

/**
 * 待提交表单：发起方下拉 + 上传（打开数据集目录浏览器）。
 * 选中文件后显示文件名与说明，并可再次上传更换文件。
 */
export default function TaskForm({
  draft, onChange, busy, error, onSubmit,
}: {
  draft: Draft;
  onChange: (d: Draft) => void;
  busy: boolean;
  error?: string;
  onSubmit: () => void;
}) {
  const [open, setOpen] = useState(false);
  const file = draft.file;

  return (
    <div className="uf">
      <div className="uf-field">
        <div className="uf-label-row">
          <label className="uf-label" htmlFor="uf-source">发起方</label>
          <i className="uf-src-id mono">{draft.source}</i>
        </div>
        <select
          id="uf-source"
          value={draft.source}
          onChange={(e) => onChange({ ...draft, source: e.target.value as SourceId })}
        >
          {SOURCES.map((s) => (
            <option key={s} value={s}>{entityName(s)}</option>
          ))}
        </select>
      </div>

      <div className="uf-up-row">
        <button type="button" className={`mini uf-up ${file ? '' : 'on'}`} onClick={() => setOpen(true)}>
          {file ? '重新上传' : '上传'}
        </button>
        {file && (
          <span className="uf-picked">
            <b className="uf-picked-name">{file.name}</b>
            <i className="uf-picked-desc">{file.description}</i>
          </span>
        )}
      </div>

      {error && <div className="errbox">{error}</div>}

      <div className="uf-actions">
        <button className="btn primary" disabled={busy || !file} onClick={onSubmit}>
          {busy ? '提交中…' : file ? `执行${MODEL_LABEL[file.kind] || ''}任务` : '执行'}
        </button>
      </div>

      <DatasetBrowser
        open={open}
        onClose={() => setOpen(false)}
        onPick={(entry: DatasetEntry) => onChange({ ...draft, file: entry })}
      />
    </div>
  );
}
