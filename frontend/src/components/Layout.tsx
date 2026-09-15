import { NavLink, Outlet, useLocation } from 'react-router-dom';
import { PLATFORM_NAME, PLATFORM_SLOGAN } from '../terms';

/** 双环字母标：纯内联 SVG，无外部资源 */
function Monogram() {
  return (
    <svg className="brand-glyph" viewBox="0 0 48 48" role="img" aria-label="三级协同医疗推理">
      <circle cx="24" cy="24" r="21" fill="none" stroke="currentColor" strokeWidth="1" opacity="0.32" />
      <circle cx="24" cy="24" r="14" fill="none" stroke="currentColor" strokeWidth="1.4" />
      <path d="M24 3v9M24 36v9M3 24h9M36 24h9" stroke="currentColor" strokeWidth="1" opacity="0.45" />
      <circle cx="24" cy="24" r="3.2" fill="#a67d48" />
    </svg>
  );
}

/** 管理平台导航（纯中文） */
const NAV = [
  { to: '/overview', no: '01', label: '平台总览' },
  { to: '/visual', no: '02', label: '可视化' },
  { to: '/load', no: '03', label: '集群负载' },
  { to: '/test', no: '04', label: '综合测试' },
  { to: '/cluster', no: '05', label: '集群编排' },
];

export default function Layout() {
  const loc = useLocation();
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <Monogram />
          <div className="brand-text">
            <div className="brand-name">管理平台</div>
            <div className="brand-caption">{PLATFORM_NAME}</div>
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

        <div className="nav-rule">
          <span className="eyebrow">前往</span>
        </div>
        <nav className="nav">
          <a className="nav-item" href="../user/">
            <span className="nav-no mono">→</span>
            <span className="nav-label">用户平台</span>
          </a>
          <a className="nav-item" href="../">
            <span className="nav-no mono">→</span>
            <span className="nav-label">首页</span>
          </a>
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
