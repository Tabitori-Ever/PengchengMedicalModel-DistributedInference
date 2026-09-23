import type { ReactNode } from 'react';
import { DEGRADE_REASON_ZH, MODE_ZH, statusZh } from '../types';

type Tone =
  | 'kind-diagnosis' | 'kind-compute' | 'kind-sync' | 'kind-routine'
  | 'mode-collaborative' | 'mode-local' | 'mode-degraded' | 'mode-auto'
  | 'st-running' | 'st-pending' | 'st-completed' | 'st-failed'
  | 'on' | 'off' | 'warn' | 'neutral';

export function Badge({ tone, children, title }: { tone: Tone; children: ReactNode; title?: string }) {
  return (
    <span className={`bdg t-${tone}`} title={title}>
      {children}
    </span>
  );
}

/**
 * 实际执行模式徽标：协同 / 本地 / 降级。
 * 降级使用独立的警示色（琥珀橙），与「本地执行」区分开。
 */
export function ModeBadge({
  modeUsed, degraded, modeRequested,
}: { modeUsed?: string | null; degraded?: boolean | null; modeRequested?: string | null }) {
  const used = (modeUsed || '').toLowerCase();
  if (degraded || used === 'degraded') {
    return (
      <Badge
        tone="mode-degraded"
        title={modeRequested ? `请求模式 ${modeRequested}` : undefined}
      >
        降级
      </Badge>
    );
  }
  if (used === 'local') return <Badge tone="mode-local">本地</Badge>;
  if (used === 'collaborative') return <Badge tone="mode-collaborative">协同</Badge>;
  if (used === 'auto') return <Badge tone="mode-auto">自动</Badge>;
  return <Badge tone="neutral">{modeUsed || '未知'}</Badge>;
}

export function RequestedModeLabel({ mode }: { mode?: string | null }) {
  const m = (mode || '').toLowerCase();
  return (
    <span className="reqmode" title={m}>
      {MODE_ZH[m] || mode || '—'}
    </span>
  );
}

export function DegradeReason({ reason }: { reason?: string | null }) {
  if (!reason) return null;
  return (
    <span className="dg-reason" title={reason}>
      {DEGRADE_REASON_ZH[reason] || reason}
    </span>
  );
}

export function StatusBadge({ status }: { status?: string | null }) {
  const s = (status || '').toLowerCase();
  const tone: Tone =
    s === 'running' ? 'st-running'
      : s === 'queued' || s === 'pending' ? 'st-pending'
        : s === 'completed' || s === 'finished' ? 'st-completed'
          : s === 'failed' || s === 'error' ? 'st-failed'
            : 'neutral';
  return <Badge tone={tone}>{statusZh(status)}</Badge>;
}

export function KindBadge({ kind }: { kind?: string | null }) {
  const k = (kind || '') as string;
  const tone: Tone =
    k === 'diagnosis' ? 'kind-diagnosis'
      : k === 'compute' ? 'kind-compute'
        : k === 'sync' ? 'kind-sync'
          : k === 'routine' ? 'kind-routine'
            : 'neutral';
  const zh = MODE_ZH[k];
  const label = ({ diagnosis: '诊断', compute: '计算', sync: '通信', routine: '日常' } as Record<string, string>)[k];
  return <Badge tone={tone}>{label || zh || k || '—'}</Badge>;
}

export function Dot({ tone }: { tone: 'ok' | 'bad' | 'warn' | 'idle' }) {
  return <span className={`dot d-${tone}`} aria-hidden />;
}
