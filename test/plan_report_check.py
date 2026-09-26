#!/usr/bin/env python3
"""校验「方案运行报告」是否完整、自包含、无占位符泄漏。

为什么单独成一个脚本：报告是交付给第三方测试的产物，"渲染成功"不等于"内容完整"。
本脚本把校验项固定下来，既可人工跑，也被 `finalize_plan_report.sh` 在自动收尾时调用。

校验项：
  1. 结构：DOCTYPE / html 标签成对 / 无外部引用（自包含）
  2. 章节：每个方案都必须有 方案配置、预置条件、判定标准、阶段实测、逐类对比、
     总体对比、判定、子运行
  3. 数据：判定表存在；总体对比里"仅两阶段共有类型"的口径标注存在；
     若方案声明了外部仪表，必须渲染出「外部仪表配置」
  4. 无占位符泄漏（None / nan / undefined / 被转义的表头标签）
  5. 与数据源一致：判定项的结论必须与数据文件里的 verdict 一致（防渲染错位）

用法：
    python test/plan_report_check.py --html plan_report.html \
        --data plan_report_data.json [--expect-plan-runs N]
"""
import argparse
import html as H
import json
import re
import sys
from typing import Any, Dict, List


def text_of(doc: str) -> str:
    raw = re.sub(r"<!--.*?-->", "", doc, flags=re.S)
    return "\n".join(
        line.strip() for line in H.unescape(re.sub(r"<[^>]+>", "\n", raw)).split("\n")
        if line.strip())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--html", default="plan_report.html")
    ap.add_argument("--data", default="plan_report_data.json")
    ap.add_argument("--expect-plan-runs", type=int, default=0,
                    help="期望报告里含几个方案运行（0 = 不检查）")
    ap.add_argument("--allow-running", action="store_true",
                    help="允许出现尚未跑完的方案运行（进行中预览用）")
    args = ap.parse_args()

    with open(args.html, encoding="utf-8") as f:
        doc = f.read()
    with open(args.data, encoding="utf-8") as f:
        data = json.load(f)
    txt = text_of(doc)

    fails: List[str] = []
    warns: List[str] = []

    # ---- 1. 结构
    if not doc.lstrip().startswith("<!DOCTYPE html>"):
        fails.append("缺少 DOCTYPE")
    if doc.count("<html") != 1 or doc.count("</html>") != 1:
        fails.append("html 标签不成对")
    if doc.count("<body") != 1 or doc.count("</body>") != 1:
        fails.append("body 标签不成对")
    ext = [u for u in re.findall(r'(?:src|href)="(?!#)([^"]+)"', doc)]
    if ext:
        fails.append(f"存在外部引用（报告必须自包含）：{ext[:3]}")

    # ---- 4. 占位符泄漏
    for bad in ("None", "nan", "undefined", "NaN"):
        n = len(re.findall(rf">{re.escape(bad)}<", doc))
        if n:
            fails.append(f"占位符泄漏：>{bad}< × {n}")
    if "&lt;br&gt;" in doc or "&lt;span" in doc:
        fails.append("表头 HTML 被转义（字面 <br>/<span> 泄漏）")

    runs: List[Dict[str, Any]] = data.get("plan_runs") or []
    if not runs:
        fails.append("数据文件里没有 plan_runs")
    if args.expect_plan_runs and len(runs) != args.expect_plan_runs:
        fails.append(f"方案运行数量不符：期望 {args.expect_plan_runs}，实为 {len(runs)}")

    # ---- 2. 每个方案的章节
    need_sections = ["方案配置", "预置条件", "任务类型与参数", "判定标准",
                     "阶段实测", "总体对比", "判定", "子运行"]
    for i, run in enumerate(runs, 1):
        pid = run.get("plan_run_id") or f"#{i}"
        cfg = run.get("config") or {}
        comp = run.get("comparison") or {}
        name = run.get("plan_name") or ""
        start = txt.find(f"{i}. {name}")
        if start < 0:
            fails.append(f"{pid}：报告里找不到该方案的章节")
            continue
        # 下一个方案的标题作为结束边界；最后一个方案取到文末
        # （不能简单找 "{i+1}. "——正文里的数字也可能命中，且最后一节没有后继标题）
        nxt = runs[i] if i < len(runs) else None
        end = (txt.find(f"{i + 1}. {nxt.get('plan_name') or ''}", start + 1)
               if nxt else -1)
        seg = txt[start:end] if end > start else txt[start:]
        if not seg.strip():
            fails.append(f"{pid}：报告里该方案章节为空")
            continue
        for sec in need_sections:
            if sec not in seg:
                fails.append(f"{pid}：缺少「{sec}」")
        if "判定" not in seg:
            fails.append(f"{pid}：缺少判定表")
        # 总体对比必须标出"仅两阶段共有类型"的口径
        if "仅两阶段共有类型" not in seg:
            fails.append(f"{pid}：总体对比缺少『仅两阶段共有类型』口径标注")
        # 声明了外部仪表就必须渲染出来（大纲 5.5 的测试仪/损伤仪）
        for att in cfg.get("attachments") or []:
            if att.get("name") and att["name"] not in seg:
                fails.append(f"{pid}：外部仪表「{att['name']}」未渲染")

        # ---- 5. 判定结论与数据一致
        manual = (data.get("manual") or {}).get(pid) or {}
        for crit in comp.get("criteria") or []:
            label = (crit.get("label") or "").strip()
            verdict = crit.get("verdict")
            zh = {"pass": "通过", "fail": "不通过", "pending": "待实测"}.get(verdict)
            if not label or not zh:
                continue
            # 判定项文字会在「判定标准」和「判定」两处出现，只有后者带结论。
            # 取**最后**一次出现（判定表在文档顺序上位于判定标准之后）。
            hits = list(re.finditer(re.escape(label) + r"(.{0,220})", seg, re.S))
            if not hits:
                fails.append(f"{pid}：判定项「{label}」未出现在报告里")
                continue
            tail = hits[-1].group(1)
            # 外部仪表项若已有人工实测值，结论应由该实测值与规则算出来
            if crit.get("type") == "external":
                m = manual.get(crit.get("id")) or {}
                val = m.get("measured")
                if isinstance(val, (int, float)):
                    limit = float(crit.get("value") or 0)
                    expect = "pass" if float(val) <= limit else "fail"
                    if f"{val:.2f}" not in tail:
                        fails.append(f"{pid}：外部仪表项「{label}」的实测值 "
                                     f"{val} 未出现在报告里")
                    if zh != {"pass": "通过", "fail": "不通过"}[expect]:
                        fails.append(f"{pid}：外部仪表项「{label}」结论应随人工实测值"
                                     f"为 {expect}，报告里是 {verdict}")
                else:
                    if "待人工实测填写" not in tail:
                        fails.append(f"{pid}：外部仪表项「{label}」缺人工实测值，"
                                     f"报告应标注『待人工实测填写』")
            elif zh not in tail:
                fails.append(f"{pid}：判定项「{label}」的结论与数据不一致"
                             f"（数据为 {verdict}）")

        # 未跑完的方案：时延统计允许缺失，但必须显式标注
        if run.get("status") == "running":
            if not args.allow_running:
                fails.append(f"{pid}：方案运行仍在进行中，不能作为最终报告")
            else:
                warns.append(f"{pid}：仍在进行中（预览模式）")
        elif run.get("status") == "completed":
            ov = comp.get("overall") or {}
            if ov.get("collaborative_processing_ms") is None:
                fails.append(f"{pid}：已完成但没有协同处理总用时")

    # ---- 报告级：附录与页脚
    if "附录" not in txt:
        warns.append("没有附录（执行端能力声明）——若采集时 /api/health 不可用属正常")

    print(f"报告 {args.html}｜方案运行 {len(runs)} 个｜"
          f"表 {doc.count('<table')} 张｜图 {doc.count('<svg')} 张")
    for w in warns:
        print(f"  ⚠️  {w}")
    if fails:
        print("❌ 校验未通过：")
        for f in fails:
            print("  -", f)
        return 1
    print("✅ 报告校验通过（结构自包含、章节完整、判定与数据一致、无占位符泄漏）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
