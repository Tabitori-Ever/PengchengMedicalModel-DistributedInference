import type { ModelKind, TaskItem, TaskResult } from '../types';
import { MODEL_LABEL } from '../utils';
import { isTerminalStatus } from '../components/TaskResultView';

/**
 * 用户平台结果视图：只给结论，不含阶段明细 / 时延统计 / 指标表 / 执行者节点。
 * 运行中只显示一句阶段说明与进度条。
 */
export default function UserResult({
  kind, task, live,
}: {
  kind: ModelKind;
  task: TaskItem | null;
  live: TaskResult | null;
}) {
  const status = String(live?.status || task?.status || 'running');
  const result: any = live?.result || task?.result || null;
  const detail: any = result?.result_detail || null;
  const progress = live?.progress ?? task?.progress ?? 0;
  const error = live?.error || task?.error;

  if (status === 'failed') {
    return (
      <div className="ur-note bad">
        {MODEL_LABEL[kind] || '任务'}未完成{error ? `：${String(error).slice(0, 160)}` : ''}
      </div>
    );
  }

  if (!isTerminalStatus(status)) {
    return (
      <div className="ur-running">
        <div className="ur-bar">
          <i style={{ width: `${Math.max(6, Math.min(100, progress))}%` }} />
        </div>
        <div className="ur-stage">
          {status === 'queued' ? '排队中，等待执行' : '正在执行'}
          <span className="mono">{`${Math.max(0, Math.min(100, progress))}%`}</span>
        </div>
      </div>
    );
  }

  if (!result) return <div className="ur-note">已完成（无结果数据）</div>;

  if (kind === 'diagnosis') {
    const p = Number(detail?.bpCR_probability);
    const hasP = Number.isFinite(p);
    const patientId = result?.result_detail?.predictions?.[0]?.patient_id
      || result?.initiator?.entity
      || '—';
    return (
      <div className="ur-card">
        <div className="ur-hero">
          <b className="ur-hero-v mono">{hasP ? p.toFixed(3) : '—'}</b>
          <span className="ur-hero-k">bpCR 概率</span>
        </div>
        <div className="ur-lines">
          <span className="ur-line">
            <i>结论</i>
            <b>{hasP ? (p >= 0.5 ? 'pCR（病理完全缓解可能性高）' : 'non-pCR（可能性偏低）') : '—'}</b>
          </span>
          <span className="ur-line"><i>患者</i><b className="mono">{patientId}</b></span>
        </div>
      </div>
    );
  }

  if (kind === 'compute') {
    const ok = Number(detail?.partitions_ok);
    const parts: any[] = Array.isArray(result?.partitions) ? result.partitions : [];
    const n = Number.isFinite(ok) ? ok : parts.filter((x) => !x?.failed).length;
    const bytes = Number(detail?.total_bytes) || parts.reduce((a, x) => a + (Number(x?.bytes) || 0), 0);
    return (
      <div className="ur-note ok">
        {`${n} 个分区计算完成 · ${fmtBytes(bytes)}`}
      </div>
    );
  }

  if (kind === 'sync') {
    const pulled = detail?.pulled ?? 0;
    const total = Number(detail?.missing) || pulled;
    const uploaded = detail?.uploaded ?? 0;
    const backup = detail?.backup?.backup_id;
    return (
      <div className="ur-note ok">
        {`同步完成 · 拉取 ${pulled}/${total} 项 · 上传 ${uploaded} 项 · ${backup ? '已生成备份' : '无备份'}`}
      </div>
    );
  }

  const jobs: any[] = Array.isArray(detail?.jobs) ? detail.jobs : [];
  const done = Number.isFinite(Number(detail?.succeeded))
    ? Number(detail?.succeeded)
    : jobs.filter((j) => String(j?.state) === 'succeeded').length;
  return (
    <div className="ur-note ok">
      {`${done} 个日常任务已完成`}
    </div>
  );
}

function fmtBytes(v: number): string {
  if (!Number.isFinite(v) || v <= 0) return '0 KB';
  if (v >= 1024 * 1024) return `${(v / 1024 / 1024).toFixed(1)} MB`;
  if (v >= 1024) return `${Math.round(v / 1024)} KB`;
  return `${v} B`;
}
