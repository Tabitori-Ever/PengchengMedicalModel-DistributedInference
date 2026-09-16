import { MODEL_LABEL, SOURCES } from '../utils';
import type { ModelKind, SourceId } from '../types';
import { entityName } from '../terms';
import type { DatasetEntry, DatasetFileMeta, DatasetFolder } from './dataset';
import { fmtSize } from './dataset';

/**
 * 提交卡：与会话记录同宽、同列、居中。
 *
 * 顶部横条选中任务类型后，这里直接列出该类型文件夹里的数据集文件
 * （来自 /app/datasets/index.json），点一下即选中该文件；只读这一个文件，
 * 没有弹窗、没有上传步骤、没有系统文件对话框。发起方是唯一另一个控件。
 */
export default function TaskForm({
  kind, source, folder, file, busy, busyPath, note, error, onSource, onPick, onSubmit,
}: {
  kind: ModelKind;
  source: SourceId;
  folder: DatasetFolder | null;
  file: DatasetEntry | null;
  busy: boolean;
  /** 正在读取的文件路径 */
  busyPath: string;
  /** 目录不可用 / 文件读取失败 */
  note: string;
  error: string;
  onSource: (s: SourceId) => void;
  onPick: (meta: DatasetFileMeta) => void;
  onSubmit: () => void;
}) {
  return (
    <section className="up-card up-submit" aria-label={`${MODEL_LABEL[kind]}任务`}>
      <header className="up-card-head">
        <b className="up-card-title">{`${MODEL_LABEL[kind]}任务`}</b>
        <i className="up-card-note">{folder ? folder.name : MODEL_LABEL[kind]}</i>
      </header>

      <div className="up-field">
        <label className="up-label" htmlFor="up-source">发起方</label>
        <select
          id="up-source"
          className="up-select"
          value={source}
          onChange={(e) => onSource(e.target.value as SourceId)}
        >
          {SOURCES.map((s) => (
            <option key={s} value={s}>{entityName(s)}</option>
          ))}
        </select>
      </div>

      <div className="up-files" aria-label={`${MODEL_LABEL[kind]}数据集`}>
        {!folder && !note && <div className="up-note">正在读取数据集目录…</div>}
        {folder && folder.files.length === 0 && <div className="up-note">该文件夹暂无文件</div>}
        {folder && folder.files.map((f) => (
          <button
            key={f.path}
            type="button"
            data-path={f.path}
            aria-pressed={file?.path === f.path}
            className={`up-file ${file?.path === f.path ? 'on' : ''} ${busyPath === f.path ? 'busy' : ''}`}
            disabled={busyPath !== ''}
            onClick={() => onPick(f)}
          >
            <span className="up-file-name">{f.name}</span>
            <span className="up-file-desc">{f.description}</span>
            <span className="up-file-size mono">{fmtSize(f.bytes)}</span>
          </button>
        ))}
      </div>

      {note && <div className="up-note bad">{note}</div>}
      {error && <div className="errbox">{error}</div>}

      <button type="button" className="btn primary up-run" disabled={busy || !file} onClick={onSubmit}>
        {busy ? '提交中…' : '执行'}
      </button>
    </section>
  );
}
