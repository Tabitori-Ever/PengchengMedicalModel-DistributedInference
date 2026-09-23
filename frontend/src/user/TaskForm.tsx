import { useState } from 'react';
import type { ExecMode, ModelKind, SourceId } from '../types';
import { EXEC_MODE_LABEL, MODEL_LABEL, MODELS, SOURCES } from '../utils';
import { entityName } from '../terms';
import type { DatasetEntry, DatasetIndex } from './dataset';
import DatasetBrowser from './DatasetBrowser';

const MODES: ExecMode[] = ['collaborative', 'local', 'auto'];

/** 每种模式的简短说明（悬停提示） */
const MODE_HINT: Record<ExecMode, string> = {
  collaborative: '数据中心调度云边端多角色协作执行（默认）',
  local: '由发起 Pod 自行编排、就地优先执行，不经数据中心调度',
  auto: '依赖或调度器不可用时，自动降级为本地执行',
};

/**
 * 提交卡：一张卡的两部分，标题行就是四个任务类型按钮。
 *
 * 上半是横条（诊断 / 计算 / 通信 / 日常），撑满整卡、等分四格，选中一格实底高亮；
 * 下半是卡身：任务位置、执行模式、上传数据文件，再点「执行」。
 * 卡身不列任何数据集文件——文件列表只出现在弹窗里。
 */
export default function TaskForm({
  kind, source, mode, forceDegraded, index, file, busy, note, error,
  onKind, onSource, onMode, onForceDegraded, onPick, onSubmit,
}: {
  kind: ModelKind;
  source: SourceId;
  /** v3.2 执行模式：云边端协同 / 本地执行 / 自动 */
  mode: ExecMode;
  /** 强制降级开关（受控实验用） */
  forceDegraded: boolean;
  /** 数据集目录树：交给弹窗进入当前类型对应的文件夹 */
  index: DatasetIndex | null;
  file: DatasetEntry | null;
  busy: boolean;
  /** 目录不可用 / 文件读取失败 */
  note: string;
  error: string;
  onKind: (k: ModelKind) => void;
  onSource: (s: SourceId) => void;
  onMode: (m: ExecMode) => void;
  onForceDegraded: (v: boolean) => void;
  onPick: (entry: DatasetEntry) => void;
  onSubmit: () => void;
}) {
  const [open, setOpen] = useState(false);

  return (
    <section className="up-card up-submit" aria-label={`${MODEL_LABEL[kind]}任务`}>
      <div className="up-bar" role="tablist" aria-label="任务类型">
        {MODELS.map((k) => (
          <button
            key={k}
            type="button"
            role="tab"
            aria-selected={kind === k}
            className={kind === k ? 'up-tab on' : 'up-tab'}
            onClick={() => onKind(k)}
          >
            {MODEL_LABEL[k]}
          </button>
        ))}
      </div>

      <div className="up-card-body">
        <div className="up-field">
          <label className="up-label" htmlFor="up-source">任务位置</label>
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
          <i className="up-src-id mono">{source}</i>
        </div>

        <div className="up-field">
          <label className="up-label" htmlFor="up-mode">执行模式</label>
          <select
            id="up-mode"
            className="up-select"
            value={forceDegraded ? 'local' : mode}
            disabled={forceDegraded}
            title={MODE_HINT[mode]}
            onChange={(e) => onMode(e.target.value as ExecMode)}
          >
            {MODES.map((m) => (
              <option key={m} value={m}>{EXEC_MODE_LABEL[m]}</option>
            ))}
          </select>
          <label className="up-check" title="忽略执行模式，直接由发起 Pod 本地执行">
            <input
              type="checkbox"
              checked={forceDegraded}
              onChange={(e) => onForceDegraded(e.target.checked)}
            />
            强制降级
          </label>
        </div>

        <div className="up-up-row">
          <button
            type="button"
            className={file ? 'up-up on' : 'up-up'}
            aria-haspopup="dialog"
            onClick={() => setOpen(true)}
          >
            <span className="up-up-glyph" aria-hidden="true">{file ? '↻' : '＋'}</span>
            {file ? '重新上传' : '上传'}
          </button>
          {file ? (
            <span className="up-picked">
              <b className="up-picked-name">{file.name}</b>
              <i className="up-picked-desc">{file.description}</i>
            </span>
          ) : (
            <div className="up-note">请先上传数据文件</div>
          )}
        </div>

        {note && <div className="up-note bad">{note}</div>}
        {error && <div className="errbox">{error}</div>}

        <button
          type="button"
          className="btn primary up-run"
          disabled={busy || !file}
          onClick={onSubmit}
        >
          {busy ? '提交中…' : '执行'}
        </button>
      </div>

      <DatasetBrowser
        open={open}
        kind={kind}
        index={index}
        onPick={onPick}
        onClose={() => setOpen(false)}
      />
    </section>
  );
}
