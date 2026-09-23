import type { ReactNode } from 'react';
import type { EntityHealth, SiteHealth } from '../types';
import { healthAutoDegrade } from '../types';
import { Dot } from './Badge';

/** 从 /api/health 里宽松取出 scheduler 可达性（字段名可能是布尔或对象） */
export function schedulerState(h: SiteHealth | null): { known: boolean; ok: boolean; url: string } {
  if (!h) return { known: false, ok: false, url: '' };
  const s = h.scheduler;
  let ok: boolean | null = null;
  if (typeof s === 'boolean') ok = s;
  else if (s && typeof s === 'object') {
    const e = s as EntityHealth;
    if (typeof e.reachable === 'boolean') ok = e.reachable;
    else if (typeof e.ok === 'boolean') ok = e.ok;
  }
  if (ok === null && typeof h.scheduler_reachable === 'boolean') ok = h.scheduler_reachable;
  const url = (typeof s === 'object' && s && (s as EntityHealth).url) || h.scheduler_url || '';
  return { known: ok !== null, ok: ok === true, url: String(url || '') };
}

export function podEntries(h: SiteHealth | null): [string, EntityHealth][] {
  const pods = h?.pods || h?.entities;
  if (!pods || typeof pods !== 'object') return [];
  return Object.entries(pods);
}

export function podReachable(v: EntityHealth | undefined): boolean | null {
  const e = v || {};
  if (typeof e.reachable === 'boolean') return e.reachable;
  if (typeof e.ok === 'boolean') return e.ok;
  return null;
}

/** Pod 的诊断能力：优先取 /local/health 自报的 capabilities，其次看是否具备本地执行接口 */
export function podDiagnosisCap(v: EntityHealth | undefined): string {
  const e = v || {};
  const lh = e.local_health || undefined;
  const caps = (lh?.capabilities || e.capabilities) as Record<string, string> | undefined;
  if (caps && caps.diagnosis) return caps.diagnosis;
  if (e.local_mode_available === true) return '本地可用';
  if (e.local_mode_available === false) return '无本地接口';
  return '';
}

/** 具备本地执行接口的 Pod 数量 */
export function localReadyCount(h: SiteHealth | null): { ready: number; total: number } {
  const pods = podEntries(h);
  return { ready: pods.filter(([, v]) => v?.local_mode_available === true).length, total: pods.length };
}

/**
 * 可达性横幅：调度器 · 各 Pod 可达性 · 本地执行能力 · 自动接管开关。
 * 数据只来自 GET /api/health，不做任何推测。
 */
export default function StatusBanner({
  health, error, extra,
}: { health: SiteHealth | null; error?: string; extra?: ReactNode }) {
  const sch = schedulerState(health);
  const auto = healthAutoDegrade(health);
  const pods = podEntries(health);
  const podsBad = pods.filter(([, v]) => podReachable(v) === false);
  const local = localReadyCount(health);

  return (
    <div className="banner">
      <div className="banner-row">
        <span className={`banner-item ${sch.known ? (sch.ok ? 'ok' : 'bad') : 'idle'}`}>
          <Dot tone={sch.known ? (sch.ok ? 'ok' : 'bad') : 'idle'} />
          调度器：{sch.known ? (sch.ok ? '可达' : '不可达') : '未知'}
          {sch.url ? <i className="mono muted">{sch.url}</i> : null}
        </span>

        <span className={`banner-item ${pods.length === 0 ? 'idle' : podsBad.length ? 'bad' : 'ok'}`}>
          <Dot tone={pods.length === 0 ? 'idle' : podsBad.length ? 'bad' : 'ok'} />
          站点：{pods.length === 0 ? '未知' : `${pods.length - podsBad.length}/${pods.length} 可达`}
        </span>

        <span className={`banner-item ${pods.length === 0 ? 'idle' : local.ready === local.total ? 'ok' : 'warn'}`}>
          <Dot tone={pods.length === 0 ? 'idle' : local.ready === local.total ? 'ok' : 'warn'} />
          本地执行能力：{local.total === 0 ? '未知' : `${local.ready}/${local.total} 可用`}
        </span>

        <span className={`banner-item ${auto ? 'warn' : 'idle'}`}>
          <Dot tone={auto ? 'warn' : 'idle'} />
          自动接管：{auto ? '开启' : '关闭'}
        </span>

        {extra}
      </div>

      {pods.length > 0 && (
        <div className="banner-pods">
          {pods.map(([name, v]) => {
            const e = v || {};
            const ok = podReachable(v);
            const cap = podDiagnosisCap(v);
            const node = e.node || e.local_health?.node;
            const url = e.url;
            return (
              <span key={name} className={`podchip ${ok === null ? '' : ok ? 'ok' : 'bad'}`}
                title={[url ? `URL ${url}` : '', e.error ? String(e.error) : ''].filter(Boolean).join(' · ')}>
                <Dot tone={ok === null ? 'idle' : ok ? 'ok' : 'bad'} />
                <b>{name}</b>
                {cap ? <i className="mono muted">{cap}</i> : null}
                {node ? <i className="mono muted">{String(node)}</i> : null}
                {e.local_mode_available === false ? <i className="mono muted">本地不可用</i> : null}
              </span>
            );
          })}
        </div>
      )}

      {error ? <div className="banner-err">站点后端 /api/health 不可用：{error}</div> : null}
    </div>
  );
}
