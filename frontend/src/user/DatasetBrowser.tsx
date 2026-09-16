import { useEffect, useState } from 'react';
import type { DatasetEntry, DatasetFileMeta, DatasetFolder, DatasetIndex } from './dataset';
import { fetchEntry, fetchIndex, fmtSize } from './dataset';

/**
 * 数据集目录浏览器（页面内弹窗）
 *
 * 上传按钮打开的就是「数据集目录」本身：根层列出各文件夹（含文件数量），
 * 进入文件夹后按中文文件名列出文件（说明 + 体积 + mono 体积数字），
 * 面包屑可退回根层，点文件即选中并关闭弹窗。
 * 文件内容只在被点选时读取一次（fetchEntry），根层与列表都用 index.json 的轻量信息。
 */
export default function DatasetBrowser({
  open, onPick, onClose,
}: {
  open: boolean;
  onPick: (entry: DatasetEntry) => void;
  onClose: () => void;
}) {
  const [index, setIndex] = useState<DatasetIndex | null>(null);
  const [folder, setFolder] = useState<DatasetFolder | null>(null);
  const [loading, setLoading] = useState(false);
  const [note, setNote] = useState('');
  const [busyPath, setBusyPath] = useState('');

  // 打开时读目录树（只读一次，之后复用）
  useEffect(() => {
    if (!open) return;
    setFolder(null);
    setNote('');
    if (index) return;
    setLoading(true);
    fetchIndex()
      .then((idx) => setIndex(idx))
      .catch((e: any) => setNote(String(e?.message || e)))
      .finally(() => setLoading(false));
  }, [open, index]);

  // Esc 关闭
  useEffect(() => {
    if (!open) return undefined;
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  if (!open) return null;

  const choose = async (meta: DatasetFileMeta) => {
    setBusyPath(meta.path);
    setNote('');
    try {
      const entry = await fetchEntry(meta);
      onPick(entry);
      onClose();
    } catch (e: any) {
      setNote(`读取失败：${String(e?.message || e)}`);
    } finally {
      setBusyPath('');
    }
  };

  const root = index?.root || '数据集';

  return (
    <div
      className="ufm-mask"
      role="dialog"
      aria-modal="true"
      aria-label={root}
      onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div className="ufm">
        <header className="ufm-head">
          <div className="ufm-crumb">
            <button
              type="button"
              className={`ufm-crumb-btn ${folder ? '' : 'on'}`}
              onClick={() => { setFolder(null); setNote(''); }}
              disabled={!folder}
            >
              {root}
            </button>
            {folder && (
              <>
                <span className="ufm-sep">/</span>
                <b className="ufm-crumb-cur">{folder.name}</b>
              </>
            )}
          </div>
          <button type="button" className="ufm-x" onClick={onClose}>关闭</button>
        </header>

        <div className="ufm-body">
          {loading && <div className="ufm-note">正在读取数据集目录…</div>}
          {note && <div className="ufm-note bad">{note}</div>}

          {!folder && index && (
            <div className="ufm-grid">
              {index.folders.map((f) => (
                <button
                  key={f.id || f.name}
                  type="button"
                  className="ufm-dir"
                  onClick={() => { setFolder(f); setNote(''); }}
                >
                  <span className="ufm-dir-ico" aria-hidden="true" />
                  <b className="ufm-dir-name">{f.name}</b>
                  <i className="ufm-dir-count mono">{`${f.files.length} 个文件`}</i>
                </button>
              ))}
            </div>
          )}

          {folder && (
            <>
              <div className="ufm-files">
                {folder.files.length === 0 && <div className="ufm-note">该文件夹暂无文件</div>}
                {folder.files.map((f) => (
                  <button
                    key={f.path}
                    type="button"
                    className={`ufm-file ${busyPath === f.path ? 'on' : ''}`}
                    disabled={busyPath !== ''}
                    onClick={() => choose(f)}
                  >
                    <span className="ufm-file-name">{f.name}</span>
                    <span className="ufm-file-desc">{f.description}</span>
                    <span className="ufm-file-size mono">{fmtSize(f.bytes)}</span>
                  </button>
                ))}
              </div>
              <button
                type="button"
                className="ufm-back"
                onClick={() => { setFolder(null); setNote(''); }}
              >
                {`返回 ${root}`}
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
