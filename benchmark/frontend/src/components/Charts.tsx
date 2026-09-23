import type { CompareRow, SuiteAgg, SuiteConfigRow } from '../api';
import { aggNum } from '../api';
import { MODE_ZH, modeColor } from '../types';
import { fmtMs, nLabel } from './format';

/**
 * 分组柱状图：同一 (kind, source) 下 协同 / 本地 / 降级 的 client_total_ms。
 * 纵向柱 = mean；p50 与 p95 用同色系标记线画在 mean 之上（p95 为空心高帽）。
 * 全部为手写 SVG + CSS，不引入任何图表库。数据只来自 /api/compare。
 */
export default function GroupedBars({ rows }: { rows: CompareRow[] }) {
  const withData = rows.filter((r) => r.mean !== null && r.n > 0);
  if (withData.length === 0) {
    return <div className="empty">暂无数据，请先下发测试任务</div>;
  }

  const W = 720;
  const H = 300;
  const padL = 78;
  const padR = 22;
  const padT = 26;
  const padB = 74;
  const plotW = W - padL - padR;
  const plotH = H - padT - padB;

  const maxVal = Math.max(
    1,
    ...withData.map((r) => Math.max(r.mean ?? 0, r.p95 ?? 0, r.max ?? 0)),
  );
  // 取整到「好看的」刻度
  const step = niceStep(maxVal / 4);
  const top = Math.ceil(maxVal / step) * step;

  const y = (v: number) => padT + plotH - (v / top) * plotH;

  const slot = plotW / withData.length;
  const barW = Math.min(96, Math.max(34, slot * 0.52));
  const ticks = Array.from({ length: 5 }, (_, i) => i * step);

  return (
    <div className="chart-wrap">
      <svg viewBox={`0 0 ${W} ${H}`} className="chart" role="img" aria-label="各模式 client_total_ms 分组柱状图">
        {/* 网格 + y 轴（带单位） */}
        {ticks.map((t, i) => (
          <g key={i}>
            <line x1={padL} x2={W - padR} y1={y(t)} y2={y(t)} className="grid" />
            <text x={padL - 10} y={y(t) + 4} className="axis" textAnchor="end">
              {t.toFixed(0)} ms
            </text>
          </g>
        ))}
        <line x1={padL} x2={W - padR} y1={y(0)} y2={y(0)} className="axis-line" />

        {withData.map((r, i) => {
          const cx = padL + slot * i + slot / 2;
          const x = cx - barW / 2;
          const m = r.mean as number;
          const h = Math.max(1, (m / top) * plotH);
          const color = modeColor(r.bucket);
          return (
            <g key={r.key}>
              <rect x={x} y={y(m)} width={barW} height={h} fill={color} opacity={0.9} />
              {/* mean 数值 */}
              <text x={cx} y={y(m) - 8} className="barval" textAnchor="middle">
                {fmtMs(m, 0)}
              </text>
              {/* p50 标记 */}
              {r.p50 !== null && (
                <g>
                  <line x1={x - 5} x2={x + barW + 5} y1={y(r.p50)} y2={y(r.p50)} className="mark-p50" />
                  <text x={x + barW + 8} y={y(r.p50) + 3} className="mark-lbl">p50</text>
                </g>
              )}
              {/* p95 标记 */}
              {r.p95 !== null && (
                <g>
                  <line x1={x - 5} x2={x + barW + 5} y1={y(r.p95)} y2={y(r.p95)} className="mark-p95" />
                  <text x={x + barW + 8} y={y(r.p95) + 3} className="mark-lbl">p95</text>
                </g>
              )}
              {/* 轴标签 */}
              <text x={cx} y={padT + plotH + 20} className="xlab" textAnchor="middle">
                {MODE_ZH[r.bucket] || r.bucket}
              </text>
              <text x={cx} y={padT + plotH + 36} className="xlab-n" textAnchor="middle">
                {nLabel(r.n, r.success)}
              </text>
              <text x={cx} y={padT + plotH + 50} className="xlab-n" textAnchor="middle">
                {r.executor ? `执行者 ${r.executor}` : '执行者 —'}
              </text>
            </g>
          );
        })}
      </svg>

      <div className="chart-legend">
        {withData.map((r) => (
          <span key={r.key} className="lg-item">
            <i className="lg-sw" style={{ background: modeColor(r.bucket) }} />
            {MODE_ZH[r.bucket] || r.bucket}
            {r.degraded ? '（降级）' : ''}
            <i className="muted">{nLabel(r.n, r.success)}</i>
          </span>
        ))}
        <span className="lg-item"><i className="lg-line p50" />p50</span>
        <span className="lg-item"><i className="lg-line p95" />p95</span>
        <span className="lg-note">纵轴单位：ms · 柱高 = mean（client_total_ms）</span>
      </div>
    </div>
  );
}

/**
 * 分阶段堆叠条：compute / network / queue 三段占该模式 client_total_ms 的比例。
 * 缺哪一段就显示「未知」，不补 0 以掩盖缺失。
 */
export function StackedBreakdown({ rows }: { rows: CompareRow[] }) {
  const withData = rows.filter((r) => r.mean !== null && r.n > 0);
  if (withData.length === 0) {
    return <div className="empty">暂无数据，请先下发测试任务</div>;
  }
  const maxTotal = Math.max(
    1,
    ...withData.map((r) => (r.compute ?? 0) + (r.network ?? 0) + (r.queue ?? 0)),
  );

  return (
    <div className="stack-wrap">
      <div className="stack-head">
        <span className="lg-item"><i className="lg-sw sw-compute" />计算 compute</span>
        <span className="lg-item"><i className="lg-sw sw-network" />网络 network</span>
        <span className="lg-item"><i className="lg-sw sw-queue" />排队 queue</span>
        <span className="lg-note">各段为该模式均值（ms），横条长度按各段之和归一</span>
      </div>
      {withData.map((r) => {
        const c = r.compute;
        const nw = r.network;
        const q = r.queue;
        const sum = (c ?? 0) + (nw ?? 0) + (q ?? 0);
        const scale = sum > 0 ? (100 / maxTotal) : 0;
        const missing = c === null || nw === null || q === null;
        return (
          <div className="stack-row" key={r.key}>
            <div className="stack-lbl">
              <span className="st-mode" style={{ color: modeColor(r.bucket) }}>
                {MODE_ZH[r.bucket] || r.bucket}
              </span>
              <i className="muted">{nLabel(r.n, r.success)}</i>
            </div>
            <div className="stack-track">
              {c !== null && c > 0 && (
                <span className="seg seg-compute" style={{ width: `${(c / maxTotal) * 100}%` }}
                  title={`计算 ${fmtMs(c)}`}>{fmtMs(c, 0)}</span>
              )}
              {nw !== null && nw > 0 && (
                <span className="seg seg-network" style={{ width: `${(nw / maxTotal) * 100}%` }}
                  title={`网络 ${fmtMs(nw)}`}>{fmtMs(nw, 0)}</span>
              )}
              {/* queue 段单独渲染；本地执行恒为 0（§3） */}
              <span className="seg seg-queue" style={{ width: `${((q ?? 0) / maxTotal) * 100}%` }}
                title={q === null ? '排队时间未知' : `排队 ${fmtMs(q)}`}>
                {q !== null && q > 0 ? fmtMs(q, 0) : ''}
              </span>
              {scale > 0 && missing ? <span className="seg-missing" title="部分阶段未返回">*</span> : null}
            </div>
            <div className="stack-sum">
              {sum > 0 ? `合计 ${fmtMs(sum, 0)}` : '—'}
              {q === 0 ? <i className="muted">本地执行无排队</i> : null}
            </div>
          </div>
        );
      })}
      <div className="stack-foot">
        注：协同含排队等待（queue_wait_ms），本地执行恒为 0。
      </div>
    </div>
  );
}

function niceStep(raw: number): number {
  if (raw <= 0) return 1;
  const pow = Math.pow(10, Math.floor(Math.log10(raw)));
  const n = raw / pow;
  const mult = n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10;
  return mult * pow;
}

/* ------------------------------------------------ 固定套件：分档位分组柱状图 */

function isNum(v: number | null): v is number {
  return typeof v === 'number' && Number.isFinite(v);
}

function paramsText(p: Record<string, unknown> | undefined): string {
  if (!p) return '—';
  const parts: string[] = [];
  if (p.instruments !== undefined) parts.push(`仪器 ${p.instruments}`);
  if (p.rows !== undefined) parts.push(`行数 ${p.rows}`);
  if (p.intensity !== undefined) parts.push(`强度 ${p.intensity}`);
  if (p.partition_count !== undefined) parts.push(`分区 ${p.partition_count}`);
  return parts.length ? parts.join(' · ') : '—';
}

interface SuiteBarRow {
  key: string;
  label: string;
  paramsText: string;
  collab: SuiteAgg | null;
  local: SuiteAgg | null;
}

/**
 * 固定套件的分档位分组柱状图：每个档位两根柱子（云边端协同 / 本地执行）的 mean，
 * 带 p95 标记线。手写 SVG，不引入任何图表库。
 */
export function SuiteBars({ configs }: { configs: SuiteConfigRow[] }) {
  const rows: SuiteBarRow[] = configs.map((c, i) => ({
    key: String(c.spec_key || `${c.label || 'cfg'}-${i}`),
    label: String(c.label || `档位 ${i + 1}`),
    paramsText: paramsText(c.params),
    collab: (c.modes?.collaborative as SuiteAgg | undefined) ?? null,
    local: (c.modes?.local as SuiteAgg | undefined) ?? null,
  }));

  const values = rows
    .flatMap((r) => [aggNum(r.collab?.mean), aggNum(r.local?.mean), aggNum(r.collab?.p95), aggNum(r.local?.p95)])
    .filter(isNum);
  if (rows.length === 0 || values.length === 0) {
    return <div className="empty">暂无数据，两种策略各跑一次套件后即可对比</div>;
  }

  const W = 780;
  const H = 330;
  const padL = 84;
  const padR = 24;
  const padT = 30;
  const padB = 70;
  const plotW = W - padL - padR;
  const plotH = H - padT - padB;

  const maxVal = Math.max(1, ...values);
  const step = niceStep(maxVal / 4);
  const top = Math.ceil(maxVal / step) * step;
  const y = (v: number) => padT + plotH - (v / top) * plotH;
  const ticks = Array.from({ length: 5 }, (_, i) => i * step);

  const slot = plotW / rows.length;
  const groupW = Math.min(slot * 0.66, 200);
  const barW = Math.min(58, (groupW - 12) / 2);

  const nOf = (a: SuiteAgg | null) => aggNum(a?.n) ?? 0;

  return (
    <div className="chart-wrap">
      <svg viewBox={`0 0 ${W} ${H}`} className="chart" role="img"
        aria-label="固定套件分档位：协同与本地执行 client_total_ms 的 mean 对比">
        {ticks.map((t, i) => (
          <g key={i}>
            <line x1={padL} x2={W - padR} y1={y(t)} y2={y(t)} className="grid" />
            <text x={padL - 10} y={y(t) + 4} className="axis" textAnchor="end">{t.toFixed(0)} ms</text>
          </g>
        ))}
        <line x1={padL} x2={W - padR} y1={y(0)} y2={y(0)} className="axis-line" />

        {rows.map((r, i) => {
          const cx = padL + slot * i + slot / 2;
          const total = barW * 2 + 12;
          const x0 = cx - total / 2;
          const bars: { agg: SuiteAgg | null; x: number; mode: string }[] = [
            { agg: r.collab, x: x0, mode: 'collaborative' },
            { agg: r.local, x: x0 + barW + 12, mode: 'local' },
          ];
          return (
            <g key={r.key}>
              {bars.map((b) => {
                const mean = aggNum(b.agg?.mean);
                const p95 = aggNum(b.agg?.p95);
                const color = modeColor(b.mode);
                const bcx = b.x + barW / 2;
                if (mean === null) {
                  return (
                    <g key={b.mode}>
                      <rect x={b.x} y={padT + plotH - 3} width={barW} height={3} fill={color} opacity={0.18} />
                      <text x={bcx} y={padT + plotH - 12} className="xlab-n" textAnchor="middle">无数据</text>
                    </g>
                  );
                }
                const h = Math.max(1, (mean / top) * plotH);
                return (
                  <g key={b.mode}>
                    <rect x={b.x} y={y(mean)} width={barW} height={h} fill={color} opacity={0.9} />
                    <text x={bcx} y={y(mean) - 8} className="barval" textAnchor="middle">{fmtMs(mean, 0)}</text>
                    {p95 !== null && (
                      <g>
                        <line x1={b.x - 4} x2={b.x + barW + 4} y1={y(p95)} y2={y(p95)} className="mark-p95" />
                        <text x={b.x + barW + 6} y={y(p95) + 3} className="mark-lbl">p95</text>
                      </g>
                    )}
                  </g>
                );
              })}
              <text x={cx} y={padT + plotH + 20} className="xlab" textAnchor="middle">{r.label}</text>
              <text x={cx} y={padT + plotH + 35} className="xlab-n" textAnchor="middle">{r.paramsText}</text>
              <text x={cx} y={padT + plotH + 50} className="xlab-n" textAnchor="middle">
                n={nOf(r.collab)}/{nOf(r.local)}
              </text>
            </g>
          );
        })}
      </svg>

      <div className="chart-legend">
        <span className="lg-item"><i className="lg-sw" style={{ background: modeColor('collaborative') }} />云边端协同</span>
        <span className="lg-item"><i className="lg-sw" style={{ background: modeColor('local') }} />本地执行</span>
        <span className="lg-item"><i className="lg-line p95" />p95</span>
        <span className="lg-note">纵轴单位：ms · 柱高 = 该档位 client_total_ms 的 mean · n=协同/本地</span>
      </div>
    </div>
  );
}

