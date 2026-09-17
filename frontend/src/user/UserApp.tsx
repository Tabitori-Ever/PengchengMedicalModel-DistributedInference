import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api } from '../api';
import { isTerminalStatus } from '../components/TaskResultView';
import type { ModelKind, SourceId, TaskItem } from '../types';
import { MODEL_LABEL, STATUS_LABEL } from '../utils';
import { PLATFORM_NAME, entityName } from '../terms';
import type { DatasetEntry } from './dataset';
import { buildSubmission, fetchIndex } from './dataset';
import type { UserRecord, UserState } from './state';
import {
  addRecord, clearFocus, focusTask, isPending, loadEntry, newTask, nextId,
  patchFocus, patchRecord, pickFile, queuedResult, recordStatus, selectKind, selectSource,
  sendSubmission, initialState, withIndex, folderFor,
} from './state';
import TaskForm from './TaskForm';
import UserResult from './UserResult';

const POLL_MS = 1500;
const HISTORY_LIMIT = 30;
const fmtTime = (iso?: string): string => {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString('zh-CN', {
    hour12: false, month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
  });
};

/**
 * 用户调用平台（/app/user/）：左侧品牌 + 历史任务，主区是一条居中列
 * （最大 880px）——提交卡在上（卡内标题行就是四个任务类型按钮），
 * 本次会话的记录依次在下，全部同宽同列。
 * 没有任何左右分栏的气泡：提交与结果同在一条列里，等宽对齐。
 */
export interface UserAppViewProps {
  state: UserState;
  busy: boolean;
  note: string;
  error: string;
  tasks: TaskItem[];
  onKind: (k: ModelKind) => void;
  onSource: (s: SourceId) => void;
  onPick: (entry: DatasetEntry) => void;
  onSubmit: () => void;
  onNew: () => void;
  onOpenTask: (t: TaskItem) => void;
  onCloseFocus: () => void;
}

export function UserAppView(props: UserAppViewProps) {
  const { state, tasks } = props;
  const focusId = state.focus?.taskId ?? null;

  return (
    <div className="up-shell">
      <aside className="up-side">
        <div className="up-brand">
          <div className="up-brand-name">{PLATFORM_NAME}</div>
        </div>

        <button type="button" className="btn primary up-new" onClick={props.onNew}>＋ 新建任务</button>

        <div className="up-side-title">历史任务</div>
        <div className="up-history">
          {tasks.length === 0 && <div className="up-empty">暂无任务记录</div>}
          {tasks.map((t) => {
            const status = String(t.status || '');
            return (
              <button
                key={t.id}
                type="button"
                className={`up-hist ${focusId === t.id ? 'on' : ''}`}
                title={t.id}
                onClick={() => props.onOpenTask(t)}
              >
                <span className="up-hist-top">
                  <b>{MODEL_LABEL[t.model] || t.model}</b>
                  <i className={`up-st s-${status}`}>{STATUS_LABEL[status] || status}</i>
                </span>
                <span className="up-hist-bottom">
                  <span>{t.source ? entityName(t.source) : '—'}</span>
                  <i className="mono">{fmtTime(t.start_time)}</i>
                </span>
              </button>
            );
          })}
        </div>
      </aside>

      <main className="up-main">
        <div className="up-scroll">
          <div className="up-col">
            <TaskForm
              kind={state.kind}
              source={state.source}
              index={state.index}
              file={state.file}
              busy={props.busy}
              note={props.note}
              error={props.error}
              onKind={props.onKind}
              onSource={props.onSource}
              onPick={props.onPick}
              onSubmit={props.onSubmit}
            />

            {state.focus && <RecordCard record={state.focus} onClose={props.onCloseFocus} />}

            {state.records.length === 0 && <div className="up-empty">本次会话暂无提交记录</div>}
            {state.records.map((r) => <RecordCard key={r.id} record={r} />)}
          </div>
        </div>
      </main>
    </div>
  );
}

/** 一条记录：提交卡同宽同列，标题是数据集文件名，状态在旁边，结论用 UserResult */
function RecordCard({ record, onClose }: { record: UserRecord; onClose?: () => void }) {
  const status = recordStatus(record);
  return (
    <article className={`up-card up-record ${record.history ? 'hist' : ''}`} data-task={record.taskId}>
      <header className="up-card-head">
        <b className="up-card-title">
          {record.history ? `${MODEL_LABEL[record.kind]}任务` : record.fileName}
        </b>
        <i className="up-card-note mono">{record.taskId}</i>
        {onClose && (
          <button type="button" className="mini up-card-x" onClick={onClose}>关闭</button>
        )}
      </header>

      <div className="up-meta">
        <span className="up-meta-item">
          <i>类型</i>
          <b>{MODEL_LABEL[record.kind]}</b>
        </span>
        <span className="up-meta-item">
          <i>任务位置</i>
          <b>{entityName(record.source)}</b>
        </span>
        <span className="up-meta-item">
          <i>状态</i>
          <b className={`up-st s-${status}`}>{STATUS_LABEL[status] || status}</b>
        </span>
      </div>

      <div className="up-result">
        <UserResult kind={record.kind} task={record.task} live={record.live} />
      </div>
    </article>
  );
}

/** 容器：读目录 → 选类型/上传选文件 → 提交 → 轮询记录 */
export default function UserApp() {
  const [state, setState] = useState<UserState>(initialState);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState('');
  const [error, setError] = useState('');
  const [tasks, setTasks] = useState<TaskItem[]>([]);
  const stateRef = useRef(state);
  stateRef.current = state;

  const loadTasks = useCallback(async () => {
    try {
      setTasks((await api.tasksRecent(HISTORY_LIMIT)) || []);
    } catch { /* 保留上一次列表 */ }
  }, []);

  useEffect(() => { loadTasks(); }, [loadTasks]);

  // 进入页面读一次目录；弹窗据此直接进入当前类型对应的文件夹
  useEffect(() => {
    let disposed = false;
    fetchIndex()
      .then((idx) => { if (!disposed) setState((s) => withIndex(s, idx)); })
      .catch((e: any) => { if (!disposed) setNote(String(e?.message || e)); });
    return () => { disposed = true; };
  }, []);

  // 轮询进行中的提交（每 1.5s），完成后拉取完整记录，并原地更新该条记录
  const pendingKey = useMemo(() => state.records.filter(isPending).map((r) => r.id).join(','),
    [state.records]);
  useEffect(() => {
    const ids = pendingKey ? pendingKey.split(',') : [];
    if (!ids.length) return undefined;
    let disposed = false;
    const iv = window.setInterval(async () => {
      for (const id of ids) {
        const rec = stateRef.current.records.find((r) => r.id === id);
        if (!rec) continue;
        try {
          const live = await api.taskResult(rec.taskId);
          if (disposed) return;
          setState((s) => patchRecord(s, id, { live }));
          if (isTerminalStatus(live.status)) {
            let detail: TaskItem | null = null;
            try { detail = await api.taskDetail(rec.taskId); } catch { /* 用轻量结果 */ }
            if (disposed) return;
            if (detail) setState((s) => patchRecord(s, id, { live, task: detail }));
            loadTasks();
          }
        } catch { /* 继续轮询 */ }
      }
    }, POLL_MS);
    return () => { disposed = true; window.clearInterval(iv); };
  }, [pendingKey, loadTasks]);

  /** 选中类型：已选文件随之失效（下次上传在弹窗里进入新类型的文件夹） */
  const onKind = (k: ModelKind) => {
    setError('');
    setNote('');
    setState((s) => selectKind(s, k));
  };

  const onSource = (s: SourceId) => setState((st) => selectSource(st, s));

  /** 弹窗里选中文件：文件内容已由 DatasetBrowser 读取，选中后 执行 才可用 */
  const onPick = (entry: DatasetEntry) => {
    setError('');
    setNote('');
    setState((s) => pickFile(s, entry));
  };

  /** 提交：按文件自带的类型派发，成功后在列首新增一条记录 */
  const onSubmit = async () => {
    const s = stateRef.current;
    const entry = s.file;
    if (!entry || entry.kind !== s.kind) return;
    setBusy(true);
    setError('');
    try {
      const sub = buildSubmission(entry, s.source);
      const taskId = await sendSubmission(sub);
      const rec: UserRecord = {
        id: nextId(),
        kind: entry.kind,
        fileName: entry.name,
        source: s.source,
        taskId,
        live: queuedResult(taskId),
        task: null,
        history: false,
      };
      setState((cur) => addRecord(cur, rec));
      loadTasks();
    } catch (e: any) {
      setError(e?.response?.data?.detail ?? e?.message ?? '提交失败');
    } finally {
      setBusy(false);
    }
  };

  /** 侧栏历史任务 → 列首聚焦该任务的结论，不动提交卡与本次会话记录 */
  const onOpenTask = (t: TaskItem) => {
    setState((s) => focusTask(s, t));
    (async () => {
      try {
        const live = await api.taskResult(t.id);
        setState((s) => patchFocus(s, t.id, { live }));
        if (isTerminalStatus(live.status)) {
          try {
            const detail = await api.taskDetail(t.id);
            setState((s) => patchFocus(s, t.id, { live, task: detail }));
          } catch { /* 保留历史列表里的轻量记录 */ }
        }
      } catch { /* 保留历史列表里的轻量记录 */ }
    })();
  };

  const onNew = () => {
    setError('');
    setNote('');
    setState((s) => newTask(s));
  };

  return (
    <UserAppView
      state={state}
      busy={busy}
      note={note}
      error={error}
      tasks={tasks}
      onKind={onKind}
      onSource={onSource}
      onPick={onPick}
      onSubmit={onSubmit}
      onNew={onNew}
      onOpenTask={onOpenTask}
      onCloseFocus={() => setState((s) => clearFocus(s))}
    />
  );
}

/** 测试钩子：纯状态迁移，便于在无 DOM 环境下验证交互逻辑 */
export const __userInternals = {
  folderFor, selectKind, selectSource, withIndex, pickFile, addRecord, patchRecord,
  focusTask, patchFocus, clearFocus, newTask, recordStatus, isPending, loadEntry,
  sendSubmission, queuedResult, buildSubmission,
};

export type { UserState, UserRecord };
