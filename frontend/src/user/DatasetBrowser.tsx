import { useEffect, useMemo, useState } from 'react';
import type { ModelKind } from '../types';
import { MODEL_LABEL } from '../utils';
import type { DatasetEntry, DatasetFileMeta, DatasetFolder, DatasetIndex } from './dataset';
import { fetchIndex, fmtSize } from './dataset';
import { folderFor, loadEntry } from './state';

/**
 * 数据集目录浏览器（页面内弹窗）
 *
 * 「上传」打开的就是数据集目录本身：进入时已经在当前任务类型对应的文件夹里
 * （诊断 → 诊断 / 计算 → 计算 / 通信 → 通信 / 日常 → 日常），面包屑可以退回
 * 根层再进别的文件夹（根层列出四个文件夹与各自的文件数量）。
 * 每个文件显示中文名 + 说明 + 体积（mono），点一下即选中并关闭弹窗：
 * 文件内容只用 fetchEntry 读被点选的那一个（index.json 提供轻量列表）。
 *
 * 关闭方式：选中文件、Esc、点遮罩、右上角「关闭」。
 */
export default function DatasetBrowser({
  open, kind, index, onPick, onClose,
}: {
  open: boolean;
  /** 当前任务类型：弹窗进入它对应的文件夹 */
  kind: ModelKind;
  /** 父级已读到的目录树（没有时自己读一次） */
  index: DatasetIndex | null;
  onPick: (entry: DatasetEntry) => void;
  onClose: () => void;
}) {
  const [own, setOwn] = useState<DatasetIndex | null>(null);
  const [folder, setFolder] = useState<DatasetFolder | null>(null);
  const [note, setNote] = useState('');
  const [busyPath, setBusyPath] = useState('');

  const idx = index || own;
  /** 当前类型对应的文件夹：每次打开都从这里进入 */
  const start = useMemo(() => folderFor(idx, kind), [idx, kind]);

  // 目录树：父级没读到就自己读一次，之后复用
  useEffect(() => {
    if (!open || idx) return undefined;
    let disposed = false;
    fetchIndex()
      .then((i) => { if (!disposed) setOwn(i); })
      .catch((e: any) => { if (!disposed) setNote(`读取失败：${String(e?.message || e)}`); });
    return () => { disposed = true; };
  }, [open, idx]);

  // 每次打开（或切换类型后打开）都落到当前类型的文件夹
  useEffect(() => {
    if (!open) return;
    setNote('');
    setFolder(start);
  }, [open, start]);

  // Esc 关闭
  useEffect(() => {
    if (!open) return undefined;
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  if (!open) return null;

  /** 点选文件：只读这一个文件，读成功即选中并关闭 */
  const choose = async (meta: DatasetFileMeta) => {
    setBusyPath(meta.path);
    setNote('');
    try {
      const entry = await loadEntry(meta);
      if (entry.kind !== kind) {
        setNote('该文件与当前任务类型不一致，请回到目录重新选择');
        return;
      }
      onPick(entry);
      onClose();
    } catch (e: any) {
      setNote(`读取失败：${String(e?.message || e)}`);
    } finally {
      setBusyPath('');
    }
  };

  const root = idx?.root || '数据集';
  const here = folder ? folder.name : '';

  return (
    <div
      className="up-modal-mask"
      role="dialog"
      aria-modal="true"
      aria-label="数据集目录"
      onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div className="up-modal">
        <header className="up-modal-head">
          <nav className="up-crumb" aria-label="当前位置">
            <button
              type="button"
              className={`up-crumb-btn ${folder ? '' : 'on'}`}
              disabled={!folder}
              onClick={() => { setFolder(null); setNote(''); }}
            >
              {root}
            </button>
            {folder && (
              <>
                <span className="up-crumb-sep">/</span>
                <b className="up-crumb-cur">{here}</b>
              </>
            )}
          </nav>
          <i className="up-modal-type">{MODEL_LABEL[kind]}</i>
          <button type="button" className="up-modal-x" onClick={onClose}>关闭</button>
        </header>

        <div className="up-modal-body">
          {!idx && !note && <div className="up-note">正在读取数据集目录…</div>}
          {note && <div className="up-note bad">{note}</div>}

          {!folder && idx && (
            <div className="up-browse-grid">
              {idx.folders.map((f) => (
                <button
                  key={f.id || f.name}
                  type="button"
                  className={`up-browse-dir ${f.id === kind ? 'on' : ''}`}
                  onClick={() => { setFolder(f); setNote(''); }}
                >
                  <span className="up-browse-ico" aria-hidden="true" />
                  <b className="up-browse-name">{f.name}</b>
                  <i className="up-browse-count mono">{`${f.files.length} 个文件`}</i>
                </button>
              ))}
            </div>
          )}

          {folder && (
            <>
              <div className="up-browse-files">
                {folder.files.length === 0 && <div className="up-note">该文件夹暂无文件</div>}
                {folder.files.map((f) => (
                  <button
                    key={f.path}
                    type="button"
                    data-path={f.path}
                    className={`up-browse-file ${busyPath === f.path ? 'on' : ''}`}
                    disabled={busyPath !== ''}
                    onClick={() => choose(f)}
                  >
                    <span className="up-browse-file-name">{f.name}</span>
                    <span className="up-browse-file-desc">{f.description}</span>
                    <span className="up-browse-file-size mono">{fmtSize(f.bytes)}</span>
                  </button>
                ))}
              </div>
              {folder.files.length > 0 && (
                <button
                  type="button"
                  className="up-browse-back"
                  onClick={() => { setFolder(null); setNote(''); }}
                >
                  {`返回 ${root}`}
                </button>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
