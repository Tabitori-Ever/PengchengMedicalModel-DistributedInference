import { useState } from 'react';
import type { ModelKind } from '../types';
import { MODEL_LABEL, MODELS } from '../utils';
import type { DatasetEntry, DatasetIndex } from './dataset';
import DatasetBrowser from './DatasetBrowser';

/**
 * 提交卡：一张卡的两部分，标题行就是四个任务类型按钮。
 *
 * 上半是横条（诊断 / 计算 / 通信 / 日常），撑满整卡、等分四格，选中一格实底高亮；
 * 下半是卡身：上传数据文件，再点「执行」。任务位置固定为当前登录身份、执行模式
 * 固定走云边端协同，因此卡身不再有位置 / 模式选择。卡身也不列任何数据集文件
 * ——文件列表只出现在上传打开的弹窗里（弹窗内保留参数说明）。
 */
export default function TaskForm({
  kind, index, file, busy, note, error, onKind, onPick, onSubmit,
}: {
  kind: ModelKind;
  /** 数据集目录树：交给弹窗进入当前类型对应的文件夹 */
  index: DatasetIndex | null;
  file: DatasetEntry | null;
  busy: boolean;
  /** 目录不可用 / 文件读取失败 */
  note: string;
  error: string;
  onKind: (k: ModelKind) => void;
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
