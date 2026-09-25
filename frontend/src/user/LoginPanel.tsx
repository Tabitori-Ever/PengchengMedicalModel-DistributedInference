import { useEffect, useState } from 'react';
import { api } from '../api';
import { PLATFORM_NAME, PLATFORM_TAGLINE, entityName } from '../terms';
import type { IdentityId } from './identity';
import { IDENTITIES, isIdentity } from './identity';

/**
 * 登录面板：进入主界面之前先选身份。
 *
 * 六项固定身份（医疗中心 A/B、医院 1/2/3/4）按固定顺序展示，离线也始终可用；
 * 集群在线时把 /cluster/status 里多出来的实体补在六项之后。点一项选中，
 * 再点「进入平台」才进入（选中结果由父级写入 localStorage）。
 * 身份显示用 entityName()（中文名），原始 id 作为小字 mono 注释。
 */
export default function LoginPanel({ onEnter }: { onEnter: (id: IdentityId) => void }) {
  const [picked, setPicked] = useState<IdentityId | null>(null);
  const [extra, setExtra] = useState<IdentityId[]>([]);

  // 集群在线时补充六项之外的实体；离线或失败则只用固定六项
  useEffect(() => {
    let disposed = false;
    api.clusterStatus()
      .then((st) => {
        if (disposed) return;
        const ids = Object.keys(st?.entities || {}).filter(isIdentity) as IdentityId[];
        setExtra(ids.filter((id) => !IDENTITIES.includes(id)));
      })
      .catch(() => { /* 离线：仅固定六项 */ });
    return () => { disposed = true; };
  }, []);

  const options: IdentityId[] = [...IDENTITIES, ...extra];

  return (
    <div className="up-login">
      <section className="up-login-card" aria-label="选择身份">
        <h1 className="up-login-title">{PLATFORM_NAME}</h1>
        <p className="up-login-hint">{`${PLATFORM_TAGLINE} · 请选择登录身份`}</p>

        <div className="up-login-opts" role="radiogroup" aria-label="登录身份">
          {options.map((id) => (
            <button
              key={id}
              type="button"
              role="radio"
              aria-checked={picked === id}
              className={picked === id ? 'up-login-opt on' : 'up-login-opt'}
              onClick={() => setPicked(id)}
            >
              <b className="up-login-opt-name">{entityName(id)}</b>
              <i className="up-login-opt-id mono">{id}</i>
            </button>
          ))}
        </div>

        <button
          type="button"
          className="btn primary up-login-enter"
          disabled={!picked}
          onClick={() => { if (picked) onEnter(picked); }}
        >
          进入平台
        </button>
      </section>
    </div>
  );
}
