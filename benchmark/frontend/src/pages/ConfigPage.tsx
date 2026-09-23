import { useEffect, useState } from 'react';
import { api, errText, EXPORT_CSV_URL, EXPORT_JSON_URL } from '../api';
import type { SiteConfig, SiteHealth } from '../types';
import { healthAutoDegrade } from '../types';
import StatusBanner, { localReadyCount, schedulerState } from '../components/StatusBanner';
import { Dot } from '../components/Badge';
import { fmtMs } from '../components/format';
import { usePolling } from '../hooks';

/** 已知的边缘实体，用于在界面上固定列出 pod URL 输入框（后端也可能返回更多键） */
const KNOWN_ENTITIES = ['hospital-a', 'hospital-b', 'clinic-1', 'clinic-2', 'clinic-3', 'clinic-4'];

interface Draft {
  scheduler_url: string;
  pod_urls: Record<string, string>;
  auto_degrade: boolean;
  probe_timeout_ms: number;
  concurrency: number;
}

function toDraft(c: SiteConfig): Draft {
  return {
    scheduler_url: c.scheduler_url || '',
    pod_urls: { ...(c.pod_urls || {}) },
    auto_degrade: c.auto_degrade !== false,
    probe_timeout_ms: typeof c.probe_timeout_ms === 'number' ? c.probe_timeout_ms : 1500,
    concurrency: typeof c.concurrency === 'number' ? c.concurrency : 1,
  };
}

export default function ConfigPage({ onChanged }: { onChanged: () => void }) {
  const [health, setHealth] = useState<SiteHealth | null>(null);
  const [healthErr, setHealthErr] = useState('');
  const [draft, setDraft] = useState<Draft | null>(null);
  const [extraKeys, setExtraKeys] = useState<string[]>([]);
  const [err, setErr] = useState('');
  const [msg, setMsg] = useState('');
  const [busy, setBusy] = useState(false);
  const [dirty, setDirty] = useState(false);

  usePolling(async () => {
    try { setHealth(await api.health()); setHealthErr(''); }
    catch (e) { setHealth(null); setHealthErr(errText(e)); }
  }, 6000, true);

  useEffect(() => {
    void (async () => {
      try {
        const c = await api.config();
        setDraft(toDraft(c));
        setExtraKeys(Object.keys(c.pod_urls || {}).filter((k) => !KNOWN_ENTITIES.includes(k)));
        setErr('');
      } catch (e) {
        setDraft(null);
        setErr(errText(e));
      }
    })();
  }, []);

  const patch = (p: Partial<Draft>) => {
    setDraft((d) => (d ? { ...d, ...p } : d));
    setDirty(true);
  };
  const patchPod = (name: string, url: string) => {
    setDraft((d) => (d ? { ...d, pod_urls: { ...d.pod_urls, [name]: url } } : d));
    setDirty(true);
  };

  const save = async () => {
    if (!draft) return;
    setBusy(true);
    setMsg('');
    setErr('');
    try {
      const payload: SiteConfig = {
        scheduler_url: draft.scheduler_url,
        pod_urls: draft.pod_urls,
        auto_degrade: draft.auto_degrade,
        probe_timeout_ms: draft.probe_timeout_ms,
        concurrency: draft.concurrency,
      };
      const saved = await api.updateConfig(payload);
      setDraft(toDraft(saved));
      setDirty(false);
      setMsg('配置已保存（PUT /api/config）');
      onChanged();
    } catch (e) {
      setErr(errText(e));
    } finally {
      setBusy(false);
    }
  };

  const allPods = draft
    ? Array.from(new Set([...KNOWN_ENTITIES, ...Object.keys(draft.pod_urls), ...extraKeys]))
    : KNOWN_ENTITIES;

  const sch = schedulerState(health);
  const local = localReadyCount(health);

  return (
    <>
      <StatusBanner health={health} error={healthErr} />

      {sch.known && !sch.ok ? (
        <div className="alert-banner">
          <Dot tone="bad" />
          <b>调度器不可达{sch.url ? `（${sch.url}）` : ''}</b>
          <span>
            云边端协同无法编排，实验会退回发起方就地执行并标记 degraded。
            此时不宜做协同 / 本地对比，请先恢复调度器。
          </span>
        </div>
      ) : null}

      <div className="cfg-grid">
        <section className="card">
          <div className="card-headrow">
            <div className="card-title">站点配置</div>
            {dirty ? <span className="dirty">有未保存修改</span> : null}
          </div>

          {err && !draft ? <div className="errbox">/api/config 不可用：{err}</div> : null}
          {!draft && !err ? <div className="hint">正在读取配置…</div> : null}

          {draft ? (
            <>
              <div className="field">
                <label className="field-label">调度器地址 scheduler_url</label>
                <input value={draft.scheduler_url}
                  placeholder="http://scheduler-service:8000"
                  onChange={(e) => patch({ scheduler_url: e.target.value })} />
                <div className="hint">站点探测该地址判断调度器是否可达；协同模式依赖它分发分区。</div>
              </div>

              <div className="card-title mt">边缘实体地址 pod_urls</div>
              <div className="podurls">
                {allPods.map((name) => (
                  <div className="podurl-row" key={name}>
                    <span className="podurl-name mono">{name}</span>
                    <input value={draft.pod_urls[name] || ''}
                      placeholder={`http://${name}-service:8000`}
                      onChange={(e) => patchPod(name, e.target.value)} />
                  </div>
                ))}
              </div>

              <div className="field-2col mt">
                <div className="field">
                  <label className="field-label">探测超时 probe_timeout_ms</label>
                  <input type="number" min={100} max={30000} value={draft.probe_timeout_ms}
                    onChange={(e) => patch({ probe_timeout_ms: Number(e.target.value) })} />
                  <div className="hint">可达性探测的短超时（默认 1500 ms）。</div>
                </div>
                <div className="field">
                  <label className="field-label">默认并发 concurrency</label>
                  <input type="number" min={1} max={64} value={draft.concurrency}
                    onChange={(e) => patch({ concurrency: Number(e.target.value) })} />
                  <div className="hint">下发实验时仍可单独覆盖。</div>
                </div>
              </div>

              <label className={`checkrow ${draft.auto_degrade ? 'on' : ''}`}>
                <input type="checkbox" checked={draft.auto_degrade}
                  onChange={(e) => patch({ auto_degrade: e.target.checked })} />
                <span>
                  <b>自动接管 auto_degrade</b>
                  <i>调度器连接失败或超时时，站点直连发起方就地执行，并标记 degraded=true。</i>
                </span>
              </label>

              {msg ? <div className="msg ok">{msg}</div> : null}
              {err && draft ? <div className="errbox">{err}</div> : null}

              <div className="btnrow end">
                <button className="btn ghost" disabled={!dirty || busy}
                  onClick={() => { void (async () => {
                    try { setDraft(toDraft(await api.config())); setDirty(false); setMsg('已放弃修改'); }
                    catch (e) { setErr(errText(e)); }
                  })(); }}>
                  放弃修改
                </button>
                <button className="btn primary" disabled={!dirty || busy} onClick={() => void save()}>
                  {busy ? '保存中…' : '保存配置'}
                </button>
              </div>
            </>
          ) : null}

          <div className="card-title mt">站点健康</div>
          {health ? (
            <div className="kvgrid">
              <div className="kv"><span>站点状态</span>
                <b>{health.site?.status || health.status || (health.ok ? 'ok' : '—')}</b></div>
              <div className="kv"><span>服务 / 版本</span>
                <b>{health.site?.service || health.service || '—'} {health.site?.version || health.version || ''}</b></div>
              <div className="kv"><span>调度器地址</span><b className="wrap mono">{health.scheduler_url || '—'}</b></div>
              <div className="kv"><span>调度器可达</span>
                <b>{sch.known ? (sch.ok ? '是' : '否') : '未知'}</b></div>
              <div className="kv"><span>自动接管</span><b>{healthAutoDegrade(health) ? '开启' : '关闭'}</b></div>
              <div className="kv"><span>本地执行能力</span>
                <b>{local.total === 0 ? '未知' : `${local.ready}/${local.total} 可用`}</b></div>
              <div className="kv"><span>数据库</span>
                <b className="wrap mono">{health.site?.db_path || health.db_path || '—'}</b></div>
            </div>
          ) : <div className="hint">/api/health 不可用，无法显示站点状态。</div>}

          <div className="card-title mt">各实体可达性与能力</div>
          {health?.pods || health?.entities ? (
            <table className="dt small">
              <thead>
                <tr><th>实体</th><th>可达</th><th>节点</th><th>诊断能力</th><th>计算</th><th>通信</th><th>日常</th><th>探测耗时</th></tr>
              </thead>
              <tbody>
                {Object.entries(health.pods || health.entities || {}).map(([name, e]) => {
                  const v = e || {};
                  const caps = (v.local_health?.capabilities || v.capabilities || {}) as Record<string, string>;
                  const ok = typeof v.reachable === 'boolean' ? v.reachable : (typeof v.ok === 'boolean' ? v.ok : null);
                  const checkedMs = typeof v.checked_ms === 'number' ? v.checked_ms
                    : (typeof v.latency_ms === 'number' ? v.latency_ms : null);
                  return (
                    <tr key={name}>
                      <td className="mono">{name}</td>
                      <td>{ok === null ? '未知' : ok ? '可达' : '不可达'}</td>
                      <td className="mono">{String(v.node || v.local_health?.node || '—')}</td>
                      <td className="mono">{caps.diagnosis || (v.local_mode_available === false ? '未提供本地接口' : '—')}</td>
                      <td className="mono">{caps.compute || '—'}</td>
                      <td className="mono">{caps.sync || '—'}</td>
                      <td className="mono">{caps.routine || '—'}</td>
                      <td className="mono num">{fmtMs(checkedMs, 1)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          ) : <div className="hint">未返回实体探测结果。</div>}
        </section>

        <section className="card">
          <div className="card-title">数据导出</div>
          <p className="hint">导出为 attempt 级明细，内容以站点数据库为准，不经过本页面加工。</p>
          <div className="btnrow mt">
            <a className="btn secondary" href={EXPORT_CSV_URL} download>导出 CSV</a>
            <a className="btn secondary" href={EXPORT_JSON_URL} download>导出 JSON</a>
          </div>
          <div className="hint mt">
            直链：<span className="mono">GET /api/export.csv</span> · <span className="mono">GET /api/export.json</span>
          </div>
        </section>
      </div>
    </>
  );
}
