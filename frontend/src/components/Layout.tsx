import { NavLink, Outlet, useLocation } from 'react-router-dom';

/** Double-ring monogram — pure SVG, no external assets. */
function Monogram() {
  return (
    <svg className="brand-glyph" viewBox="0 0 48 48" role="img" aria-label="云边端协同推理">
      <circle cx="24" cy="24" r="21" fill="none" stroke="currentColor" strokeWidth="1" opacity="0.32" />
      <circle cx="24" cy="24" r="14" fill="none" stroke="currentColor" strokeWidth="1.4" />
      <path d="M24 3v9M24 36v9M3 24h9M36 24h9" stroke="currentColor" strokeWidth="1" opacity="0.45" />
      <circle cx="24" cy="24" r="3.2" fill="#a67d48" />
    </svg>
  );
}

const NAV = [
  { to: '/', no: '01', label: '总览' },
  { to: '/architecture', no: '02', label: '实时架构' },
  { to: '/submit', no: '03', label: '任务提交' },
  { to: '/history', no: '04', label: '任务记录' },
  { to: '/test', no: '05', label: '综合测试' },
  { to: '/cluster', no: '06', label: '集群编排' },
];

export default function Layout() {
  const loc = useLocation();
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <Monogram />
          <div className="brand-text">
            <div className="brand-name">云边端协同推理</div>
            <div className="brand-caption">云边端协同推理平台</div>
          </div>
        </div>

        <div className="nav-rule">
          <span className="eyebrow">控制台</span>
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
            数据不出院 · 算力云端化
          </div>
          <div className="foot-ver mono">v3.0 · 四类任务编排</div>
          <div className="foot-meta mono">K8S 集群 · 2 边 / 1 云 / 1 控制面</div>
        </div>
      </aside>
      <main className="content">
        <Outlet key={loc.pathname} />
      </main>
    </div>
  );
}
