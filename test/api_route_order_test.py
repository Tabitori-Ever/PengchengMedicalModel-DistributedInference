#!/usr/bin/env python3
"""API 路由顺序回归测试：静态路径不能被更早声明的动态路径吞掉。

背景（真实踩过的坑）：`/api/plans/runs` 与 `/api/plans/{plan_id}` 都是 2 段路径，
Starlette 按**声明顺序**匹配。`{plan_id}` 写在前面时，`GET /api/plans/runs`
会命中它并因为 plan_id="runs" 不存在而返回 404——列表接口直接消失。
这种缺陷不写测试很难发现：动态路由本身工作正常，只有那一个静态兄弟 404。

本脚本扫描 app 上注册的全部路由，找出"会被更早的动态路由匹配掉的静态路径"。

用法：
    python test/api_route_order_test.py
"""
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
BACKEND = HERE.parent / "benchmark" / "backend"
sys.path.insert(0, str(BACKEND))


def segments(path: str):
    return [p for p in path.strip("/").split("/") if p != ""]


def matches(pattern_segs, concrete_segs) -> bool:
    """动态路由 pattern 是否能匹配具体路径 concrete。"""
    if len(pattern_segs) != len(concrete_segs):
        return False
    for pat, con in zip(pattern_segs, concrete_segs):
        if pat.startswith("{") and pat.endswith("}"):
            if not con:            # path 参数不匹配空段
                return False
            continue
        if pat != con:
            return False
    return True


def main() -> int:
    import app as app_mod

    routes = []
    for r in app_mod.app.routes:
        path = getattr(r, "path", None)
        methods = sorted(getattr(r, "methods", None) or [])
        if path and path.startswith("/api"):
            routes.append((path, methods))

    print(f"扫描 {len(routes)} 条 /api 路由")

    fails = []
    # 必须按**下标**遍历：dict 以 path 为键会丢掉重复注册的同名路由，
    # 而"重复注册 + 顺序颠倒"恰恰就是要抓的情形。
    for si, (spath, smethods) in enumerate(routes):
        if "{" in spath:
            continue
        ssegs = segments(spath)
        shadowed = False
        for di, (dpath, dmethods) in enumerate(routes):
            if di >= si or "{" not in dpath:
                continue
            if not (set(smethods) & set(dmethods)):
                continue                      # 方法不重叠，不会互相吞
            if matches(segments(dpath), ssegs):
                fails.append(
                    f"{spath} [{','.join(smethods)}] 被更早声明的 {dpath} 遮蔽"
                    f"（第 {di} 条 vs 第 {si} 条）")
                shadowed = True
        if not shadowed:
            print(f"  ✓ {spath} 未被更早的动态路由遮蔽")

    # 同一 path+method 重复注册本身就可疑（生效的是第一条）
    seen = {}
    for i, (p, m) in enumerate(routes):
        key = (p, ",".join(m))
        if key in seen:
            fails.append(f"{p} [{key[1]}] 重复注册（第 {seen[key]} 条与第 {i} 条）")
        else:
            seen[key] = i

    # 关键接口必须真的注册上
    must = {"/api/plans", "/api/plans/runs", "/api/plans/{plan_id}",
            "/api/plans/runs/{plan_run_id}", "/api/suites", "/api/runs"}
    have = {p for p, _ in routes}
    for path in sorted(must - have):
        fails.append(f"缺少接口 {path}")

    print()
    if fails:
        print("❌ 路由顺序检查未通过：")
        for f in fails:
            print("  -", f)
        return 1
    print("✅ 路由顺序检查通过（无静态路径被动态路由遮蔽）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
