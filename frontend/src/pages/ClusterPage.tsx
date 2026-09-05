import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api } from '../api';
import { useTicker } from '../hooks';
import ClusterMap from '../components/ClusterMap';
import PodPanel from '../components/PodPanel';
import { canonicalEntities } from '../utils';
import type { ApplyResult, ClusterDefault, ClusterStatus, EntityModel, EntityLive } from '../types';

const EMPTY_RES: { requests: { cpu: string; memory: string }; limits: { cpu: string; memory: string } } = {
  requests: { cpu: '100m', memory: '128Mi' },
  limits: { cpu: '500m', memory: '512Mi' },
};

export default function ClusterPage() {
  const [live, setLive] = useState<ClusterStatus | null>(null);
  const [desired, setDesired] = useState<Record<string, EntityModel> | null>(null);
  const [lastSaved, setLastSaved] = useState('');
  const [defaultIds, setDefaultIds] = useState<Set<string>>(new Set());
  const [selected, setSelected] = useState('');
  const [busy, setBusy] = useState(false);
  const [msgs, setMsgs] = useState<{ kind: 'ok' | 'err'; text: string }[]>([]);
  const [addNode, setAddNode] = useState('node1');
  const initRef = useRef(false);

  const notify = (kind: 'ok' | 'err', text: string) =>
    setMsgs((m) => [...m.slice(-6), { kind, text }]);

  const refreshLive = useCallback(async () => {
    try {
      const s = await api.clusterStatus();
      setLive(s);
    } catch (e: any) {
      setLive((old) => old ? { ...old, ok: false, error: e?.message || '刷新失败' } : null);
    }
  }, []);

  // ---- init: default model + live state --------------------------------
  useEffect(() => {
    if (initRef.current) return;
    initRef.current = true;
    (async () => {
      let def: ClusterDefault | null = null;
      let liveNow: ClusterStatus | null = null;
      try { def = await api.clusterDefault(); } catch { notify('err', '无法获取默认集群模型'); }
      try {
        liveNow = await api.clusterStatus();
        setLive(liveNow);
      } catch { notify('err', '无法获取集群实时状态'); }

      const base: Record<string, EntityModel> = {};
      // start from defaults (or live truth when live exists)
      const sourceLive = liveNow?.entities || {};
      const ids = new Set([...Object.keys(def?.editable || {}), ...Object.keys(sourceLive)]);
      ids.forEach((eid) => {
        const lv = sourceLive[eid];
        const dv = def?.editable?.[eid];
        if (lv) {
          base[eid] = pickFields(lv);
        } else if (dv) {
          base[eid] = cloneE(dv);
        } else {
          base[eid] = {
            kind: 'clinic', affinity: 'fixed', node: 'node1', replicas: 1,
            image: '', resources: JSON.parse(JSON.stringify(EMPTY_RES)), labels: {},
          };
        }
      });
      setDefaultIds(new Set(Object.keys(def?.editable || {})));
      setDesired(base);
      setLastSaved(canonicalEntities(base));
      const first = Object.keys(base).sort()[0];
      setSelected(first || '');
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useTicker(refreshLive, 10000);

  const dirty = useMemo(
    () => !!desired && canonicalEntities(desired) !== lastSaved,
    [desired, lastSaved],
  );

  const edgeNodes = useMemo(
    () => (live?.nodes || []).filter((n) => n.role === 'edge' || n.name === 'node1' || n.name === 'node2').map((n) => n.name),
    [live],
  );

  const patch = (id: string, p: Partial<EntityModel>) =>
    setDesired((d) => (d ? { ...d, [id]: { ...d[id], ...p } } : d));

  const move = (id: string, node: string) =>
    setDesired((d) => {
      if (!d?.[id]) return d;
      return {
        ...d,
        [id]: { ...d[id], node, affinity: 'fixed' as const },
      };
    });

  const addClinic = () => {
    setDesired((d) => {
      if (!d) return d;
      const used = new Set(Object.keys(d));
      let n = 3;
      while (used.has(`clinic-${n}`)) n += 1;
      const id = `clinic-${n}`;
      return {
        ...d,
        [id]: {
          kind: 'clinic', affinity: 'fixed', node: addNode, replicas: 1,
          image: '', resources: JSON.parse(JSON.stringify(EMPTY_RES)), labels: {},
        },
      };
    });
    notify('ok', '已加入新的 clinic Pod（草稿），点击“应用”后创建');
  };

  const removeClinic = (id: string) =>
    setDesired((d) => {
      if (!d) return d;
      if (defaultIds.has(id)) {
        notify('err', `${id} 是默认实体，不能移除`);
        return d;
      }
      const next = { ...d };
      delete next[id];
      notify('ok', `${id} 已从草稿移除，点击“应用”后删除集群中的部署`);
      return next;
    });

  const validate = async () => {
    if (!desired) return;
    setBusy(true);
    try {
      const r = await api.clusterValidate(desired);
      if (r.ok) notify('ok', '校验通过 ✓');
      else r.errors.forEach((e) => notify('err', e));
    } catch (e: any) { notify('err', `校验失败：${e?.message}`); }
    finally { setBusy(false); }
  };

  const apply = async () => {
    if (!desired) return;
    setBusy(true);
    try {
      const r: ApplyResult = await api.clusterApply(desired);
      if (r.ok) {
        setLastSaved(canonicalEntities(desired));
        notify('ok', '应用成功 ✓');
        (r.applied || []).forEach((a) => notify('ok', `${a.name}: ${a.message}`));
        refreshLive();
      } else {
        (r.errors || []).forEach((e) => notify('err', e));
      }
    } catch (e: any) {
      const detail = e?.response?.data?.detail;
      if (detail && Array.isArray(detail?.errors)) {
        detail.errors.forEach((x: string) => notify('err', x));
        (detail.applied || []).forEach((a: any) => notify('ok', `${a.name}: ${a.message}`));
      } else if (detail && typeof detail === 'string') {
        notify('err', detail);
      } else {
        notify('err', `应用失败：${e?.message}`);
      }
    } finally { setBusy(false); }
  };

  const reset = async () => {
    if (!window.confirm('恢复当前版本(v2.0)的默认集群配置？未应用的草稿将被丢弃。')) return;
    setBusy(true);
    try {
      const def = await api.clusterDefault();
      const base: Record<string, EntityModel> = {};
      Object.keys(def.editable).forEach((k) => { base[k] = cloneE(def.editable[k]); });
      setDesired(base);
      const r = await api.clusterReset();
      setLastSaved(canonicalEntities(base));
      (r.errors || []).forEach((e) => notify('err', e));
      (r.applied || []).forEach((a) => notify('ok', `已恢复 ${a.name}: ${a.message}`));
      notify('ok', '已恢复默认配置');
      refreshLive();
    } catch (e: any) { notify('err', `重置失败：${e?.message}`); }
    finally { setBusy(false); }
  };

  const restart = async (id: string) => {
    try {
      await api.clusterRestart(id);
      notify('ok', `${id} 已触发滚动重启`);
    } catch (e: any) { notify('err', `重启失败：${e?.response?.data?.detail || e?.message}`); }
  };

  const busyAny = busy;
  const selEnt = desired?.[selected];
  const selLive: EntityLive | undefined = live?.entities?.[selected];

  return (
    <div className="page wide">
      <header className="page-head row">
        <div>
          <h1>集群编排 <span className="ver-tag">v{live?.version || '2.0'}</span></h1>
          <p>
            大圆圈 = 节点（外环显示 CPU/内存负载）；小圆圈 = 任务 Pod。
            拖拽 hospital / clinic Pod 到其它边缘节点，或在右侧面板调整副本/亲和/资源/镜像等运维参数，
            最后点击「应用」下发集群，或「重置」恢复当前版本默认拓扑。
          </p>
        </div>
        <div className="toolbar">
          <span className={`dirty ${dirty ? 'on' : ''}`}>
            {dirty ? '● 有未应用的修改' : '○ 已与集群一致'}
          </span>
          <button className="btn ghost sm" onClick={validate} disabled={busyAny}>校验</button>
          <button className="btn primary sm" onClick={apply} disabled={busyAny}>应用</button>
          <button className="btn danger sm" onClick={reset} disabled={busyAny}>重置</button>
        </div>
      </header>

      {live?.ok === false && live.error && (
        <div className="errbox">Kubernetes 不可达：{live.error}（地图只读展示，应用/重置需集群可用）</div>
      )}

      {msgs.map((m, i) => (
        <div key={i} className={`msg ${m.kind}`} onClick={() => setMsgs((x) => x.filter((_, j) => j !== i))}>
          {m.text}
        </div>
      ))}

      <div className="cluster-grid">
        <section className="card map-card">
          {live && desired ? (
            <ClusterMap
              status={live}
              desired={desired}
              selected={selected}
              onPick={setSelected}
              onMove={move}
            />
          ) : (
            <div className="empty" style={{ height: 360 }}>加载集群状态…</div>
          )}
          <div className="map-addrow">
            <span className="muted xs">新增 Pod：</span>
            <select value={addNode} onChange={(e) => setAddNode(e.target.value)}>
              {(edgeNodes.length ? edgeNodes : ['node1', 'node2']).map((n) => (
                <option key={n} value={n}>{n}</option>
              ))}
            </select>
            <button className="btn ghost sm" onClick={addClinic}>＋ 新增 clinic</button>
            <span className="muted xs">（hospital 双站点固定，可通过拖动调整所在节点）</span>
          </div>
        </section>

        <aside className="card side-panel">
          {!selEnt ? (
            <div className="empty">点击地图上的 Pod 进行编辑</div>
          ) : (
            <PodPanel
              id={selected}
              ent={selEnt}
              live={selLive}
              isDefault={defaultIds.has(selected)}
              edgeNodes={edgeNodes.length ? edgeNodes : ['node1', 'node2']}
              onChange={patch}
              onDelete={removeClinic}
              onRestart={restart}
            />
          )}
        </aside>
      </div>
    </div>
  );
}

function pickFields(lv: EntityLive): EntityModel {
  return {
    kind: lv.kind, affinity: lv.affinity, node: lv.node, replicas: lv.replicas,
    image: lv.image, resources: JSON.parse(JSON.stringify(lv.resources || EMPTY_RES)),
    labels: { ...(lv.labels || {}) },
  };
}
function cloneE(e: EntityModel): EntityModel {
  return JSON.parse(JSON.stringify(e));
}
