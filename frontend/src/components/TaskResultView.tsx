import { Fragment } from 'react';
import { fmtMemBytes, fmtMs, MODEL_LABEL } from '../utils';
import type { TaskItem, TaskResult } from '../types';

interface Props {
  task: TaskItem | null;
  live?: TaskResult | null;
}

export function isTerminalStatus(s?: string): boolean {
  return s === 'finished' || s === 'completed' || s === 'failed';
}

const TERMINAL = ['finished', 'completed', 'failed'] as const;

export default function TaskResultView({ task, live }: Props) {
  const meta = task ?? null;
  const status: string = live?.status || meta?.status || 'unknown';
  const result: any = live?.result || meta?.result;
  const model: string = meta?.model || (result?.initiator?.entity ? inferKind(result) : '') || '';
  const error = live?.error || meta?.error;
  const progress = live?.progress ?? meta?.progress;

  if (status === 'not_found') {
    return <div className="empty">任务不存在或已删除</div>;
  }
  if (!TERMINAL.includes(status as any)) {
    return (
      <div className="result-running">
        <div className="progressbar">
          <div className="progressfill" style={{ width: `${progress ?? 10}%` }} />
        </div>
        <div className="muted">
          阶段：{live?.stage || meta?.stage || '排队'} · 位置：{live?.node || meta?.node || '—'}
          {progress != null ? ` · ${progress}%` : ''}
        </div>
      </div>
    );
  }
  if (status === 'failed' || error) {
    return <div className="errbox">任务失败：{error || '未知错误'}</div>;
  }
  if (!result) {
    return <div className="empty">暂无结果数据（{MODEL_LABEL[model] || model} 任务未返回负载）</div>;
  }

  const kind = model || inferKind(result) || '';
  return (
    <div className="result-wrap">
      <HeaderBlock kind={kind} result={result} />
      <div className="result-kind">
        {kind === 'diagnosis' && <DiagnosisBody result={result} />}
        {kind === 'compute' && <ComputeBody result={result} />}
        {kind === 'sync' && <SyncBody result={result} />}
        {kind === 'routine' && <RoutineBody result={result} />}
        {!kind && <GenericBody result={result} />}
      </div>
      <StagesBlock result={result} />
    </div>
  );
}

/** Some payloads arrive without a task.model (test page racing) — infer from shape. */
function inferKind(r: any): string {
  if (r?.result_detail) {
    if (r.result_detail.predictions || r.result_detail.bpCR_probability != null) return 'diagnosis';
    if (r.result_detail.partitions_ok != null || r.partitions) return 'compute';
    if (r.result_detail.pulled != null || r.result_detail.chunks) return 'sync';
    if (r.result_detail.jobs) return 'routine';
  }
  return '';
}

// ---------------------------------------------------------------------------
function HeaderBlock({ kind, result }: { kind: string; result: any }) {
  const ini = result?.initiator;
  if (!ini) return null;
  const forwarded = result?.forwarded_to;
  return (
    <div className="res-head">
      <div className="kvrow">
        <span>发起端</span>
        <b className="mono">{ini.entity ?? '—'}</b>
        <span>角色</span><span>{ini.role === 'edge' ? '边 edge' : ini.role === 'terminal' ? '端 terminal' : ini.role || '—'}</span>
        <span>所在节点</span><span className="mono">{ini.node || '—'}</span>
      </div>
      {(kind === 'diagnosis' || forwarded) && (
        <div className="kvrow">
          <span>转诊</span>
          {forwarded
            ? <b className="mono" style={{ color: '#b45309' }}>{forwarded}</b>
            : <span className="muted">{ini.entity?.startsWith('clinic') ? '直接执行（无转诊）' : '本医院直接执行'}</span>}
          <span>优先级</span><span className="mono">P{ini.priority ?? '—'}</span>
          <span>截止</span><span className="mono">{ini.deadline || '—'}</span>
        </div>
      )}
      {!forwarded && kind !== 'diagnosis' && (
        <div className="kvrow">
          <span>优先级</span><span className="mono">P{ini.priority ?? '—'}</span>
          <span>截止</span><span className="mono">{ini.deadline || '—'}</span>
        </div>
      )}
    </div>
  );
}

// ------------------------------ diagnosis ------------------------------
function DiagnosisBody({ result }: { result: any }) {
  const rd = result?.result_detail || {};
  const prob = rd.bpCR_probability;
  const predictions: any[] = Array.isArray(rd.predictions) ? rd.predictions : [];
  return (
    <>
      {prob != null && (
        <div className="result-hero violet">
          <div className="hero-value">{(Number(prob) * 100).toFixed(1)}%</div>
          <div className="hero-label">
            {Number(prob) >= 0.5 ? 'pCR · 完全缓解（预测）' : 'non-pCR'}
            <span className="muted">bpCR 概率</span>
          </div>
        </div>
      )}
      {predictions.length > 0 && (
        <>
          <h4 className="sub-title">患者预测明细</h4>
          <table className="datatable">
            <thead><tr><th>患者</th><th>bpCR 概率</th><th>预测</th></tr></thead>
            <tbody>
              {predictions.map((p: any, i: number) => (
                <tr key={i}>
                  <td className="mono">{p.patient_id ?? i + 1}</td>
                  <td>{p.bpCR_probability != null ? `${(Number(p.bpCR_probability) * 100).toFixed(1)}%` : '—'}</td>
                  <td>{p.prediction === 1 ? 'pCR' : p.prediction === 0 ? 'non-pCR' : '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
      {(rd.worker_latency_ms != null || rd.server_latency_ms != null) && (
        <div className="kvrow">
          <span>Worker 延迟</span><span className="mono">{fmtMs(rd.worker_latency_ms)}</span>
          <span>Server 延迟</span><span className="mono">{fmtMs(rd.server_latency_ms)}</span>
        </div>
      )}
    </>
  );
}

// ------------------------------ compute ------------------------------
function ComputeBody({ result }: { result: any }) {
  const produced = result?.produced || {};
  const rd = result?.result_detail || {};
  const partitions: any[] = Array.isArray(result?.partitions) ? result.partitions : [];
  return (
    <>
      <div className="chips-line">
        <span className="mini-chip">仪器 {produced.instruments ?? '—'} 台</span>
        <span className="mini-chip">行数 {produced.rows ?? '—'}</span>
        <span className="mini-chip">样本 {produced.samples ?? '—'}</span>
        <span className="mini-chip">分区成功 {rd.partitions_ok ?? '—'}/{partitions.length || '—'}</span>
        <span className="mini-chip">数据量 {fmtMemBytes(rd.total_bytes)}</span>
        <span className="mini-chip">合计 CPU {fmtMs(rd.aggregate_cpu_ms)}</span>
      </div>
      {partitions.length > 0 && (
        <>
          <h4 className="sub-title">协同计算分区</h4>
          <table className="datatable">
            <thead>
              <tr><th>分区</th><th>执行者</th><th>节点</th><th>行数</th><th>字节</th>
                <th>checksum</th><th>CPU</th><th>耗时</th><th>状态</th></tr>
            </thead>
            <tbody>
              {partitions.map((p: any, i: number) => (
                <tr key={i}>
                  <td className="mono">{p.partition ?? i}</td>
                  <td className="mono">{p.actor ?? '—'}</td>
                  <td className="mono muted">{p.node ?? '—'}</td>
                  <td className="mono">{p.rows ?? '—'}</td>
                  <td className="mono">{fmtMemBytes(p.bytes)}</td>
                  <td className="mono xs">{(p.checksum || '—').slice(0, 8)}</td>
                  <td className="mono">{p.cpu_ms != null ? `${Number(p.cpu_ms).toFixed(0)}ms` : '—'}</td>
                  <td className="mono">{fmtMs(p.ms)}</td>
                  <td>{p.failed
                    ? <span className="badge s-failed">失败 {p.error ? `· ${String(p.error).slice(0, 24)}` : ''}</span>
                    : <span className="badge s-finished">成功</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </>
  );
}

// ------------------------------ sync ------------------------------
function SyncBody({ result }: { result: any }) {
  const rd = result?.result_detail || {};
  const chunks: any[] = Array.isArray(rd.chunks) ? rd.chunks : [];
  const backup = rd.backup || {};
  return (
    <>
      <div className="kvgrid">
        <div className="kv"><span>云端版本</span><b className="mono">{rd.db_version ?? '—'}</b></div>
        <div className="kv"><span>云端条目</span><b className="mono">{rd.cloud_items ?? '—'}</b></div>
        <div className="kv"><span>缺失 / 已拉取</span><b className="mono">{rd.missing ?? '—'} / {rd.pulled ?? '—'}</b></div>
        <div className="kv"><span>失败分块</span><b className="mono">{rd.failed_chunks ?? 0}</b></div>
        <div className="kv"><span>拉取字节</span><b className="mono">{fmtMemBytes(rd.bytes_pulled)}</b></div>
        <div className="kv"><span>拉取耗时</span><b className="mono">{fmtMs(rd.pull_ms)}</b></div>
        <div className="kv"><span>并发 / 带宽</span><b className="mono">{rd.concurrency ?? '—'} · {rd.bandwidth_mbps ?? '—'}Mbps</b></div>
        <div className="kv"><span>上传 / 云端总量</span><b className="mono">{rd.uploaded ?? '—'} / {rd.cloud_total ?? '—'}</b></div>
      </div>
      <div className="kvrow">
        <span>数据对端</span>
        <span>{(rd.peers || []).map((p: string) => <i key={p} className="kv-peer">{p}</i>)}</span>
        <span>备份</span>
        <span className="muted">
          {backup.backup_id
            ? `${backup.backup_id} · ${backup.total_items ?? '—'} 条${backup.hash ? ` · ${String(backup.hash).slice(0, 8)}` : ''}`
            : '—'}
        </span>
      </div>
      {chunks.length > 0 && (
        <>
          <h4 className="sub-title">分块同步明细（前 {Math.min(chunks.length, 30)} / {chunks.length}）</h4>
          <div className="tbl-scroll">
            <table className="datatable">
              <thead><tr><th>记录 ID</th><th>对端</th><th>大小</th><th>hash</th><th>耗时</th><th>状态</th></tr></thead>
              <tbody>
                {chunks.slice(0, 30).map((c: any, i: number) => (
                  <tr key={i}>
                    <td className="mono">{c.id}</td>
                    <td className="mono">{c.peer ?? '—'}</td>
                    <td className="mono">{fmtMemBytes(c.size)}</td>
                    <td className="mono xs">{(c.hash || '—').slice(0, 8)}</td>
                    <td className="mono">{c.ms != null ? `${Number(c.ms).toFixed(0)}ms` : '—'}</td>
                    <td>{c.ok
                      ? <span className="badge s-finished">成功</span>
                      : <span className="badge s-failed">失败 {c.error ? `· ${String(c.error).slice(0, 24)}` : ''}</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </>
  );
}

// ------------------------------ routine ------------------------------
function RoutineBody({ result }: { result: any }) {
  const rd = result?.result_detail || {};
  const jobs: any[] = Array.isArray(rd.jobs) ? rd.jobs : [];
  return (
    <>
      <div className="chips-line">
        <span className="mini-chip">一次性 Job {jobs.length} 个</span>
        <span className="mini-chip">成功 {rd.succeeded ?? '—'}</span>
        <span className="mini-chip">失败 {jobs.length - (rd.succeeded ?? 0)}</span>
      </div>
      {jobs.length > 0 && (
        <>
          <h4 className="sub-title">Job 明细（均已回收删除）</h4>
          <div className="tbl-scroll">
            <table className="datatable">
              <thead>
                <tr><th>Job</th><th>节点</th><th>状态</th><th>Pod</th><th>输出 CPU</th><th>输出字节</th><th>checksum</th><th>删除</th></tr>
              </thead>
              <tbody>
                {jobs.map((j: any, i: number) => (
                  <tr key={i}>
                    <td className="mono xs">{j.job ?? '—'}</td>
                    <td className="mono">{j.node ?? '—'}</td>
                    <td>
                      {j.state === 'succeeded' && <span className="badge s-finished">成功</span>}
                      {j.state === 'failed' && <span className="badge s-failed">失败</span>}
                      {j.state === 'timeout' && <span className="badge s-queued">超时</span>}
                    </td>
                    <td className="mono xs">{j.pod || '—'}</td>
                    <td className="mono">{j.output?.cpu_ms != null ? `${Number(j.output.cpu_ms).toFixed(0)}ms` : '—'}</td>
                    <td className="mono">{fmtMemBytes(j.output?.bytes)}</td>
                    <td className="mono xs">{(j.output?.checksum || '—').slice(0, 8)}</td>
                    <td>{j.deleted ? '✓' : '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </>
  );
}

function GenericBody({ result }: { result: any }) {
  const rows: string[] = Object.keys(result).filter((k) => k !== 'stages' && k !== 'metrics');
  if (rows.length === 0) return null;
  return (
    <table className="datatable">
      <tbody>
        {rows.map((k) => (
          <tr key={k}>
            <td className="mono">{k}</td>
            <td className="mono">{JSON.stringify(result[k])}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

// ------------------------------ stages ------------------------------
function StagesBlock({ result }: { result: any }) {
  const stages: any[] = Array.isArray(result?.stages) ? result.stages : [];
  const metrics: any = result?.metrics || {};
  if (stages.length === 0) {
    const kvs = Object.entries(metrics).filter(([, v]) => v != null);
    if (kvs.length === 0) return null;
    return (
      <>
        <h4 className="sub-title">计时指标</h4>
        <div className="kvrow">
          {kvs.map(([k, v]) => (
            <Fragment key={k}><span>{k}</span><span className="mono">{fmtMs(Number(v))}</span></Fragment>
          ))}
        </div>
      </>
    );
  }
  const max = Math.max(...stages.map((s) => Number(s.ms) || 0), 1);
  return (
    <>
      <h4 className="sub-title">执行阶段</h4>
      <div className="wf">
        {stages.map((s: any, i: number) => (
          <div className="wf-row" key={i}>
            <span className="wf-lbl">
              {s.name}{s.actor && <span className="muted xs"> · {s.actor}</span>}
            </span>
            <div className="wf-bar">
              <div className="wf-fill"
                style={{ width: `${Math.max((Number(s.ms) / max) * 100, 1)}%`, background: stageColor(i) }} />
            </div>
            <span className="wf-val mono">{fmtMs(Number(s.ms))}</span>
            {s.node && <span className="mono xs muted wf-node">{s.node}</span>}
            {s.detail && <span className="mono xs muted wf-node">{s.detail}</span>}
          </div>
        ))}
      </div>
    </>
  );
}

function stageColor(i: number): string {
  const palette = ['#0ea5e9', '#6366f1', '#0d9488', '#d97706', '#64748b', '#7c3aed'];
  return palette[i % palette.length];
}
