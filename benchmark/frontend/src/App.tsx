import { useState } from 'react';
import DispatchPage from './pages/DispatchPage';
import ResultsPage from './pages/ResultsPage';
import ComparePage from './pages/ComparePage';
import ConfigPage from './pages/ConfigPage';

type Tab = 'dispatch' | 'results' | 'compare' | 'config';

const TABS: { id: Tab; no: string; zh: string; hint: string }[] = [
  { id: 'dispatch', no: '01', zh: '测试下发', hint: '固定套件 / 单次任务 · 调度策略、发起方与并发' },
  { id: 'results', no: '02', zh: '结果明细', hint: '运行进度、逐次 attempt 指标与正确性' },
  { id: 'compare', no: '03', zh: '性能对比', hint: '云边端协同与本地执行的分档位对比' },
  { id: 'config', no: '04', zh: '实验配置', hint: '可达性、站点参数与数据导出' },
];

export default function App() {
  const [tab, setTab] = useState<Tab>('dispatch');
  // 让「下发 → 结果明细 / 性能对比」能带上刚创建的实验
  const [focusRun, setFocusRun] = useState<string>('');
  const [refreshKey, setRefreshKey] = useState(0);

  const current = TABS.find((t) => t.id === tab) || TABS[0];

  return (
    <div className="shell">
      <aside className="side">
        <div className="brand">
          <div className="brand-name">测试与性能对比平台</div>
          <div className="brand-sub">云边端协同推理</div>
        </div>

        <nav className="nav">
          {TABS.map((t) => (
            <button
              key={t.id}
              className={`nav-item ${tab === t.id ? 'on' : ''}`}
              onClick={() => setTab(t.id)}
            >
              <i className="nav-no">{t.no}</i>
              <span className="nav-zh">{t.zh}</span>
            </button>
          ))}
        </nav>
      </aside>

      <main className="main">
        <header className="page-head">
          <div className="page-head-main">
            <h1>{current.zh}</h1>
            <p>{current.hint}</p>
          </div>
        </header>

        {tab === 'dispatch' && (
          <DispatchPage
            onCreated={(runId) => {
              setFocusRun(runId);
              setRefreshKey((k) => k + 1);
            }}
            onGoto={(t) => setTab(t)}
          />
        )}
        {tab === 'results' && <ResultsPage focusRun={focusRun} />}
        {tab === 'compare' && <ComparePage focusRun={focusRun} refreshKey={refreshKey} />}
        {tab === 'config' && <ConfigPage onChanged={() => setRefreshKey((k) => k + 1)} />}
      </main>
    </div>
  );
}
