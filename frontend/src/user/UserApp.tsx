import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api } from '../api';
import { isTerminalStatus } from '../components/TaskResultView';
import type { ModelKind, TaskItem, TaskResult } from '../types';
import { MODEL_LABEL, STATUS_LABEL } from '../utils';
import { PLATFORM_NAME, entityName } from '../terms';
import type { DatasetEntry } from './dataset';
import { buildSubmission } from './dataset';
import TaskForm, { Draft, draftFor, draftSummary } from './TaskForm';
import UserResult from './UserResult';

const POLL_MS = 1500;
const HISTORY_LIMIT = 30;

interface UserMsg {
  id: string;
  role: 'user';
  /** 提交后由所选数据集文件决定；待提交草稿尚未选择文件时为 null */
  kind: ModelKind | null;
  draft: Draft;
  taskId?: string;
}

interface BotMsg {
  id: string;
  role: 'assistant';
  kind: ModelKind;
  taskId: string;
  live: TaskResult | null;
  task: TaskItem | null;
}

type Msg = UserMsg | BotMsg;

let seq = 0;
const nextId = () => `m${(seq += 1)}`;

interface ChatState {
  messages: Msg[];
  pendingId: string | null;
  draft: Draft | null;
}

/** 平台打开即呈现一条待提交草稿（表单渲染在该气泡内） */
function initialChat(): { id: string; draft: Draft } {
  return { id: `u${nextId()}`, draft: draftFor() };
}
const FIRST = initialChat();

/** 在会话末尾追加一条待提交草稿消息，并把它标记为 pending */
function composeTransition(state: ChatState, newId: string, source?: string): ChatState {
  const draft = draftFor(source);
  return {
    messages: [...state.messages, { id: newId, role: 'user', kind: null, draft }],
    pendingId: newId,
    draft,
  };
}

/**
 * 提交成功：把这条待提交消息补上 taskId 与任务类型（不新增 user 消息），
 * 追加助手消息，并在末尾再开一条新的待提交草稿，便于连续发起。
 */
function submitTransition(
  state: ChatState, taskId: string, botId: string, composerId: string, kind: ModelKind, draft: Draft,
): ChatState {
  const pid = state.pendingId;
  const messages: Msg[] = state.messages.map((m) => (m.id === pid && m.role === 'user'
    ? { ...m, kind, draft, taskId } : m));
  messages.push({ id: botId, role: 'assistant', kind, taskId, live: null, task: null });
  const next = draftFor(draft.source);
  messages.push({ id: composerId, role: 'user', kind: null, draft: next });
  return { messages, pendingId: composerId, draft: next };
}

function fmtTime(iso?: string): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString('zh-CN', {
    hour12: false, month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
  });
}

/**
 * 用户调用平台（/app/user/）：左侧历史任务，右侧对话流。
 * 用户只做两件事：选发起方、上传数据集文件；任务类型与参数全部来自该文件。
 */
export default function UserApp() {
  const [messages, setMessages] = useState<Msg[]>([
    { id: FIRST.id, role: 'user', kind: null, draft: FIRST.draft },
  ]);
  const [pendingId, setPendingId] = useState<string | null>(FIRST.id);
  const [draft, setDraft] = useState<Draft | null>(FIRST.draft);
  const [busy, setBusy] = useState(false);
  const [formErr, setFormErr] = useState('');
  const [tasks, setTasks] = useState<TaskItem[]>([]);
  const [openId, setOpenId] = useState<string | null>(null);
  const streamRef = useRef<HTMLDivElement | null>(null);

  const loadTasks = useCallback(async () => {
    try {
      const list = await api.listTasks();
      setTasks((list || []).slice(0, HISTORY_LIMIT));
    } catch { /* 保留上一次列表 */ }
  }, []);

  useEffect(() => { loadTasks(); }, [loadTasks]);

  // 轮询进行中的任务（每 1.5s），完成后拉取完整记录
  useEffect(() => {
    const active = messages.filter((m): m is BotMsg => m.role === 'assistant'
      && !isTerminalStatus(m.live?.status || m.task?.status));
    if (!active.length) return undefined;
    let disposed = false;
    const iv = window.setInterval(async () => {
      for (const m of active) {
        try {
          const live = await api.taskResult(m.taskId);
          if (disposed) return;
          setMessages((prev) => prev.map((x) => (x.id === m.id && x.role === 'assistant' ? { ...x, live } : x)));
          if (isTerminalStatus(live.status)) {
            let detail: TaskItem | null = null;
            try { detail = await api.taskDetail(m.taskId); } catch { /* 用轻量结果 */ }
            if (disposed) return;
            setMessages((prev) => prev.map((x) => (x.id === m.id && x.role === 'assistant'
              ? { ...x, live, task: detail ?? x.task } : x)));
            loadTasks();
          }
        } catch { /* 继续轮询 */ }
      }
    }, POLL_MS);
    return () => { disposed = true; window.clearInterval(iv); };
  }, [messages, loadTasks]);

  useEffect(() => {
    const el = streamRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages.length, pendingId]);

  /** 提交：按所选文件自带的类型派发到对应接口 */
  const submit = async () => {
    if (!draft?.file || !pendingId) return;
    const d = draft;
    const file: DatasetEntry = draft.file;
    const pid = pendingId;
    setBusy(true);
    setFormErr('');
    try {
      const sub = buildSubmission(file, d.source);
      let resp: { task_id: string };
      if (sub.kind === 'diagnosis') {
        resp = await api.submitDiagnosis(sub.body);
      } else if (sub.kind === 'compute') {
        resp = await api.submitCompute(sub.body);
      } else if (sub.kind === 'sync') {
        resp = await api.submitSync(sub.body);
      } else {
        resp = await api.submitRoutine(sub.body);
      }
      const id = nextId();
      const next = submitTransition(
        { messages, pendingId: pid, draft: d }, resp.task_id, `a${id}`, `u${id}`, file.kind, d,
      );
      setMessages(next.messages);
      setPendingId(next.pendingId);
      setDraft(next.draft);
      loadTasks();
    } catch (e: any) {
      setFormErr(e?.response?.data?.detail ?? e?.message ?? '提交失败');
    } finally {
      setBusy(false);
    }
  };

  /** 历史任务 → 载入对话与结果，并保持底部有一条待提交草稿（草稿永远是最后一条） */
  const openTask = (t: TaskItem) => {
    const kind = (t.model as ModelKind) || 'diagnosis';
    const id = nextId();
    const cid = `u${nextId()}`;
    const nd = draftFor();
    setOpenId(t.id);
    const hist: Msg[] = [
      {
        id: `u${id}`,
        role: 'user',
        kind,
        draft: draftFor(String(t.source || '')),
        taskId: t.id,
      },
      { id: `a${id}`, role: 'assistant', kind, taskId: t.id, live: null, task: t },
      { id: cid, role: 'user', kind: null, draft: nd },
    ];
    setMessages((prev) => [...prev.filter((m) => m.id !== pendingId), ...hist]);
    setPendingId(cid);
    setDraft(nd);
  };

  const newTask = () => {
    const id = `u${nextId()}`;
    const nd = draftFor();
    setMessages([{ id, role: 'user', kind: null, draft: nd }]);
    setPendingId(id);
    setDraft(nd);
    setFormErr('');
    setOpenId(null);
  };

  const history = useMemo(() => tasks, [tasks]);

  return (
    <div className="u-shell">
      <aside className="u-side">
        <div className="u-brand">
          <div className="u-brand-name">{PLATFORM_NAME}</div>
        </div>

        <button className="btn primary" onClick={newTask}>＋ 新建任务</button>

        <div className="u-side-title">历史任务</div>
        <div className="u-history">
          {history.length === 0 && <div className="u-side-empty">暂无任务记录</div>}
          {history.map((t) => (
            <button
              key={t.id}
              type="button"
              className={`u-hist ${openId === t.id ? 'on' : ''}`}
              onClick={() => openTask(t)}
              title={`${t.id} · ${t.source || '—'}`}
            >
              <span className="u-hist-top">
                <b>{MODEL_LABEL[t.model] || t.model}</b>
                <i className={`u-hist-st s-${String(t.status || '')}`}>
                  {STATUS_LABEL[String(t.status || '')] || t.status}
                </i>
              </span>
              <span className="u-hist-bottom">
                <span>{t.source ? entityName(t.source) : '—'}</span>
                <i className="mono">{fmtTime(t.start_time)}</i>
              </span>
            </button>
          ))}
        </div>
      </aside>

      <main className="u-main">
        <header className="u-head">
          <span className="u-head-title">任务对话</span>
        </header>

        <div className="u-stream" ref={streamRef}>
          {messages.map((m) => (m.role === 'user' ? (
            <div className="u-row u-row-user" key={m.id}>
              <div className="u-bubble u-bubble-user">
                <div className="u-bubble-head">
                  <b>{m.kind ? `${MODEL_LABEL[m.kind]}任务` : '新建任务'}</b>
                  {m.taskId && <i className="mono">{m.taskId}</i>}
                </div>
                {m.id === pendingId && draft ? (
                  <TaskForm
                    draft={draft}
                    onChange={setDraft}
                    busy={busy}
                    error={formErr}
                    onSubmit={submit}
                  />
                ) : (
                  <div className="u-bubble-text">{draftSummary(m.draft)}</div>
                )}
              </div>
            </div>
          ) : (
            <div className="u-row u-row-bot" key={m.id}>
              <div className="u-bubble u-bubble-bot">
                <div className="u-bubble-head">
                  <b>{`${MODEL_LABEL[m.kind]}任务`}</b>
                </div>
                <UserResult kind={m.kind} task={m.task} live={m.live} />
              </div>
            </div>
          )))}
        </div>
      </main>
    </div>
  );
}

/** 测试钩子：纯状态迁移，便于在无 DOM 环境下验证交互逻辑 */
export const __userInternals = { composeTransition, submitTransition };
