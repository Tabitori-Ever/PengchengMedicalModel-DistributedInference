#!/usr/bin/env bash
# 等 5.5 跑完后自动收尾：采集 → 渲染 → 校验，并留下日志与完成标记。
#
# 为什么需要它：大纲 §5.5 是 10 小时产生窗口 × 两阶段顺序执行（合计 20+ 小时）。
# 人工盯着不现实，所以把收尾流程固定成脚本，可以安全地挂在后台跑：
#   * 每 60s 查一次方案运行状态，直到 completed / failed；
#   * 再把指定的方案运行（默认 5.4 + 5.5）采集成报告数据；
#   * 渲染 HTML 并用 plan_report_check.py 校验；
#   * 全程写入日志，最后落一个 .done 标记（内容为最终结论摘要）。
#
# 用法：
#   bash test/finalize_plan_report.sh \
#       --site http://localhost:30082 \
#       --plans plan-5.4-id,plan-5.5-id \
#       --log plan_finalize.log --marker plan_finalize.done
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

SITE="http://localhost:30082"
PLANS=""
LOG="plan_finalize.log"
MARKER="plan_finalize.done"
OUT_HTML="plan_report.html"
OUT_JSON="plan_report_data.json"
POLL=60
MAX_WAIT=$((30 * 3600))     # 最多等 30 小时
MANUAL=""
SNAP_EVERY=15               # 每 N 次轮询做一次库快照（默认 15×120s ≈ 30 分钟）

while [ $# -gt 0 ]; do
  case "$1" in
    --site) SITE="$2"; shift 2;;
    --plans) PLANS="$2"; shift 2;;
    --log) LOG="$2"; shift 2;;
    --marker) MARKER="$2"; shift 2;;
    --poll) POLL="$2"; shift 2;;
    --out-html) OUT_HTML="$2"; shift 2;;
    --out-json) OUT_JSON="$2"; shift 2;;
    --manual) MANUAL="$2"; shift 2;;
    *) echo "未知参数: $1" >&2; exit 2;;
  esac
done
[ -n "$PLANS" ] || { echo "必须用 --plans 指定要收尾的 plan_run_id" >&2; exit 2; }
IFS=',' read -r -a PLAN_ARR <<< "$PLANS"

log() { echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] $*" | tee -a "$LOG"; }

# 给站点 SQLite 库做一次一致性快照。
# 20 小时的外业测试最怕"跑到一半库坏了/节点盘坏了"——结果数据丢了就得重跑一整天。
# 用 sqlite3 的 backup API（而不是 cp）保证 WAL 下也是一致快照；再尽量拷一份到本机，
# 这样连节点盘故障也能扛。快照失败只告警，不中断收尾流程。
snapshot_db() {
  local pod
  pod=$(kubectl get pods -l app=benchmark-site -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)
  [ -n "$pod" ] || { log "⚠️  快照跳过：找不到站点 Pod"; return 0; }
  if ! kubectl exec "$pod" -- python3 -c "
import sqlite3
src = sqlite3.connect('/data/bench/bench.db')
dst = sqlite3.connect('/data/bench/bench.db.snap')
with dst:
    src.backup(dst)
dst.close(); src.close()
print('snapshot ok')
" >>"$LOG" 2>&1; then
    log "⚠️  站内快照失败（不中断收尾）"; return 0
  fi
  if kubectl cp "default/$pod:/data/bench/bench.db.snap" .bench_snapshot.db \
        >>"$LOG" 2>&1; then
    log "已快照：站内 /data/bench/bench.db.snap + 本机 .bench_snapshot.db"
  else
    log "已快照：仅站内 /data/bench/bench.db.snap（本机拷贝失败）"
  fi
}
rm -f "$MARKER"
: > "$LOG"

log "收尾任务启动：site=$SITE plans=${PLAN_ARR[*]}"
snapshot_db

# ---------------------------------------------------------------- 1. 等待完成
waited=0
while :; do
  pending=0
  summary=""
  for p in "${PLAN_ARR[@]}"; do
    st=$(curl -fsS "$SITE/api/plans/runs/$p" 2>/dev/null \
         | python3 -c "import json,sys; print(json.load(sys.stdin).get('status','?'))" 2>/dev/null || echo "?")
    summary="$summary $p=$st"
    case "$st" in
      completed|failed|cancelled) : ;;
      *) pending=$((pending + 1));;
    esac
  done
  log "等待中…$summary"
  [ "$pending" -eq 0 ] && break
  if [ "$waited" -ge "$MAX_WAIT" ]; then
    log "❌ 超过最大等待时间（$((MAX_WAIT / 3600)) 小时），放弃收尾"
    echo "TIMEOUT" > "$MARKER"
    exit 1
  fi
  sleep "$POLL"
  waited=$((waited + POLL))
  if [ $((waited / POLL % SNAP_EVERY)) -eq 0 ]; then
    snapshot_db
  fi
done
snapshot_db   # 收尾前再快照一次，保留最终状态
log "全部方案运行已结束，开始采集"

# ---------------------------------------------------------------- 2. 采集数据
MANUAL_ARG=()
[ -n "$MANUAL" ] && MANUAL_ARG=(--manual "$MANUAL")
if ! python3 -u test/collect_plan_data.py --site "$SITE" --plan-runs "$PLANS" \
        --out "$OUT_JSON" "${MANUAL_ARG[@]}" >>"$LOG" 2>&1; then
  log "❌ 采集失败（见日志）"
  echo "COLLECT_FAILED" > "$MARKER"
  exit 1
fi

# ---------------------------------------------------------------- 3. 渲染报告
if ! python3 -u test/render_plan_report.py --data "$OUT_JSON" \
        --out "$OUT_HTML" >>"$LOG" 2>&1; then
  log "❌ 渲染失败（见日志）"
  echo "RENDER_FAILED" > "$MARKER"
  exit 1
fi

# ---------------------------------------------------------------- 4. 校验报告
if ! python3 -u test/plan_report_check.py --html "$OUT_HTML" \
        --data "$OUT_JSON" --expect-plan-runs "${#PLAN_ARR[@]}" \
        >>"$LOG" 2>&1; then
  log "❌ 报告校验未通过（见日志）"
  echo "CHECK_FAILED" > "$MARKER"
  exit 1
fi

# ---------------------------------------------------------------- 5. 结论摘要
python3 - "$MARKER" "$OUT_JSON" <<'PY' >>"$LOG" 2>&1
import json, sys
d = json.load(open(sys.argv[2], encoding="utf-8"))
lines = ["OK", ""]
for r in d.get("plan_runs", []):
    comp = r.get("comparison") or {}
    ov = comp.get("overall") or {}
    crit = comp.get("criteria") or []
    verdicts = ", ".join(
        f"{c.get('label')}={c.get('verdict')}({c.get('measured')}{c.get('unit')})"
        for c in crit)
    lines.append(f"{r.get('plan_run_id')} {r.get('plan_name')} [{r.get('status')}]")
    lines.append(f"  阶段: " + " · ".join(
        f"{p.get('phase')} {p.get('completed')}/{p.get('tasks_total')}"
        f"({p.get('completion_pct')}%) 重传{p.get('retries')}"
        for p in r.get("phases") or []))
    lines.append(f"  处理总用时: 本地 {ov.get('local_processing_ms')} → "
                 f"协同 {ov.get('collaborative_processing_ms')} "
                 f"({ov.get('processing_gain_pct')}%)")
    lines.append(f"  判定: {verdicts}")
print("\n".join(lines))
open(sys.argv[1], "w", encoding="utf-8").write("\n".join(lines))
PY

log "✅ 收尾完成：$OUT_HTML / $OUT_JSON 已生成并通过校验"
tail -n 20 "$MARKER" | sed 's/^/    /' | tee -a "$LOG"
exit 0
