/* ===========================================================================
   三级命名体系 —— 单一来源（数据中心 → 医疗中心 → 医院）

   数据中心  cloud    node3              调度 / 医疗推理 / 患者库 / 队列
   医疗中心  medical  node1 / node2      原 hospital-a / hospital-b（诊断 · 计算 · 通信 · 日常）
   医院      hospital node1 / node2      原 clinic-1 / clinic-2（转诊 · 计算 · 通信 · 日常）

   所有页面与组件的中文命名、能力简述、流转标签都从这里取，避免各自拼写；
   实体 id（hospital-a / clinic-1 …）只作为小字技术标识保留。
   =========================================================================== */

export type TierKey = 'cloud' | 'medical' | 'hospital' | 'ops' | 'control';

/** 层级名称（纯中文，无英文注记） */
export const TIER_LABEL: Record<TierKey, string> = {
  cloud: '数据中心',
  medical: '医疗中心',
  hospital: '医院',
  ops: '运维',
  control: '控制面',
};

/** 层级落在哪台机器上（小字技术标识） */
export const TIER_NODE: Record<TierKey, string> = {
  cloud: 'node3',
  medical: 'node1 / node2',
  hospital: 'node1 / node2',
  ops: 'desktop-jm5iec6',
  control: 'desktop-jm5iec6',
};

/** 层级顺序（自下而上：医院 → 医疗中心 → 数据中心） */
export const TIER_ORDER: TierKey[] = ['hospital', 'medical', 'cloud'];

/** 实体 id → 中文显示名 */
export const ENTITY_NAME: Record<string, string> = {
  'hospital-a': '医疗中心 A',
  'hospital-b': '医疗中心 B',
  'clinic-1': '医院 1',
  'clinic-2': '医院 2',
};

/** 任意实体 id → 中文显示名（编辑器新增的 clinic-N → 医院 N；未知保持原样） */
export function entityName(id?: string | null): string {
  const key = String(id ?? '').trim();
  if (!key) return '—';
  if (ENTITY_NAME[key]) return ENTITY_NAME[key];
  const clinic = /^clinic[-_]?(\d+)$/i.exec(key);
  if (clinic) return `医院 ${Number(clinic[1])}`;
  const hospital = /^hospital[-_]?([a-z])$/i.exec(key);
  if (hospital) return `医疗中心 ${hospital[1].toUpperCase()}`;
  return key;
}

/** 实体 id → 所属层级 */
export function entityTier(id?: string | null): TierKey {
  const key = String(id ?? '').toLowerCase();
  if (key.startsWith('hospital')) return 'medical';
  if (key.startsWith('clinic')) return 'hospital';
  return 'cloud';
}

/** 实体 id → 角色名（医疗中心 / 医院） */
export function entityRole(id?: string | null): string {
  return TIER_LABEL[entityTier(id)];
}

export const CAPTION_MEDICAL = '诊断 · 计算 · 通信 · 日常';
export const CAPTION_HOSPITAL = '转诊 · 计算 · 通信 · 日常';

/** 实体 id → 能力简述 */
export function entityCaption(id?: string | null): string {
  return entityTier(id) === 'hospital' ? CAPTION_HOSPITAL : CAPTION_MEDICAL;
}

/** 数据中心组件说明 */
export const CLOUD_CAPTION: Record<string, string> = {
  scheduler: '调度',
  'medical-server': '医疗推理',
  'dc-services': '患者库 · 协同计算 · 备份',
  redis: '队列 · 记录',
};

export function cloudCaption(name?: string | null): string {
  return CLOUD_CAPTION[String(name ?? '')] || '';
}

/** 任务流转标签 */
export const FLOW = {
  /** 医院 → 医疗中心 */
  referral: '转诊',
  /** 医疗中心 ⇄ 数据中心 */
  medicalCloud: '协同诊断 / 计算 / 同步 / 日常',
  /** 医院 ⇄ 数据中心 */
  hospitalCloud: '协同计算 / 同步 / 日常',
  /** 运维 → 数据中心 */
  opsCloud: '指标观测',
} as const;

/** 四类任务一句话说明（用户平台任务卡片） */
export const KIND_HINT: Record<string, string> = {
  diagnosis: '医疗中心提取医学特征，数据中心完成融合推理，返回 bpCR 概率',
  compute: '发起方与数据中心按分区并行计算，再由数据中心汇总校验',
  sync: '与数据中心及对端同步患者库，并触发数据中心备份',
  routine: '调度器在空闲节点创建一次性任务，输出结果后自动回收',
};

/** 发起端选择提示 */
export const SOURCE_HINT = '发起端可以是医疗中心或医院，任务执行过程对发起方透明';

/** 节点角色文案（node1 / node2 同时承载医疗中心与医院） */
export function nodeRoleText(role?: string | null, name?: string): string {
  const key = String(name ?? '');
  const r = String(role ?? '').toLowerCase();
  if (key === 'node3') return TIER_LABEL.cloud;
  if (key === 'desktop-jm5iec6' || r.includes('control')) return TIER_LABEL.control;
  if (key === 'node1' || key === 'node2' || r === 'edge') {
    return `${TIER_LABEL.medical} · ${TIER_LABEL.hospital}`;
  }
  return TIER_LABEL.cloud;
}

/** 平台名称（供登录页 / 侧栏 / 首页使用） */
export const PLATFORM_NAME = 'AI诊疗云边端协同应用平台';
export const PLATFORM_TAGLINE = '数据中心 · 医疗中心 · 医院 协同推理';
export const PLATFORM_SLOGAN = '数据不出院 · 算力集中化';
