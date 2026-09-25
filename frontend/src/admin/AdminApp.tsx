import { HashRouter, Navigate, Route, Routes } from 'react-router-dom';
import Layout from '../components/Layout';
import ArchitecturePage from '../pages/ArchitecturePage';
import LoadPage from '../pages/LoadPage';
import PlatformOverviewPage from '../pages/PlatformOverviewPage';

/**
 * 管理平台（/app/admin/）：01 运行总览 · 02 协同轨迹 · 03 集群资源。
 * 集群资源页内含节点 / Pod 负载与编排调度两段。
 * 使用 HashRouter，静态托管（scheduler 挂载 /app）下刷新任意子路由都可用。
 */
export default function AdminApp() {
  return (
    <HashRouter>
      <Routes>
        <Route element={<Layout />}>
          <Route path="/" element={<Navigate to="/overview" replace />} />
          <Route path="/overview" element={<PlatformOverviewPage />} />
          <Route path="/visual" element={<ArchitecturePage />} />
          <Route path="/load" element={<LoadPage />} />
          <Route path="*" element={<Navigate to="/overview" replace />} />
        </Route>
      </Routes>
    </HashRouter>
  );
}
