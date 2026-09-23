/* 离线渲染核对：把真实 API 返回喂进展示层，检查输出里是否出现预期数字/文案。
   期望值全部由传入的 API 数据推导，避免写死数字。 */
import { readFileSync } from 'node:fs';
import { renderToStaticMarkup } from 'react-dom/server';
import App from '../src/App';
import DispatchPage, { SuiteSpecTable } from '../src/pages/DispatchPage';
import ResultsPage from '../src/pages/ResultsPage';
import ComparePage from '../src/pages/ComparePage';
import ConfigPage from '../src/pages/ConfigPage';
import { SuiteCompareView } from '../src/components/SuiteCompare';
import { SuiteBars } from '../src/components/Charts';
import { AttemptTable, AttemptStats, ProgressBar } from '../src/components/AttemptTable';
import type { Attempt, SuiteAgg, SuiteCompareResp, SuiteInfo } from '../src/types';

const noop = () => {};
const cmp = JSON.parse(readFileSync(process.argv[2], 'utf8')) as SuiteCompareResp;
const suites = JSON.parse(readFileSync(process.argv[3], 'utf8')) as SuiteInfo[];
const run = JSON.parse(readFileSync(process.argv[4], 'utf8'));

const ms1 = (v: number) => `${v.toFixed(1)} ms`;
const ms0 = (v: number) => `${v.toFixed(0)} ms`;
const text = (html: string) => html.replace(/<[^>]+>/g, ' ').replace(/&nbsp;/g, ' ').replace(/\s+/g, ' ').trim();

const local = (cmp.overall?.local || {}) as SuiteAgg;
const configs = cmp.configs || [];
const attempts = (run.attempts || []) as Attempt[];
const suite = suites[0];
const BANNED = ['BENCHMARK', '时延权威口径', '站点独立部署', 'API 基址', '强制降级', '实时状态', 'NodePort'];

const checks: [string, string, string[]][] = [];

/* 1) 套件对比展示层 —— 真实返回（当前只有本地执行） */
const view = renderToStaticMarkup(
  <SuiteCompareView data={cmp} suites={suites} suiteId={cmp.suite_id || ''} source="clinic-1" />,
);
checks.push(['套件对比（真实数据：仅本地执行）', text(view), [
  '数据不足，需两种策略各跑一次套件',
  `本地 n=${local.n}`,
  ms1(local.mean as number),
  ...configs.map((c) => ms1((c.modes?.local as SuiteAgg).mean as number)),
  ...configs.map((c) => String(c.label)),
]]);

/* 2) 分档位柱状图 —— 真实 configs */
const chart = renderToStaticMarkup(<SuiteBars configs={configs} />);
checks.push(['分档位柱状图（真实数据）', text(chart), [
  '云边端协同', '本地执行', 'p95',
  ...configs.map((c) => `n=0/${(c.modes?.local as SuiteAgg).n}`),
  ...configs.map((c) => `仪器 ${c.params?.instruments} · 行数 ${c.params?.rows}`),
]]);

/* 3) speedup 分支（协同数字为合成展示层用例，未在集群跑协同套件） */
const synthLevelAgg: SuiteAgg = { n: 4, mean: 22, p50: 21, p95: 30, min: 18, max: 31, success: 4, fail: 0 };
const synth: SuiteCompareResp = {
  ...cmp,
  overall: {
    collaborative: { n: 20, mean: 600, p50: 400, p95: 1900, min: 12, max: 1900, success: 20, fail: 0 },
    local,
  },
  configs: configs.map((c) => ({ ...c, modes: { ...c.modes, collaborative: synthLevelAgg } })),
  speedup: { collaborative_mean_ms: 600, local_mean_ms: local.mean, collaborative_faster_pct: 35.22, ratio: 1.544 },
};
const view2 = renderToStaticMarkup(
  <SuiteCompareView data={synth} suites={suites} suiteId={cmp.suite_id || ''} source="clinic-1" />,
);
const lvl0 = (configs[0].modes?.local as SuiteAgg).mean as number;
checks.push(['套件对比（speedup 分支，协同数字为合成）', text(view2), [
  `协同平均 ${ms1(600)}`, `本地平均 ${ms1(local.mean as number)}`, '协同快 35.2%', '本地 / 协同 = 1.54 倍',
  `协同快 ${(((lvl0 - 22) / lvl0) * 100).toFixed(1)}%`,
]]);

/* 4) 档位清单（真实 GET /api/suites）：表头 + 每档参数原样呈现 */
if (suite) {
  const spec = renderToStaticMarkup(<SuiteSpecTable suite={suite} />);
  checks.push(['档位清单（真实 /api/suites）', text(spec), [
    '档位 仪器 行数 强度 分区 重复次数',
    ...suite.specs.map((s) => `${s.label} ${s.params.instruments} ${s.params.rows} ${s.params.intensity} ${s.params.partition_count} ${s.repeats}`),
  ]]);
}

/* 5) 真实 run 的进度与逐次明细 */
const detail = renderToStaticMarkup(
  <>
    <ProgressBar finished={run.progress.completed} total={run.progress.total} />
    <AttemptStats attempts={attempts} />
    <AttemptTable attempts={attempts} />
  </>,
);
checks.push(['进度 + 逐次明细（真实 attempt）', text(detail), [
  `完成 ${run.progress.completed} / ${run.progress.total}`,
  ...attempts.slice(0, 20).map((a) => ms0(a.client_total_ms as number)),
  '本地', 'clinic-1', `n=${attempts.length}`,
]]);

/* 6) 四个视图的初始渲染冒烟（无浏览器，仅静态渲染）＋ 禁用文案检查 */
const pages: [string, string][] = [
  ['App', renderToStaticMarkup(<App />)],
  ['DispatchPage', renderToStaticMarkup(<DispatchPage onCreated={noop} onGoto={noop} />)],
  ['ResultsPage', renderToStaticMarkup(<ResultsPage focusRun="run-x" />)],
  ['ComparePage', renderToStaticMarkup(<ComparePage focusRun="run-x" refreshKey={0} />)],
  ['ConfigPage', renderToStaticMarkup(<ConfigPage onChanged={noop} />)],
];
for (const [name, html] of pages) {
  const t = text(html);
  const hit = BANNED.filter((b) => t.includes(b));
  checks.push([`视图渲染冒烟：${name}`, t, hit.length ? hit.map((h) => `!!${h}`) : []]);
}

checks.push(['导航与页头文案', text(pages[0][1]), [
  '01 测试下发', '02 结果明细', '03 性能对比', '04 实验配置',
]]);

let failed = 0;
for (const [name, got, wants] of checks) {
  const miss = wants.filter((w) => !got.includes(w));
  const ok = miss.length === 0;
  if (!ok) failed += 1;
  console.log(`\n[${ok ? 'PASS' : 'FAIL'}] ${name}`);
  console.log('   ' + got.slice(0, 700));
  if (miss.length) console.log('   MISSING: ' + JSON.stringify(miss));
}
console.log(`\n=== ${checks.length - failed}/${checks.length} checks passed ===`);
process.exit(failed === 0 ? 0 : 1);
