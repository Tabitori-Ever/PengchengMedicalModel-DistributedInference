/* ===========================================================================
   登录身份（用户调用平台）

   进入界面之前先选身份：医疗中心 A / 医疗中心 B / 医院 1 / 2 / 3 / 4，共六项，
   顺序固定。这六项离线也必须可用，因此写死在这里而不是依赖集群接口；集群在线时
   可把接口里多出来的实体补在六项之后（见 LoginPanel）。

   身份即任务位置：提交时的 source 始终取当前登录身份（登录后不再提供位置选择）。
   **不做持久化**：每次打开/刷新页面都要重新选择身份（同一页面会话内保持登录态）。
   =========================================================================== */

import type { SourceId } from '../types';

/** 平台身份 id：四种基础身份 + 登录面板要求新增的 clinic-3 / clinic-4 */
export type IdentityId = SourceId | 'clinic-3' | 'clinic-4';

/** 固定六项身份（顺序即登录面板的展示顺序） */
export const IDENTITIES: IdentityId[] = [
  'hospital-a',
  'hospital-b',
  'clinic-1',
  'clinic-2',
  'clinic-3',
  'clinic-4',
];

/** 登录态存储键 */
export const IDENTITY_KEY = 'dsh.user.identity';

export function isIdentity(v: unknown): v is IdentityId {
  return typeof v === 'string' && (IDENTITIES as string[]).includes(v);
}

function safeStorage(): Storage | null {
  try {
    return typeof localStorage !== 'undefined' ? localStorage : null;
  } catch {
    return null;
  }
}

/** 读取已保存的身份：按需求**不持久化**，永远返回 null（每次访问都重新登录） */
export function loadIdentity(_storage: Storage | null = safeStorage()): IdentityId | null {
  void _storage;
  return null;
}

/** 兼容旧调用：不再写入任何存储（登录态只存在于当前页面内存中） */
export function saveIdentity(id: IdentityId | null, storage: Storage | null = safeStorage()): void {
  void id; void storage;
}
