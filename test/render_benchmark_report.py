#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""云边端协同推理基准报告生成器（自包含单文件 HTML，纯标准库）。

把 Benchmark 站点（FastAPI, NodePort 30082）导出的对比 JSON 渲染成一份**离线可读**的
中文 HTML 报告：结论摘要 → 方法与实验设计 → 四类任务逐类对比 → 总体对比 →
正确性校验 → 口径与假设 → 局限与后续工作。

设计要点：
* 单文件、无外部依赖：CSS 内联，图表为**手写 SVG**（不引入任何图表库，无 CDN/字体/脚本）。
  SVG 上不写 ``xmlns``，因此报告里不会出现任何 ``http(s)://`` 形式的资源引用。
* 数据容错：``speedup`` 可为 ``null``、``extra`` 可缺失、``overall.<mode>`` 与
  ``configs[].modes.<mode>`` 可缺失、``p50/p95/min/max`` 可为 ``null``、
  甚至整个 ``cluster`` 缺失——缺失一律渲染为「数据不足 / —」，绝不编造数字。
* 结论由数据推导：每类任务的「为什么快/慢」小结逐档比对时延与 ``extra``
  （并行加速比、派发墙钟）后生成，未达标/协同更慢的档位如实指出。

用法::

    python3 test/render_benchmark_report.py --data bench.json --out benchmark_report.html
    python3 test/render_benchmark_report.py --data bench.json --title "院内协同推理基准"

也可作为模块导入：``from render_benchmark_report import render_report``。
"""
from __future__ import annotations

import argparse
import html
import json
import math
import sys
from datetime import datetime
from pathlib import Path

# --------------------------------------------------------------------------- #
# 常量
# --------------------------------------------------------------------------- #
GAIN_TARGET = 10.0          # 验收线：协同须比本地快 ≥10%
MS_ND = 1                   # 表格/摘要里的毫秒小数位
DASH = "—"

MODE_COLLAB = "collaborative"
MODE_LOCAL = "local"
MODE_CN = {MODE_COLLAB: "云边端协同", MODE_LOCAL: "本地执行"}
MODE_COLOR = {MODE_COLLAB: "#946b3c", MODE_LOCAL: "#a4562a"}

KIND_ORDER = ("compute", "diagnosis", "sync", "routine")
KIND_CN = {
    "compute": "计算类",
    "diagnosis": "诊断类",
    "sync": "通信类",
    "routine": "日常类",
}
KIND_MECHANISM = {
    "compute": (
        "协同：把固定的 <b>3 个分区</b>并发派给发起 Pod、数据中心与空闲边缘节点，"
        "调度侧并行分摊；本地：发起 Pod 上<b>串行</b>跑完 3 份分区，"
        "受该 Pod 的 CPU 配额（clinic 1500m = 1.5 核）限制。"
    ),
    "diagnosis": (
        "只有协同：医疗中心不能执行模型 server 半段（layer4 + 融合 + 分类器），"
        "诊断任务没有本地执行策略，因此本类<b>不提供本地基线</b>，"
        "只报协同路径的绝对时延。"
    ),
    "sync": (
        "协同：数据中心持有<b>分块归属表</b>，开 <code>concurrency</code> 条并行同步流；"
        "本地：无中心分块表，<b>单流</b>顺序拉取。"
    ),
    "routine": (
        "协同：把一次性 Job 并发派发到空闲边缘节点与数据中心（每个 Job 有真实的容器启动开销）；"
        "本地：在发起 Pod 内<b>内联执行</b>，无 Job 调度与容器冷启动。"
    ),
}
LOAD_AXIS_FALLBACK = {
    "compute": "算力负载（仪器×行数×强度）",
    "diagnosis": "批量大小（每次诊断的病例数）",
    "sync": "链路带宽（每源链路 Mbps）",
    "routine": "一次性作业数量",
}
PARAM_LABELS = {
    "instruments": "仪器数",
    "rows": "行数",
    "intensity": "强度",
    "partition_count": "分区数",
    "seed": "种子",
    "jobs": "作业数",
    "batch": "批量",
    "batch_size": "批量",
    "patients": "病例数",
    "sources": "源数",
    "bandwidth_mbps": "每源链路带宽",
    "concurrency": "并行流数",
    "repeats": "重复次数",
    "model": "模型",
}
PARAM_ORDER = (
    "instruments", "rows", "intensity", "partition_count", "jobs", "batch",
    "batch_size", "patients", "sources", "bandwidth_mbps", "concurrency", "model",
    "seed", "repeats",
)

TITLE_DEFAULT = "云边端协同推理基准报告"

CSS = """
:root{
  --paper:#f4f1ec; --ink:#2f2b26; --brass:#946b3c; --amber:#a4562a;
  --line:#ded5c7; --line2:#c9bfae; --muted:#6b6156; --card:#fbf9f5;
  --ok:#2f6b4f; --okbg:#e6efe6; --okline:#b9d2bd;
  --bad:#a4562a; --badbg:#f7e8dd; --badline:#e0bda1;
  --nabg:#eae5dc;
}
*{box-sizing:border-box}
body{
  margin:0; background:var(--paper); color:var(--ink);
  font:16px/1.75 system-ui,"PingFang SC","Microsoft YaHei","Hiragino Sans GB",
       "Source Han Sans SC","Noto Sans CJK SC",sans-serif;
}
.wrap{max-width:1140px;margin:0 auto;padding:38px 26px 70px}
h1{font-size:32px;line-height:1.35;margin:0 0 10px;letter-spacing:.4px}
h2{font-size:23px;margin:44px 0 16px;padding-bottom:9px;border-bottom:2px solid var(--brass)}
h3{font-size:18px;margin:28px 0 10px}
h4{font-size:16px;margin:18px 0 8px;color:var(--brass)}
p{margin:10px 0}
a{color:var(--brass)}
code{background:#efe9df;border:1px solid var(--line);border-radius:4px;
     padding:1px 5px;font-size:14px;
     font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
small{color:var(--muted)}
.masthead{border-bottom:3px solid var(--brass);padding-bottom:18px;margin-bottom:6px}
.sub{color:var(--muted);font-size:15px;margin:0 0 12px}
.chips{display:flex;flex-wrap:wrap;gap:8px;margin-top:12px}
.chip{background:var(--card);border:1px solid var(--line);border-radius:999px;
      padding:4px 12px;font-size:13.5px;color:var(--muted)}
.chip b{color:var(--ink);font-weight:600}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(255px,1fr));
       gap:14px;margin:20px 0 8px}
.card{background:var(--card);border:1px solid var(--line);border-radius:9px;
      border-left:6px solid var(--brass);padding:14px 16px}
.card.ok{border-left-color:var(--ok)}
.card.bad{border-left-color:var(--bad)}
.card.na{border-left-color:#9a9186}
.card .kname{font-size:17px;font-weight:700;margin-bottom:2px}
.card .sname{font-size:12.5px;color:var(--muted);margin-bottom:10px;line-height:1.5}
.card .row{display:flex;justify-content:space-between;gap:10px;font-size:14.5px;
           padding:2px 0;border-bottom:1px dotted var(--line)}
.card .row:last-of-type{border-bottom:0}
.card .row span:last-child{font-variant-numeric:tabular-nums}
.badge{display:inline-block;padding:2px 11px;border-radius:999px;font-size:13px;
       font-weight:700;letter-spacing:.3px}
.badge.ok{background:var(--okbg);color:var(--ok);border:1px solid var(--okline)}
.badge.bad{background:var(--badbg);color:var(--bad);border:1px solid var(--badline)}
.badge.na{background:var(--nabg);color:var(--muted);border:1px solid var(--line2)}
.verdict-line{margin-top:10px;display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.gain-pos{color:var(--ok);font-weight:700;font-variant-numeric:tabular-nums}
.gain-neg{color:var(--bad);font-weight:700;font-variant-numeric:tabular-nums}
.gain-none{color:#8a8177;font-variant-numeric:tabular-nums}
.big{font-size:20px;font-weight:700;font-variant-numeric:tabular-nums}
.tbl-wrap{overflow-x:auto;margin:14px 0 6px}
table{width:100%;border-collapse:collapse;background:var(--card);font-size:14.5px}
th,td{border:1px solid var(--line);padding:7px 9px;text-align:right;
      white-space:nowrap;font-variant-numeric:tabular-nums}
th{background:#efe9df;font-weight:600}
th:first-child,td:first-child{text-align:left}
td.wrapcell,th.wrapcell{white-space:normal;min-width:170px;text-align:left}
tbody tr:nth-child(even) td{background:#f7f4ee}
caption{caption-side:bottom;text-align:left;color:var(--muted);font-size:13px;
        padding-top:8px;line-height:1.6}
figure{margin:18px 0 6px;background:var(--card);border:1px solid var(--line);
       border-radius:9px;padding:8px 10px 4px}
figure svg{width:100%;height:auto;display:block}
figcaption{color:var(--muted);font-size:13px;padding:6px 4px 8px;line-height:1.6}
.chart-svg text{font-family:inherit}
.svg-lab{font-size:11px;fill:#2f2b26}
.svg-lab-sm{font-size:9.5px;fill:#6b6156}
.svg-lab-in{font-size:10.5px;fill:#fdfbf7;font-weight:600}
.svg-ax{font-size:11px;fill:#6b6156}
.svg-title{font-size:12px;fill:#2f2b26;font-weight:600}
.svg-note{font-size:10.5px;fill:#6b6156}
.legend{display:flex;gap:18px;flex-wrap:wrap;align-items:center;
        font-size:13.5px;color:var(--muted);margin:10px 0 2px}
.legend i{display:inline-block;width:13px;height:13px;border-radius:3px;
          margin-right:6px;vertical-align:-2px}
.callout{background:#fdf6ec;border:1px solid #e3c9a4;border-left:6px solid var(--amber);
         border-radius:9px;padding:16px 20px;margin:18px 0}
.callout h3{margin:0 0 10px;color:var(--amber)}
.callout ol,.callout ul{margin:0;padding-left:22px}
.callout li{margin:9px 0}
.flag{display:inline-block;background:var(--badbg);color:var(--bad);
      border:1px solid var(--badline);border-radius:4px;padding:0 7px;
      font-size:12.5px;font-weight:700;margin-right:6px}
.note{background:#f7f4ee;border:1px dashed var(--line2);border-radius:8px;
      padding:12px 16px;color:var(--muted);font-size:14.5px;margin:14px 0}
.empty{background:#f7f4ee;border:1px dashed var(--line2);border-radius:9px;
       padding:26px 20px;text-align:center;color:var(--muted);font-size:14.5px}
.empty strong{display:block;color:var(--ink);font-size:16px;margin-bottom:6px}
.insights{background:var(--card);border:1px solid var(--line);border-radius:9px;
          padding:6px 20px 14px;margin:16px 0}
.insights h4{margin:14px 0 6px}
.insights ul{margin:8px 0;padding-left:22px}
.insights li{margin:8px 0}
.axis-note{color:var(--muted);font-size:13.5px}
footer{margin-top:48px;padding-top:16px;border-top:1px solid var(--line2);
       color:var(--muted);font-size:13px;line-height:1.8}
@media print{body{background:#fff}.wrap{max-width:none;padding:0}}
"""


# --------------------------------------------------------------------------- #
# 取值容错助手
# --------------------------------------------------------------------------- #
def as_dict(value):
    """任何非 dict 都当作空 dict（数据缺失/类型异常都不应让渲染崩溃）。"""
    return value if isinstance(value, dict) else {}


def as_list(value):
    return value if isinstance(value, list) else []


def num(value):
    """尽量转成有限浮点数；失败或 NaN/Inf 返回 None。"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        out = float(value)
    else:
        try:
            out = float(str(value).strip())
        except (TypeError, ValueError):
            return None
    if math.isnan(out) or math.isinf(out):
        return None
    return out


def esc(value):
    return html.escape("" if value is None else str(value), quote=True)


def ms(value, nd=MS_ND):
    """毫秒数值 + 单位；缺失返回破折号。"""
    out = num(value)
    if out is None:
        return DASH
    return f"{out:,.{nd}f} ms"


def pct_signed(value, nd=1):
    out = num(value)
    if out is None:
        return DASH
    return f"{out:+.{nd}f}%"


def count_of(stat):
    out = num(as_dict(stat).get("n"))
    return int(out) if out is not None else None


def n_txt(stat):
    out = count_of(stat)
    return f"n={out}" if out is not None else "n=" + DASH


def agg(stat):
    """聚合值的统一呈现：`482.2 ms（n=20）`。"""
    body = as_dict(stat)
    if num(body.get("mean")) is None:
        return DASH
    return f"{ms(body.get('mean'))}（{n_txt(body)}）"


def gain_pct(collab_mean, local_mean):
    """协同相对本地的加速百分比（正 = 协同更快）。除零/缺数据返回 None。"""
    collab, local = num(collab_mean), num(local_mean)
    if collab is None or local is None or local <= 0:
        return None
    return (local - collab) / local * 100.0


def gain_html(value):
    """带正负号且按正/负着色的百分比单元格。"""
    out = num(value)
    if out is None:
        return f'<span class="gain-none">{DASH}</span>'
    cls = "gain-pos" if out > 0 else ("gain-neg" if out < 0 else "gain-none")
    return f'<span class="{cls}">{pct_signed(out)}</span>'


def verdict_of(gain, target=GAIN_TARGET):
    """→ (css 后缀, 中文判定)。数据不足 / 达标 / 未达标。"""
    if gain is None:
        return "na", "数据不足"
    if gain >= target:
        return "ok", "达标"
    return "bad", "未达标"


def verdict_caveat(gain, target=GAIN_TARGET):
    """一位小数四舍五入后与判定线可能"看起来矛盾"时，补一句原始值说明。"""
    out = num(gain)
    if out is None:
        return ""
    if round(out, 1) >= target and out < target:
        return f"（未取整值 {out:.3f}%，低于 ≥{target:.0f}% 验收线）"
    if round(out, 1) < target and out >= target:
        return f"（未取整值 {out:.3f}%）"
    return ""


def fmt_params(params, limit=None):
    """把 params dict 渲染成 `仪器数 4 · 行数 512 · 种子 1100` 形式。"""
    body = as_dict(params)
    keys = [k for k in PARAM_ORDER if k in body]
    keys += sorted(k for k in body if k not in PARAM_ORDER)
    parts = []
    for key in keys:
        val = body.get(key)
        label = PARAM_LABELS.get(key, key)
        if isinstance(val, bool) or val is None:
            parts.append(f"{label} {DASH if val is None else str(val).lower()}")
        elif isinstance(val, (int, float)):
            shown = f"{val:,}" if isinstance(val, int) else f"{val:g}"
            parts.append(f"{label} {shown}")
        elif isinstance(val, str):
            parts.append(f"{label} {val}")
        elif isinstance(val, (list, dict)):
            parts.append(f"{label} {json.dumps(val, ensure_ascii=False)}")
    if limit and len(parts) > limit:
        parts = parts[:limit] + ["…"]
    return " · ".join(parts) if parts else DASH


def parse_dt(value):
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


# --------------------------------------------------------------------------- #
# 数据抽取
# --------------------------------------------------------------------------- #
def mode_stat(container, mode):
    """取 `{...}["<mode>"]`，缺失/非 dict → {}。"""
    return as_dict(as_dict(container).get(mode))


def suite_rows(suite):
    """逐档（config）抽取两种策略的统计与差值。"""
    rows = []
    for cfg in as_list(as_dict(suite).get("configs")):
        if not isinstance(cfg, dict):
            continue
        modes = as_dict(cfg.get("modes"))
        collab, local = mode_stat(modes, MODE_COLLAB), mode_stat(modes, MODE_LOCAL)
        cm, lm = num(collab.get("mean")), num(local.get("mean"))
        rows.append({
            "key": str(cfg.get("spec_key") or ""),
            "label": str(cfg.get("label") or cfg.get("spec_key") or "未命名档位"),
            "params": as_dict(cfg.get("params")),
            "collab": collab,
            "local": local,
            "collab_mean": cm,
            "local_mean": lm,
            "gain": gain_pct(cm, lm),
        })
    return rows


def suite_overall(suite):
    return as_dict(as_dict(suite).get("overall"))


def suite_gain(suite):
    """套件级加速比：优先用 speedup 字段，缺失则由 overall 均值现算。"""
    speedup = as_dict(as_dict(suite).get("speedup"))
    value = num(speedup.get("collaborative_faster_pct"))
    overall = suite_overall(suite)
    collab_mean = num(speedup.get("collaborative_mean_ms")) or num(
        mode_stat(overall, MODE_COLLAB).get("mean"))
    local_mean = num(speedup.get("local_mean_ms")) or num(
        mode_stat(overall, MODE_LOCAL).get("mean"))
    if value is None:
        value = gain_pct(collab_mean, local_mean)
    if collab_mean is None:
        collab_mean = num(mode_stat(overall, MODE_COLLAB).get("mean"))
    if local_mean is None:
        local_mean = num(mode_stat(overall, MODE_LOCAL).get("mean"))
    ratio = num(speedup.get("ratio"))
    if ratio is None and collab_mean and local_mean and collab_mean > 0:
        ratio = local_mean / collab_mean
    return {"gain": value, "collab_mean": collab_mean,
            "local_mean": local_mean, "ratio": ratio}


def ordered_suites(data):
    suites = [s for s in as_list(as_dict(data).get("suites")) if isinstance(s, dict)]

    def sort_key(item):
        idx, suite = item
        kind = str(suite.get("kind") or "")
        return (KIND_ORDER.index(kind) if kind in KIND_ORDER else len(KIND_ORDER), idx)

    return [s for _, s in sorted(enumerate(suites), key=sort_key)]


def kind_label(kind):
    key = str(kind or "")
    return KIND_CN.get(key, key or "未标注类别")


def suite_title(suite):
    return str(suite.get("suite_name") or suite.get("suite_id") or "未命名套件")


# --------------------------------------------------------------------------- #
# SVG：数值刻度 / 文本宽度
# --------------------------------------------------------------------------- #
def nice_ceil(value):
    """把上界抬到一个"好看"的刻度值。"""
    val = num(value) or 0.0
    if val <= 0:
        return 1.0
    exp = math.floor(math.log10(val))
    base = 10.0 ** exp
    for mult in (1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10):
        if val <= mult * base * (1 + 1e-9):
            return mult * base
    return 10.0 * base


def _ch_units(text):
    total = 0.0
    for ch in str(text):
        total += 1.0 if ord(ch) > 0x2E80 else 0.55
    return total


def wrap_cjk(text, max_units, max_lines=2):
    text = str(text).strip()
    if not text:
        return [""]
    if _ch_units(text) <= max_units:
        return [text]
    lines, cur, used = [], "", 0.0
    for idx, ch in enumerate(text):
        width = 1.0 if ord(ch) > 0x2E80 else 0.55
        if used + width > max_units and cur:
            lines.append(cur)
            cur, used = "", 0.0
            if len(lines) == max_lines - 1:
                lines.append(text[idx:].strip())
                return lines
        cur += ch
        used += width
    if cur:
        lines.append(cur)
    return lines[:max_lines]


def empty_state(note):
    return ('<div class="empty"><strong>本图无可用数据</strong>'
            f'<span>{esc(note)}</span></div>')


# --------------------------------------------------------------------------- #
# SVG：分组柱状图（手写，含 p95 标记线）
# --------------------------------------------------------------------------- #
def grouped_bar_svg(categories, series, y_label="墙钟时延（毫秒 ms）",
                    empty_note=None, width=980.0, height=446.0):
    """每档两根柱（mean），柱上画 p95 虚线标记；数据全空/全零则返回空态说明。"""
    cats = [str(c) for c in as_list(categories)]
    ser = [s for s in as_list(series) if isinstance(s, dict)]

    values = []
    for one in ser:
        for key in ("mean", "p95"):
            for raw in as_list(one.get(key)):
                val = num(raw)
                if val is not None and val >= 0:
                    values.append(val)

    if not cats or not ser or not values or max(values) <= 0:
        return empty_state(empty_note or "该套件没有可用的时延数据"
                                       "（所有档位的 mean/p95 均为空或为 0），"
                                       "因此不绘制图表。")

    top = max(values) * 1.10
    step = nice_ceil(top / 5.0)
    ymax = step * max(1.0, math.ceil(top / step - 1e-9))
    if ymax <= 0:
        return empty_state("时延上界计算出 0，无法确定纵轴刻度。")

    ml, mr, mt, mb = 94.0, 26.0, 70.0, 98.0
    pw, ph = width - ml - mr, height - mt - mb
    n = len(cats)
    band = pw / n
    group_w = min(band * 0.58, 190.0)
    bar_gap = 8.0
    bar_w = max(9.0, (group_w - bar_gap) / 2.0)
    tick_count = int(round(ymax / step)) if step > 0 else 5
    decade = f"{step:g}"

    def y_of(value):
        ratio = min(max(value / ymax, 0.0), 1.0)
        return mt + ph * (1.0 - ratio)

    parts = []
    parts.append(f'<svg class="chart-svg" viewBox="0 0 {width:.0f} {height:.0f}" '
                 f'preserveAspectRatio="xMidYMid meet" role="img">')
    parts.append("<title>" + esc(y_label + " 分组柱状图") + "</title>")

    # 横向网格 + 纵轴刻度
    for i in range(tick_count + 1):
        value = step * i
        y = y_of(value)
        parts.append(f'<line x1="{ml:.1f}" y1="{y:.1f}" x2="{width - mr:.1f}" '
                     f'y2="{y:.1f}" stroke="#e3dccf" stroke-width="1"/>')
        label = f"{value:,.0f}" if step >= 1 else f"{value:.1f}"
        parts.append(f'<text x="{ml - 10:.1f}" y="{y + 4:.1f}" text-anchor="end" '
                     f'class="svg-ax">{esc(label)}</text>')

    # 坐标轴
    parts.append(f'<line x1="{ml:.1f}" y1="{mt:.1f}" x2="{ml:.1f}" '
                 f'y2="{mt + ph:.1f}" stroke="#c9bfae" stroke-width="1.4"/>')
    parts.append(f'<line x1="{ml:.1f}" y1="{mt + ph:.1f}" x2="{width - mr:.1f}" '
                 f'y2="{mt + ph:.1f}" stroke="#c9bfae" stroke-width="1.4"/>')
    parts.append('<text x="22" y="{:.1f}" text-anchor="middle" class="svg-title" '
                 'transform="rotate(-90 22 {:.1f})">{}</text>'.format(
                     mt + ph / 2.0, mt + ph / 2.0, esc(y_label)))

    # 图例（内联在 SVG 内，保持自包含）
    legend_x = ml
    for one in ser:
        color = str(one.get("color") or "#946b3c")
        name = str(one.get("name") or "系列")
        parts.append(f'<rect x="{legend_x:.1f}" y="{mt - 54:.1f}" width="13" '
                     f'height="13" rx="3" fill="{esc(color)}"/>')
        parts.append(f'<text x="{legend_x + 19:.1f}" y="{mt - 43:.1f}" '
                     f'class="svg-ax">{esc(name)}</text>')
        legend_x += 26 + _ch_units(name) * 11.0
    parts.append(f'<line x1="{legend_x:.1f}" y1="{mt - 47.5:.1f}" '
                 f'x2="{legend_x + 26:.1f}" y2="{mt - 47.5:.1f}" stroke="#2f2b26" '
                 f'stroke-width="1.2" stroke-dasharray="4 3"/>')
    parts.append(f'<text x="{legend_x + 32:.1f}" y="{mt - 43:.1f}" class="svg-ax">'
                 f'虚线 = 该档 p95</text>')

    # 标注避让：标签按 x 顺序放置，若与已放标签同处一条线带则上移（空间不足时下移）
    placed = []

    def place_label(cx, y_pref, text, size):
        width = _ch_units(text) * size
        y = y_pref
        for _ in range(16):
            clash = any(abs(py - y) < 11.0
                        and abs(px - cx) < (pw + width) / 2.0 - 0.5
                        for px, py, pw in placed)
            if not clash:
                break
            y -= 11.0
            if y < mt + 9.0:
                y = y_pref + 11.0
                for _ in range(16):
                    if not any(abs(py - y) < 11.0
                               and abs(px - cx) < (pw + width) / 2.0 - 0.5
                               for px, py, pw in placed):
                        break
                    y += 11.0
                break
        placed.append((cx, y, width))
        return y

    label_units = max(6.0, (band - 10.0) / 11.0)
    for i, cat in enumerate(cats):
        center = ml + band * i + band / 2.0
        group_x = ml + band * i + (band - group_w) / 2.0
        for j, one in enumerate(ser):
            means = as_list(one.get("mean"))
            p95s = as_list(one.get("p95"))
            mean_val = num(means[i]) if i < len(means) else None
            p95_val = num(p95s[i]) if i < len(p95s) else None
            color = str(one.get("color") or "#946b3c")
            x = group_x + j * (bar_w + bar_gap)
            cx = x + bar_w / 2.0
            if mean_val is None:
                y_dash = place_label(cx, mt + ph - 6.0, DASH, 9.5)
                parts.append(f'<text x="{cx:.1f}" y="{y_dash:.1f}" '
                             f'text-anchor="middle" class="svg-lab-sm">{DASH}</text>')
                continue
            bar_top = y_of(mean_val)
            bar_h = max(1.0, mt + ph - bar_top)
            parts.append(f'<rect x="{x:.1f}" y="{bar_top:.1f}" width="{bar_w:.1f}" '
                         f'height="{bar_h:.1f}" rx="2.5" fill="{esc(color)}"/>')
            mean_txt = f"{mean_val:,.0f} ms"
            if bar_h >= 30 and _ch_units(mean_txt) * 10.5 <= bar_w:
                # 柱够高 → 均值写在柱内（白字），不会与 p95 标注抢位置
                placed.append((cx, bar_top + 16.0, _ch_units(mean_txt) * 10.5))
                parts.append(f'<text x="{cx:.1f}" y="{bar_top + 16:.1f}" '
                             f'text-anchor="middle" class="svg-lab-in">'
                             f'{esc(mean_txt)}</text>')
            else:
                y_mean = place_label(cx, bar_top - 6.0, mean_txt, 11.0)
                parts.append(f'<text x="{cx:.1f}" y="{y_mean:.1f}" '
                             f'text-anchor="middle" class="svg-lab">'
                             f'{esc(mean_txt)}</text>')
            if p95_val is not None:
                y95 = y_of(p95_val)
                parts.append(f'<line x1="{x - 5:.1f}" y1="{y95:.1f}" '
                             f'x2="{x + bar_w + 5:.1f}" y2="{y95:.1f}" '
                             f'stroke="#2f2b26" stroke-width="1.2" '
                             f'stroke-dasharray="4 3"/>')
                if band >= 76:
                    p95_txt = f"p95 {p95_val:,.0f}"
                elif band >= 46:
                    p95_txt = f"{p95_val:,.0f}"
                else:
                    p95_txt = ""
                if p95_txt:
                    above = y95 - 5.0
                    if above < mt + 9:
                        above = y95 + 12.0
                    y95_lab = place_label(cx, above, p95_txt, 9.5)
                    parts.append(f'<text x="{cx:.1f}" y="{y95_lab:.1f}" '
                                 f'text-anchor="middle" class="svg-lab-sm">'
                                 f'{esc(p95_txt)}</text>')
        # 横轴档位标签（超宽自动折行）
        lines = wrap_cjk(cat, label_units, max_lines=2)
        for k, line in enumerate(lines):
            parts.append(f'<text x="{center:.1f}" y="{mt + ph + 20 + k * 15:.1f}" '
                         f'text-anchor="middle" class="svg-lab">{esc(line)}</text>')

    parts.append(f'<text x="{ml:.1f}" y="{height - 12:.1f}" class="svg-note">'
                 f'纵轴：{esc(y_label)}｜刻度步长 {esc(decade)}｜柱高为 mean，'
                 f'虚线为 p95（样本数见同节对比表）</text>')
    parts.append("</svg>")
    return "".join(parts)


# --------------------------------------------------------------------------- #
# SVG：两种调度策略执行模型示意
# --------------------------------------------------------------------------- #
def _svg_box(x, y, w, h, lines, stroke, fill="#fbf9f5", size=12.0):
    out = [f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" rx="7" '
           f'fill="{fill}" stroke="{stroke}" stroke-width="1.4"/>']
    count = len(lines)
    for idx, line in enumerate(lines):
        ty = y + h / 2.0 + 4.5 - (count - 1) * 8.5 + idx * 17.0
        weight = "700" if idx == 0 else "400"
        color = "#2f2b26" if idx == 0 else "#6b6156"
        out.append(f'<text x="{x + w / 2.0:.1f}" y="{ty:.1f}" text-anchor="middle" '
                   f'font-size="{size:.1f}" font-weight="{weight}" fill="{color}">'
                   f'{esc(line)}</text>')
    return "".join(out)


def _svg_arrow(x1, y1, x2, y2, color, dashed=False):
    dash = ' stroke-dasharray="5 4"' if dashed else ""
    return (f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
            f'stroke="{color}" stroke-width="1.5"{dash} marker-end="url(#ah)"/>')


def strategy_diagram_svg():
    """静态对比示意图：协同（并行/流水线/多流）vs 本地（串行/单流/内联）。"""
    p = ['<svg class="chart-svg" viewBox="0 0 980 336" preserveAspectRatio="xMidYMid meet" '
         'role="img">',
         "<title>云边端协同与本地执行的执行模型差异示意图</title>",
         '<defs><marker id="ah" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" '
         'markerHeight="6" orient="auto-start-reverse">'
         '<path d="M 0 0 L 10 5 L 0 10 z" fill="#6b6156"/></marker></defs>',
         '<line x1="490" y1="16" x2="490" y2="320" stroke="#ded5c7" stroke-width="1"/>']

    # 左：协同
    p.append('<text x="16" y="26" font-size="14" font-weight="700" fill="#946b3c">'
             '云边端协同（collaborative）</text>')
    p.append('<text x="16" y="45" font-size="11.5" fill="#6b6156">'
             '数据中心统一编排：并发分区派发 · 两段流水线 · 多源并行流 · Job 并发派发</text>')
    p.append(
        _svg_box(20, 58, 190, 44, ["发起 Pod（clinic-1）", "提交任务 + 等待聚合"], "#946b3c")
        + _svg_box(255, 58, 210, 44, ["数据中心调度器", "分块归属表 / 任务编排"],
                   "#946b3c")
        + _svg_arrow(212, 80, 252, 80, "#946b3c"))
    p.append(
        _svg_box(20, 150, 138, 46, ["发起 Pod", "分区 3"], "#946b3c")
        + _svg_box(172, 150, 138, 46, ["边缘节点 A", "分区 1（并行）"], "#946b3c")
        + _svg_box(324, 150, 138, 46, ["边缘节点 B", "分区 2（并行）"], "#946b3c"))
    p.append(_svg_arrow(300, 104, 90, 146, "#946b3c")
             + _svg_arrow(345, 104, 243, 146, "#946b3c")
             + _svg_arrow(392, 104, 396, 146, "#946b3c"))
    p.append('<text x="20" y="222" font-size="11.5" fill="#6b6156">'
             '三份分区并行 ⇒ 墙钟 ≈ 最慢分区 + 编排开销；'
             '诊断批量越大，边端/云端两段重叠越充分</text>')
    p.append('<rect x="20" y="238" width="442" height="26" rx="6" fill="#f0e6d6" '
             'stroke="#d9c2a2"/>')
    p.append('<text x="241" y="255" text-anchor="middle" font-size="11.5" fill="#946b3c" '
             'font-weight="600">并行分摊 + 流水线重叠 + 多流并发（关键路径被切开）</text>')
    p.append('<text x="20" y="292" font-size="11.5" fill="#6b6156">'
             '代价：异步提交 + 轮询、跨节点传输、Job 容器启动（真实开销约 2–3 s/Job）</text>')
    p.append('<text x="20" y="312" font-size="11.5" fill="#6b6156">'
             '这些固定开销在“工作量太小”的档位上无法被摊薄，可能反超本地</text>')

    # 右：本地
    p.append('<text x="514" y="26" font-size="14" font-weight="700" fill="#a4562a">'
             '本地执行（local）</text>')
    p.append('<text x="514" y="45" font-size="11.5" fill="#6b6156">'
             '发起 Pod 自己做完一切：串行分区 · 串行批处理 · 单流拉取 · Job 内联</text>')
    p.append(
        _svg_box(514, 58, 200, 44, ["发起 Pod（clinic-1）", "一次同步调用，无排队等待"],
                 "#a4562a")
        + _svg_box(744, 58, 216, 44, ["无中心分块表", "无 Job 调度"], "#a4562a", "#f6ece2")
        + _svg_arrow(714, 80, 741, 80, "#a4562a"))
    for idx, (label, sub) in enumerate((
            ("分区 1", "串行"), ("分区 2", "串行"), ("分区 3", "串行"),
            ("全模型批量诊断", "串行"), ("单一数据源同步", "单流顺序拉取"),
            ("一次性作业", "Pod 内联执行"))):
        y = 118 + idx * 30
        p.append(_svg_box(514, y, 330, 25, [label + "  " + sub], "#a4562a", "#f6ece2",
                          size=11.5))
        if idx:
            p.append(_svg_arrow(679, y - 5, 679, y - 1, "#a4562a"))
    p.append('<text x="860" y="132" font-size="11.5" fill="#a4562a" font-weight="600">'
             '↑</text>')
    p.append('<rect x="856" y="140" width="16" height="176" rx="4" fill="#a4562a" '
             'opacity="0.18" stroke="#a4562a" stroke-dasharray="4 3"/>')
    p.append('<text x="880" y="230" font-size="11.5" fill="#a4562a" font-weight="600">'
             '墙钟</text>')
    p.append('<text x="880" y="248" font-size="11.5" fill="#a4562a" font-weight="600">'
             '= 总和</text>')
    p.append('<text x="514" y="312" font-size="11.5" fill="#6b6156">'
             '优势：无编排/传输/冷启动开销，小任务档位可能更快；'
             '劣势：受发起 Pod CPU 配额（1.5 核）限制</text>')
    p.append("</svg>")
    return "".join(p)


# --------------------------------------------------------------------------- #
# HTML 片段
# --------------------------------------------------------------------------- #
def h2(number, title):
    return f"<h2>{esc(number)}&nbsp;&nbsp;{esc(title)}</h2>"


def overall_table(overall):
    """套件级 overall 统计表（两种策略各一行）。"""
    body = as_dict(overall)
    rows = []
    for mode in (MODE_COLLAB, MODE_LOCAL):
        stat = mode_stat(body, mode)
        if not stat:
            rows.append(f'<tr><td>{MODE_CN[mode]}</td>'
                        + ("<td>" + DASH + "</td>") * 8 + "</tr>")
            continue
        n = count_of(stat)
        rows.append(
            f'<tr><td>{MODE_CN[mode]}</td>'
            f'<td>{n if n is not None else DASH}</td>'
            f'<td>{esc(stat.get("success", DASH))}</td>'
            f'<td>{esc(stat.get("fail", DASH))}</td>'
            f'<td>{ms(stat.get("mean"))}</td>'
            f'<td>{ms(stat.get("p50"))}</td>'
            f'<td>{ms(stat.get("p95"))}</td>'
            f'<td>{ms(stat.get("min"))}</td>'
            f'<td>{ms(stat.get("max"))}</td></tr>')
    return ('<div class="tbl-wrap"><table>'
            "<caption>口径：站点后端墙钟 <code>client_total_ms</code>；"
            "缺失字段以 " + DASH + " 表示（不补零、不外推）。</caption>"
            "<thead><tr>"
            "<th>策略</th><th>样本数 n</th><th>成功</th><th>失败</th>"
            "<th>mean</th><th>p50</th><th>p95</th><th>min</th><th>max</th>"
            "</tr></thead><tbody>" + "".join(rows) + "</tbody>"
            "</table></div>")


def spec_table(suite):
    specs = [s for s in as_list(as_dict(suite).get("specs")) if isinstance(s, dict)]
    if not specs:
        return ('<div class="note">数据文件中未提供该套件的 <code>specs</code>'
                "（固定任务清单缺失），无法列出档位参数与重复次数。</div>")
    rows = []
    for spec in specs:
        params = as_dict(spec.get("params"))
        repeats = spec.get("repeats")
        seeds = params.get("seed")
        rows.append(
            f'<tr><td>{esc(spec.get("label", DASH))}</td>'
            f'<td class="wrapcell">{esc(fmt_params(params))}</td>'
            f'<td>{esc(repeats if repeats is not None else DASH)}</td>'
            f'<td>{esc(seeds if seeds is not None else DASH)}</td></tr>')
    return ('<div class="tbl-wrap"><table>'
            "<caption>固定任务清单：协同与本地在每一档使用同一份参数与同一个 seed，"
            "因此两次运行的分区输入逐字节相同，计算结果可逐档比对。</caption>"
            "<thead><tr>"
            "<th>档位</th><th class=\"wrapcell\">参数</th><th>重复次数</th><th>固定 seed</th>"
            "</tr></thead><tbody>" + "".join(rows) + "</tbody>"
            "</table></div>")


def compare_table(rows):
    if not rows:
        return ('<div class="note">该套件没有 <code>configs</code>（逐档统计）数据，'
                "无法给出逐档对比表。</div>")
    body = []
    for row in rows:
        collab, local = row["collab"], row["local"]
        c_n = count_of(collab)
        l_n = count_of(local)
        body.append(
            f'<tr><td>{esc(row["label"])}</td>'
            f'<td>{c_n if c_n is not None else DASH}</td>'
            f'<td>{ms(collab.get("mean"))}</td>'
            f'<td>{ms(collab.get("p50"))}</td>'
            f'<td>{ms(collab.get("p95"))}</td>'
            f'<td>{l_n if l_n is not None else DASH}</td>'
            f'<td>{ms(local.get("mean"))}</td>'
            f'<td>{ms(local.get("p50"))}</td>'
            f'<td>{ms(local.get("p95"))}</td>'
            f'<td>{gain_html(row["gain"])}</td></tr>')
    return ('<div class="tbl-wrap"><table>'
            "<caption>单位均为毫秒（ms）；差值 = （本地 mean − 协同 mean）/ 本地 mean，"
            "正值（绿色）= 协同更快，负值（琥珀/红色）= 协同更慢，"
            + DASH + " 表示该档缺一侧数据、不可比。</caption>"
            "<thead><tr>"
            '<th rowspan="2">档位</th><th colspan="4">云边端协同</th>'
            '<th colspan="4">本地执行</th><th rowspan="2">差值（协同快为正）</th>'
            '</tr><tr><th>n</th><th>mean</th><th>p50</th><th>p95</th>'
            '<th>n</th><th>mean</th><th>p50</th><th>p95</th></tr></thead><tbody>'
            + "".join(body) + "</tbody>"
            "</table></div>")


# --------------------------------------------------------------------------- #
# 数据推导的小结
# --------------------------------------------------------------------------- #
def _workload_proxy(kind, params):
    """该档“工作量”的可比代理指标（越小 = 任务越轻），用于档位排名。"""
    p = as_dict(params)
    if kind == "compute":
        vals = [num(p.get(k)) for k in ("instruments", "rows", "intensity")]
        return vals[0] * vals[1] * vals[2] if all(v is not None for v in vals) else None
    if kind == "diagnosis":
        ids = p.get("patient_ids")
        return len(ids) if isinstance(ids, list) else None
    if kind == "sync":
        return num(p.get("bandwidth_mbps"))
    if kind == "routine":
        return num(p.get("jobs"))
    return None


def _rank_phrase(kind, row, rows):
    """按工作量代理指标给出该档在全部档位中的位置（不照抄类别套话）。"""
    proxies = [(r, _workload_proxy(kind, r.get("params"))) for r in (rows or [])]
    proxies = [(r, v) for r, v in proxies if v is not None]
    mine = _workload_proxy(kind, row.get("params"))
    if mine is None or len(proxies) < 2:
        return "该档工作量偏小"
    proxies.sort(key=lambda t: t[1])
    total = len(proxies)
    shared = [i for i, (_, v) in enumerate(proxies) if v == mine]
    lo, hi = min(shared) + 1, max(shared) + 1
    if hi == 1:
        return f"该档工作量在全部 {total} 档中最小"
    if lo == total:
        return f"该档工作量在全部 {total} 档中最大"
    if lo == hi:
        return f"该档工作量在全部 {total} 档中排第 {lo} 小"
    return f"该档工作量在全部 {total} 档中并列第 {lo}–{hi} 小"


def _neg_mechanism(kind):
    """协同更慢时该类任务的机制（按类别固定，位置信息由 _rank_phrase 提供）。"""
    if kind == "compute":
        return ("协同新增的派发、分区结果汇聚与编排开销无法被并行收益摊薄，"
                "出现“固定开销淹没并行收益”")
    if kind == "diagnosis":
        return ("协同的关键路径是“切片 → 跨节点下发 → 远端执行 → 结果回传”，"
                "本地则是一次调用、一次数据拷贝、零编排；"
                "本档并行收益不足以抵消这笔固定的控制面往返")
    if kind == "sync":
        return "分块并行流的建立与归属表维护开销高于单流顺序拉取节省的时间"
    if kind == "routine":
        return "每个 Job 真实的容器启动开销与调度往返没有足够作业数去摊薄"
    return "协同新增的编排与传输开销在该档工作量下无法被摊薄"


def neg_reason(kind, row, rows=None):
    """协同更慢的档位的机制解释（结合该档参数与档位排名，不照抄机制结论）。"""
    params = fmt_params(row["params"], limit=4)
    rank = _rank_phrase(kind, row, rows)
    mech = _neg_mechanism(kind)
    return (f"{row['label']} 档：协同 {ms(row['collab_mean'])} 慢于本地 "
            f"{ms(row['local_mean'])}（{pct_signed(row['gain'])}，"
            f"协同为本地耗时的 {(row['collab_mean'] / row['local_mean']):.2f}×）；"
            f"{rank}，{mech}。该档参数：{params}。")


def kind_insights(suite, target=GAIN_TARGET):
    """按数据推导该类任务“为什么快/慢”，返回 HTML 列表项。"""
    kind = str(as_dict(suite).get("kind") or "")
    rows = suite_rows(suite)
    both = [r for r in rows if r["gain"] is not None]
    overall = suite_overall(suite)
    gain_info = suite_gain(suite)
    items = []

    c_stat, l_stat = mode_stat(overall, MODE_COLLAB), mode_stat(overall, MODE_LOCAL)
    if gain_info["gain"] is None:
        items.append(
            "本类缺少可比的整类均值（协同或本地的 <code>mean</code> 缺失，"
            "或 <code>speedup</code> 为 <code>null</code>），"
            "因此<b>不给结论</b>：按“数据不足”处理。已具备数据的档位见上表。")
        if both:
            best = max(both, key=lambda r: r["gain"])
            worst = min(both, key=lambda r: r["gain"])
            items.append(
                f"仅就可比档位而言：增益最大为 {esc(best['label'])}（"
                f"{ms(best['collab_mean'])} vs {ms(best['local_mean'])}，"
                f"{pct_signed(best['gain'])}），最小为 {esc(worst['label'])}（"
                f"{pct_signed(worst['gain'])}），可比的档位共 {len(both)}/"
                f"{len(rows)} 档。")
        elif rows:
            c_only = [r["label"] for r in rows if r["collab_mean"] is not None
                      and r["local_mean"] is None]
            l_only = [r["label"] for r in rows if r["local_mean"] is not None
                      and r["collab_mean"] is None]
            parts = []
            if c_only:
                parts.append(f"{len(c_only)}/{len(rows)} 档仅有协同数据"
                             f"（{'、'.join(esc(x) for x in c_only[:4])}）")
            if l_only:
                parts.append(f"{len(l_only)}/{len(rows)} 档仅有本地数据"
                             f"（{'、'.join(esc(x) for x in l_only[:4])}）")
            if parts:
                items.append("仅有单侧数据，无法计算差值：" + "；".join(parts)
                             + "。补齐缺失一侧的测量后本类才能判定。")
        return items

    _, verdict_txt = verdict_of(gain_info["gain"], target)
    items.append(
        f"整类结论：协同 {agg(c_stat)} vs 本地 {agg(l_stat)}，"
        f"协同相对本地 {pct_signed(gain_info['gain'])}"
        + (f"（比值 {gain_info['ratio']:.2f}×）" if gain_info["ratio"] else "")
        + f"，判定 <b>{verdict_txt}</b>（验收线 ≥{target:.0f}%）。"
        + verdict_caveat(gain_info["gain"], target))

    if both:
        best = max(both, key=lambda r: r["gain"])
        worst = min(both, key=lambda r: r["gain"])
        items.append(
            f"逐档极值：增益最大 {esc(best['label'])}"
            f"（协同 {ms(best['collab_mean'])} vs 本地 {ms(best['local_mean'])}，"
            f"{pct_signed(best['gain'])}）；最小 {esc(worst['label'])}"
            f"（协同 {ms(worst['collab_mean'])} vs 本地 {ms(worst['local_mean'])}，"
            f"{pct_signed(worst['gain'])}）。")

        if len(both) >= 3:
            ordered = sorted(both, key=lambda r: r["local_mean"])
            chain = " → ".join(pct_signed(r["gain"]) for r in ordered)
            mono = all(ordered[i]["gain"] <= ordered[i + 1]["gain"] + 1e-9
                       for i in range(len(ordered) - 1))
            trend = ("随任务量单调上升" if mono else "随任务量并非单调上升")
            items.append(
                f"负载趋势：按各档本地耗时（任务量的代理指标）从小到大排列，"
                f"协同增益依次为 {chain}，整体表现{trend}。"
                + ("说明协同收益主要来自“工作量越大、被摊薄得越充分”。" if mono else
                   "说明收益并非只由任务量决定，编排与传输开销在个别档位改变了排序。"))

        slower = [r for r in both if r["gain"] < 0]
        if slower:
            worst_neg = min(slower, key=lambda r: r["gain"])
            items.append("如实指出协同反而更慢的档位：" + neg_reason(kind, worst_neg, rows))
        else:
            items.append(
                "本类所有可比档位协同均不慢于本地，未出现增益为负的档位"
                f"（最小增益 {pct_signed(worst['gain'])}）。")

    extra = as_dict(as_dict(suite).get("extra"))
    c_par = num(extra.get("collaborative_parallel_speedup_mean"))
    l_par = num(extra.get("local_parallel_speedup_mean"))
    c_disp = num(extra.get("collaborative_dispatch_wall_ms_mean"))
    l_disp = num(extra.get("local_dispatch_wall_ms_mean"))
    c_comp = num(extra.get("collaborative_compute_ms_total_mean"))
    l_comp = num(extra.get("local_compute_ms_total_mean"))
    c_net = num(extra.get("collaborative_network_ms_total_mean"))
    l_net = num(extra.get("local_network_ms_total_mean"))
    evidence = []
    if (c_comp or 0) > 0 or (l_comp or 0) > 0:
        evidence.append("平均累计计算时间（<code>extra.compute_ms_total</code>，"
                        "协同为<b>两侧分段之和</b>，本地为单侧一次推理）"
                        + ("协同 " + (ms(c_comp) if c_comp is not None else DASH))
                        + "、"
                        + ("本地 " + (ms(l_comp) if l_comp is not None else DASH)))
    if (c_net is not None and c_net > 0) or (l_net is not None and l_net > 0):
        evidence.append("平均累计网络时间（<code>extra.network_ms_total</code>）"
                        + ("协同 " + (ms(c_net) if c_net is not None else DASH))
                        + "、"
                        + ("本地 " + (ms(l_net) if l_net is not None else DASH)))
    if c_par is not None or l_par is not None:
        evidence.append("平均调度并行加速比（<code>extra.parallel_speedup</code>）"
                        + ("协同 " + (f"{c_par:.2f}×" if c_par is not None else DASH))
                        + "、"
                        + ("本地 " + (f"{l_par:.2f}×" if l_par is not None else DASH)))
    if c_disp is not None or l_disp is not None:
        evidence.append("平均派发墙钟 "
                        + ("协同 " + (ms(c_disp) if c_disp is not None else DASH))
                        + "、"
                        + ("本地 " + (ms(l_disp) if l_disp is not None else DASH)))
    if evidence:
        tail = ""
        if c_par is not None and l_par is not None:
            if c_par >= l_par * 1.15:
                tail = ("协同的调度并行度显著高于本地（本地≈1，即串行），"
                        "方向与增益一致，说明增益主要来自把关键路径切开，"
                        "而不是单个任务跑得更快。")
            elif c_par > l_par:
                tail = ("协同并行度仅略高于本地（差距不足 15%），"
                        "单独不足以解释本类的增益或损失，"
                        "须结合派发墙钟与逐档时延判断。")
            else:
                tail = ("协同记录的并行度并不高于本地，"
                        "本类差异无法用并行分摊解释，需另找原因。")
        if c_disp is not None and l_disp is not None and l_disp > 0:
            ratio = c_disp / l_disp
            if ratio >= 2:
                tail += (f"协同的派发墙钟是本地 {ratio:.1f}×"
                         f"（{ms(c_disp)} vs {ms(l_disp)}），"
                         "这笔固定编排开销在低负载档位无法摊薄，"
                         "与上表的负增益档位一致。")
            elif ratio <= 0.5:
                tail += (f"协同的派发墙钟反而只有本地 {ratio:.2f}×"
                         f"（{ms(c_disp)} vs {ms(l_disp)}），"
                         "本类没有出现“编排开销反超”的迹象。")
        if (kind == "diagnosis" and c_par is None and l_par is None
                and c_comp is not None and l_comp not in (None, 0)):
            if c_comp > l_comp * 1.3:
                tail += (f"该类没有记录并行加速比；可比的只有分段计时："
                         f"协同两侧累计计算时间为本地的 {c_comp / l_comp:.2f}×"
                         f"（{ms(c_comp)} vs {ms(l_comp)}）。"
                         "协同把一次推理拆成两段、两侧各自计时，相加后必然高于单侧一次推理，"
                         "这正是拆分带来的固有代价；"
                         "在边缘不缺算力时，这笔代价无法由并行收益抵消。")
            else:
                tail += (f"该类没有记录并行加速比；分段计时显示协同两侧累计计算时间为"
                         f"本地的 {c_comp / l_comp:.2f}×"
                         f"（{ms(c_comp)} vs {ms(l_comp)}），本身不足以解释本类差异，"
                         "须结合逐档时延与传输开销判断。")
        items.append("调度侧证据：" + "；".join(evidence) + "。" + tail)
    else:
        items.append("该套件未提供 <code>extra</code>（并行加速比 / 派发墙钟），"
                     "以上归因仅依据逐档时延与参数推断，相关机制尚未被独立数据点验证。")

    if both:
        slowest = min(both, key=lambda r: r["gain"])
        if slowest["gain"] < 0 and kind == "routine":
            items.append("补充：日常类的 Job 容器启动开销是真实测得的平台行为"
                         "（每个 Job 约 2–3 s 量级），作业数越多摊得越薄，"
                         "这与上表增益随作业数变化的趋势一致。")
    return items


# --------------------------------------------------------------------------- #
# 正确性校验
# --------------------------------------------------------------------------- #
CORRECT_KEYS = ("checksum", "bpcr", "match", "correct", "accuracy")


def _scan_correctness(node, path, out, depth=0):
    if depth > 6 or len(out) > 40:
        return
    if isinstance(node, dict):
        for key, val in node.items():
            _scan_correctness(val, f"{path}.{key}" if path else str(key), out, depth + 1)
    elif isinstance(node, list):
        leaves = [v for v in node if not isinstance(v, (dict, list))]
        if leaves and any(k in path.lower() for k in CORRECT_KEYS):
            out.append((path, f"列表，共 {len(node)} 项"))
        elif node:
            for idx, val in enumerate(node[:20]):
                _scan_correctness(val, f"{path}[{idx}]", out, depth + 1)
    else:
        if any(k in path.lower() for k in CORRECT_KEYS) and node is not None:
            out.append((path, node))


def collect_correctness(data, suite):
    found = []
    _scan_correctness(as_dict(suite).get("correctness"), "suite.correctness", found)
    _scan_correctness(as_dict(suite).get("extra"), "suite.extra", found)
    _scan_correctness(as_dict(data).get("correctness"), "数据根.correctness", found)
    return found


def render_correctness(data, suites):
    parts = [
        "<p>校验思路：每一档使用<b>固定 seed</b>，协同与本地拿到<b>完全相同的任务参数</b>"
        "（同样的仪器数 / 行数 / 强度 / 分区数 / 病例批量），因此两边应当产出"
        "同一组分区 checksum 与同一个 bpCR（诊断概率）。"
        "报告只呈现数据文件里真实带有的校验字段；字段缺失时只描述方法，不补数字。</p>",
        '<div class="insights"><h4>方法（无论数据是否带校验字段都成立）</h4><ul>'
        "<li>参数同源：档位参数由套件定义冻结（见第 3 节各表的“参数 / 固定 seed”），"
        "调度策略是两侧<b>唯一</b>的自变量。</li>"
        "<li>计算类：逐分区比对 checksum；协同侧的分区结果为基准，"
        "本地侧同档位 checksum 应逐项相同。</li>"
        "<li>诊断类：比对 bpCR（bpcr_actual / bpcr_expected）；"
        "当期望值为 0/1 标签时按 0.5 二值化后判等。</li>"
        "<li>站点后端的对比视图内含 correctness 列，可直接复核；"
        "本报告不重算 checksum。</li>"
        "</ul></div>",
    ]
    any_found = False
    for suite in suites:
        found = collect_correctness(data, suite)
        if not found:
            continue
        any_found = True
        rows = "".join(
            f'<tr><td class="wrapcell">{esc(path)}</td>'
            f'<td class="wrapcell">{esc(value)}</td></tr>'
            for path, value in found)
        parts.append(
            f"<h3>{esc(suite_title(suite))}（{esc(kind_label(suite.get('kind')))}）</h3>"
            '<div class="tbl-wrap"><table><thead><tr><th class="wrapcell">字段</th>'
            f'<th class="wrapcell">值</th></tr></thead><tbody>{rows}</tbody></table></div>')
    if not any_found:
        parts.append(
            '<div class="note">当前数据文件中<b>未包含</b> <code>correctness</code> / '
            "<code>checksum</code> / <code>bpcr</code> 相关字段，"
            "因此本节只描述校验方法，<b>不给出任何一致性数字</b>。"
            "如需在报告里展示逐档 checksum 一致性，请在导出 JSON 时补充该字段。</div>")
    return "".join(parts)


# --------------------------------------------------------------------------- #
# 各章节
# --------------------------------------------------------------------------- #
def render_summary(suites, target):
    """第 1 节：每类任务一张卡 + 总体判断。"""
    cards = []
    verdicts = []
    for suite in suites:
        kind = str(suite.get("kind") or "")
        info = suite_gain(suite)
        css, txt = verdict_of(info["gain"], target)
        verdicts.append((kind, suite_title(suite), css, txt, info))
        overall = suite_overall(suite)
        cards.append(
            f'<div class="card {css}">'
            f'<div class="kname">{esc(kind_label(kind))}</div>'
            f'<div class="sname">{esc(suite_title(suite))}'
            + (f'｜发起方 {esc(suite.get("source"))}' if suite.get("source") else "")
            + "</div>"
            f'<div class="row"><span>{MODE_CN[MODE_COLLAB]} mean</span>'
            f'<span>{agg(mode_stat(overall, MODE_COLLAB))}</span></div>'
            f'<div class="row"><span>{MODE_CN[MODE_LOCAL]} mean</span>'
            f'<span>{agg(mode_stat(overall, MODE_LOCAL))}</span></div>'
            f'<div class="row"><span>协同快</span>'
            f'<span class="big">{gain_html(info["gain"])}</span></div>'
            + (f'<div class="row"><span>比值（本地/协同）</span>'
               f'<span>{info["ratio"]:.2f}×</span></div>' if info["ratio"] else "")
            + f'<div class="verdict-line"><span class="badge {css}">{txt}</span>'
            f'<small>验收线 ≥{target:.0f}%</small></div>'
            + (f'<div><small>{esc(verdict_caveat(info["gain"], target))}</small></div>'
               if verdict_caveat(info["gain"], target) else "")
            + "</div>")

    ok_kinds = [t for t in verdicts if t[2] == "ok"]
    bad_kinds = [t for t in verdicts if t[2] == "bad"]
    na_kinds = [t for t in verdicts if t[2] == "na"]
    total = len(verdicts)

    if not total:
        judgement = ("<p>数据文件中没有任何套件数据，<b>无法给出总体判断</b>"
                     "（判定：数据不足）。</p>")
    else:
        pieces = []
        if ok_kinds:
            pieces.append("达标：" + "、".join(kind_label(k) for k, _, _, _, _ in ok_kinds))
        if bad_kinds:
            names = "、".join(kind_label(k) for k, _, _, _, _ in bad_kinds)
            if len(bad_kinds) > 1:
                best_off = max(bad_kinds,
                               key=lambda t: (t[4]["gain"] if t[4]["gain"] is not None
                                              else -1e9))
                names += (f"（其中 {kind_label(best_off[0])} 最接近验收线，"
                          f"{pct_signed(best_off[4]['gain'])}）")
            pieces.append("未达标：" + names)
        if na_kinds:
            pieces.append("数据不足：" + "、".join(kind_label(k) for k, _, _, _, _ in na_kinds))
        if len(ok_kinds) == total:
            headline = (f"<b>总体判断：{total}/{total} 类任务的协同策略均达到 "
                        f"≥{target:.0f}% 验收线</b>——就本数据集而言，"
                        "“云边端协同比本地执行快 ≥10%”这一研究目标在四类任务上均成立。")
        elif ok_kinds:
            headline = (f"<b>总体判断：{len(ok_kinds)}/{total} 类任务达标，"
                        f"{len(bad_kinds)} 类未达标，{len(na_kinds)} 类数据不足</b>——"
                        "研究目标只被部分支持，未达标与缺数据的类别在下方逐类说明。")
        else:
            headline = (f"<b>总体判断：0/{total} 类任务达到 ≥{target:.0f}% 验收线</b>——"
                        "本数据集不支持“协同普遍快 ≥10%”的结论。")
        judgement = ("<p>" + headline + "</p><ul>"
                     + "".join(f"<li>{esc(piece)}</li>" for piece in pieces)
                     + "</ul>")
        gains = [(kind_label(k), info["gain"]) for k, _, _, _, info in verdicts
                 if info["gain"] is not None]
        if gains:
            top = max(gains, key=lambda g: g[1])
            bottom = min(gains, key=lambda g: g[1])
            judgement += (
                f'<p class="axis-note">跨类别看：增益最高 {esc(top[0])} '
                f'{pct_signed(top[1])}，最低 {esc(bottom[0])} {pct_signed(bottom[1])}；'
                "类别间差异反映的是“可并行/可重叠空间”与“固定编排开销”的相对大小，"
                "而不是单次任务本身的快慢。</p>")

    return ('<div class="cards">' + "".join(cards) + "</div>"
            '<div class="callout"><h3>总体判断</h3>' + judgement + "</div>")


def render_cluster(cluster):
    nodes = [n for n in as_list(as_dict(cluster).get("nodes")) if isinstance(n, dict)]
    images = as_dict(as_dict(cluster).get("images"))
    parts = []
    if nodes:
        rows = []
        for node in nodes:
            rows.append(
                f'<tr><td>{esc(node.get("name", DASH))}</td>'
                f'<td>{esc(node.get("role", DASH))}</td>'
                f'<td>{esc(node.get("cpu", DASH))}</td>'
                f'<td>{esc(node.get("memory", DASH))}</td>'
                f'<td class="wrapcell">{esc(node.get("pod", DASH))}</td></tr>')
        parts.append(
            '<div class="tbl-wrap"><table>'
            "<caption>云 = 数据中心（调度器与后端服务所在节点）；"
            "边 = 医院节点；端 = 诊所 Pod。发起方决定了任务从哪一侧进入系统。</caption>"
            "<thead><tr><th>节点</th><th>角色</th>"
            '<th>CPU（核）</th><th>内存</th><th class="wrapcell">其上 Pod</th>'
            "</tr></thead><tbody>" + "".join(rows) + "</tbody>"
            "</table></div>")
    else:
        parts.append('<div class="note">数据文件未提供集群拓扑'
                     "（<code>cluster.nodes</code> 缺失），本节省略拓扑表。</div>")

    if images:
        items = "".join(
            f'<li><code>{esc(key)}</code>：{esc(value)}</li>'
            for key, value in sorted(images.items(), key=lambda kv: str(kv[0])))
        parts.append("<h4>镜像版本</h4><ul>" + items + "</ul>")
    else:
        parts.append('<div class="note">数据文件未提供镜像版本'
                     "（<code>cluster.images</code> 缺失）。</div>")

    # 配额补充：拓扑表未含 resources，这里解释“本地受 Pod CPU 配额限制”
    parts.append(
        "<h4>影响结论的两条部署约束（来自集群清单，不在本数据文件内）</h4><ul>"
        "<li>诊所（端）Pod 的 CPU limit 为 <code>1500m</code>（1.5 核），"
        "医院（边）Pod 为 4 核量级，数据中心节点为 4 核量级——"
        "这直接决定“本地执行把 N 份分区串行压在 1.5 核上”的下限。</li>"
        "<li>节点上还混布着其它服务（如调度器、监控、Redis），"
        "本次测量<b>未做节点间负载隔离</b>，见第 7 节。</li></ul>")
    return "".join(parts)


def render_method(data, suites, target):
    cluster = as_dict(data.get("cluster"))
    notes = as_dict(data.get("notes"))
    parts = [render_cluster(cluster), "<h3>2.2 两种调度策略的执行模型</h3>"]

    mech_rows = []
    for suite in suites:
        kind = str(suite.get("kind") or "")
        mech_rows.append(
            f'<tr><td>{esc(kind_label(kind))}</td>'
            f'<td class="wrapcell">{KIND_MECHANISM.get(kind, "（该类别未在脚本内置机制描述中，按数据本身解读。）")}</td></tr>')
    if mech_rows:
        parts.append('<div class="tbl-wrap"><table><thead><tr><th>任务类别</th>'
                     '<th class="wrapcell">协同 vs 本地 的执行差异</th></tr></thead><tbody>'
                     + "".join(mech_rows) + "</tbody></table></div>")

    parts.append(strategy_diagram_svg())
    parts.append('<div class="legend"><span>上图左右两栏用同一时间轴直觉对照：'
                 '协同把关键路径切开，本地把所有环节串起来。</span></div>')

    # 测量口径
    samples = []
    for suite in suites:
        overall = suite_overall(suite)
        c_n = count_of(mode_stat(overall, MODE_COLLAB))
        l_n = count_of(mode_stat(overall, MODE_LOCAL))
        repeats = [s.get("repeats") for s in as_list(suite.get("specs"))
                   if isinstance(s, dict) and s.get("repeats") is not None]
        samples.append(
            f'<tr><td>{esc(suite_title(suite))}</td>'
            f'<td>{esc(kind_label(suite.get("kind")))}</td>'
            f'<td>{c_n if c_n is not None else DASH}</td>'
            f'<td>{l_n if l_n is not None else DASH}</td>'
            f'<td>{esc(len(as_list(suite.get("specs"))))} 档</td>'
            f'<td>{esc(repeats[0] if repeats else DASH)}</td></tr>')
    note_rows = "".join(
        f'<tr><td>{esc(key)}</td><td class="wrapcell">{esc(value)}</td></tr>'
        for key, value in notes.items())
    parts.append(
        "<h3>2.3 测量口径</h3>"
        "<ul>"
        "<li><b>主指标</b>：站点后端墙钟 <code>client_total_ms</code>，"
        "即“从站点发出任务到拿到结果”的端到端耗时，含网络与排队。</li>"
        "<li><b>两种策略的调用形态不同</b>：协同为<b>异步提交 + 轮询</b>，"
        "墙钟里包含排队与轮询间隔；本地为<b>一次同步调用</b>，"
        "墙钟里不含排队。这是本报告最重要的口径差异，见第 6 节。</li>"
        "<li><b>统计量</b>：每档给出样本数 n、均值 mean、p50、p95（线性插值分位数），"
        "套件级另有成功/失败计数。</li>"
        "<li><b>重复与种子</b>：每档重复若干次，且每档固定 seed，"
        "保证协同与本地在同一档位上产出可逐项比对的结果。</li>"
        "<li><b>报告只读数据</b>：渲染脚本不重算统计量，直接呈现 JSON 中的聚合值；"
        "聚合值本身由 <code>test/rebuild_report_data.py</code> 从站点数据库按指定 run 集合离线重算"
        "（见 2.4），与站点后端同口径；"
        "若某档缺一侧数据则标记为不可比，不做外推。</li>"
        "</ul>")
    if samples:
        parts.append('<div class="tbl-wrap"><table>'
                     "<caption>样本数直接取自数据文件的 <code>overall.&lt;mode&gt;.n</code>；"
                     "验收线固定为 ≥" + f"{target:.0f}%" + "。</caption>"
                     "<thead><tr><th>套件</th><th>类别</th>"
                     "<th>协同样本 n</th><th>本地样本 n</th><th>档位数</th>"
                     "<th>每档重复次数</th></tr></thead><tbody>"
                     + "".join(samples) + "</tbody></table></div>")
    # 数据来源可追溯：本报告只统计下列运行
    prov_rows = []
    for suite in suites:
        runs = [str(r) for r in as_list(suite.get("run_ids")) if r]
        scope = str(suite.get("scope") or "")
        if runs:
            scope_txt = ("本轮采集的指定运行（协同 / 本地各一次）"
                         if scope == "explicit-runs"
                         else f"范围标记：{scope or '未标记'}")
            run_html = "<br>".join(f"<code>{esc(r)}</code>" for r in runs)
        else:
            scope_txt = "（数据文件未记录运行 ID）"
            run_html = DASH
        prov_rows.append(
            f'<tr><td>{esc(suite_title(suite))}</td>'
            f'<td class="wrapcell">{esc(scope_txt)}</td>'
            f'<td class="wrapcell">{run_html}</td></tr>')
    if prov_rows:
        parts.append(
            "<h3>2.4 数据来源（本次统计范围）</h3>"
            "<p class=\"note\">对比站点会长期累积历史运行；若按“该套件全部历史”聚合，"
            "旧构建（例如诊断早期的切分点、旧传输路径）的结果会混入统计。"
            "因此本报告按<b>显式运行的 run_id 集合</b>重算统计量，"
            "每个套件只含本轮同一构建下的协同与本地两次运行，"
            "口径与站点后端 <code>engine.suite_compare</code> 一致（同样的分位数插值、"
            "同样的成功/失败判定、同样的档位标签匹配）。</p>"
            '<div class="tbl-wrap"><table>'
            "<caption>复现命令："
            "<code>python test/rebuild_report_data.py --db bench.db --runs &lt;run_id,...&gt;</code>"
            "</caption><thead><tr><th>套件</th><th>统计范围</th>"
            '<th class="wrapcell">运行 ID</th></tr></thead><tbody>'
            + "".join(prov_rows) + "</tbody></table></div>")
    if note_rows:
        parts.append('<div class="tbl-wrap"><table>'
                     "<caption>数据文件 <code>notes</code> 字段原样呈现。</caption>"
                     "<thead><tr><th>环境项</th>"
                     '<th class="wrapcell">取值</th></tr></thead><tbody>'
                     + note_rows + "</tbody></table></div>")
    return "".join(parts)


def render_kind_section(suite, index, target):
    kind = str(suite.get("kind") or "")
    rows = suite_rows(suite)
    overall = suite_overall(suite)
    axis = suite.get("load_axis") or LOAD_AXIS_FALLBACK.get(kind) or "（数据文件未给出负载轴）"
    parts = [f"<h3>3.{index} {esc(kind_label(kind))}｜{esc(suite_title(suite))}</h3>"]

    meta = []
    if suite.get("suite_id"):
        meta.append(f"套件 ID <code>{esc(suite.get('suite_id'))}</code>")
    if suite.get("source"):
        meta.append(f"发起方 <code>{esc(suite.get('source'))}</code>")
    if meta:
        parts.append('<p class="axis-note">' + "｜".join(meta) + "</p>")
    if suite.get("description"):
        parts.append(f"<p>{esc(suite.get('description'))}</p>")

    parts.append(f"<h4>负载轴</h4><p><b>{esc(axis)}</b></p>")
    parts.append("<h4>固定任务清单</h4>" + spec_table(suite))
    parts.append("<h4>逐档对比</h4>" + compare_table(rows))

    categories = [r["label"] for r in rows]
    series = [{
        "name": MODE_CN[MODE_COLLAB],
        "color": MODE_COLOR[MODE_COLLAB],
        "mean": [r["collab_mean"] for r in rows],
        "p95": [num(r["collab"].get("p95")) for r in rows],
    }, {
        "name": MODE_CN[MODE_LOCAL],
        "color": MODE_COLOR[MODE_LOCAL],
        "mean": [r["local_mean"] for r in rows],
        "p95": [num(r["local"].get("p95")) for r in rows],
    }]
    chart = grouped_bar_svg(
        categories, series,
        y_label="墙钟时延 mean / p95（毫秒 ms）",
        empty_note="该套件的 <code>configs</code> 里没有可用的 mean/p95"
                   "（或全部为 0），无法绘制逐档柱状图。")
    c_n = count_of(mode_stat(overall, MODE_COLLAB))
    l_n = count_of(mode_stat(overall, MODE_LOCAL))
    parts.append(
        '<figure>' + (chart if chart.startswith("<svg") else chart)
        + "<figcaption>柱高为各档 mean，虚线为该档 p95；"
        + f"整类样本：协同 n={c_n if c_n is not None else DASH}、"
          f"本地 n={l_n if l_n is not None else DASH}。"
        + "插图内数字取整显示，精确值见上表。</figcaption></figure>")

    parts.append("<h4>套件级总体统计</h4>" + overall_table(overall))

    items = kind_insights(suite, target)
    parts.append('<div class="insights"><h4>本类小结（由数据推导）</h4><ul>'
                 + "".join(f"<li>{item}</li>" for item in items) + "</ul>"
                 + '<p class="axis-note">小结中的每个数字都可回溯到上面的对比表与 '
                   "<code>extra</code> 字段；机制解释是对“数据为何如此”的归因，"
                   "在缺少 <code>extra</code> 时已标注为推断。</p></div>")
    return "".join(parts)


def render_overall(suites, target):
    cats, c_means, l_means, c_p95, l_p95 = [], [], [], [], []
    rows = []
    gains = []
    for suite in suites:
        overall = suite_overall(suite)
        c_stat, l_stat = mode_stat(overall, MODE_COLLAB), mode_stat(overall, MODE_LOCAL)
        info = suite_gain(suite)
        cats.append(kind_label(suite.get("kind")))
        c_means.append(info["collab_mean"])
        l_means.append(info["local_mean"])
        c_p95.append(num(c_stat.get("p95")))
        l_p95.append(num(l_stat.get("p95")))
        css, txt = verdict_of(info["gain"], target)
        if info["gain"] is not None:
            gains.append(info["gain"])
        ratio = f"{info['ratio']:.2f}×" if info["ratio"] else DASH
        rows.append(
            f'<tr><td>{esc(kind_label(suite.get("kind")))}</td>'
            f'<td>{agg(c_stat)}</td>'
            f'<td>{agg(l_stat)}</td>'
            f'<td>{gain_html(info["gain"])}</td>'
            f'<td>{esc(ratio)}</td>'
            f'<td><span class="badge {css}">{txt}</span></td></tr>')
    if not rows:
        return ('<div class="note">数据文件中没有套件数据，无法生成总体对比。</div>')

    avg_gain = sum(gains) / len(gains) if gains else None
    if avg_gain is not None:
        rows.append(
            f'<tr><td><b>四类算术平均（未加权）</b></td>'
            f"<td>{DASH}</td><td>{DASH}</td>"
            f'<td>{gain_html(avg_gain)}</td><td>{DASH}</td>'
            f'<td>{esc(verdict_of(avg_gain, target)[1])}</td></tr>')

    table = ('<div class="tbl-wrap"><table>'
             "<caption>末行是四个类别加速百分比的<b>未加权算术平均</b>，"
             "仅用于给出跨类别概览，不代表统一的端到端时延；"
             "类别间样本数不同，加权口径会得到不同数值。</caption>"
             "<thead><tr><th>任务类别</th>"
             "<th>协同 mean（n=）</th><th>本地 mean（n=）</th>"
             "<th>差值（协同快为正）</th><th>比值</th><th>判定（≥"
             + f"{target:.0f}%" + "）</th></tr></thead><tbody>"
             + "".join(rows) + "</tbody></table></div>")

    chart = grouped_bar_svg(
        cats,
        [{"name": MODE_CN[MODE_COLLAB], "color": MODE_COLOR[MODE_COLLAB],
          "mean": c_means, "p95": c_p95},
         {"name": MODE_CN[MODE_LOCAL], "color": MODE_COLOR[MODE_LOCAL],
          "mean": l_means, "p95": l_p95}],
        y_label="各类 mean / p95（毫秒 ms）",
        empty_note="四类任务的 overall 均值均缺失或为 0，无法绘制总体图。")

    observations = []
    comparable = [(kind_label(s.get("kind")), suite_gain(s)["gain"])
                  for s in suites if suite_gain(s)["gain"] is not None]
    if comparable:
        top = max(comparable, key=lambda kv: kv[1])
        bottom = min(comparable, key=lambda kv: kv[1])
        observations.append(
            f"增益跨度：最高 {esc(top[0])} {pct_signed(top[1])}，"
            f"最低 {esc(bottom[0])} {pct_signed(bottom[1])}，"
            f"跨度 {abs(top[1] - bottom[1]):.1f} 个百分点。")
        hit = [k for k, g in comparable if g >= target]
        miss = [k for k, g in comparable if g < target]
        observations.append(
            f"验收线：可比类别中 {len(hit)}/{len(comparable)} 达到 ≥{target:.0f}%"
            + (f"（达标：" + "、".join(esc(k) for k in hit) + "）" if hit else "")
            + (f"；未达标：" + "、".join(esc(k) for k in miss) if miss else "")
            + "。")
    if any(g is None for _, g in [(kind_label(s.get("kind")), suite_gain(s)["gain"])
                                  for s in suites]):
        observations.append(
            "存在只有单一策略数据的类别（<code>speedup</code> 为 <code>null</code>），"
            "这些类别按“数据不足”处理，未纳入上面的达标统计。")

    # 从 absolute 时延规模给出跨类别观察
    scale = [(kind_label(s.get("kind")), suite_gain(s)["local_mean"])
             for s in suites if suite_gain(s)["local_mean"]]
    if scale:
        biggest = max(scale, key=lambda kv: kv[1])
        smallest = min(scale, key=lambda kv: kv[1])
        observations.append(
            f"绝对时延规模：本地基线最大 {esc(biggest[0])} {ms(biggest[1])}，"
            f"最小 {esc(smallest[0])} {ms(smallest[1])}。"
            "协同的固定编排开销是绝对量，因此<b>基线越大、可被摊薄的余量越大</b>，"
            "这与类别间增益高低大体一致；基线小的类别要特别关注负增益档位。")

    observations.append(
        "口径提示：本表的“快 X%”全部基于站点后端墙钟，协同侧含异步排队与轮询，"
        "本地侧不含（见第 6 节）——读者若关心纯执行时间，应改用平台内部的 "
        "compute/network 阶段指标重新对比。")

    return ('<figure>' + chart + "<figcaption>四类任务的协同 vs 本地 mean（虚线为 p95）。"
            "各类样本数不同：协同/本地的 n 与 mean 已并列在上表括号内，"
            "逐档样本数见第 3 节各表。</figcaption></figure>"
            + table
            + '<div class="insights"><h4>跨类别观察</h4><ul>'
            + "".join(f"<li>{item}</li>" for item in observations) + "</ul></div>")


def render_assumptions():
    return (
        '<div class="callout"><h3>口径与假设（必读）</h3><ol>'
        "<li><b>墙钟口径</b>：主指标 <code>client_total_ms</code> 是<b>站点侧</b>墙钟。"
        "协同是“异步提交 + 轮询”，因此其间歇式等待与排队时间<b>计入</b>协同的一方；"
        "本地是“一次同步调用”，<b>不含</b>排队。"
        "这一不对称对协同是<b>不利</b>的（协同被计入了额外等待），"
        "因此本报告的加速比不应被解读为“协同的纯执行更快”。</li>"
        "<li><b>计算类</b>：协同把固定的 3 个分区<b>并发</b>派给 3 个节点"
        "（发起 Pod / 数据中心 / 空闲边缘节点）；本地在发起 Pod 上<b>串行</b>执行同样 3 份分区。"
        "本地一侧受该 Pod 的 CPU 配额限制（诊所 limit = 1.5 核），"
        "因此本类的差距很大一部分来自“1.5 核串行”而非模型本身。</li>"
        "<li><b>诊断类</b>：协同把「边端前端 → 云端后端」做成<b>两段流水线</b>，"
        "批量越大、两段重叠越充分；本地在发起 Pod 上串行跑完整模型，无重叠。</li>"
        "<li><span class=\"flag\">建模假设</span><b>通信类</b>："
        "<code>bandwidth_mbps</code> 按<b>每源链路</b>计价。协同由数据中心持有"
        "<b>分块归属表</b>，开 <code>concurrency</code> 条并行流；"
        "本地<b>无中心分块表</b>，按单流顺序拉取。"
        "“协同能开出并行流、本地只能单流”是平台内置模型的<b>建模假设</b>，"
        "不是从真实网络测得的结论——真实瓶颈（上行带宽、出口队列、跨机房 RTT）"
        "可能显著改变本类的对比结果，欢迎按此假设质疑本节数据。</li>"
        "<li><b>日常类</b>：协同把一次性 Job <b>并发派发</b>到空闲边缘节点与数据中心，"
        "每个 Job 带有<b>真实的容器启动开销（约 2–3 s 量级）</b>；"
        "本地在 Pod 内<b>内联执行</b>，没有 Job 调度与冷启动。"
        "因此本类的胜负高度依赖“作业数 × 启动开销”与“并行收益”的相对大小。</li>"
        "<li><b>模拟模型</b>：所有算力与带宽数值都来自平台内置的"
        "<b>模拟模型</b>（<code>common/v3_common.py</code>），"
        "并非真实医疗数据的吞吐测量；本报告度量的是<b>调度策略的相对差异</b>，"
        "不能当作临床吞吐或生产容量规划的依据。</li>"
        "<li><b>不做的推断</b>：渲染脚本不重算统计量、不填补缺失字段、不做外推；"
        "缺数据一律记作“数据不足”，缺一侧的档位一律记作“不可比”。</li>"
        "</ol></div>")



def _f(value, nd=1):
    """数值 → 带千分位/固定小数位的显示串；缺失 → DASH。"""
    v = num(value)
    return DASH if v is None else f"{v:,.{nd}f}"



def render_edge_constraint(data):
    """可选小节：端侧算力约束下的交叉点实验（data["edge_constraint"]）。"""
    ec = as_dict((data or {}).get("edge_constraint"))
    rows = as_list(ec.get("rows"))
    if not rows:
        return ""
    parts = ['<div class="callout">',
             "<h3>端侧算力约束下的交叉点（诊断类）</h3>",
             "<p>诊断走的是<b>串行模型拆分</b>：端侧做前端、云端做后端，中间特征必须跨节点搬运。"
             "因此协同能否占优，取决于「端侧算力劣势」与「中间特征搬运成本」的对比。"
             "下表通过在端侧 Pod 内制造 CPU 竞争（占满 cgroup 配额）模拟不同强度的端设备，"
             "在同一批量（4 例）下对比两种策略。</p>"]
    if ec.get("network_note"):
        parts.append(f"<p>本集群 Pod 间有效载荷吞吐实测：{esc(ec.get('network_note'))}</p>")
    parts.append('<table><caption>端侧算力从 4 核降到约 1/25 时的策略对比（批量 4 例）</caption>'
                 "<thead><tr><th>端侧竞争负载</th><th>端侧有效算力</th><th>本地 mean</th>"
                 "<th>协同 mean</th><th>差值</th><th>结论</th></tr></thead><tbody>")
    for row in rows:
        row = as_dict(row)
        gain = num(row.get("gain_pct"))
        cls = "gain-pos" if (gain is not None and gain > 0) else (
            "gain-neg" if gain is not None else "gain-none")
        verdict = ("协同更快" if (gain or 0) > 0 else "本地更快")
        parts.append(
            "<tr>"
            f'<td>{esc(row.get("load"))}</td>'
            f'<td>{esc(row.get("edge_cores"))}</td>'
            f'<td>{ms(row.get("local_ms"), 0)}</td>'
            f'<td>{ms(row.get("collaborative_ms"), 0)}</td>'
            f'<td class="{cls}">{pct_signed(gain)}</td>'
            f'<td>{verdict}</td>'
            "</tr>")
    parts.append("</tbody></table>")
    base = as_dict(ec.get("baseline"))
    base_rows = as_list(base.get("rows"))
    if base_rows:
        parts.append(f'<table><caption>{esc(base.get("label") or "对照：改造前")}</caption>'
                     "<thead><tr><th>端侧竞争负载</th><th>端侧有效算力</th><th>本地 mean</th>"
                     "<th>协同 mean</th><th>差值</th><th>结论</th></tr></thead><tbody>")
        for row in base_rows:
            row = as_dict(row)
            gain = num(row.get("gain_pct"))
            cls = "gain-pos" if (gain is not None and gain > 0) else (
                "gain-neg" if gain is not None else "gain-none")
            parts.append(
                "<tr>"
                f'<td>{esc(row.get("load"))}</td>'
                f'<td>{esc(row.get("edge_cores"))}</td>'
                f'<td>{ms(row.get("local_ms"), 0)}</td>'
                f'<td>{ms(row.get("collaborative_ms"), 0)}</td>'
                f'<td class="{cls}">{pct_signed(gain)}</td>'
                f'<td>{"协同更快" if (gain or 0) > 0 else "本地更快"}</td>'
                "</tr>")
        parts.append("</tbody></table>")
    if ec.get("conclusion"):
        parts.append(f'<p><b>结论：</b>{esc(ec.get("conclusion"))}</p>')
    parts.append("</div>")
    return "".join(parts)



def render_diagnosis_ab(data):
    """可选小节：诊断类「脱站点」对照（去掉测量工具本身的影响）。"""
    ab = as_dict((data or {}).get("diagnosis_ab"))
    rows = as_list(ab.get("rows"))
    if not rows:
        return ""
    parts = ['<div class="callout">',
             "<h3>诊断类：脱站点对照（去掉测量工具的影响）</h3>",
             "<p>上表的诊断数字是**经对比站点**测得的。站点在「控制面/数据面分离」方案里承担了"
             "数据面编排（下计划、直投执行者、回传记账），因此它自身的 2 核配额也会进入协同路径的时延。"
             "下表用独立探针（<code>test/diagnosis_ab.py</code>，不经站点）复测同一批量，"
             "两边都取**各自最优实现**（本地串行、协同按算力加权分片）。</p>",
             '<table><caption>不经站点的对照（两边各自最优）</caption>'
             "<thead><tr><th>批量</th><th>本地 mean</th><th>本地 p50</th>"
             "<th>协同 mean</th><th>协同 p50</th><th>差值(mean)</th><th>差值(p50)</th></tr></thead><tbody>"]
    for row in rows:
        row = as_dict(row)
        g = num(row.get("gain_mean"))
        gp = num(row.get("gain_p50"))
        cls = "gain-pos" if (g is not None and g > 0) else (
            "gain-neg" if g is not None else "gain-none")
        parts.append(
            "<tr>"
            f'<td>{esc(row.get("batch"))} 例</td>'
            f'<td>{ms(row.get("local_mean"), 0)}</td>'
            f'<td>{ms(row.get("local_p50"), 0)}</td>'
            f'<td>{ms(row.get("collab_mean"), 0)}</td>'
            f'<td>{ms(row.get("collab_p50"), 0)}</td>'
            f'<td class="{cls}">{pct_signed(g)}</td>'
            f'<td class="{cls}">{pct_signed(gp)}</td>'
            "</tr>")
    parts.append("</tbody></table>")
    if ab.get("conclusion"):
        parts.append(f'<p><b>结论：</b>{esc(ab.get("conclusion"))}</p>')
    parts.append("</div>")
    return "".join(parts)


def render_split_analysis(data):
    """可选小节：模型切分点分析（来自 data["split_analysis"]）。

    诊断类任务的对照结论取决于「在哪一层切分」：切分点越浅，中间特征越大、
    端侧卸下的算力越少。这份数据由离线测量填入，用于解释诊断类的实测结论。
    """
    sa = as_dict((data or {}).get("split_analysis"))
    rows = as_list(sa.get("rows"))
    if not rows:
        return ""
    input_mb = sa.get("input_mb")
    full_ms = sa.get("full_ms")
    parts = ['<div class="callout">',
             "<h3>模型切分点分析（诊断类归因）</h3>",
             "<p>诊断任务的协同路径是「边端做前端 → 云端做后端」。切分点决定了"
             "<b>中间特征体积</b>与<b>端侧卸下的算力</b>，也就决定了拆分能否划算："
             "中间特征越大、端侧留下的算力越少，拆分越是纯亏。</p>"]
    if input_mb is not None:
        parts.append(f"<p>输入体积（DCE+DWI，float32）= "
                     f"<b>{_f(input_mb, 2)} MB</b>；端侧完整模型耗时（离线测量）= "
                     f"<b>{_f(full_ms, 1)} ms</b>。</p>")
    parts.append('<table><caption>候选切分点：中间特征体积与算力分配（离线测量）</caption>'
                 "<thead><tr><th>切分点</th><th>中间特征（双视图）</th><th>相对输入</th>"
                 "<th>端侧承担</th><th>云端承担</th></tr></thead><tbody>")
    for row in rows:
        row = as_dict(row)
        ratio = row.get("ratio")
        parts.append(
            "<tr>"
            f'<td>{esc(row.get("point"))}</td>'
            f'<td>{_f(row.get("intermediate_mb"), 2)} MB</td>'
            f'<td>{(str(_f(ratio, 2)) + "×") if isinstance(ratio, (int, float)) else DASH}</td>'
            f'<td>{_f(row.get("edge_ms"), 1)} ms</td>'
            f'<td>{_f(row.get("cloud_ms"), 1)} ms</td>'
            "</tr>")
    parts.append("</tbody></table>")
    if sa.get("conclusion"):
        parts.append(f'<p><b>结论：</b>{esc(sa.get("conclusion"))}</p>')
    parts.append("</div>")
    return "".join(parts)


def render_limitations(suites, target):
    items = []
    slower = []
    for suite in suites:
        rows = suite_rows(suite)
        for row in rows:
            if row["gain"] is not None and row["gain"] < 0:
                slower.append((str(suite.get("kind") or ""), kind_label(suite.get("kind")),
                               suite_title(suite), row, rows))
    items.append("<b>单集群 · 单次测量</b>：全部数据来自一套 Kubernetes 集群、"
                 "每种配置重复有限次；报告未给出置信区间与显著性检验，"
                 "档位间的轻微差异可能落在测量噪声内。")
    items.append("<b>节点负载未隔离</b>：边缘与数据中心节点上混布着调度器、"
                 "监控、Redis 等常驻服务，且四类套件是顺序跑的，"
                 "前一类任务的余温（镜像拉取、缓存、Job 残留）可能影响后一类。"
                 "严格复现需要在空载节点上逐类单独测量。")
    items.append("<b>协同的绝对时延下限受编排开销约束</b>：异步提交、轮询间隔、"
                 "跨节点传输与 Job 容器启动构成固定成本；"
                 "当单档工作量小于该固定成本时，协同必然更慢——"
                 "这不是策略缺陷，而是<b>适用边界</b>。")
    if slower:
        ordered = sorted(slower, key=lambda t: t[3]["gain"])
        shown = ordered[:4]
        head = f"本数据集里共有 {len(slower)} 个这样的档位"
        head += f"，下列最严重的 {len(shown)} 个：" if len(ordered) > len(shown) else "："
        items.append("<b>反例（需在论文中如实报告）</b>。" + head)
        for kind, kind_cn, suite_name, row, rows in shown:
            items.append(
                f"{esc(kind_cn)} · {esc(suite_name)} · "
                f"{esc(row['label'])} 档：协同 {ms(row['collab_mean'])} 反而慢于本地 "
                f"{ms(row['local_mean'])}（{pct_signed(row['gain'])}）；"
                f"{_rank_phrase(kind, row, rows)}，{_neg_mechanism(kind)}"
                f"（参数：{esc(fmt_params(row['params'], limit=4))}）。")
    else:
        items.append("<b>反例</b>：本数据集中没有出现协同更慢的档位；"
                     "但这不排除在更小工作量的档位上发生，建议后续补测更细的低负载档位。")
    items.append("<b>后续工作</b>：① 每档增加重复次数并给出 95% 置信区间；"
                 "② 为协同单独记录“纯执行时间”，把轮询与排队从墙钟中剥离，"
                 "与本地做同口径对比；③ 在空载节点上重跑以消除混布干扰；"
                 "④ 通信类补做真实链路带宽测量，替换 <code>bandwidth_mbps</code> 建模假设；"
                 "⑤ 补充逐档 checksum / bpCR 一致性导出，使正确性校验可量化。")
    return '<div class="insights"><ul>' + "".join(f"<li>{i}</li>" for i in items) + \
           "</ul></div>"


def render_appendix(suites, notes):
    rows = []
    for suite in suites:
        overall = suite_overall(suite)
        info = suite_gain(suite)
        rows.append(
            f'<tr><td>{esc(suite_title(suite))}</td>'
            f'<td>{esc(suite.get("suite_id", DASH))}</td>'
            f'<td>{esc(kind_label(suite.get("kind")))}</td>'
            f'<td>{esc(suite.get("load_axis") or LOAD_AXIS_FALLBACK.get(str(suite.get("kind")), DASH))}</td>'
            f'<td>{esc(len(as_list(suite.get("specs"))))}</td>'
            f'<td>{esc(len(as_list(suite.get("configs"))))}</td>'
            f'<td>{ms(info["collab_mean"])}</td>'
            f'<td>{ms(info["local_mean"])}</td>'
            f'<td>{gain_html(info["gain"])}</td></tr>')
    table = ('<div class="tbl-wrap"><table><thead><tr><th>套件</th><th>suite_id</th>'
             "<th>类别</th><th>负载轴</th><th>档位数</th><th>有数据的档位数</th>"
             "<th>协同 mean</th><th>本地 mean</th><th>差值</th>"
             "</tr></thead><tbody>" + ("".join(rows) or
             '<tr><td colspan="9">数据文件中没有套件。</td></tr>')
             + "</tbody></table></div>")
    if notes:
        table += ('<div class="tbl-wrap"><table><thead><tr><th>环境记录</th>'
                  '<th class="wrapcell">值</th></tr></thead><tbody>'
                  + "".join(f'<tr><td>{esc(k)}</td>'
                            f'<td class="wrapcell">{esc(v)}</td></tr>'
                            for k, v in notes.items())
                  + "</tbody></table></div>")
    return table


# --------------------------------------------------------------------------- #
# 主渲染
# --------------------------------------------------------------------------- #
def render_report(data, title=TITLE_DEFAULT, min_gain=GAIN_TARGET, data_path=None):
    """把数据 dict 渲染成完整 HTML 文本。缺失字段全部容错。"""
    data = as_dict(data)
    suites = ordered_suites(data)
    target = float(min_gain)
    cluster = as_dict(data.get("cluster"))
    notes = as_dict(data.get("notes"))

    generated = data.get("generated_at")
    generated_dt = parse_dt(generated)
    generated_txt = (generated_dt.strftime("%Y-%m-%d %H:%M:%S %z")
                     if generated_dt else (str(generated) if generated else DASH))
    rendered_txt = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %z")
    site = data.get("site")

    chips = [f"数据生成时间 <b>{esc(generated_txt)}</b>",
             f"报告渲染时间 <b>{esc(rendered_txt)}</b>",
             f"套件数 <b>{len(suites)}</b>",
             f"验收线 <b>协同比本地快 ≥{target:.0f}%</b>"]
    if site:
        chips.append(f"站点 <b>{esc(site)}</b>")
    if cluster.get("nodes"):
        chips.append(f"集群节点 <b>{len(as_list(cluster.get('nodes')))}</b>")
    if data_path:
        chips.append(f"数据文件 <b>{esc(data_path)}</b>")

    head = (
        '<header class="masthead">'
        f"<h1>{esc(title)}</h1>"
        '<p class="sub">云（数据中心）· 边（医院节点）· 端（诊所 Pod）三级协同推理 · '
        "四类冻结任务套件 · 单一自变量：调度策略（云边端协同 vs 本地执行）</p>"
        '<div class="chips">' + "".join(f'<span class="chip">{c}</span>' for c in chips)
        + "</div></header>")

    sections = [
        head,
        h2("1", "结论摘要（先看这里）"),
        render_summary(suites, target),
        h2("2", "方法与实验设计"),
        "<h3>2.1 集群拓扑与镜像版本</h3>",
        render_method(data, suites, target),
        h2("3", "四类任务逐类详细对比"),
    ]
    if not suites:
        sections.append('<div class="note">数据文件中没有套件数据'
                        "（<code>suites</code> 缺失或为空），第 3、4 节无可渲染内容。</div>")
    for idx, suite in enumerate(suites, start=1):
        sections.append(render_kind_section(suite, idx, target))

    sections += [
        h2("4", "总体对比"),
        render_overall(suites, target),
        h2("5", "正确性校验"),
        render_correctness(data, suites),
        h2("6", "口径与假设"),
        render_assumptions(),
        render_split_analysis(data),
        render_diagnosis_ab(data),
        render_edge_constraint(data),
        h2("7", "局限与后续工作"),
        render_limitations(suites, target),
        h2("附录 A", "数据清单与环境记录"),
        render_appendix(suites, notes),
        '<footer>本报告由 <code>test/render_benchmark_report.py</code> 生成：'
        "纯标准库、单文件、无外部资源（无 CDN / 字体 / 脚本 / 图表库）。"
        "所有数字均直接取自基准数据 JSON；缺失字段渲染为 "
        f"{DASH} 或“数据不足”，不做任何填补或外推。<br>"
        f"验收线：协同 mean 比本地 mean 快 ≥{target:.0f}%。</footer>",
    ]
    return ("<!DOCTYPE html>\n"
            '<html lang="zh-CN">\n<head>\n<meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
            f"<title>{esc(title)}</title>\n<style>{CSS}</style>\n</head>\n<body>\n"
            '<div class="wrap">\n' + "\n".join(sections) + "\n</div>\n</body>\n</html>\n")


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def load_data(path):
    """读 JSON；根为 list 或套了一层包装时也能容忍。"""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(raw, list):
        return {"suites": raw}
    if isinstance(raw, dict) and "suites" not in raw:
        for key in ("data", "result", "payload", "report"):
            nested = raw.get(key)
            if isinstance(nested, dict) and "suites" in nested:
                merged = dict(nested)
                for extra_key in ("cluster", "notes", "site", "generated_at"):
                    if extra_key in raw and extra_key not in merged:
                        merged[extra_key] = raw[extra_key]
                return merged
    return raw


def build_parser():
    ap = argparse.ArgumentParser(
        prog="render_benchmark_report.py",
        description="渲染云边端协同推理基准报告（自包含单文件 HTML，纯标准库）")
    ap.add_argument("--data", required=True, help="基准对比 JSON 路径")
    ap.add_argument("--out", default="benchmark_report.html",
                    help="输出 HTML 路径（默认：当前目录 benchmark_report.html）")
    ap.add_argument("--title", default=TITLE_DEFAULT, help="报告标题")
    ap.add_argument("--min-gain", type=float, default=GAIN_TARGET,
                    help="验收线：协同须比本地快的百分比（默认 10）")
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    data_file = Path(args.data)
    if not data_file.is_file():
        print(f"错误：数据文件不存在：{data_file}", file=sys.stderr)
        return 2
    try:
        data = load_data(data_file)
    except (OSError, ValueError) as exc:
        print(f"错误：读取/解析 JSON 失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    html_text = render_report(data, title=args.title, min_gain=args.min_gain,
                              data_path=str(data_file))
    out_file = Path(args.out)
    if out_file.parent and str(out_file.parent) not in ("", "."):
        out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(html_text, encoding="utf-8")

    suites = ordered_suites(data)
    print(f"已写入 {out_file.resolve()}（{len(html_text.encode('utf-8')):,} 字节，"
          f"{len(html_text):,} 字符）")
    print(f"套件 {len(suites)} 个，验收线 ≥{args.min_gain:.0f}%：")
    for suite in suites:
        info = suite_gain(suite)
        css, txt = verdict_of(info["gain"], args.min_gain)
        print(f"  - {kind_label(suite.get('kind')):6s} "
              f"协同 {ms(info['collab_mean'])} vs 本地 {ms(info['local_mean'])} "
              f"→ {pct_signed(info['gain'])} [{txt}]")
    if not suites:
        print("  （数据文件中没有 suites，报告按空数据渲染）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
