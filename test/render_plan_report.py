#!/usr/bin/env python3
"""把「测试方案运行」的采集数据渲染成自包含 HTML 报告。

对应大纲 §5.4 协同调度测试 与 §5.5 医疗智联专网测试：每个方案一节，
给出配置快照、两阶段实测、逐类对比、判定与子运行清单。

口径说明：报告只呈现数据，不写解释性推断——结论由第三方测试判读。
数据全部来自采集 JSON（`collect_plan_data.py` 的产物），渲染脚本不重算统计量。

用法：
    python test/render_plan_report.py --data plan_report_data.json \
        --out plan_report.html
"""
import argparse
import html
import json
import os
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional

DASH = "—"
MODE_ZH = {"local": "本地执行", "collaborative": "云边端协同"}
KIND_ZH = {"diagnosis": "医疗模型远程调度（诊断）",
           "compute": "医疗数据处理（算力）",
           "sync": "患者数据库同步（通信）",
           "routine": "医疗信息远程查询（日常）"}
CRIT_RULE = {"gain_gt": "> {v}{u}", "complete_gt": "> {v}{u}",
             "complete_gte": "≥ {v}{u}", "external": "≤ {v}{u}"}
VERDICT_ZH = {"pass": "通过", "fail": "不通过", "pending": "待实测"}


# --------------------------------------------------------------------- helpers
def esc(v: Any) -> str:
    return html.escape(str(v if v is not None else DASH))


def ms(v: Any, nd: int = 1) -> str:
    if v is None:
        return DASH
    try:
        return f"{float(v):,.{nd}f} ms"
    except (TypeError, ValueError):
        return DASH


def sec(v: Any, nd: int = 1) -> str:
    if v is None:
        return DASH
    try:
        return f"{float(v) / 1000.0:,.{nd}f} s"
    except (TypeError, ValueError):
        return DASH


def num(v: Any, nd: int = 0) -> str:
    if v is None:
        return DASH
    try:
        return f"{float(v):,.{nd}f}"
    except (TypeError, ValueError):
        return DASH


def pct(v: Any, nd: int = 1) -> str:
    if v is None:
        return DASH
    try:
        return f"{float(v):,.{nd}f}%"
    except (TypeError, ValueError):
        return DASH


def signed(v: Any, nd: int = 2) -> str:
    if v is None:
        return DASH
    try:
        return f"{float(v):+,.{nd}f}%"
    except (TypeError, ValueError):
        return DASH


def ratio(a: Any, b: Any) -> str:
    try:
        a, b = float(a), float(b)
        if b <= 0:
            return DASH
        return f"{a / b:,.2f}×"
    except (TypeError, ValueError):
        return DASH


def gain_cls(v: Any) -> str:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "na"
    return "up" if f > 0 else ("down" if f < 0 else "flat")


def table(head: List[str], rows: List[List[str]], caption: str = "",
          cls: str = "") -> str:
    # 表头**不转义**：这里全部是本脚本内的字面量，允许用 <br>/<span> 做两行表头。
    # 数据单元格一律走 esc()——用户数据不会进表头。
    th = "".join(f"<th>{h}</th>" for h in head)
    body = "".join(
        "<tr>" + "".join(f'<td class="{c}">{v}</td>' if c else f"<td>{v}</td>"
                         for v, c in zip(r, _cells(len(head))))
        + "</tr>" for r in rows)
    cap = f"<caption>{caption}</caption>" if caption else ""
    return (f'<div class="tw"><table class="{cls}">{cap}'
            f"<thead><tr>{th}</tr></thead><tbody>{body}</tbody></table></div>")


def _cells(n: int) -> List[str]:
    """默认列样式：数字列右对齐等宽，其余左对齐。"""
    return [""] * n


def numrow(vals: List[str]) -> List[str]:
    return vals


# ------------------------------------------------------------------- 图表（手写 SVG）
def grouped_bars(labels: List[str], collab: List[Optional[float]],
                 local: List[Optional[float]], title: str) -> str:
    vals = [v for v in list(collab) + list(local) if isinstance(v, (int, float))]
    if not labels or not vals:
        return '<div class="empty">无数据</div>'
    W, H = 820, 340
    padL, padR, padT, padB = 96, 28, 34, 76
    plotW, plotH = W - padL - padR, H - padT - padB
    top = max(vals) * 1.12

    def y(v: float) -> float:
        return padT + plotH - (v / top) * plotH

    slot = plotW / len(labels)
    bw = min(56.0, slot * 0.3)
    parts = [f'<svg viewBox="0 0 {W} {H}" class="chart" role="img" '
             f'aria-label="{esc(title)}">']
    for i in range(5):
        v = top * i / 4
        parts.append(f'<line class="grid" x1="{padL}" x2="{W - padR}" '
                     f'y1="{y(v):.1f}" y2="{y(v):.1f}"/>')
        parts.append(f'<text class="axis" x="{padL - 10}" y="{y(v) + 4:.1f}" '
                     f'text-anchor="end">{v:,.0f}</text>')
    for i, lab in enumerate(labels):
        cx = padL + slot * i + slot / 2
        for j, (ser, cls) in enumerate(((collab, "b-collab"), (local, "b-local"))):
            v = ser[i] if i < len(ser) else None
            x = cx - bw - 6 + j * (bw + 12)
            if not isinstance(v, (int, float)):
                parts.append(f'<rect class="{cls} miss" x="{x:.1f}" '
                             f'y="{padT + plotH - 3}" width="{bw:.1f}" height="3"/>')
                continue
            h = max(1.0, (v / top) * plotH)
            parts.append(f'<rect class="{cls}" x="{x:.1f}" y="{y(v):.1f}" '
                         f'width="{bw:.1f}" height="{h:.1f}"/>')
            parts.append(f'<text class="bv" x="{x + bw / 2:.1f}" '
                         f'y="{y(v) - 7:.1f}" text-anchor="middle">{v:,.0f}</text>')
        parts.append(f'<text class="xl" x="{cx:.1f}" y="{padT + plotH + 20}" '
                     f'text-anchor="middle">{esc(lab)}</text>')
    parts.append("</svg>")
    legend = ('<div class="lg"><span><i class="sw b-collab"></i>云边端协同</span>'
              '<span><i class="sw b-local"></i>本地执行</span>'
              '<span class="lgn">纵轴：处理总用时（ms）</span></div>')
    return "".join(parts) + legend


# ------------------------------------------------------------------- 报告正文
def render_plan(run: Dict[str, Any], idx: int) -> str:
    cfg = run.get("config") or {}
    comp = run.get("comparison") or {}
    ov = comp.get("overall") or {}
    phases = {p["phase"]: p for p in run.get("phases") or []}
    out: List[str] = []

    out.append(f'<h2>{idx}. {esc(run.get("plan_name"))}'
               f'<span class="tag">{esc(run.get("label") or "")}</span></h2>')
    out.append('<div class="kv">'
               f'<span>plan_run_id</span><b class="mono">{esc(run.get("plan_run_id"))}</b>'
               f'<span>状态</span><b>{esc(run.get("status"))}</b>'
               f'<span>创建</span><b>{esc(run.get("created_at"))}</b>'
               f'<span>结束</span><b>{esc(run.get("finished_at"))}</b>'
               "</div>")

    # ---- 方案配置
    out.append('<h3>方案配置</h3>')
    cfg_rows = [
        ["方案依据", esc(cfg.get("outline_ref"))],
        ["测试对象", esc(cfg.get("test_object"))],
        ["测试目的", esc(cfg.get("purpose"))],
        ["测试内容", esc(cfg.get("objective"))],
        ["测试时长", f'{num(cfg.get("duration_min"))} 分钟'],
        ["任务产生窗口", f'{num(cfg.get("generate_window_min"))} 分钟'],
        ["失败重传额度", f'{num(cfg.get("retries"))} 次（首次之外）'],
        ["发起设备", esc("、".join(f'{d.get("id")}({d.get("source")})'
                                   for d in cfg.get("devices") or []))],
        ["调度策略", " → ".join(MODE_ZH.get(s, s) for s in run.get("strategies") or [])],
    ]
    out.append(table(["项目", "取值"], cfg_rows))

    pre = cfg.get("preconditions") or []
    if pre:
        out.append('<h4>预置条件</h4><ol class="tight">'
                   + "".join(f"<li>{esc(x)}</li>" for x in pre) + "</ol>")

    kinds = cfg.get("kinds") or []
    if kinds:
        out.append('<h4>任务类型与参数</h4>')
        krows = []
        for k in kinds:
            params = " · ".join(f"{a}={b}" for a, b in (k.get("params") or {}).items())
            strat = " · ".join(MODE_ZH.get(s, s) for s in
                               (k.get("strategies") or []))
            krows.append([f'<b>{esc(KIND_ZH.get(k.get("kind"), k.get("kind")))}</b>',
                          esc(k.get("outline_name")), esc(strat),
                          f'{num(k.get("per_device"))}',
                          esc(params)])
        out.append(table(["任务类型", "对应大纲任务", "适用策略", "每设备任务数", "任务参数"],
                         krows))

    atts = cfg.get("attachments") or []
    if atts:
        out.append('<h4>外部仪表配置</h4>')
        arows = [[f'<b>{esc(a.get("name"))}</b>', esc(a.get("detail")),
                  esc(a.get("verified_by"))] for a in atts]
        out.append(table(["项目", "配置", "验证方"], arows))

    crit = cfg.get("criteria") or []
    if crit:
        out.append('<h4>判定标准</h4>')
        crows = [[esc(c.get("label")),
                  esc(CRIT_RULE.get(c.get("type"), "{v}{u}").format(
                      v=num(c.get("value"), 2), u=c.get("unit") or "")),
                  esc(c.get("source"))] for c in crit]
        out.append(table(["判定项", "要求", "测量方"], crows))

    # ---- 阶段实测
    out.append('<h3>阶段实测</h3>')
    prows = []
    progress = run.get("progress") or {}
    for ph in run.get("strategies") or []:
        p = phases.get(ph)
        if not p:
            # 阶段还没结束：用进度里的计数兜底，让"进行中"的报表也有意义
            g = progress.get(ph) or {}
            prows.append([
                f'<b>{MODE_ZH.get(ph, ph)}</b>',
                num(g.get("total")), num(g.get("completed")), num(g.get("failed")),
                pct(100.0 * (g.get("completed") or 0) / g["total"]
                    if g.get("total") else None),
                DASH, DASH, DASH, DASH, DASH, DASH, DASH,
            ])
            continue
        prows.append([
            f'<b>{MODE_ZH.get(ph, ph)}</b>',
            num(p.get("tasks_total")), num(p.get("completed")), num(p.get("failed")),
            pct(p.get("completion_pct")), num(p.get("retries")),
            ms(p.get("retry_ms_total"), 0), ms(p.get("processing_ms"), 0),
            ms(p.get("exec_ms"), 0), sec(p.get("total_wall_ms")),
            ms((p.get("latency") or {}).get("mean")),
            ms((p.get("latency") or {}).get("p95")),
        ])
    out.append('<p class="note">阶段处理总用时 = 该阶段<b>全部</b>任务之和；'
               '总体对比里的处理总用时只计<b>两阶段共有</b>的类型，两者口径不同。'
               '阶段未结束时只给出任务计数（时延统计要等该阶段跑完）。</p>')
    out.append(table(["阶段", "任务数", "完成", "失败", "完成率", "重传次数",
                      "重传耗时", "处理总用时<br><span class=\"hint\">该阶段全部任务</span>",
                      "执行总用时", "阶段墙钟",
                      "单任务均值", "p95"], prows))

    # ---- 负载画像（回答"这份负载到底有多重、比例如何"）
    per_kind = comp.get("per_kind") or []
    out.append('<h3>负载画像</h3>')
    kinds_cfg = cfg.get("kinds") or []
    per_device_total = sum(int(k.get("per_device") or 0) for k in kinds_cfg)
    lrows = []
    for ph in run.get("strategies") or []:
        p = phases.get(ph)
        g = progress.get(ph) or {}
        total = (p or {}).get("tasks_total") or g.get("total") or 0
        done = (p or {}).get("completed") or g.get("completed") or 0
        mean = ((p or {}).get("latency") or {}).get("mean")
        wall_ms = (p or {}).get("total_wall_ms")
        if not total:
            continue
        busy_s = (mean or 0) / 1000.0 * done
        wall_s = (wall_ms or 0) / 1000.0
        conc = (busy_s / wall_s) if wall_s else None
        units = len([k for k in kinds_cfg
                     if ph in (k.get("strategies") or [])]) * len(cfg.get("devices") or [])
        occ = (100.0 * conc / units) if (conc is not None and units) else None
        lrows.append([
            f'<b>{MODE_ZH.get(ph, ph)}</b>', num(total), num(units),
            f"{busy_s:,.0f} s", sec(wall_ms),
            (f"{conc:,.2f}" if conc is not None else DASH),
            pct(occ, 1) if occ is not None else DASH,
            ms(mean),
        ])
    if lrows:
        out.append(table(
            ["阶段", "任务数", "执行单元数", "累计任务时间", "阶段墙钟",
             "平均并发任务数", "单元平均占用率", "单任务均值"],
            lrows,
            caption="平均并发任务数 = 累计任务时间 / 阶段墙钟（越接近 0 说明系统越空闲、任务几乎不重叠）；"
                    "单元平均占用率 = 平均并发任务数 / 执行单元数。"
                    "「累计任务时间/墙钟」大于 1 表示任务确实在并行重叠。"))

    if kinds_cfg and per_device_total:
        krows = []
        order = {"routine": 0, "compute": 1, "diagnosis": 2, "sync": 3}
        for k in sorted(kinds_cfg, key=lambda x: order.get(x.get("kind"), 9)):
            per = int(k.get("per_device") or 0)
            share = (100.0 * per / per_device_total) if per_device_total else 0.0
            km = next((e for e in per_kind if e.get("kind") == k.get("kind")), None)
            lm = ((km or {}).get("modes") or {}).get("local") or {}
            cm = ((km or {}).get("modes") or {}).get("collaborative") or {}
            krows.append([
                f'<b>{esc(KIND_ZH.get(k.get("kind"), k.get("kind")))}</b>',
                num(per), pct(share), esc(k.get("outline_name")),
                " · ".join(MODE_ZH.get(x, x) for x in (k.get("strategies") or [])),
                ms(lm.get("mean")), ms(cm.get("mean")),
                num((km or {}).get("gain_pct"), 1) if (km or {}).get("gain_pct") is not None else DASH,
            ])
        out.append(table(
            ["任务类型", "每设备任务数", "占比", "对应大纲任务", "适用策略",
             "单任务均值(本地)", "单任务均值(协同)", "差值(%)"],
            krows,
            caption=f"任务比例：{' : '.join(str(int(k.get('per_device') or 0)) for k in kinds_cfg)}"
                    f"（按大纲任务名顺序）。占比 = 该类型每设备任务数 / 每设备任务总数。"))

    # ---- 逐类对比
    per_kind = comp.get("per_kind") or []
    if per_kind:
        out.append('<h3>逐类对比</h3>')
        labels = [KIND_ZH.get(e.get("kind"), e.get("kind")) for e in per_kind]
        coll = [(e.get("modes") or {}).get("collaborative", {}).get("processing_ms")
                for e in per_kind]
        loc = [(e.get("modes") or {}).get("local", {}).get("processing_ms")
               for e in per_kind]
        out.append(grouped_bars(labels, coll, loc, "逐类处理总用时对比"))
        rows = []
        for e in per_kind:
            l = (e.get("modes") or {}).get("local") or {}
            c = (e.get("modes") or {}).get("collaborative") or {}
            rows.append([
                f'<b>{esc(KIND_ZH.get(e.get("kind"), e.get("kind")))}</b>',
                num(l.get("n")), num(l.get("completed")), ms(l.get("processing_ms"), 0),
                ms(l.get("mean")), ms(l.get("p95")), num(l.get("retries")),
                num(c.get("n")), num(c.get("completed")), ms(c.get("processing_ms"), 0),
                ms(c.get("mean")), ms(c.get("p95")), num(c.get("retries")),
                f'<span class="g {gain_cls(e.get("gain_pct"))}">{signed(e.get("gain_pct"))}</span>',
                ratio(l.get("processing_ms"), c.get("processing_ms")),
            ])
        head = (["任务类型",
                 "本地 n", "本地完成", "本地处理总用时", "本地均值", "本地 p95", "本地重传",
                 "协同 n", "协同完成", "协同处理总用时", "协同均值", "协同 p95", "协同重传",
                 "差值", "比值"])
        out.append(table(head, rows))

    # ---- 总体
    out.append('<h3>总体对比</h3>')
    common = ov.get("common_kinds") or []
    only = ov.get("collaborative_only_kinds") or []
    orows = [
        ["处理总用时（含重传）<br><span class=\"hint\">仅两阶段共有类型 · 判定口径</span>",
         sec(ov.get("local_processing_ms")), sec(ov.get("collaborative_processing_ms")),
         f'<span class="g {gain_cls(ov.get("processing_gain_pct"))}">'
         f'{signed(ov.get("processing_gain_pct"))}</span>',
         ratio(ov.get("local_processing_ms"), ov.get("collaborative_processing_ms"))],
        ["执行总用时<br><span class=\"hint\">仅成功那次 · 仅共有类型</span>",
         sec(ov.get("local_exec_ms")), sec(ov.get("collaborative_exec_ms")),
         DASH, ratio(ov.get("local_exec_ms"), ov.get("collaborative_exec_ms"))],
        ["阶段墙钟<br><span class=\"hint\">含固定产生窗口</span>",
         sec(ov.get("local_wall_ms")), sec(ov.get("collaborative_wall_ms")),
         f'<span class="g {gain_cls(ov.get("wall_gain_pct"))}">'
         f'{signed(ov.get("wall_gain_pct"))}</span>',
         ratio(ov.get("local_wall_ms"), ov.get("collaborative_wall_ms"))],
        ["重传次数", num(ov.get("retries_local")), num(ov.get("retries_collaborative")),
         DASH, DASH],
    ]
    out.append(table(["对比项", "本地执行", "云边端协同", "差值", "比值"], orows))
    out.append(f'<p class="note">参与对比的任务类型：'
               f'{esc("、".join(KIND_ZH.get(k, k) for k in common) or DASH)}'
               + (f'；仅协同阶段执行：'
                  f'{esc("、".join(KIND_ZH.get(k, k) for k in only))}'
                  f'（{sec(ov.get("collaborative_only_processing_ms"))}，无本地基线）'
                  if only else "") + "。</p>")

    # ---- 判定
    crit_res = comp.get("criteria") or []
    if crit_res:
        out.append('<h3>判定</h3>')
        rows = []
        for c in crit_res:
            verdict = c.get("verdict")
            measured = DASH
            extra = ""
            if c.get("type") == "external":
                # 外部仪表项（大纲 5.5 的丢包率）平台不自动判定。人工实测值由
                # collect_plan_data.py --manual 写回判定项本身，这里只负责呈现；
                # 没有实测值就明确写"待人工实测填写"，避免被误读成平台漏测。
                val = c.get("measured")
                if isinstance(val, (int, float)):
                    measured = f'{num(val, 2)}{c.get("unit") or ""}'
                    who = c.get("measured_by") or "外部仪表"
                    when = c.get("measured_at") or ""
                    extra = f'（{who}{" · " + str(when) if when else ""}）'
                    if c.get("measured_note"):
                        extra += f' · {c["measured_note"]}'
                else:
                    measured = f'待人工实测填写（{c.get("source") or "外部仪表"}）'
            elif c.get("measured") is None:
                measured = DASH
            else:
                measured = f'{num(c.get("measured"), 2)}{c.get("unit") or ""}'
            if c.get("measured_wall_gain_pct") is not None:
                extra += f'（墙钟 {signed(c.get("measured_wall_gain_pct"))}）'
            rows.append([esc(c.get("label")),
                         esc(CRIT_RULE.get(c.get("type"), "{v}{u}").format(
                             v=num(c.get("value"), 2), u=c.get("unit") or "")),
                         esc(measured) + esc(extra),
                         f'<span class="v {esc(verdict)}">'
                         f'{esc(VERDICT_ZH.get(verdict, verdict))}</span>'])
        out.append(table(["判定项", "要求", "实测", "结论"], rows))
        if any(c.get("type") == "external" and c.get("measured") is None
               for c in crit_res):
            ext = [c for c in crit_res if c.get("type") == "external"]
            if ext:
                out.append('<p class="note">外部仪表项的实测值由人工填写：'
                           '在采集数据的 <span class="mono">manual.&lt;plan_run_id&gt;</span> '
                           '里按判定项 id 记录后重新渲染，报告会据此给出结论。</p>')

    # ---- 子运行
    runs = run.get("runs") or []
    if runs:
        out.append('<h3>子运行</h3>')
        rows = [[MODE_ZH.get(r.get("phase"), r.get("phase")), esc(r.get("unit")),
                 esc(r.get("kind")), f'<span class="mono">{esc(r.get("source"))}</span>',
                 num(r.get("repeats")), esc(r.get("status")),
                 f'<span class="mono sm">{esc(r.get("run_id"))}</span>'] for r in runs]
        out.append(table(["阶段", "执行单元", "任务类型", "发起方", "任务数", "状态", "run_id"],
                         rows))
    return "\n".join(out)


CSS = """
:root{--ink:#14161a;--ink2:#3d434d;--mut:#6b7280;--line:#d9dde3;--line2:#eceff3;
--bg:#fff;--sunk:#f6f7f9;--up:#1c7c4a;--dn:#b3341f;--warn:#a8620a;--acc:#2b5f9e;}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
font:14px/1.6 -apple-system,"Segoe UI","Noto Sans CJK SC","Microsoft YaHei",sans-serif}
.wrap{max-width:1180px;margin:0 auto;padding:36px 30px 70px}
h1{font-size:25px;margin:0 0 6px;letter-spacing:.02em}
h2{font-size:20px;margin:36px 0 12px;padding-bottom:8px;border-bottom:2px solid var(--ink)}
h3{font-size:16px;margin:26px 0 10px;color:var(--ink2)}
h4{font-size:13px;margin:18px 0 8px;color:var(--mut);letter-spacing:.06em}
.sub{color:var(--mut);font-size:13px;margin-bottom:4px}
.tag{margin-left:10px;font-size:12px;font-weight:400;color:var(--mut)}
.kv{display:flex;flex-wrap:wrap;gap:6px 14px;margin:10px 0 4px;font-size:12.5px}
.kv span{color:var(--mut)}
.kv b{font-weight:600}
.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
.sm{font-size:11px}
.tw{overflow-x:auto;border:1px solid var(--line);border-radius:3px;margin:10px 0}
table{border-collapse:collapse;width:100%;font-size:13px}
caption{caption-side:top;text-align:left;padding:8px 10px;color:var(--mut);font-size:12px}
th,td{border-bottom:1px solid var(--line2);padding:7px 9px;text-align:right;
white-space:nowrap}
th:first-child,td:first-child{text-align:left}
thead th{background:var(--sunk);font-size:11px;color:var(--mut);
letter-spacing:.04em;font-weight:600}
tbody tr:last-child td{border-bottom:none}
tbody tr:nth-child(even) td{background:#fcfcfd}
.g{font-weight:600}
.g.up{color:var(--up)}.g.down{color:var(--dn)}.g.flat,.g.na{color:var(--mut)}
.v{font-weight:600;padding:1px 8px;border-radius:9px;font-size:12px}
.v.pass{color:var(--up);background:rgba(28,124,74,.1)}
.v.fail{color:var(--dn);background:rgba(179,52,31,.09)}
.v.pending{color:var(--warn);background:rgba(168,98,10,.1)}
.chart{width:100%;height:auto;display:block}
.chart .grid{stroke:var(--line2);stroke-width:1}
.chart .axis{fill:var(--mut);font-size:10px}
.chart .xl{fill:var(--ink2);font-size:11px}
.chart .bv{fill:var(--ink2);font-size:10px}
.b-collab{fill:var(--acc)}.b-local{fill:#8a8f98}
rect.miss{opacity:.22}
.lg{display:flex;gap:18px;flex-wrap:wrap;color:var(--mut);font-size:12px;
margin:4px 0 0}
.lg .sw{display:inline-block;width:10px;height:10px;margin-right:5px}
.lg .lgn{margin-left:auto}
ol.tight{margin:6px 0 0;padding-left:22px}
ol.tight li{margin:2px 0}
.note{color:var(--mut);font-size:12.5px;margin:8px 0 0}
.hint{color:var(--mut);font-size:10.5px;font-weight:400;letter-spacing:0}
.empty{color:var(--mut);padding:16px;border:1px dashed var(--line);border-radius:3px}
footer{margin-top:44px;padding-top:14px;border-top:1px solid var(--line);
color:var(--mut);font-size:12px}
"""


def render(data: Dict[str, Any]) -> str:
    runs = data.get("plan_runs") or []
    env = data.get("env") or {}
    parts = ['<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8"/>',
             '<meta name="viewport" content="width=device-width, initial-scale=1"/>',
             "<title>AI诊疗云边端协同应用 · 测试报告</title>",
             f"<style>{CSS}</style></head><body><div class=\"wrap\">"]
    parts.append("<h1>AI诊疗云边端协同应用 · 测试报告</h1>")
    parts.append(f'<div class="sub">生成时间 {esc(data.get("generated_at"))}'
                 f' · 数据来源 {esc(data.get("site"))}'
                 f' · 方案运行 {len(runs)} 个'
                 f' · 站点版本 {esc(env.get("site_version"))}</div>')
    for i, run in enumerate(runs, 1):
        parts.append(render_plan(run, i))
    # ---- 跨方案：负载敏感度（同一任务类在不同负载下的增益对比）
    matrix: Dict[str, Dict[str, Any]] = {}
    plans_meta = []
    for run in runs:
        cfg = run.get("config") or {}
        comp = run.get("comparison") or {}
        phases = {p["phase"]: p for p in run.get("phases") or []}
        busy = sum((p.get("latency") or {}).get("mean", 0) / 1000.0
                   * (p.get("completed") or 0) for p in phases.values())
        wall = sum((p.get("total_wall_ms") or 0) for p in phases.values()) / 1000.0
        conc = (busy / wall) if wall else None
        title = f'{run.get("plan_name")}'[:28]
        if run.get("label"):
            title += f'（{run["label"]}）'
        plans_meta.append((run.get("plan_run_id"), title, conc,
                           int(cfg.get("generate_window_min") or 0),
                           sum(int(k.get("per_device") or 0)
                               for k in cfg.get("kinds") or []) * len(cfg.get("devices") or [])))
        for e in comp.get("per_kind") or []:
            matrix.setdefault(e.get("kind"), {})[run.get("plan_run_id")] = e.get("gain_pct")
    if len(plans_meta) >= 2 and matrix:
        parts.append("<h2>负载敏感度对比</h2>")
        parts.append('<p class="note">同一任务类在不同负载强度下的协同增益（正 = 协同更快）。'
                     '负载强度用「累计任务时间 / 阶段墙钟」表示的平均并发任务数刻画：'
                     '数值越小系统越空闲，比较就越被固定开销主导。</p>')
        head = ["任务类型"] + [m[1] for m in plans_meta]
        order = {"routine": 0, "compute": 1, "diagnosis": 2, "sync": 3}
        rows = []
        for kind in sorted(matrix, key=lambda k: order.get(k, 9)):
            cells = [f'<b>{esc(KIND_ZH.get(kind, kind))}</b>']
            for pid, _, _, _, _ in plans_meta:
                g = matrix[kind].get(pid)
                cells.append(
                    f'<span class="g {gain_cls(g)}">{signed(g)}</span>'
                    if g is not None else f'{DASH}<span class="hint">（无本地基线）</span>')
            rows.append(cells)
        parts.append(table(head, rows))
        meta_rows = [[esc(m[1]), num(m[3]), num(m[4]),
                      (f"{m[2]:,.2f}" if m[2] is not None else DASH)]
                     for m in plans_meta]
        parts.append(table(["方案", "产生窗口(分)", "任务数/阶段", "平均并发任务数"],
                           meta_rows,
                           caption="负载强度按方案对比：平均并发任务数 = 累计任务时间 / 阶段墙钟。"))
        parts.append(
            '<p class="note">读法：<b>加密到达（提高并发）本身不会让协同变好</b>——协同路径的每个任务都要经'
            '调度器中转（提交 → 编排 → 轮询），这条集中链路不随负载扩展；而本地执行是各 Pod 直接执行、'
            '相互独立。因此在"轻任务 + 高并发"下协同反而更差。真正决定胜负的是<b>单任务体量与固定开销之比</b>：'
            '单任务越重，集中编排开销被摊得越薄，协同的并行优势才显现。'
            '下表按任务类型给出不同负载下的增益，可用于判断各类型的适用边界。</p>')
        parts.append('<h3>任务比例的设计依据（V2 忙时混合负载）</h3>')
        ratio_rows = [
            ["医疗信息远程查询（日常）", "4", "最高",
             "医护人员随时查询检验/影像结果，请求频次最高、单次体量小"],
            ["医疗数据处理（算力）", "3", "较高",
             "监护与检验数据持续产生，需要批量处理，单次体量中等"],
            ["远端医疗大模型调用（诊断）", "2", "中",
             "按需触发的患者级推理，单次体量高但频次低于前两者"],
            ["患者数据库同步（通信）", "1", "最低",
             "周期性同步（而非持续），单次体量最大、频次最低"],
        ]
        parts.append(table(["任务类型", "比例", "现实频次", "依据"], ratio_rows,
                           caption="比例 = 每设备每类任务数之比（V2 取 24:18:12:6）。"
                                   "大纲原文四类等量，未反映真实业务的频次差异。"))

    images = env.get("images") or {}
    if images:
        parts.append("<h2>附录 · 平台镜像版本</h2>")
        rows = [[f'<span class="mono">{esc(k)}</span>', esc(v)]
                for k, v in sorted(images.items())]
        parts.append(table(["组件", "镜像 tag"], rows))
    caps = env.get("pod_capabilities") or {}
    if caps:
        parts.append("<h2>附录 · 执行端能力声明</h2>")
        rows = [[f'<span class="mono">{esc(k)}</span>',
                 " · ".join(f"{a}={b}" for a, b in sorted((v or {}).items()))]
                for k, v in sorted(caps.items())]
        parts.append(table(["实体", "capabilities"], rows))
    parts.append('<footer>本报告由 <span class="mono">test/render_plan_report.py</span> '
                 '从采集数据渲染生成；所有数字直接取自数据文件，未做重算、填补或外推。'
                 "时延口径为站点侧墙钟 <span class=\"mono\">client_total_ms</span>。"
                 "</footer>")
    parts.append("</div></body></html>")
    return "\n".join(parts)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="plan_report_data.json")
    ap.add_argument("--out", default="plan_report.html")
    args = ap.parse_args()
    with open(args.data, encoding="utf-8") as f:
        data = json.load(f)
    doc = render(data)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(doc)
    print(f"已写入 {os.path.abspath(args.out)}"
          f"（{os.path.getsize(args.out):,} 字节，{len(doc):,} 字符）")
    for run in data.get("plan_runs") or []:
        comp = run.get("comparison") or {}
        ov = comp.get("overall") or {}
        print(f"  {run.get('plan_name')} [{run.get('status')}] "
              f"本地 {sec(ov.get('local_processing_ms'))} → 协同 "
              f"{sec(ov.get('collaborative_processing_ms'))} · "
              f"{signed(ov.get('processing_gain_pct'))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
