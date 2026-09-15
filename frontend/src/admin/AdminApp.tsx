import { HashRouter, Navigate, Route, Routes } from 'react-router-dom';
import Layout from '../components/Layout';
import ArchitecturePage from '../pages/ArchitecturePage';
import ClusterPage from '../pages/ClusterPage';
import LoadPage from '../pages/LoadPage';
import TestPage from '../pages/TestPage';

/**
 * 管理平台（/app/admin/）：可视化 · 集群负载 · 综合测试 · 集群编排。
 * 使用 HashRouter，静态托管（scheduler 挂载 /app）下刷新任意子路由都可用。
 */
export default function AdminApp() {
  return (
    <HashRouter>
      <Routes>
        <Route element={<Layout />}>
          <Route path="/" element={<Navigate to="/visual" replace />} />
          <Route path="/visual" element={<ArchitecturePage />} />
          <Route path="/load" element={<LoadPage />} />
          <Route path="/test" element={<TestPage />} />
          <Route path="/cluster" element={<ClusterPage />} />
          <Route path="*" element={<Navigate to="/visual" replace />} />
        </Route>
      </Routes>
    </HashRouter>
  );
}
