import { useCallback, useEffect, useMemo, useState } from 'react';
import { api } from '../api';
import ClusterOverviewMap from '../components/ClusterOverviewMap';
import TaskTrajectoryMap from '../components/TaskTrajectoryMap';
import { useTicker } from '../hooks';
import type {
  ClusterStatus, ClusterSummary, HealthMap, NodeInfo, TaskItem, TaskStats,
} from '../types';
import {
  CAPTION_HOSPITAL, CAPTION_MEDICAL, TIER_LABEL, entityName,
} from '../terms';
import {
  healthDetail, healthState, MODEL_COLOR, MODEL_LABEL, MODELS,
  nodeRoleLabel, READONLY_LABEL, sortNodes,
} from '../utils';

const REFRESH_MS = 10000;

interface Chip {
  key: string;
  name: string;
  en: string;
  /** key inside GET /health (may be absent → unknown) */
  healthKey?: string;
  zh?: string;
}

interface TierNode {
  node: string;
  chips: Chip[];
}

interface Tier {
  zh: string;
  desc: string;
  pod: string;
  nodes: TierNode[];
}

const TIERS: Tier[] = [
  {
    zh: TIER_LABEL.cloud,
    desc: '集中算力与状态：调度、医疗推理、患者库与队列',
    pod: 'node3',
    nodes: [{
      node: 'node3',
      chips: [
        { key: 'scheduler', name: 'scheduler', en: '', healthKey: 'scheduler', zh: '调度' },
        { key: 'medical-server', name: 'medical-server', en: '', healthKey: 'medical_server', zh: '医疗推理服务' },
        { key: 'dc-services', name: 'dc-services', en: '', healthKey: 'datacenter(patient-db)', zh: '患者库 · 协同计算' },
        { key: 'redis', name: 'redis', en: '', healthKey: 'redis', zh: '队列 / 记录' },
      ],
    }],
  },
  {
    zh: TIER_LABEL.medical,
    desc: '诊断前端与协同计算分区，数据不出院',
    pod: 'node1 / node2',
    nodes: [
      {
        node: 'node1',
        chips: [{ key: 'hospital-a', name: 'hospital-a', en: '', healthKey: 'hospital-a', zh: `${entityName('hospital-a')} · ${CAPTION_MEDICAL}` }],
      },
      {
        node: 'node2',
        chips: [{ key: 'hospital-b', name: 'hospital-b', en: '', healthKey: 'hospital-b', zh: `${entityName('hospital-b')} · ${CAPTION_MEDICAL}` }],
      },
    ],
  },
  {
    zh: TIER_LABEL.hospital,
    desc: '转诊发起、患者库同步与日常例行任务',
    pod: 'node1 / node2',
    nodes: [
      {
        node: 'node1',
        chips: [{ key: 'clinic-1', name: 'clinic-1', en: '', healthKey: 'clinic-1', zh: `${entityName('clinic-1')} · ${CAPTION_HOSPITAL}` }],
      },
      {
        node: 'node2',
        chips: [{ key: 'clinic-2', name: 'clinic-2', en: '', healthKey: 'clinic-2', zh: `${entityName('clinic-2')} · ${CAPTION_HOSPITAL}` }],
      },
    ],
  },
];

const FLOW_STEPS = ['① 发起', '② 调度', '③ 协同执行', '④ 结果记录'];

const FLOW_ACTORS: Record<string, string[]> = {
  diagnosis: ['医疗中心 / 医院发起 bpCR', 'scheduler 入队 + 选点转诊', '医疗中心提特征 → 数据中心融合', 'task.result 落库 · 任务记录'],
  compute: ['医疗中心 / 医院提交分区参数', 'scheduler 切分 N 个分区', '多执行体分区并行计算', '数据中心聚合校验'],
  sync: ['发起方发现缺失条目', 'scheduler 分配带宽 / 并发', '与对端 / 数据中心并行拉取', '数据中心全量备份'],
  routine: ['医疗中心 / 医院例行触发', 'scheduler 选空闲节点', '一次性 Job 执行', '输出结果后立即回收'],
};

function errText(e: any): string {
  if (e?.response?.status) return `HTTP ${e.response.status}`;
  return e?.message || '请求失败';
}

export default function ArchitecturePage() {
  const [summary, setSummary] = useState<ClusterSummary | null>(null);
  const [stats, setStats] = useState<TaskStats | null>(null);
  const [health, setHealth] = useState<HealthMap | null>(null);
  const [tasks, setTasks] = useState<TaskItem[]>([]);
  const [cluster, setCluster] = useState<ClusterStatus | null>(null);
  const [clusterErr, setClusterErr] = useState<string | null>(null);
  const [errs, setErrs] = useState<string[]>([]);
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null);
  const [countdown, setCountdown] = useState(REFRESH_MS / 1000);

  const refresh = useCallback(async () => {
    const problems: string[] = [];
    const [s, t, h, ts, c] = await Promise.all([
      api.clusterSummary().catch((e) => {
        problems.push(`集群摘要 /cluster/summary 不可用：${errText(e)}`);
        return null;
      }),
      api.taskStats().catch((e) => {
        problems.push(`任务统计 /tasks/stats 不可用：${errText(e)}`);
        return null;
      }),
      api.health().catch((e) => {
        problems.push(`健康探针 /health 不可用：${errText(e)}`);
        return null;
      }),
      api.tasksRecent(20).catch((e) => {
        problems.push(`任务列表 /tasks 不可用：${errText(e)}`);
        return null;
      }),
      api.clusterStatus().catch((e) => {
        const msg = errText(e);
        problems.push(`集群拓扑 /cluster/status 不可用：${msg}`);
        setClusterErr(msg);
        return null;
      }),
    ]);
    if (s) setSummary(s);
    if (t) setStats(t);
    if (h) setHealth(h);
    if (ts) setTasks(ts);
    if (c) { setCluster(c); setClusterErr(null); } // keep the last snapshot on failure
    setErrs(problems);
    setUpdatedAt(new Date());
    setCountdown(REFRESH_MS / 1000);
  }, []);

  useEffect(() => { refresh(); }, [refresh]);
  useTicker(refresh, REFRESH_MS);
  useTicker(() => setCountdown((c) => (c <= 1 ? REFRESH_MS / 1000 : c - 1)), 1000);

  const nodes = useMemo(() => sortNodes(summary?.nodes || []), [summary]);
  const nodeByName = useMemo(() => {
    const m: Record<string, NodeInfo> = {};
    nodes.forEach((n) => { m[n.name] = n; });
    return m;
  }, [nodes]);

  const byModel = stats?.by_model || {};
  const readyNodes = nodes.filter((n) => n.ready).length;
  const k8sDown = summary?.ok === false;

  const usedHealthKeys = useMemo(
    () => new Set(TIERS.flatMap((t) => t.nodes.flatMap((n) => n.chips.map((c) => c.healthKey || '')))),
    [],
  );
  const extraKeys = Object.keys(health || {}).filter((k) => !usedHealthKeys.has(k));

  return (
    <div className="page wide arch">
      <header className="page-head row">
        <div>
          <h1>可视化 <span className="ver-tag">{REFRESH_MS / 1000}s 自动刷新</span></h1>
          <p>
            三级命名体系：数据中心（node3 调度 / 医疗推理 / 患者库 / 队列）、医疗中心（node1 / node2）、
            医院（node1 / node2）、控制面（desktop-jm5iec6）。总览图展示实体与归属，轨迹图按任务类型回放执行链路；
            节点负载请前往「集群负载」页。数据每 {REFRESH_MS / 1000} 秒自动刷新。
          </p>
        </div>
        <div className="toolbar">
          <span className="arch-meta mono">
            {updatedAt ? `最后更新 ${updatedAt.toLocaleTimeString('zh-CN', { hour12: false })}` : '尚未更新'}
            <i className="arch-meta-sep">·</i>
            {countdown}s 后刷新
          </span>
          <button className="btn primary sm" onClick={refresh}>刷新</button>
        </div>
      </header>

      {k8sDown && summary?.error && (
        <div className="errbox">Kubernetes 不可达：{summary.error}（负载与实例状态可能不完整）</div>
      )}
      {errs.map((e, i) => <div className="errbox" key={i}>{e}</div>)}

      <ClusterOverviewMap
        status={cluster}
        summary={summary}
        health={health}
        error={clusterErr}
        updatedAt={updatedAt}
        showResources={false}
      />

      <TaskTrajectoryMap tasks={tasks} />

      <section className="card arch-summary">
        <div className="card-headrow">
          <h3 className="card-title">节点与实体</h3>
          <span className="muted xs mono">
            {summary?.version ? `v${summary.version}` : '—'}
          </span>
        </div>
        <div className="arch-kpis">
          <ArchKpi label="节点就绪" value={`${readyNodes}/${nodes.length}`} />
          <ArchKpi label="可编排实体" value={summary?.editable ?? '—'} />
          <ArchKpi label="业务实例" value={summary?.editable_pods ?? '—'} />
          <ArchKpi label="平台实例" value={summary?.readonly_pods ?? '—'} />
          <ArchKpi label="队列长度" value={stats?.queue_length ?? 0} tone="brass" />
          <ArchKpi label="运行中任务" value={stats?.running ?? 0} tone="brass" />
          <ArchKpi label="任务总数" value={stats?.total ?? 0} />
        </div>
      </section>

      <section className="arch-tiers">
        {TIERS.map((tier) => (
          <div className="card arch-tier" key={tier.zh}>
            <header className="arch-tier-head">
              <span className="tier-name">{tier.zh}</span>
              <span className="tier-pod mono">{tier.pod}</span>
              <span className="tier-desc muted xs">{tier.desc}</span>
            </header>
            <div className="arch-tier-body">
              {tier.nodes.map((tn) => {
                const n = nodeByName[tn.node];
                const role = nodeRoleLabel(n?.role, tn.node);
                return (
                  <div className="arch-node" key={`${tier.zh}-${tn.node}`}>
                    <div className="arch-node-head">
                      <span className={`an-flag ${n?.ready ? 'ok' : 'bad'}`} />
                      <span className="an-name mono">{tn.node}</span>
                      <span className="an-role">{role.zh}</span>
                      <span className={`an-ready ${n?.ready ? 'ok' : 'bad'}`}>
                        <i className="st-dot" />
                        {!n ? '未上报' : n.ready ? '就绪' : '未就绪'}
                      </span>
                    </div>

                    <div className="arch-chips">
                      {tn.chips.map((c) => (
                        <HealthChip key={c.key} chip={c} health={health} />
                      ))}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        ))}
      </section>

      {extraKeys.length > 0 && (
        <section className="card arch-extra">
          <div className="card-headrow">
            <h3 className="card-title">其它探针</h3>
            <span className="muted xs mono">{extraKeys.length}</span>
          </div>
          <div className="arch-chips">
            {extraKeys.map((k) => (
              <HealthChip key={k} chip={{ key: k, name: k, en: 'PROBE' }} health={health} />
            ))}
          </div>
        </section>
      )}

      <section className="card arch-bus">
        <div className="card-headrow">
          <h3 className="card-title">任务总线 / 调度</h3>
          <span className="muted xs mono">
            队列 {stats?.queue_length ?? 0} · 运行 {stats?.running ?? 0} · 总数 {stats?.total ?? 0}
          </span>
        </div>

        <div className="bus-stats">
          <BusStat label="队列长度" value={stats?.queue_length ?? 0} tone="brass" />
          <BusStat label="运行中" value={stats?.running ?? 0} tone="brass" />
          <BusStat label="任务总数" value={stats?.total ?? 0} />
          <div className="bus-kinds">
            {MODELS.map((m) => (
              <span className="bus-kind" key={m}>
                <i className="bus-kind-dot" style={{ background: MODEL_COLOR[m] }} />
                <b>{MODEL_LABEL[m]}</b>
                <i className="bus-kind-en mono">{MODEL_LABEL[m]}</i>
                <span className="bus-kind-n mono">{byModel[m] ?? 0}</span>
              </span>
            ))}
          </div>
        </div>

        <div className="bus-flows">
          <div className="flow-cols">
            <span>任务类型</span>
            <span>流转</span>
          </div>
          {MODELS.map((m) => (
            <div className="flow-row" key={m}>
              <span className="flow-kind">
                <i className="flow-dot" style={{ background: MODEL_COLOR[m] }} />
                <b>{MODEL_LABEL[m]}</b>
                <i className="flow-en mono">{MODEL_LABEL[m]}</i>
                <span className="flow-count mono">{byModel[m] ?? 0}</span>
              </span>
              <span className="flow-steps">
                {FLOW_STEPS.map((s, i) => (
                  <span className="flow-step" key={s}>
                    <span className="fs-title">{s}</span>
                    <span className="fs-actor mono">{FLOW_ACTORS[m][i]}</span>
                    {i < FLOW_STEPS.length - 1 && <i className="fs-arrow">→</i>}
                  </span>
                ))}
              </span>
            </div>
          ))}
        </div>

        {!stats && <div className="muted xs">任务统计暂不可用，流程结构为静态示意。</div>}
      </section>
    </div>
  );
}

function HealthChip({ chip, health }: { chip: Chip; health: HealthMap | null }) {
  const raw = chip.healthKey ? health?.[chip.healthKey] : undefined;
  const state = healthState(raw);
  const meta = READONLY_LABEL[chip.name];
  const zh = chip.zh || meta?.zh;
  const en = chip.en || meta?.en || 'SERVICE';
  return (
    <div className={`arch-chip ${state}`} title={`${chip.name} · ${healthDetail(raw)}`}>
      <i className="chip-dot" />
      <span className="chip-body">
        <b className="chip-name mono">{chip.name}</b>
        <span className="chip-zh">{zh || ''}<i className="chip-en mono">{en}</i></span>
      </span>
      <span className={`chip-state mono ${state}`}>{state}</span>
    </div>
  );
}

function ArchKpi({
  label, value, tone,
}: { label: string; value: string | number; tone?: 'brass' }) {
  return (
    <div className={`arch-kpi ${tone || ''}`}>
      <div className="akpi-value mono">{value}</div>
      <div className="akpi-label">{label}</div>
    </div>
  );
}

function BusStat({
  label, value, tone,
}: { label: string; value: number; tone?: 'brass' }) {
  return (
    <div className={`bus-stat ${tone || ''}`}>
      <div className="bs-value mono">{value}</div>
      <div className="bs-label">{label}</div>
    </div>
  );
}
