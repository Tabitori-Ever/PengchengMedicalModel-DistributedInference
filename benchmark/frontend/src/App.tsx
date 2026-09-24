import { useCallback, useEffect, useRef, useState } from 'react';
import { api, errText } from './api';
import type { PlanInfo, PlanRun } from './types';
import type { Draft } from './planDraft';
import { customDraft, draftFromPlan, draftOverrides } from './planDraft';
import PlanSection from './sections/PlanSection';
import PlanRunSection from './sections/PlanRunSection';
import OverviewSection from './sections/OverviewSection';
import StatsSection from './sections/StatsSection';
import CompareSection from './sections/CompareSection';
import { usePolling } from './hooks';

/**
 * 单页版测试平台：不再有侧边栏，方案 / 配置 / 进度 / 总览 / 统计 / 对比
 * 自上而下依次排布在同一页里。
 */
export default function App() {
  const [plans, setPlans] = useState<PlanInfo[]>([]);
  const [plansErr, setPlansErr] = useState('');
  const [draft, setDraft] = useState<Draft | null>(null);
  const [planRunId, setPlanRunId] = useState('');
  const [planRun, setPlanRun] = useState<PlanRun | null>(null);
  const [busy, setBusy] = useState(false);
  const [submitErr, setSubmitErr] = useState('');
  const [note, setNote] = useState('');
  const bottomRef = useRef<HTMLDivElement | null>(null);

  /* 方案目录 */
  useEffect(() => {
    api.plans()
      .then((list) => {
        setPlans(list);
        setPlansErr('');
        setDraft((cur) => cur ?? (list[0] ? draftFromPlan(list[0]) : null));
      })
      .catch((e) => setPlansErr(errText(e)));
  }, []);

  /* 打开页面时把最近一次已完成的方案运行直接显示出来，
     否则页面永远是空的，用户得先自己跑一次才看得到结果区。 */
  useEffect(() => {
    let alive = true;
    api.listPlanRuns(10)
      .then(async (runs) => {
        const last = runs.find((r) => r.status === 'completed' || r.status === 'failed')
          || runs[0];
        if (!last || !alive) return;
        const detail = await api.planRun(last.plan_run_id);
        if (!alive) return;
        setPlanRunId(detail.plan_run_id);
        setPlanRun(detail);
        setNote(detail.plan_run_id);
      })
      .catch(() => { /* 没有历史运行时静默 */ });
    return () => { alive = false; };
  }, []);

  /* 轮询方案运行 */
  const live = Boolean(planRunId) && (planRun === null || planRun.live);
  usePolling(async () => {
    if (!planRunId) return;
    try {
      setPlanRun(await api.planRun(planRunId));
    } catch {
      /* 静默，下一轮再试 */
    }
  }, 2000, live);

  const selectPlan = useCallback((plan: PlanInfo) => {
    setDraft(draftFromPlan(plan));
    setSubmitErr('');
  }, []);

  const startCustom = useCallback(() => {
    setDraft((cur) => customDraft(plans.find((p) => p.plan_id === cur?.basePlanId) ?? plans[0] ?? null));
    setSubmitErr('');
  }, [plans]);

  const resetDraft = useCallback(() => {
    const base = plans.find((p) => p.plan_id === draft?.basePlanId);
    if (base) {
      setDraft(draftFromPlan(base));
      setSubmitErr('');
    }
  }, [plans, draft?.basePlanId]);

  const submit = useCallback(async () => {
    if (!draft) return;
    setBusy(true);
    setSubmitErr('');
    setNote('');
    setPlanRun(null);
    setPlanRunId('');
    try {
      const created = await api.createPlanRun({
        plan_id: draft.basePlanId,
        overrides: draftOverrides(draft),
      });
      setPlanRunId(created.plan_run_id);
      setPlanRun(created);
      setNote(created.plan_run_id);
      // 滚到结果区，便于直接看到进度与结果
      window.setTimeout(() => bottomRef.current?.scrollIntoView(
        { behavior: 'smooth', block: 'start' }), 120);
    } catch (e) {
      setSubmitErr(errText(e));
    } finally {
      setBusy(false);
    }
  }, [draft]);

  const running = Boolean(planRunId) && (planRun === null || planRun.live);
  const here = typeof window !== 'undefined' ? window.location.host : '';

  return (
    <div className="page">
      <header className="top-head">
        <div className="th-main">
          <h1>AI诊疗云边端协同应用 · 测试平台</h1>
        </div>
        <div className="th-meta">
          <span className={`live-dot ${running ? 'on' : ''}`} />
          <span>{running ? '方案运行中' : '空闲'}</span>
          {here ? <span className="mono">{here}</span> : null}
        </div>
      </header>

      {note ? <div className="msg ok mono">{note}</div> : null}

      <PlanSection
        plans={plans}
        plansErr={plansErr}
        selectedId={draft?.basePlanId ?? ''}
        draft={draft}
        planRunId={planRunId}
        busy={busy}
        submitErr={submitErr}
        onSelectPlan={selectPlan}
        onCustom={startCustom}
        onChangeDraft={setDraft}
        onResetDraft={resetDraft}
        onSubmit={() => void submit()}
      />

      <div ref={bottomRef} />

      <PlanRunSection run={planRun} />
      <OverviewSection run={planRun} />
      <StatsSection run={planRun} />
      <CompareSection run={planRun} />


    </div>
  );
}
