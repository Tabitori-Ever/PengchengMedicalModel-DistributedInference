import { NavLink, Outlet, useLocation } from 'react-router-dom';
import { PLATFORM_SLOGAN } from '../terms';

/** 管理平台名称（用户侧平台名保持不变，见 terms.PLATFORM_NAME）。 */
export const ADMIN_PLATFORM_NAME = 'AI诊疗云边端协同管控平台';

/** 双环字母标：纯内联 SVG，无外部资源 */
function Monogram() {
  return (
    <svg className="brand-glyph" viewBox="0 0 48 48" role="img" aria-label={ADMIN_PLATFORM_NAME}>
      <circle cx="24" cy="24" r="21" fill="none" stroke="currentColor" strokeWidth="1" opacity="0.32" />
      <circle cx="24" cy="24" r="14" fill="none" stroke="currentColor" strokeWidth="1.4" />
      <path d="M24 3v9M24 36v9M3 24h9M36 24h9" stroke="currentColor" strokeWidth="1" opacity="0.45" />
      <circle cx="24" cy="24" r="3.2" fill="#a67d48" />
    </svg>
  );
}

/** 管理平台导航（纯中文，三段式编号） */
const NAV = [
  { to: '/overview', no: '01', label: '集群总览' },
  { to: '/visual', no: '02', label: '集群架构' },
  { to: '/load', no: '03', label: '集群负载' },
];

export default function Layout() {
  const loc = useLocation();
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <Monogram />
          <div className="brand-text">
            <div className="brand-name">{ADMIN_PLATFORM_NAME}</div>
          </div>
        </div>

        <div className="nav-rule">
          <span className="eyebrow">管理功能</span>
        </div>

        <nav className="nav">
          {NAV.map((n) => (
            <NavLink
              key={n.to}
              to={n.to}
              className={({ isActive }) => 'nav-item' + (isActive ? ' active' : '')}
            >
              <span className="nav-no mono">{n.no}</span>
              <span className="nav-label">{n.label}</span>
            </NavLink>
          ))}
        </nav>

        <div className="sidebar-foot">
          <div className="foot-line">
            <span className="pulse-dot" />
            {PLATFORM_SLOGAN}
          </div>
          <div className="foot-ver mono">v3.0 · 四类任务编排</div>
          <div className="foot-meta">K8S 集群 · 4 节点 · 2 业务 / 1 数据中心 / 1 控制面</div>
        </div>
      </aside>
      <main className="content">
        <Outlet key={loc.pathname} />
      </main>
    </div>
  );
}
