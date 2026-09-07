import { useState } from 'react';
import type { AffinityKind, EntityLive, EntityModel } from '../types';
import { AFFINITY_LABEL, KIND_LABEL } from '../utils';

interface Props {
  id: string;
  ent: EntityModel;
  live?: EntityLive;
  isDefault: boolean;
  edgeNodes: string[];
  onChange: (id: string, patch: Partial<EntityModel>) => void;
  onDelete: (id: string) => void;
  onRestart: (id: string) => void;
}

export default function PodPanel({ id, ent, live, isDefault, edgeNodes, onChange, onDelete, onRestart }: Props) {
  const [newK, setNewK] = useState('');
  const [newV, setNewV] = useState('');

  const maxReplicas = ent.affinity === 'fixed' ? 1 : Math.min(2, Math.max(edgeNodes.length, 1));
  const patchRes = (section: 'requests' | 'limits', key: 'cpu' | 'memory', value: string) => {
    const cur = ent.resources || { requests: {}, limits: {} };
    const next = {
      ...cur,
      [section]: { ...(cur[section] || {}), [key]: value },
    };
    onChange(id, { resources: next as any });
  };

  const setLabels = (labels: Record<string, string>) => onChange(id, { labels });
  const addLabel = () => {
    if (!newK.trim()) return;
    setLabels({ ...(ent.labels || {}), [newK.trim()]: newV.trim() });
    setNewK(''); setNewV('');
  };

  const pods = live?.pods || [];
  const anyUnready = pods.some((p) => !p.ready);

  return (
    <div className="pod-panel">
      <div className="pp-head">
        <div>
          <div className="pp-title">{id}</div>
          <div className="muted xs">{KIND_LABEL[ent.kind]}{isDefault ? ' · 默认实体' : ' · 新增实体'}</div>
        </div>
        <span className={`live-dot ${pods.length === 0 ? 'gray' : anyUnready ? 'red' : 'green'}`} />
      </div>

      {pods.length > 0 && (
        <div className="pp-inst">
          {pods.map((p) => (
            <div key={p.name} className={`inst ${p.ready ? '' : 'bad'}`}>
              <span className="mono xs">{p.name}</span>
              <span className="muted xs">{p.node || ''} · {p.phase}</span>
            </div>
          ))}
        </div>
      )}
      {pods.length === 0 && <div className="muted xs mb">暂无就绪实例（可能仍在调度/拉取镜像）</div>}

      <div className="pp-row">
        <label>副本数</label>
        <div className="stepper">
          <button disabled={ent.replicas <= 1} onClick={() => onChange(id, { replicas: ent.replicas - 1 })}>−</button>
          <span className="mono">{ent.replicas}</span>
          <button disabled={ent.replicas >= maxReplicas} onClick={() => onChange(id, { replicas: ent.replicas + 1 })}>＋</button>
        </div>
        <span className="muted xs">上限 {maxReplicas}{ent.affinity === 'fixed' ? '（固定节点=1）' : ''}</span>
      </div>

      <div className="pp-row col">
        <label>亲和策略</label>
        <select value={ent.affinity}
          onChange={(e) => onChange(id, { affinity: e.target.value as AffinityKind })}>
          {(['fixed', 'role-edge', 'spread-edge'] as AffinityKind[]).map((a) => (
            <option key={a} value={a}>{AFFINITY_LABEL[a]}</option>
          ))}
        </select>
      </div>

      {ent.affinity === 'fixed' && (
        <div className="pp-row col">
          <label>目标节点（也可在地图上拖动）</label>
          <select value={ent.node || ''}
            onChange={(e) => onChange(id, { node: e.target.value })}>
            <option value="">— 选择边缘节点 —</option>
            {edgeNodes.map((n) => <option key={n} value={n}>{n}</option>)}
          </select>
        </div>
      )}
      {ent.affinity !== 'fixed' && (
        <div className="muted xs">非固定策略：Pod 将分布在边缘节点（role=edge）上</div>
      )}

      <div className="pp-row col">
        <label>镜像</label>
        <input className="mono" value={ent.image} spellCheck={false}
          onChange={(e) => onChange(id, { image: e.target.value })} />
      </div>

      <div className="pp-row col">
        <label>资源 requests / limits</label>
        <div className="res-grid">
          <span className="res-label">CPU req</span>
          <input value={ent.resources?.requests?.cpu ?? ''}
            onChange={(e) => patchRes('requests', 'cpu', e.target.value)} />
          <span className="res-label">Mem req</span>
          <input value={ent.resources?.requests?.memory ?? ''}
            onChange={(e) => patchRes('requests', 'memory', e.target.value)} />
          <span className="res-label">CPU lim</span>
          <input value={ent.resources?.limits?.cpu ?? ''}
            onChange={(e) => patchRes('limits', 'cpu', e.target.value)} />
          <span className="res-label">Mem lim</span>
          <input value={ent.resources?.limits?.memory ?? ''}
            onChange={(e) => patchRes('limits', 'memory', e.target.value)} />
        </div>
      </div>

      <div className="pp-row col">
        <label>额外标签（不会覆盖选择器标签 app/hospital/clinic）</label>
        {Object.entries(ent.labels || {}).map(([k, v]) => (
          <div key={k} className="label-row">
            <span className="mono xs">{k}: {v}</span>
            <button className="mini danger" title="删除"
              onClick={() => {
                const next = { ...(ent.labels || {}) };
                delete next[k];
                setLabels(next);
              }}>✕</button>
          </div>
        ))}
        <div className="label-add">
          <input placeholder="键" value={newK} onChange={(e) => setNewK(e.target.value)} />
          <input placeholder="值" value={newV} onChange={(e) => setNewV(e.target.value)} />
          <button className="mini" onClick={addLabel}>添加</button>
        </div>
      </div>

      <div className="pp-actions">
        <button className="btn secondary sm" onClick={() => onRestart(id)}>重启 (滚动更新)</button>
        {ent.kind === 'clinic' && (
          <button className="btn danger sm" onClick={() => onDelete(id)}>
            删除该 Clinic Pod
          </button>
        )}
      </div>
      {ent.kind === 'clinic' ? (
        <div className="muted xs">
          删除后需点击「应用」才会从集群移除；「重置」可恢复默认（含 clinic-1/2）。
        </div>
      ) : (
        <div className="muted xs">
          hospital 为医院核心实体（承载诊断 worker 前端与协同计算能力），不可删除；可拖动换节点或修改右侧参数。
        </div>
      )}
    </div>
  );
}
