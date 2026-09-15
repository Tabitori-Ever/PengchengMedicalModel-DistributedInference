import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api, normalizeTarget } from '../api';
import TaskResultView, { isTerminalStatus } from '../components/TaskResultView';
import type { ModelKind, Patient, TaskItem, TaskResult } from '../types';
import { MODEL_LABEL, MODELS, STATUS_LABEL } from '../utils';
import { KIND_HINT, PLATFORM_NAME, PLATFORM_TAGLINE, entityName } from '../terms';
import TaskForm, { Draft, draftFor, draftSummary } from './TaskForm';

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
  error?: string;
}

type Msg = UserMsg | BotMsg;

const STAGE_TEXT: Record<string, string> = {
  scheduler: '调度排队',
  worker: '医疗中心推理',
  server: '数据中心融合',
  compute: '分区计算',
  sync: '拉取 / 同步',
  routine: 'Job 执行',
  finished: '已完成',
  completed: '已完成',
  failed: '失败',
};

let seq = 0;
const nextId = () => `m${(seq += 1)}`;

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
 * 不展示集群负载 / 架构 / 编排等管理信息。
 */
export default function UserApp() {
  const [messages, setMessages] = useState<Msg[]>([]);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [busy, setBusy] = useState(false);
  const [formErr, setFormErr] = useState('');
  const [tasks, setTasks] = useState<TaskItem[]>([]);
  const [patients, setPatients] = useState<Patient[]>([]);
  const [openId, setOpenId] = useState<string | null>(null);
  const streamRef = useRef<HTMLDivElement | null>(null);

  // 历史任务 + 患者数据集
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
          setMessages((prev) => prev.map((x) => (x.id === m.id && x.role === 'assistant'
            ? { ...x, live } : x)));
          if (isTerminalStatus(live.status)) {
            let detail: TaskItem | null = null;
            try { detail = await api.taskDetail(m.taskId); } catch { /* 使用轻量结果 */ }
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
  }, [messages.length, openId]);

  const pickKind = (kind: ModelKind) => {
    setDraft(draftFor(kind));
    setFormErr('');
    setOpenId(null);
  };

  const submit = async () => {
    if (!draft) return;
    setBusy(true);
    setFormErr('');
    const d = draft;
    try {
      let resp: { task_id: string };
      if (d.kind === 'diagnosis') {
        resp = await api.submitDiagnosis({
          source: d.source,
          patient_id: d.patientId || undefined,
          target_hospital: d.source.startsWith('clinic') ? normalizeTarget(d.target) : undefined,
        });
      } else if (d.kind === 'compute') {
        resp = await api.submitCompute({
          source: d.source,
          instruments: d.instruments,
          rows: d.rows,
          intensity: d.intensity,
          partition_count: d.partitions,
        });
      } else if (d.kind === 'sync') {
        resp = await api.submitSync({
          source: d.source,
          bandwidth_mbps: d.bandwidth,
          concurrency: d.concurrency,
          chunk_kb: d.chunkKb,
        });
      } else {
        resp = await api.submitRoutine({
          source: d.source, jobs: d.jobs, rows: d.rows, intensity: d.intensity,
        });
      }
      const id = nextId();
      setMessages((prev) => [
        ...prev,
        { id: `u${id}`, role: 'user', kind: d.kind, draft: d, taskId: resp.task_id },
        { id: `a${id}`, role: 'assistant', kind: d.kind, taskId: resp.task_id, live: null, task: null },
      ]);
      setDraft(null);
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
    setDraft(null);
    setMessages((prev) => [
      ...prev,
      {
        id: `u${id}`,
        role: 'user',
        kind,
        draft: {
          ...draftFor(kind),
          source: (t.source as any) || draftFor(kind).source,
          patientId: '(历史记录)',
        },
        taskId: t.id,
      },
      { id: `a${id}`, role: 'assistant', kind, taskId: t.id, live: null, task: t },
    ]);
  };

  const newTask = () => {
    setMessages([]);
    setDraft(null);
    setFormErr('');
    setOpenId(null);
  };

  const grouped = useMemo(() => messages, [messages]);

  return (
    <div className="u-shell">
      <aside className="u-side">
        <div className="u-brand">
          <div className="u-brand-name">{PLATFORM_NAME}</div>
          <div className="u-brand-sub">{PLATFORM_TAGLINE}</div>
        </div>

        <button className="btn primary" onClick={newTask}>＋ 新建任务</button>

        <div className="u-side-title">历史任务</div>
        <div className="u-history">
          {tasks.length === 0 && <div className="u-side-empty">暂无任务记录</div>}
          {tasks.map((t) => (
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

        <div className="u-side-foot">
          <a className="link" href="../admin/">在管理平台查看 →</a>
          <a className="link" href="../">返回首页</a>
        </div>
      </aside>

      <main className="u-main">
        <header className="u-head">
          <span className="u-head-title">任务对话</span>
          <span className="u-head-sub">选择任务类型 → 填写参数 → 在本页直接查看执行过程与结果</span>
        </header>

        <div className="u-stream" ref={streamRef}>
          {grouped.length === 0 && (
            <div className="u-welcome">
              <h2>要执行什么任务？</h2>
              <p className="muted">四类任务均在本页发起并实时反馈，无需跳转。</p>
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

          {grouped.map((m) => (m.role === 'user' ? (
            <div className="u-row u-row-user" key={m.id}>
              <div className="u-bubble u-bubble-user">
                <div className="u-bubble-head">
                  <b>{`${MODEL_LABEL[m.kind]}任务`}</b>
                  {m.taskId && <i className="mono">{m.taskId}</i>}
                </div>
                {!m.taskId && draft && (
                  <TaskForm
                    draft={draft}
                    onChange={setDraft}
                    patients={patients}
                    busy={busy}
                    error={formErr}
                    onSubmit={submit}
                  />
                )}
                {m.taskId && <div className="u-bubble-text">{draftSummary(m.draft)}</div>}
              </div>
            </div>
          ) : (
            <div className="u-row u-row-bot" key={m.id}>
              <div className="u-bubble u-bubble-bot">
                <div className="u-bubble-head">
                  <b>{`${MODEL_LABEL[m.kind]}执行情况`}</b>
                  <i className="mono">{m.taskId}</i>
                </div>
                {!isTerminalStatus(m.live?.status || m.task?.status) && (
                  <div className="u-live">
                    <div className="progressbar">
                      <div
                        className="progressfill"
                        style={{ width: `${m.live?.progress ?? m.task?.progress ?? 8}%` }}
                      />
                    </div>
                    <div className="u-live-line">
                      <span>阶段：{STAGE_TEXT[String(m.live?.stage || m.task?.stage || 'scheduler')] || '排队'}</span>
                      <span>执行者：{m.live?.node || m.task?.node || '待分配'}</span>
                      <span className="mono">{m.live?.progress ?? m.task?.progress ?? 0}%</span>
                    </div>
                  </div>
                )}
                <TaskResultView task={m.task} live={m.live} />
                {isTerminalStatus(m.live?.status || m.task?.status) && (
                  <div className="u-bubble-foot">
                    <a className="link" href="../admin/#/visual">在管理平台查看 →</a>
                  </div>
                )}
              </div>
            </div>
          )))}
        </div>

        {draft && (
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
