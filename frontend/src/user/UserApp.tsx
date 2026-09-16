import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api, normalizeTarget } from '../api';
import { isTerminalStatus } from '../components/TaskResultView';
import type { ModelKind, Patient, TaskItem, TaskResult } from '../types';
import { MODEL_LABEL, MODELS, STATUS_LABEL } from '../utils';
import { KIND_HINT, PLATFORM_NAME, entityName } from '../terms';
import TaskForm, { Draft, draftFor, draftSummary } from './TaskForm';
import UserResult from './UserResult';

const POLL_MS = 1500;
const HISTORY_LIMIT = 30;

interface UserMsg {
  id: string;
  role: 'user';
  kind: ModelKind;
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

/**
 * 选择任务类型：已有待提交消息则就地更新其类型与草稿；否则追加一条
 * “待提交草稿消息”（无 taskId）并把它标记为 pending —— 表单即渲染在该气泡内。
 */
function pickTransition(state: ChatState, kind: ModelKind, draft: Draft, newId: string): ChatState {
  if (state.pendingId) {
    return {
      messages: state.messages.map((m) => (m.id === state.pendingId && m.role === 'user'
        ? { ...m, kind, draft } : m)),
      pendingId: state.pendingId,
      draft,
    };
  }
  return {
    messages: [...state.messages, { id: newId, role: 'user', kind, draft }],
    pendingId: newId,
    draft,
  };
}

/**
 * 提交成功：把这条待提交消息补上 taskId（不新增 user 消息），
 * 清空 pending，并追加一条助手消息。
 */
function submitTransition(state: ChatState, taskId: string, botId: string, kind: ModelKind, draft: Draft): ChatState {
  const pid = state.pendingId;
  const messages: Msg[] = state.messages.map((m) => (m.id === pid && m.role === 'user'
    ? { ...m, kind, draft, taskId } : m));
  messages.push({ id: botId, role: 'assistant', kind, taskId, live: null, task: null });
  return { messages, pendingId: null, draft: null };
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
 * 只呈现用户关心的内容：任务类型、一句话用途、必要参数、结论。
 */
export default function UserApp() {
  const [messages, setMessages] = useState<Msg[]>([]);
  const [pendingId, setPendingId] = useState<string | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [busy, setBusy] = useState(false);
  const [formErr, setFormErr] = useState('');
  const [tasks, setTasks] = useState<TaskItem[]>([]);
  const [patients, setPatients] = useState<Patient[]>([]);
  const [openId, setOpenId] = useState<string | null>(null);
  const streamRef = useRef<HTMLDivElement | null>(null);

  const loadTasks = useCallback(async () => {
    try {
      const list = await api.listTasks();
      setTasks((list || []).slice(0, HISTORY_LIMIT));
    } catch { /* 保留上一次列表 */ }
  }, []);

  useEffect(() => { loadTasks(); }, [loadTasks]);
  useEffect(() => {
    api.patients().then(setPatients).catch(() => setPatients([]));
  }, []);

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

  /**
   * 选择任务类型：已有待提交消息则就地更新，否则追加一条“待提交草稿消息”，
   * 表单即渲染在该气泡内（修复此前“点了没反应”的问题）。
   */
  const pickKind = (kind: ModelKind) => {
    const next = draftFor(kind);
    setFormErr('');
    setOpenId(null);
    const st = pickTransition({ messages, pendingId, draft }, kind, next, `u${nextId()}`);
    setMessages(st.messages);
    setPendingId(st.pendingId);
    setDraft(st.draft);
  };

  const submit = async () => {
    if (!draft || !pendingId) return;
    setBusy(true);
    setFormErr('');
    const d = draft;
    const pid = pendingId;
    try {
      let resp: { task_id: string };
      if (d.kind === 'diagnosis') {
        resp = await api.submitDiagnosis({
          source: d.source as any,
          patient_id: d.patientId || undefined,
          target_hospital: normalizeTarget(d.target),
        });
      } else if (d.kind === 'compute') {
        resp = await api.submitCompute({
          source: d.source as any,
          instruments: d.instruments,
          rows: d.rows,
          intensity: d.intensity,
          partition_count: d.partitions,
        });
      } else if (d.kind === 'sync') {
        resp = await api.submitSync({
          source: d.source as any,
          bandwidth_mbps: d.bandwidth,
          concurrency: d.concurrency,
          chunk_kb: d.chunkKb,
        });
      } else {
        resp = await api.submitRoutine({
          source: d.source as any, jobs: d.jobs, rows: d.rows, intensity: d.intensity,
        });
      }
      const st = submitTransition({ messages, pendingId: pid, draft: d }, resp.task_id, `a${nextId()}`, d.kind, d);
      setMessages(st.messages);
      setPendingId(st.pendingId);
      setDraft(st.draft);
      loadTasks();
    } catch (e: any) {
      setFormErr(e?.response?.data?.detail ?? e?.message ?? '提交失败');
    } finally {
      setBusy(false);
    }
  };

  /** 历史任务 → 载入对话与结果 */
  const openTask = (t: TaskItem) => {
    const kind = (t.model as ModelKind) || 'diagnosis';
    const id = nextId();
    setOpenId(t.id);
    setPendingId(null);
    setDraft(null);
    setMessages((prev) => [
      ...prev,
      {
        id: `u${id}`,
        role: 'user',
        kind,
        draft: { ...draftFor(kind), source: String(t.source || ''), patientId: '' },
        taskId: t.id,
      },
      { id: `a${id}`, role: 'assistant', kind, taskId: t.id, live: null, task: t },
    ]);
  };

  const newTask = () => {
    setMessages([]);
    setPendingId(null);
    setDraft(null);
    setFormErr('');
    setOpenId(null);
  };

  const showWelcome = !pendingId && messages.length === 0;
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
          {showWelcome && (
            <div className="u-welcome">
              <h2>要执行什么任务？</h2>
              <div className="u-cards">
                {MODELS.map((m) => (
                  <button key={m} type="button" className="u-card" onClick={() => pickKind(m)}>
                    <b className="u-card-title">{MODEL_LABEL[m]}</b>
                    <span className="u-card-desc">{KIND_HINT[m]}</span>
                  </button>
                ))}
              </div>
            </div>
          )}

          {messages.map((m) => (m.role === 'user' ? (
            <div className="u-row u-row-user" key={m.id}>
              <div className="u-bubble u-bubble-user">
                <div className="u-bubble-head">
                  <b>{`${MODEL_LABEL[m.kind]}任务`}</b>
                  {m.taskId && <i className="mono">{m.taskId}</i>}
                </div>
                {m.id === pendingId && draft ? (
                  <TaskForm
                    draft={draft}
                    onChange={setDraft}
                    patients={patients}
                    busy={busy}
                    error={formErr}
                    onSubmit={submit}
                  />
                ) : (
                  <div className="u-bubble-text">{draftSummary(m.taskId ? m.draft : m.draft)}</div>
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

        {pendingId && draft && (
          <div className="u-quick">
            <span className="muted xs">切换任务类型：</span>
            {MODELS.map((m) => (
              <button
                key={m}
                type="button"
                className={`mini ${draft.kind === m ? 'on' : ''}`}
                onClick={() => pickKind(m)}
              >
                {MODEL_LABEL[m]}
              </button>
            ))}
          </div>
        )}
      </main>
    </div>
  );
}

/** 测试钩子：纯状态迁移，便于在无 DOM 环境下验证交互逻辑 */
export const __userInternals = { pickTransition, submitTransition };
