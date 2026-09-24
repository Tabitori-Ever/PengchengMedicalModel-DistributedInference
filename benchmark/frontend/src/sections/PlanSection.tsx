import { useMemo, useState } from 'react';
import type { Kind, PlanInfo, ReqMode, Source } from '../types';
import { CLINICS, HOSPITALS, KIND_ZH, MODE_ZH } from '../types';
import type { Draft, DraftKind } from '../planDraft';
import {
  KIND_PARAM_FIELDS, draftFromPlan, draftOverrides, draftProblems,
  draftTotalAttempts, draftTotalTasks, estimateMinutes, fmtMinutes,
} from '../planDraft';
import { fmtInt } from '../components/format';

type CardId = string;

export default function PlanSection({
  plans, plansErr, selectedId, draft, planRunId, busy, submitErr,
  onSelectPlan, onCustom, onChangeDraft, onResetDraft, onSubmit,
}: {
  plans: PlanInfo[];
  plansErr: string;
  selectedId: CardId;
  draft: Draft | null;
  planRunId: string;
  busy: boolean;
  submitErr: string;
  onSelectPlan: (plan: PlanInfo) => void;
  onCustom: () => void;
  onChangeDraft: (next: Draft) => void;
  onResetDraft: () => void;
  onSubmit: () => void;
}) {
  const selected = plans.find((p) => p.plan_id === draft?.basePlanId) || null;
  const problems = draft ? draftProblems(draft) : [];
  const totalTasks = draft ? draftTotalTasks(draft) : 0;
  const totalAttempts = draft ? draftTotalAttempts(draft) : 0;

  return (
    <>
      <div className="card">
        <div className="card-headrow">
          <div className="card-title">① 测试方案</div>
        </div>

        {plansErr ? <div className="errbox">/api/plans 不可用：{plansErr}</div> : null}
        {!plansErr && plans.length === 0 ? <div className="hint">正在读取…</div> : null}

        <div className="plan-cards">
          {plans.map((p) => (
            <button key={p.plan_id}
              className={`plan-card ${draft?.basePlanId === p.plan_id && !draft?.customized ? 'on' : ''}`}
              onClick={() => onSelectPlan(p)}>
              <div className="plan-card-top">
                <span className="plan-card-name">{p.name}</span>
                <span className="plan-card-ref">{p.outline_ref}</span>
              </div>
              <div className="plan-card-facts">
                <span><i>任务总数</i><b>{fmtInt(p.total_tasks)}</b></span>
                <span><i>设备</i><b>{p.device_count} 台</b></span>
                <span><i>每类/设备</i><b>{p.per_device_per_kind}</b></span>
                <span><i>产生窗口</i><b>{fmtMinutes(p.generate_window_min)}</b></span>
                <span><i>测试时长</i><b>{fmtMinutes(p.duration_min)}</b></span>
                <span><i>策略</i><b>{p.strategies.map((s) => MODE_ZH[s]).join(' → ')}</b></span>
              </div>
              {p.total_tasks_mismatch ? (
                <div className="plan-card-warn">任务总数 {fmtInt(p.total_tasks)}</div>
              ) : null}
            </button>
          ))}
          <button className={`plan-card custom ${draft?.customized ? 'on' : ''}`} onClick={onCustom}>
            <div className="plan-card-top">
              <span className="plan-card-name">自定义实验配置</span>
              <span className="plan-card-ref">从零开始</span>
            </div>
            <div className="plan-card-facts">
              <span><i>任务总数</i><b>—</b></span>
              <span><i>设备</i><b>1 台起</b></span>
              <span><i>窗口</i><b>—</b></span>
              <span><i>时长</i><b>—</b></span>
              <span><i>策略</i><b>任选</b></span>
            </div>
          </button>
        </div>
      </div>

      {draft ? (
        <div className="card">
          <div className="card-headrow">
            <div className="card-title">
              ② 实验配置
              {draft.customized ? <span className="tag-custom">自定义</span> : null}
            </div>
            <div className="btnrow">
              <button className="mini" onClick={onResetDraft} disabled={busy}>
                恢复方案默认
              </button>
            </div>
          </div>

          {selected ? <PlanOutline plan={selected} /> : null}

          {/* ---------------------------------------------- 总体配置 */}
          <div className="card-title mt">总体配置</div>
          <div className="cfg-grid">
            <div className="field">
              <label className="field-label">测试时长（分钟）</label>
              <input type="number" min={1} value={draft.duration_min}
                onChange={(e) => onChangeDraft({ ...draft, customized: true,
                  duration_min: Number(e.target.value) })} />
              <div className="hint">{fmtMinutes(draft.duration_min)}</div>
            </div>
            <div className="field">
              <label className="field-label">任务产生窗口（分钟）</label>
              <input type="number" min={0} step={0.5} value={draft.generate_window_min}
                onChange={(e) => onChangeDraft({ ...draft, customized: true,
                  generate_window_min: Number(e.target.value) })} />
              <div className="hint">{fmtMinutes(draft.generate_window_min)}</div>
            </div>
            <div className="field">
              <label className="field-label">失败重传额度（次）</label>
              <input type="number" min={0} max={10} value={draft.retries}
                onChange={(e) => onChangeDraft({ ...draft, customized: true,
                  retries: Number(e.target.value) })} />
              <div className="hint">首次之外</div>
            </div>
            <div className="field">
              <label className="field-label">调度策略</label>
              <div className="modes">
                {(['local', 'collaborative'] as ReqMode[]).map((m) => (
                  <button key={m}
                    className={`modebtn m-${m} ${draft.strategies.includes(m) ? 'on' : ''}`}
                    onClick={() => {
                      const has = draft.strategies.includes(m);
                      const next = has ? draft.strategies.filter((x) => x !== m)
                                       : [...draft.strategies, m];
                      onChangeDraft({ ...draft, customized: true, strategies: next });
                    }}>
                    <b>{MODE_ZH[m]}</b>
                    <i className="mono">{m === 'local' ? '未调度' : '协同'}</i>
                  </button>
                ))}
              </div>

            </div>
          </div>

          <div className="sumline">
            <span className="ss-item"><i>任务总数</i><b>{fmtInt(totalTasks)}</b></span>
            <span className="ss-item"><i>设备</i><b>{draft.devices.length} 台</b></span>
            <span className="ss-item"><i>策略遍数</i><b>{draft.strategies.length}</b></span>
            <span className="ss-item"><i>累计任务数</i><b>{fmtInt(totalAttempts)}</b></span>
            <span className="ss-item"><i>重传额度</i><b>{fmtInt(draft.retries)}</b></span>
            <span className="ss-item"><i>预计耗时</i><b>{fmtMinutes(estimateMinutes(draft))}</b></span>
          </div>

          {/* ---------------------------------------------- 发起设备 */}
          <div className="card-headrow mt">
            <div className="card-title">发起设备</div>
            <div className="btnrow">
              <button className="mini" disabled={busy}
                onClick={() => onChangeDraft({ ...draft, customized: true,
                  devices: [...draft.devices, {
                    id: String.fromCharCode(65 + draft.devices.length),
                    name: `设备${String.fromCharCode(65 + draft.devices.length)}`,
                    source: 'clinic-3',
                  }] })}>
                + 添加设备
              </button>
            </div>
          </div>
          <div className="tbl-scroll-x">
            <table className="dt small">
              <thead>
                <tr><th>设备</th><th>发起方</th><th>操作</th></tr>
              </thead>
              <tbody>
                {draft.devices.map((d, i) => (
                  <tr key={`${d.id}-${i}`}>
                    <td><b>{d.id}</b><span className="hint"> · {d.name}</span></td>
                    <td>
                      <select value={d.source}
                        onChange={(e) => {
                          const devices = draft.devices.map((x, j) =>
                            j === i ? { ...x, source: e.target.value as Source } : x);
                          onChangeDraft({ ...draft, customized: true, devices });
                        }}>
                        <optgroup label="医院（边缘）">
                          {HOSPITALS.map((s) => <option key={s} value={s}>{s}</option>)}
                        </optgroup>
                        <optgroup label="诊所（端侧）">
                          {CLINICS.map((s) => <option key={s} value={s}>{s}</option>)}
                        </optgroup>
                      </select>
                    </td>
                    <td>
                      <button className="mini" disabled={busy || draft.devices.length <= 1}
                        onClick={() => onChangeDraft({ ...draft, customized: true,
                          devices: draft.devices.filter((_, j) => j !== i) })}>
                        移除
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* ---------------------------------------------- 任务类型 */}
          <div className="card-title mt">任务类型与任务参数</div>
          <div className="tbl-scroll-x">
            <table className="dt small kind-cfg">
              <thead>
                <tr>
                  <th>任务类型</th>
                  <th>对应大纲任务</th>
                  <th>每设备任务数</th>
                  <th>任务参数</th>
                </tr>
              </thead>
              <tbody>
                {draft.kinds.map((k, i) => (
                  <tr key={k.kind}>
                    <td><b>{KIND_ZH[k.kind]}</b><div className="hint mono">{k.kind}</div></td>
                    <td className="wrapcell">
                      {selected?.kinds.find((x) => x.kind === k.kind)?.outline_name ?? '—'}
                    </td>
                    <td>
                      <input className="num-in" type="number" min={0} max={500}
                        value={k.per_device}
                        onChange={(e) => {
                          const kinds = draft.kinds.map((x, j) =>
                            j === i ? { ...x, per_device: Number(e.target.value) } : x);
                          onChangeDraft({ ...draft, customized: true, kinds });
                        }} />
                      <div className="hint">合计 {fmtInt(k.per_device * draft.devices.length)} 个</div>
                    </td>
                    <td>
                      <div className="param-grid">
                        {KIND_PARAM_FIELDS[k.kind].map((f) => (
                          <label key={f.key} className="param-field" title={f.hint}>
                            <span>{f.label}</span>
                            {f.kind === 'bool' ? (
                              <input type="checkbox"
                                checked={Boolean(k.params[f.key])}
                                onChange={(e) => {
                                  const kinds = draft.kinds.map((x, j) => j === i
                                    ? { ...x, params: { ...x.params, [f.key]: e.target.checked } }
                                    : x);
                                  onChangeDraft({ ...draft, customized: true, kinds });
                                }} />
                            ) : (
                              <input className="num-in" type="number"
                                min={f.min} max={f.max} step={f.step ?? 1}
                                value={Number(k.params[f.key] ?? 0)}
                                onChange={(e) => {
                                  const v = f.kind === 'int' ? Math.round(Number(e.target.value))
                                                             : Number(e.target.value);
                                  const kinds = draft.kinds.map((x, j) => j === i
                                    ? { ...x, params: { ...x.params, [f.key]: v } }
                                    : x);
                                  onChangeDraft({ ...draft, customized: true, kinds });
                                }} />
                            )}
                          </label>
                        ))}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* ---------------------------------------------- 判定标准 */}
          {selected?.attachments?.length ? (
            <>
              <div className="card-title mt">外部仪表配置</div>
              <div className="tbl-scroll-x">
                <table className="dt small">
                  <thead><tr><th>项目</th><th className="wrapcell">配置</th><th>验证方</th></tr></thead>
                  <tbody>
                    {selected.attachments.map((a) => (
                      <tr key={a.id}>
                        <td><b>{a.name}</b></td>
                        <td className="wrapcell">{a.detail}</td>
                        <td>{a.verified_by}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          ) : null}

          {submitErr ? <div className="errbox">下发失败：{submitErr}</div> : null}
          {problems.length > 0
            ? <div className="hint">待修正：{problems.join('；')}</div>
            : null}

          <div className="btnrow end">
            <button className="btn primary"
              disabled={busy || problems.length > 0 || !draft}
              onClick={onSubmit}>
              {busy ? '下发中…' : `下发方案运行（${fmtInt(totalTasks)} 任务 × ${draft.strategies.length} 遍）`}
            </button>
          </div>
          {planRunId ? (
            <div className="hint">本次方案运行：<code className="mono">{planRunId}</code></div>
          ) : null}
        </div>
      ) : null}
    </>
  );
}

/** 方案依据：测试对象/目的/内容 + 预置条件 + 判定标准 + 大纲完整操作步骤（可展开） */
function PlanOutline({ plan }: { plan: PlanInfo }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="outline-block">
      <div className="plan-basis">
        <div className="pb-row"><i>依据</i><b>{plan.outline_ref}</b></div>
      </div>

      <div className="outline-cols">
        <div className="outline-col">
          <div className="outline-title">预置条件</div>
          <ol className="steps-list">
            {plan.preconditions.map((x, i) => <li key={i}>{x}</li>)}
          </ol>
        </div>
        <div className="outline-col">
          <div className="outline-title">判定标准</div>
          <ol className="steps-list">
            {plan.criteria.map((c) => (
              <li key={c.id}>
                {c.label}
                <span className="crit-meta">{criteriaRule(c)}</span>
              </li>
            ))}
          </ol>
        </div>
      </div>

      <button className="mini" onClick={() => setOpen((v) => !v)}>
        {open ? '收起大纲完整描述' : '展开大纲完整描述'}
      </button>
      {open ? (
        <>
          <div className="outline-col mt">
            <div className="outline-title">测试对象 / 目的 / 内容（大纲原文）</div>
            <ol className="steps-list">
              <li>测试对象：{plan.test_object}</li>
              <li>测试目的：{plan.purpose}</li>
              <li>测试内容：{plan.objective}</li>
            </ol>
          </div>
          <div className="outline-col">
            <div className="outline-title">测试时长 / 任务产生</div>
            <ol className="steps-list">
              <li>测试时长：{fmtMinutes(plan.duration_min)}</li>
              <li>任务产生窗口：{fmtMinutes(plan.generate_window_min)}</li>
              <li>失败重传：{plan.retries ?? 0} 次（首次之外）</li>
            </ol>
          </div>
          <div className="outline-col">
            <div className="outline-title">操作步骤（大纲原文）</div>
            <ol className="steps-list">
              {plan.steps.map((x, i) => <li key={i}>{x}</li>)}
            </ol>
          </div>
          {plan.notes?.length ? (
            <div className="outline-col">
              <div className="outline-title">备注</div>
              <ol className="steps-list">
                {plan.notes.map((x, i) => <li key={i}>{x}</li>)}
              </ol>
            </div>
          ) : null}
        </>
      ) : null}
    </div>
  );
}

function criteriaRule(c: PlanInfo['criteria'][number]): string {
  if (c.type === 'gain_gt') return `> ${c.value}${c.unit}`;
  if (c.type === 'complete_gt') return `> ${c.value}${c.unit}`;
  if (c.type === 'complete_gte') return `≥ ${c.value}${c.unit}`;
  return `≤ ${c.value}${c.unit}`;
}

export type { DraftKind };
