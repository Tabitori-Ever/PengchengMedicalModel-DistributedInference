import { useMemo, useState } from 'react';
import type { ClusterStatus, EntityModel, NodeInfo, PodInfo, ReadonlyDeploy } from '../types';
import {
  AFFINITY_SHORT, imageTag, isEdgeNode, loadColor, nodeLoad,
  nodeRoleLabel, READONLY_LABEL, sortNodes,
} from '../utils';

interface Props {
  status: ClusterStatus;
  desired: Record<string, EntityModel>;
  selected: string;
  onPick: (id: string) => void;
  onMove: (id: string, node: string) => void;
  onRestart: (id: string) => void;
  onDelete: (id: string) => void;
  onNotice?: (kind: 'ok' | 'err', text: string) => void;
}

interface Row {
  id: string;
  ent: EntityModel;
}

interface NodeGroup {
  node: NodeInfo;
  rows: Row[];
  readonly: { deploy: ReadonlyDeploy; pods: PodInfo[] }[];
}

const KIND_TEXT: Record<string, { zh: string; en: string }> = {
  hospital: { zh: '医院 · 边', en: 'HOSPITAL · EDGE' },
  clinic: { zh: '诊所 · 端', en: 'CLINIC · TERMINAL' },
};

export default function ClusterList({
  status, desired, selected, onPick, onMove, onRestart, onDelete, onNotice,
}: Props) {
  const [dragId, setDragId] = useState('');
  const [overNode, setOverNode] = useState('');
  const [openRo, setOpenRo] = useState<Record<string, boolean>>({});

  const nodes = useMemo(() => sortNodes(status.nodes || []), [status.nodes]);
  const edgeNames = useMemo(
    () => nodes.filter((n) => isEdgeNode(n)).map((n) => n.name),
    [nodes],
  );
  const edgeList = edgeNames.length ? edgeNames : ['node1', 'node2'];

  const groups = useMemo<NodeGroup[]>(() => {
    const ids = Object.keys(desired).sort();
    const per: Record<string, NodeGroup> = {};
    nodes.forEach((n) => { per[n.name] = { node: n, rows: [], readonly: [] }; });

    ids.forEach((id, idx) => {
      const ent = desired[id];
      const node = ent.affinity === 'fixed' && ent.node
        ? ent.node
        : edgeList[idx % edgeList.length];
      if (!per[node]) {
        per[node] = {
          node: {
            name: node,
            role: edgeNames.includes(node) ? 'edge' : null,
            ready: undefined,
            load: null,
          },
          rows: [],
          readonly: [],
        };
      }
      per[node].rows.push({ id, ent });
    });

    (status.readonly || []).forEach((deploy) => {
      nodes.forEach((n) => {
        const pods = (deploy.pods || []).filter((p) => (p.node || '') === n.name);
        if (pods.length > 0) per[n.name].readonly.push({ deploy, pods });
      });
    });

    const ordered: NodeGroup[] = [];
    nodes.forEach((n) => ordered.push(per[n.name]));
    Object.keys(per).forEach((k) => {
      if (!nodes.some((n) => n.name === k)) ordered.push(per[k]);
    });
    return ordered;
  }, [desired, nodes, edgeList, edgeNames, status.readonly]);

  const dragging = !!dragId && !!desired[dragId];

  const canDrop = (node: string) =>
    isEdgeNode(nodes.find((n) => n.name === node), node);

  const startDrag = (e: React.DragEvent, id: string) => {
    setDragId(id);
    onPick(id);
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData('text/plain', id);
  };

  const endDrag = () => {
    setDragId('');
    setOverNode('');
  };

  const drop = (e: React.DragEvent, node: string) => {
    e.preventDefault();
    const id = e.dataTransfer.getData('text/plain') || dragId;
    setDragId('');
    setOverNode('');
    if (!id || !desired[id]) return;
    if (!canDrop(node)) {
      onNotice?.('err', `仅边缘节点（node1 / node2）可承载 hospital / clinic Pod，${node} 不接受迁移`);
      return;
    }
    if (desired[id].node === node && desired[id].affinity === 'fixed') return;
    onMove(id, node);
    onNotice?.('ok', `${id} 已迁移到 ${node}（草稿，点击「应用」后下发）`);
  };

  if (nodes.length === 0) {
    return <div className="empty">集群节点不可用，无法展示列表视图。</div>;
  }

  return (
    <div className="clist">
      <div className="clist-legend">
        <span className="eyebrow">业务实体 · WORKLOAD ROSTER</span>
        <span className="muted xs">
          拖拽行到目标节点分组即可迁移（仅边缘节点 node1 / node2 可承载医院 / 诊所 Pod），点击行在右侧面板编辑。
        </span>
      </div>

      {groups.map((g) => {
        const n = g.node;
        const role = nodeRoleLabel(n.role, n.name);
        const cpu = Number(n.load?.cpu || 0);
        const mem = Number(n.load?.memory || 0);
        const pct = nodeLoad(n);
        const roPods = g.readonly.reduce((s, r) => s + r.pods.length, 0);
        const dropOk = canDrop(n.name);
        const dragOver = overNode === n.name;
        const roOpen = !!openRo[n.name];

        return (
          <section
            key={n.name}
            className={
              'clist-node'
              + (dragOver ? (dropOk ? ' drop-ok' : ' drop-bad') : '')
            }
            onDragOver={(e) => {
              e.preventDefault();
              e.dataTransfer.dropEffect = dropOk ? 'move' : 'none';
              if (overNode !== n.name) setOverNode(n.name);
            }}
            onDragLeave={(e) => {
              const rt = e.relatedTarget as Node | null;
              if (!rt || !e.currentTarget.contains(rt)) {
                setOverNode((cur) => (cur === n.name ? '' : cur));
              }
            }}
            onDrop={(e) => drop(e, n.name)}
          >
            <header className="clist-node-head">
              <span className="cn-bar" style={{ background: loadColor(pct) }} />
              <span className="cn-name mono">{n.name}</span>
              <span className="cn-role">
                {role.zh}
                <i className="cn-role-en mono">{role.en}</i>
              </span>
              <span className={`cn-ready ${n.ready ? 'ok' : 'bad'}`}>
                <i className="st-dot" />
                {n.ready === undefined ? '未知' : n.ready ? 'Ready' : 'NotReady'}
              </span>
              <span className="cn-load">
                <span className="cn-load-cell">
                  <i className="cn-load-lbl mono">CPU</i>
                  <span className="mini-bar">
                    <i style={{ width: `${Math.min(cpu, 1) * 100}%`, background: loadColor(cpu) }} />
                  </span>
                  <b className="mono">{Math.round(cpu * 100)}%</b>
                </span>
                <span className="cn-load-cell">
                  <i className="cn-load-lbl mono">内存</i>
                  <span className="mini-bar">
                    <i style={{ width: `${Math.min(mem, 1) * 100}%`, background: loadColor(mem) }} />
                  </span>
                  <b className="mono">{Math.round(mem * 100)}%</b>
                </span>
              </span>
              <span className="cn-pods mono">
                {g.rows.length} 业务
                {roPods > 0 && <i className="cn-pods-ro"> / {roPods} 只读</i>}
              </span>
              <span className={`cn-drophint ${dragOver ? (dropOk ? 'ok' : 'bad') : ''}`}>
                {dragOver
                  ? (dropOk ? '松开迁移到此节点' : '不可迁移 · 仅边缘节点')
                  : (dragging ? (dropOk ? '可迁移' : '只读节点') : '')}
              </span>
            </header>

            <div className="clist-cols">
              <span>实体名 ENTITY</span>
              <span>类型 KIND</span>
              <span>副本</span>
              <span>亲和策略 AFFINITY</span>
              <span>镜像 IMAGE</span>
              <span>实例状态 PODS</span>
              <span>操作 ACTION</span>
            </div>

            <div className="clist-rows">
              {g.rows.length === 0 && <div className="clist-empty">该节点暂无业务实体</div>}
              {g.rows.map(({ id, ent }) => {
                const live = status.entities?.[id];
                const pods: PodInfo[] = live?.pods || [];
                const ready = pods.filter((p) => p.ready).length;
                const kindText = KIND_TEXT[ent.kind] || { zh: ent.kind, en: ent.kind.toUpperCase() };
                return (
                  <div
                    key={id}
                    className={'clist-row' + (selected === id ? ' sel' : '') + (dragId === id ? ' dragging' : '')}
                    draggable
                    onDragStart={(e) => startDrag(e, id)}
                    onDragEnd={endDrag}
                    onClick={() => onPick(id)}
                  >
                    <span className="cr-name">
                      <i className={`cr-kindbar ${ent.kind}`} />
                      <b className="mono">{id}</b>
                      <i className="cr-handle" title="拖拽迁移到其它边缘节点">⠿</i>
                    </span>
                    <span className="cr-kind">
                      {kindText.zh}
                      <i className="cr-kind-en mono">{kindText.en}</i>
                    </span>
                    <span className="cr-replicas mono">{ent.replicas}</span>
                    <span className="cr-affinity">
                      {AFFINITY_SHORT[ent.affinity] || ent.affinity}
                      {ent.affinity === 'fixed' && ent.node
                        ? <i className="cr-node mono"> @ {ent.node}</i>
                        : null}
                    </span>
                    <span className="cr-image mono" title={ent.image || '未设置镜像'}>
                      {imageTag(ent.image)}
                    </span>
                    <span
                      className={`cr-pods ${pods.length === 0 ? 'pending' : ready === pods.length ? 'ok' : 'warn'}`}
                      title={pods.length
                        ? pods.map((p) => `${p.name}${p.ready ? '' : ' (未就绪)'}${p.node ? ` @ ${p.node}` : ''}`).join('\n')
                        : '暂无实例（可能仍在调度 / 拉取镜像）'}
                    >
                      <i className="st-dot" />
                      <b className="mono">{ready}/{pods.length}</b>
                      <i className="cr-pods-lbl">ready/total</i>
                    </span>
                    <span className="cr-actions">
                      <button
                        className="mini"
                        title="滚动重启该部署"
                        onClick={(e) => { e.stopPropagation(); onRestart(id); }}
                      >
                        重启
                      </button>
                      {ent.kind === 'clinic' && (
                        <button
                          className="mini danger"
                          title="从草稿移除该 clinic（点击「应用」后删除）"
                          onClick={(e) => { e.stopPropagation(); onDelete(id); }}
                        >
                          删除
                        </button>
                      )}
                    </span>
                  </div>
                );
              })}
            </div>

            {g.readonly.length > 0 && (
              <div className={`clist-ro ${roOpen ? 'open' : ''}`}>
                <button
                  className="clist-ro-head"
                  onClick={() => setOpenRo((s) => ({ ...s, [n.name]: !s[n.name] }))}
                >
                  <span className="ro-caret mono">{roOpen ? '▾' : '▸'}</span>
                  <span className="eyebrow">只读部署 · READ-ONLY DEPLOYMENTS</span>
                  <span className="mono ro-count">{g.readonly.length} 项</span>
                </button>
                {roOpen && (
                  <div className="clist-ro-rows">
                    {g.readonly.map(({ deploy, pods }) => {
                      const meta = READONLY_LABEL[deploy.name] || { zh: deploy.name, en: 'SERVICE' };
                      return (
                        <div className="clist-ro-row" key={`${n.name}-${deploy.name}`}>
                          <span className="ror-name mono">{deploy.name}</span>
                          <span className="ror-zh">{meta.zh}</span>
                          <span className="ror-en mono">{meta.en}</span>
                          <span className="ror-rep mono">×{deploy.replicas ?? '—'}</span>
                          <span className="ror-pods mono" title={pods.map((p) => p.name).join('\n')}>
                            {pods.filter((p) => p.ready).length}/{pods.length} ready
                          </span>
                          <span className="ror-tag mono" title={deploy.image || ''}>
                            {imageTag(deploy.image)}
                          </span>
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
            )}

            {dragging && dragOver && !dropOk && (
              <div className="clist-invalid">该节点不可承载业务 Pod（仅边缘节点可迁移）</div>
            )}
          </section>
        );
      })}
    </div>
  );
}
