/**
 * 无浏览器环境下的前端渲染自检（用服务端渲染喂真实数据）。
 *
 * 这里没有可用的 Chromium、也没有外网装一个，所以改用 react-dom/server 把
 * 各结果区块**按真实响应数据**渲染成 HTML 字符串，再断言关键字段确实出现。
 * 它能抓到 tsc 抓不到的渲染期缺陷：字段名写错、对 null 取属性、数组没判空、
 * 与后端契约不一致。
 *
 * 数据来源：站点 `/api/plans` 与 `/api/plans/runs/{id}` 的真实响应。
 *
 * 用法（在仓库根目录）：
 *   node test/render_frontend_ssr.mjs .ssrcheck/plans.json .ssrcheck/run.json
 */
import { createRequire } from 'node:module';
import { existsSync, readFileSync, writeFileSync, mkdirSync, rmSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const [plansPath, runPath] = process.argv.slice(2);
if (!plansPath || !runPath) {
  console.error('用法: node test/render_frontend_ssr.mjs <plans.json> <run.json>');
  process.exit(2);
}

/* 脚本可能在 test/ 下，也可能被拷到 benchmark/frontend/ 下执行：按文件位置找根 */
const here = path.dirname(fileURLToPath(import.meta.url));
const root = [
  here,
  path.resolve(here, '..', 'benchmark', 'frontend'),
  path.resolve(here, '..', '..', 'benchmark', 'frontend'),
].find((p) => existsSync(path.join(p, 'src', 'sections', 'PlanSection.tsx')));
if (!root) {
  console.error('找不到 benchmark/frontend/src（请在仓库内运行本脚本）');
  process.exit(2);
}

// 从 frontend 包内解析依赖（bare specifier 按脚本位置解析，不能靠 cwd）
const req = createRequire(path.join(root, 'package.json'));
const { build } = req('esbuild');
const React = req('react');
const { renderToString } = req('react-dom/server');

const plans = JSON.parse(readFileSync(plansPath, 'utf8')).plans;
const run = JSON.parse(readFileSync(runPath, 'utf8'));

// 产物必须落在 frontend 包内，node 才能解析 external 的 react
const out = path.join(root, '.ssr-build');
rmSync(out, { recursive: true, force: true });
mkdirSync(out, { recursive: true });
const entry = path.join(out, 'entry.tsx');

writeFileSync(entry, `
import React from 'react';
import PlanSection from '${root}/src/sections/PlanSection.tsx';
import PlanRunSection from '${root}/src/sections/PlanRunSection.tsx';
import OverviewSection from '${root}/src/sections/OverviewSection.tsx';
import StatsSection from '${root}/src/sections/StatsSection.tsx';
import CompareSection from '${root}/src/sections/CompareSection.tsx';
import { customDraft, draftFromPlan } from '${root}/src/planDraft.ts';
export { PlanSection, PlanRunSection, OverviewSection, StatsSection, CompareSection,
         customDraft, draftFromPlan };
`);

const bundle = path.join(out, 'bundle.mjs');
await build({
  entryPoints: [entry],
  outfile: bundle,
  bundle: true,
  format: 'esm',
  platform: 'node',
  jsx: 'automatic',
  external: ['react', 'react-dom', 'react-dom/server', 'react/jsx-runtime', 'axios'],
  logLevel: 'error',
});

const m = await import(pathToFileURL(bundle).href);
const noop = () => {};
/** React SSR 会在相邻文本节点间插 <!-- -->，断言前先清掉，避免假失败 */
const clean = (html) => html.replace(/<!--.*?-->/g, '');
const planProps = (plan) => ({
  plans, plansErr: '', selectedId: plan.plan_id, draft: m.draftFromPlan(plan),
  planRunId: '', busy: false, submitErr: '',
  onSelectPlan: noop, onCustom: noop, onChangeDraft: noop,
  onResetDraft: noop, onSubmit: noop,
});

const cases = [
  ['PlanSection(§5.4)', () => React.createElement(m.PlanSection, planProps(plans[0])),
    ['5.4 协同调度测试', '5.5 医疗智联专网测试', '自定义实验配置', '任务类型与任务参数',
     '判定标准', '下发方案运行', '每设备任务数', '任务产生窗口']],
  ['PlanSection(§5.5)', () => React.createElement(m.PlanSection, planProps(plans[1])),
    ['5.5 医疗智联专网测试', '测试仪流量注入', '损伤仪路径受损', '丢包率', '200',
     '12 小时']],
  ['PlanSection(自定义)', () => {
    const d = m.customDraft(plans[0]);
    return React.createElement(m.PlanSection, { ...planProps(plans[0]), draft: d });
  }, ['自定义', '失败重传额度', '预置条件', '判定标准']],
  ['PlanRunSection', () => React.createElement(m.PlanRunSection, { run }),
    ['运行进度', '本地执行', '云边端协同', run.plan_run_id, '子运行', '重传']],
  ['OverviewSection', () => React.createElement(m.OverviewSection, { run }),
    ['测试结果总览', '处理总用时', '判定', '完成率', '重传']],
  ['StatsSection', () => React.createElement(m.StatsSection, { run }),
    ['统计信息', '按阶段汇总', '医疗模型远程调度', '医疗数据处理', '患者数据库同步',
     '医疗信息远程查询', '重传']],
  ['CompareSection', () => React.createElement(m.CompareSection, { run }),
    ['性能对比', '本地处理总用时', '协同处理总用时', '差值', '比值']],
  ['空数据不崩', () => React.createElement('div', null,
    React.createElement(m.PlanRunSection, { run: null }),
    React.createElement(m.OverviewSection, { run: null }),
    React.createElement(m.StatsSection, { run: null }),
    React.createElement(m.CompareSection, { run: null })),
    ['运行进度', '测试结果总览', '统计信息', '性能对比']],
];

let failed = 0;
for (const [name, make, expects] of cases) {
  let html = '';
  try {
    html = clean(renderToString(make()));
  } catch (e) {
    console.log(`❌ ${name} 渲染抛异常: ${e && e.message}`);
    failed++;
    continue;
  }
  const missing = expects.filter((k) => !html.includes(k));
  if (missing.length) {
    console.log(`❌ ${name} 渲染成功但缺少内容: ${missing.join(' / ')}`);
    failed++;
  } else {
    console.log(`✅ ${name} 渲染通过（${html.length} 字符）`);
  }
}

/* 数值必须真的渲染出来，而不是全靠 "—" 兜底 */
const stats = clean(renderToString(React.createElement(m.StatsSection, { run })));
const compare = clean(renderToString(React.createElement(m.CompareSection, { run })));
const nDigits = (stats.match(/\d/g) || []).length;
if (nDigits < 40) {
  console.log(`❌ 统计区数字过少（${nDigits} 个），可能字段对不上后端契约`);
  failed++;
} else {
  console.log(`✅ 统计区数字充分（${nDigits} 个数字字符）`);
}
if (!/\d+\.\d+%/.test(compare)) {
  console.log('❌ 对比区没有渲染出百分比差值');
  failed++;
} else {
  console.log('✅ 对比区渲染出百分比差值');
}

/* 硬约束：结果区不得出现说明性文案（页面只展示结果，结论由第三方判读） */
const BANNED = [
  '判定口径', '仅作参考', '窗口提示', '到达速率', '注意口径',
  '与大纲的稀疏到达相比', '方案来自', '按《测试大纲》', '大纲中的端侧计算机',
  '说明：', '参考值', '建议把窗口', '接近连续负载', '会被放大',
  '本报告', '逐类结论可能不同', '不是同一个场景', '口径 =',
];
const allHtml = [
  renderToString(React.createElement(m.PlanRunSection, { run })),
  renderToString(React.createElement(m.OverviewSection, { run })),
  renderToString(React.createElement(m.StatsSection, { run })),
  renderToString(React.createElement(m.CompareSection, { run })),
  ...cases.map(([, make]) => { try { return renderToString(make()); } catch { return ''; } }),
].map(clean).join('\n');
const hit = BANNED.filter((b) => allHtml.includes(b));
if (hit.length) {
  console.log(`❌ 结果区出现说明性文案：${hit.join(' / ')}`);
  failed++;
} else {
  console.log(`✅ 未出现说明性文案（检查了 ${BANNED.length} 个禁用词）`);
}

/* 配置区必须给出预置条件、判定标准与大纲完整描述（可展开） */
const plan54 = clean(renderToString(React.createElement(m.PlanSection, planProps(plans[0]))));
for (const must of ['预置条件', '判定标准', '展开大纲完整描述', '失败重传额度']) {
  if (!plan54.includes(must)) {
    console.log(`❌ 实验配置区缺少「${must}」`);
    failed++;
  }
}

rmSync(out, { recursive: true, force: true });
console.log(failed ? `\n❌ ${failed} 项未通过` : '\n✅ 全部区块按真实数据渲染通过');
process.exit(failed ? 1 : 0);
