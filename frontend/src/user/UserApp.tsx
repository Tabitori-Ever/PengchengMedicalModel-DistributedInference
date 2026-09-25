import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api } from '../api';
import { isTerminalStatus } from '../components/TaskResultView';
import type { ModelKind, TaskItem } from '../types';
import { MODEL_LABEL, STATUS_LABEL } from '../utils';
import { PLATFORM_NAME, entityName } from '../terms';
import type { DatasetEntry } from './dataset';
import { buildSubmission, fetchIndex } from './dataset';
import type { IdentityId } from './identity';
import { loadIdentity } from './identity';
import type { UserRecord, UserState } from './state';
import {
  addRecord, clearFocus, focusTask, focusTasks, isPending, loadEntry, newTask, nextId, toggleFocus,
  patchFocus, patchRecord, pickFile, queuedResult, recordStatus, removeFocus, selectKind,
  selectSource, sendSubmission, initialState, withIndex, folderFor,
} from './state';
import LoginPanel from './LoginPanel';
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
 * 用户调用平台（/app/user/）
 *
 * 登录门：进入主界面前先在居中的登录面板选身份（医疗中心 A/B、医院 1/2/3/4），
 * 选中结果写入 localStorage 并作为本次任务位置（source）。
 *
 * 主界：左侧品牌 + 身份 + 可折叠 / 多选的历史任务，主区是一条居中列（最大 880px）：
 * 提交卡在上（卡内标题行就是四个任务类型按钮，卡身只有上传与执行），
 * 聚焦的历史记录与本次会话的记录依次在下，全部同宽同列。
 */
export interface UserAppViewProps {
  state: UserState;
  busy: boolean;
  note: string;
  error: string;
  tasks: TaskItem[];
  /** 当前登录身份（即任务位置，提交时作为 source） */
  identity: IdentityId;
  /** 历史任务列表是否展开（折叠后只剩分区标题） */
  historyOpen: boolean;
  /** 已勾选的历史任务 id（多选） */
  onKind: (k: ModelKind) => void;
  onPick: (entry: DatasetEntry) => void;
  onSubmit: () => void;
  onNew: () => void;
  onCloseFocus: (taskId: string) => void;
  onSwitchIdentity: () => void;
  onToggleHistory: () => void;
  onRefreshHistory: () => void;
  onToggleTask: (t: TaskItem) => void;
  onClearFocus: () => void;
}

export function UserAppView(props: UserAppViewProps) {
  const { state, tasks } = props;
  const focusIds = state.focusList.map((r) => r.taskId);

  return (
    <div className="up-shell">
      <aside className="up-side">
        <div className="up-brand">
          <div className="up-brand-name">{PLATFORM_NAME}</div>
          <div className="up-brand-id">{entityName(props.identity)}<i className="mono">{props.identity}</i></div>
        </div>

        <button type="button" className="btn primary up-new" onClick={props.onNew}>＋ 新建任务</button>

        <div className="up-side-title">
          <span>历史任务</span>
          <span className="up-side-acts">
            <button type="button" className="up-side-act" onClick={props.onRefreshHistory}>刷新</button>
            <button
              type="button"
              className="up-side-act"
              aria-expanded={props.historyOpen}
              onClick={props.onToggleHistory}
            >
              {props.historyOpen ? '收起' : '展开'}
            </button>
          </span>
        </div>

        {props.historyOpen && (
          <>
            <div className="up-history">
              {tasks.length === 0 && <div className="up-empty">暂无任务记录</div>}
              {tasks.map((t) => {
                const status = String(t.status || '');
                const on = focusIds.includes(t.id);
                return (
                  <button
                    key={t.id}
                    type="button"
                    className={`up-hist ${on ? 'on' : ''}`}
                    title={t.id}
                    aria-pressed={on}
                    onClick={() => props.onToggleTask(t)}
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
          </>
        )}

        <button type="button" className="up-switch" onClick={props.onSwitchIdentity}>切换身份</button>
      </aside>

      <main className="up-main">
        <div className="up-scroll">
          <div className="up-col">
            <TaskForm
              kind={state.kind}
              index={state.index}
              file={state.file}
              busy={props.busy}
              note={props.note}
              error={props.error}
              onKind={props.onKind}
              onPick={props.onPick}
              onSubmit={props.onSubmit}
            />

            {state.focusList.map((r, i) => (
              <RecordCard
                key={r.id}
                record={r}
                onClose={() => props.onCloseFocus(r.taskId)}
                onClearAll={i === 0 && state.focusList.length > 0 ? props.onClearFocus : undefined}
              />
            ))}

            {state.records.length === 0 && <div className="up-empty">本次会话暂无提交记录</div>}
            {state.records.map((r) => <RecordCard key={r.id} record={r} />)}
          </div>
        </div>
      </main>
    </div>
  );
}

/** 一条记录：提交卡同宽同列，标题是数据集文件名，状态在旁边，结论用 UserResult */
function RecordCard({ record, onClose, onClearAll }: {
  record: UserRecord; onClose?: () => void; onClearAll?: () => void;
}) {
  const status = recordStatus(record);
  return (
    <article className={`up-card up-record ${record.history ? 'hist' : ''}`} data-task={record.taskId}>
      <header className="up-card-head">
        <b className="up-card-title">
          {record.history ? `${MODEL_LABEL[record.kind]}任务` : record.fileName}
        </b>
        <i className="up-card-note mono">{record.taskId}</i>
        {onClearAll && (
          <button type="button" className="up-card-clear" onClick={onClearAll}>清空</button>
        )}
        {onClose && (
          <button type="button" className="up-card-x" onClick={onClose} aria-label="移除这条">×</button>
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

/** 容器：登录选身份 → 读目录 → 选类型/上传选文件 → 提交 → 轮询记录 */
export default function UserApp() {
  const [identity, setIdentity] = useState<IdentityId | null>(() => loadIdentity());
  const [state, setState] = useState<UserState>(
    () => ({ ...initialState(), source: identity ?? initialState().source }),
  );
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState('');
  const [error, setError] = useState('');
  const [tasks, setTasks] = useState<TaskItem[]>([]);
  const [historyOpen, setHistoryOpen] = useState(true);
  const stateRef = useRef(state);
  stateRef.current = state;
  const identityRef = useRef(identity);
  identityRef.current = identity;

  /** 历史任务：只保留本登录身份（该医疗中心 / 医院）自己发起的任务 */
  const loadTasks = useCallback(async () => {
    try {
      const list = (await api.tasksRecent(HISTORY_LIMIT)) || [];
      const mine = list.filter((t) => String(t?.source || '') === String(identityRef.current || ''));
      setTasks(mine);
    } catch { /* 保留上一次列表 */ }
  }, []);

  // 登录后才读历史任务与数据集目录（登录面板不触发任何数据请求）
  useEffect(() => {
    if (!identity) return undefined;
    loadTasks();
    return undefined;
  }, [identity, loadTasks]);

  // 进入主界面后读一次目录；弹窗据此直接进入当前类型对应的文件夹
  useEffect(() => {
    if (!identity) return undefined;
    let disposed = false;
    fetchIndex()
      .then((idx) => { if (!disposed) setState((s) => withIndex(s, idx)); })
      .catch((e: any) => { if (!disposed) setNote(String(e?.message || e)); });
    return () => { disposed = true; };
  }, [identity]);

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

  /** 登录：保存身份并把它作为任务位置 */
  const onEnter = (id: IdentityId) => {
    setIdentity(id);
    setState((s) => selectSource(s, id));
  };

  /** 切换身份：清除登录态，回到登录面板 */
  const onSwitchIdentity = () => {
    setIdentity(null);
    setState((s) => clearFocus(s));
  };

  /** 选中类型：已选文件随之失效（下次上传在弹窗里进入新类型的文件夹） */
  const onKind = (k: ModelKind) => {
    setError('');
    setNote('');
    setState((s) => selectKind(s, k));
  };

  /** 弹窗里选中文件：文件内容已由 DatasetBrowser 读取，选中后 执行 才可用 */
  const onPick = (entry: DatasetEntry) => {
    setError('');
    setNote('');
    setState((s) => pickFile(s, entry));
  };

  /** 提交：按文件自带的类型派发，source 固定为登录身份，成功后在列首新增一条记录 */
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

  /** 点历史卡片：选中即加入（新的排最上），再点一次取消；多条可同时显示 */
  const onToggleTask = (t: TaskItem) => {
    const already = stateRef.current.focusList.some((r) => r.taskId === String(t.id));
    setState((s) => toggleFocus(s, t));
    if (!already) refreshFocus([t]);
  };

  /** 逐条拉取聚焦任务的实时结果与详情，原地更新主列里的记录 */
  const refreshFocus = (list: TaskItem[]) => {
    (async () => {
      for (const t of list) {
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
      }
    })();
  };

  const onNew = () => {
    setError('');
    setNote('');
    setState((s) => newTask(s));
  };

  if (!identity) return <LoginPanel onEnter={onEnter} />;

  return (
    <UserAppView
      state={state}
      busy={busy}
      note={note}
      error={error}
      tasks={tasks}
      identity={identity}
      historyOpen={historyOpen}
      onKind={onKind}
      onPick={onPick}
      onSubmit={onSubmit}
      onNew={onNew}
      onToggleTask={onToggleTask}
      onCloseFocus={(taskId) => setState((s) => removeFocus(s, taskId))}
      onSwitchIdentity={onSwitchIdentity}
      onToggleHistory={() => setHistoryOpen((v) => !v)}
      onRefreshHistory={() => { loadTasks(); }}
      onClearFocus={() => setState((s) => clearFocus(s))}
    />
  );
}

/** 测试钩子：纯状态迁移，便于在无 DOM 环境下验证交互逻辑 */
export const __userInternals = {
  folderFor, selectKind, selectSource,
  withIndex, pickFile, addRecord, patchRecord,
  focusTask, focusTasks, patchFocus, removeFocus, clearFocus, newTask,
  recordStatus, isPending, loadEntry,
  sendSubmission, queuedResult, buildSubmission,
};

export type { UserState, UserRecord };
