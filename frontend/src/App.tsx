import { HashRouter, Navigate, Route, Routes } from 'react-router-dom';
import Layout from './components/Layout';
import ClusterPage from './pages/ClusterPage';
import HistoryPage from './pages/HistoryPage';
import OverviewPage from './pages/OverviewPage';
import SubmitPage from './pages/SubmitPage';
import TestPage from './pages/TestPage';

export default function App() {
  return (
    <HashRouter>
      <Routes>
        <Route element={<Layout />}>
          <Route path="/" element={<OverviewPage />} />
          <Route path="/submit" element={<SubmitPage />} />
          <Route path="/history" element={<HistoryPage />} />
          <Route path="/test" element={<TestPage />} />
          <Route path="/cluster" element={<ClusterPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
    </HashRouter>
  );
}
