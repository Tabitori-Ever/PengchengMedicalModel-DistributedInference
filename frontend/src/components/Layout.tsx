import { NavLink, Outlet, useLocation } from 'react-router-dom';

const NAV = [
  { to: '/', label: '总览', icon: '◈' },
  { to: '/submit', label: '任务提交', icon: '＋' },
  { to: '/history', label: '任务记录', icon: '≡' },
  { to: '/test', label: '综合测试', icon: '⚡' },
  { to: '/cluster', label: '集群编排', icon: '◉' },
];

export default function Layout() {
  const loc = useLocation();
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark">医</div>
          <div>
            <div className="brand-name">云边端协同推理</div>
            <div className="brand-sub">Hospital · Clinic Pods v3.0</div>
          </div>
        </div>
        <nav className="nav">
          {NAV.map((n) => (
            <NavLink
              key={n.to}
              to={n.to}
              className={({ isActive }) =>
                'nav-item' + (isActive ? ' active' : '')}
            >
              <span className="nav-ico">{n.icon}</span>
              {n.label}
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-foot">
          <span className="pulse-dot" />
          数据不出院 · 算力云端化
          <div className="foot-ver">v3.0 四类任务编排</div>
        </div>
      </aside>
      <main className="content">
        <Outlet key={loc.pathname} />
      </main>
    </div>
  );
}
