#!/usr/bin/env bash
# 前端渲染自检：抓站点真实响应 → 服务端渲染各区块 → 断言关键字段
#
# 本机没有 Chromium、也没有外网装一个，所以用 react-dom/server 渲染，
# 能抓到 tsc 抓不到的渲染期缺陷（字段名写错 / 对 null 取属性 / 契约不一致）。
#
# 用法：
#   bash test/frontend_render_check.sh [site_url] [plan_run_id]
#   不给 plan_run_id 时自动取最近一次已完成的方案运行。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SITE="${1:-http://localhost:30082}"
PLAN_RUN="${2:-}"
OUT="$ROOT/.ssrcheck"
mkdir -p "$OUT"

echo "[1/3] 抓取 $SITE/api/plans"
curl -fsS "$SITE/api/plans" -o "$OUT/plans.json"

if [ -z "$PLAN_RUN" ]; then
  echo "[2/3] 未指定 plan_run_id，取最近一次已完成运行"
  PLAN_RUN=$(curl -fsS "$SITE/api/plans/runs?limit=20" | python3 -c "
import json,sys
for r in json.load(sys.stdin)['plan_runs']:
    if r['status'] in ('completed','failed'):
        print(r['plan_run_id']); break
")
fi
[ -n "$PLAN_RUN" ] || { echo "找不到已完成的方案运行；请先下发一次（或在页面上点下发）"; exit 1; }
echo "      使用 run: $PLAN_RUN"
curl -fsS "$SITE/api/plans/runs/$PLAN_RUN" -o "$OUT/run.json"

echo "[3/3] 服务端渲染各区块"
cd "$ROOT"
node test/render_frontend_ssr.mjs "$OUT/plans.json" "$OUT/run.json"
