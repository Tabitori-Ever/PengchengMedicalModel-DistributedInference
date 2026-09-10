import { useCallback, useEffect, useMemo, useState } from 'react';
import { api } from '../api';
import TaskTrajectoryMap from '../components/TaskTrajectoryMap';
import { useTicker } from '../hooks';
import type { ClusterSummary, HealthMap, NodeInfo, TaskItem, TaskStats } from '../types';
import {
  healthDetail, healthState, loadColor, MODEL_COLOR, MODEL_EN, MODEL_LABEL, MODELS,
  nodeLoad, nodeRoleLabel, READONLY_LABEL, sortNodes,
} from '../utils';

const REFRESH_MS = 5000;

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
  no: string;
  zh: string;
  en: string;
  desc: string;
  nodes: TierNode[];
}

const TIERS: Tier[] = [
  {
    no: '01',
    zh: '云 Data Center',
    en: 'CLOUD TIER',
    desc: '集中算力与状态：调度、推理服务、数据中心与缓存',
    nodes: [{
      node: 'node3',
      chips: [
        { key: 'scheduler', name: 'scheduler', en: 'SCHEDULER', healthKey: 'scheduler', zh: '任务调度器' },
        { key: 'medical-server', name: 'medical-server', en: 'MEDICAL SERVER', healthKey: 'medical_server', zh: '医学推理服务' },
        { key: 'dc-services', name: 'dc-services', en: 'DC SERVICES', healthKey: 'datacenter(patient-db)', zh: '数据中心 / 患者库' },
        { key: 'redis', name: 'redis', en: 'STATE STORE', healthKey: 'redis', zh: '任务状态库' },
      ],
    }],
  },
  {
    no: '02',
    zh: '边 hospital',
    en: 'EDGE TIER',
    desc: '医院 Pod：诊断前端与协同计算分区，数据不出院',
    nodes: [
      { node: 'node1', chips: [{ key: 'hospital-a', name: 'hospital-a', en: 'HOSPITAL · EDGE', healthKey: 'hospital-a', zh: '医院 Pod A' }] },
      { node: 'node2', chips: [{ key: 'hospital-b', name: 'hospital-b', en: 'HOSPITAL · EDGE', healthKey: 'hospital-b', zh: '医院 Pod B' }] },
    ],
  },
  {
    no: '03',
    zh: '端 clinic',
    en: 'TERMINAL TIER',
    desc: '诊所 Pod：转诊发起、患者库同步与日常例行任务',
    nodes: [
      { node: 'node1', chips: [{ key: 'clinic-1', name: 'clinic-1', en: 'CLINIC · TERMINAL', healthKey: 'clinic-1', zh: '诊所 Pod 1' }] },
      { node: 'node2', chips: [{ key: 'clinic-2', name: 'clinic-2', en: 'CLINIC · TERMINAL', healthKey: 'clinic-2', zh: '诊所 Pod 2' }] },
    ],
  },
];

const FLOW_STEPS = ['① 发起', '② 调度', '③ 协同执行(边/云)', '④ 结果记录'];

const FLOW_ACTORS: Record<string, string[]> = {
  diagnosis: ['医院 / 诊所 Pod 发起 bpCR', 'scheduler 入队 + 选院转诊', '边 worker 提特征 → 云 server 融合', 'task.result 落库 · 任务记录'],
  compute: ['边 / 端 提交分区参数', 'scheduler 切分 N 个分区', '边 + 云 分区并行计算', '云端聚合 checksum'],
  sync: ['端侧发现缺失条目', 'scheduler 分配带宽 / 并发', '端 ↔ 边 / 云 P2P 拉取', '云端全量备份'],
  routine: ['边 / 端 例行触发', 'scheduler 选空闲边缘节点', '边 一次性 K8s Job 执行', '输出 JSON 后立即回收'],
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
  const [errs, setErrs] = useState<string[]>([]);
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null);
  const [countdown, setCountdown] = useState(REFRESH_MS / 1000);

  const refresh = useCallback(async () => {
    const problems: string[] = [];
    const [s, t, h, ts] = await Promise.all([
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
      api.listTasks().catch((e) => {
        problems.push(`任务列表 /tasks 不可用：${errText(e)}`);
        return null;
      }),
    ]);
    if (s) setSummary(s);
    if (t) setStats(t);
    if (h) setHealth(h);
    if (ts) setTasks(ts);
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
          <h1>实时架构 <span className="ver-tag">LIVE · {REFRESH_MS / 1000}s</span></h1>
          <p>
            当前平台真实拓扑：云（node3 调度 / 推理 / 数据中心）、边（node1 / node2 医院 Pod）、
            端（node1 / node2 诊所 Pod）。顶部轨迹图按任务类型实时绘制执行链路，节点负载与探针状态每 {REFRESH_MS / 1000} 秒自动刷新。
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

      <TaskTrajectoryMap tasks={tasks} />

      <section className="card arch-summary">
        <div className="card-headrow">
          <h3 className="card-title">节点与实体</h3>
          <span className="muted xs mono">
            {summary?.version ? `v${summary.version}` : '—'}
          </span>
        </div>
        <div className="arch-kpis">
          <ArchKpi label="节点就绪" value={`${readyNodes}/${nodes.length}`} en="NODES READY" />
          <ArchKpi label="可编排实体" value={summary?.editable ?? '—'} en="EDITABLE" />
          <ArchKpi label="业务 Pod" value={summary?.editable_pods ?? '—'} en="APP PODS" />
          <ArchKpi label="只读 Pod" value={summary?.readonly_pods ?? '—'} en="INFRA PODS" />
          <ArchKpi label="队列长度" value={stats?.queue_length ?? 0} en="QUEUE" tone="brass" />
          <ArchKpi label="运行中任务" value={stats?.running ?? 0} en="RUNNING" tone="brass" />
          <ArchKpi label="任务总数" value={stats?.total ?? 0} en="TOTAL" />
        </div>
      </section>

      <section className="arch-tiers">
        {TIERS.map((tier) => (
          <div className="card arch-tier" key={tier.no}>
            <header className="arch-tier-head">
              <span className="tier-no mono">{tier.no}</span>
              <span className="tier-name">{tier.zh}</span>
              <span className="tier-en mono">{tier.en}</span>
              <span className="tier-desc muted xs">{tier.desc}</span>
            </header>
            <div className="arch-tier-body">
              {tier.nodes.map((tn) => {
                const n = nodeByName[tn.node];
                const role = nodeRoleLabel(n?.role, tn.node);
                const pct = nodeLoad(n);
                const cpu = Number(n?.load?.cpu || 0);
                const mem = Number(n?.load?.memory || 0);
                return (
                  <div className="arch-node" key={`${tier.no}-${tn.node}`}>
                    <div className="arch-node-head">
                      <span className="an-flag" style={{ background: loadColor(pct) }} />
                      <span className="an-name mono">{tn.node}</span>
                      <span className="an-role">
                        {role.zh}
                        <i className="an-role-en mono">{role.en}</i>
                      </span>
                      <span className={`an-ready ${n?.ready ? 'ok' : 'bad'}`}>
                        <i className="st-dot" />
                        {!n ? '未上报' : n.ready ? 'Ready' : 'NotReady'}
                      </span>
                      <span className="an-load mono">
                        CPU {Math.round(cpu * 100)}% · 内存 {Math.round(mem * 100)}%
                      </span>
                    </div>

                    <div className="arch-loadbars">
                      <span className="al-cell">
                        <i className="al-lbl mono">CPU</i>
                        <span className="mini-bar">
                          <i style={{ width: `${Math.min(cpu, 1) * 100}%`, background: loadColor(cpu) }} />
                        </span>
                      </span>
                      <span className="al-cell">
                        <i className="al-lbl mono">内存</i>
                        <span className="mini-bar">
                          <i style={{ width: `${Math.min(mem, 1) * 100}%`, background: loadColor(mem) }} />
                        </span>
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
          <BusStat label="队列长度" en="QUEUE LENGTH" value={stats?.queue_length ?? 0} tone="brass" />
          <BusStat label="运行中" en="RUNNING" value={stats?.running ?? 0} tone="brass" />
          <BusStat label="任务总数" en="TOTAL" value={stats?.total ?? 0} />
          <div className="bus-kinds">
            {MODELS.map((m) => (
              <span className="bus-kind" key={m}>
                <i className="bus-kind-dot" style={{ background: MODEL_COLOR[m] }} />
                <b>{MODEL_LABEL[m]}</b>
                <i className="bus-kind-en mono">{MODEL_EN[m]}</i>
                <span className="bus-kind-n mono">{byModel[m] ?? 0}</span>
              </span>
            ))}
          </div>
        </div>

        <div className="bus-flows">
          <div className="flow-cols">
            <span>任务类型 KIND</span>
            <span>流转 FLOW</span>
          </div>
          {MODELS.map((m) => (
            <div className="flow-row" key={m}>
              <span className="flow-kind">
                <i className="flow-dot" style={{ background: MODEL_COLOR[m] }} />
                <b>{MODEL_LABEL[m]}</b>
                <i className="flow-en mono">{MODEL_EN[m]}</i>
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
  label, value, en, tone,
}: { label: string; value: string | number; en: string; tone?: 'brass' }) {
  return (
    <div className={`arch-kpi ${tone || ''}`}>
      <div className="akpi-value mono">{value}</div>
      <div className="akpi-label">{label}</div>
      <div className="akpi-en mono">{en}</div>
    </div>
  );
}

function BusStat({
  label, en, value, tone,
}: { label: string; en: string; value: number; tone?: 'brass' }) {
  return (
    <div className={`bus-stat ${tone || ''}`}>
      <div className="bs-value mono">{value}</div>
      <div className="bs-label">{label}</div>
      <div className="bs-en mono">{en}</div>
    </div>
  );
}
