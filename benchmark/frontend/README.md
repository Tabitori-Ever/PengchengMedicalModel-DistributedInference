# benchmark-site frontend（测试与性能对比平台 · 站点前端）

React 18 + TypeScript + Vite 单页站点，对应设计契约 `调度模式与降级-设计方案.md` §6。
独立部署，**不依赖 scheduler 存活**（这是能做降级测试的前提）。

## 构建与开发

```bash
# 依赖（离线机器：直接从既有前端目录复制 node_modules）
cp -r ../../frontend/node_modules ./node_modules

npx tsc --noEmit     # 类型检查
npx vite build       # 产出 dist/（base '/'，后端同源挂载）
npx vite             # 开发服务器 :5174，/api 代理到 http://localhost:8099
npx vite preview     # 预览构建产物 :5175
```

依赖版本与既有前端一致：react/react-dom `^18.3.1`、vite `^5.4.0`、
@vitejs/plugin-react `^4.3.0`、typescript `^5.5.0`、axios `^1.7.0`。
**无图表库**：所有柱状图 / 堆叠图均为手写 SVG + CSS。

## 五个视图与接口对应

| 视图 | 主要接口 |
|---|---|
| 测试下发 | `POST /api/runs`、`GET /api/runs/{id}`（轮询进度）、`GET /api/patients` |
| 实时状态 | `GET /api/health`、`GET /api/runs?limit=50`、`GET /api/runs/{id}`（2.5 s 轮询，页面隐藏时暂停） |
| 结果明细 | `GET /api/results?limit=100`（5 s 轮询） |
| 性能对比 | `GET /api/compare?kind=&source=`、`GET /api/compare/matrix`、`GET /api/runs/{id}`（本次实验内对比） |
| 实验配置 | `GET/PUT /api/config`、`GET /api/health`、`GET /api/export.csv`、`GET /api/export.json` |

## 口径（页面内已注明，勿改）

- **`client_total_ms`** 由站点后端用墙钟在 HTTP 调用外层测量，是**唯一跨模式可比**指标。
- **协同**包含排队等待 `queue_wait_ms`，**本地执行**恒为 0；两者差异同时含「调度开销」与「排队开销」。
- **降级**（琥珀橙徽标）≠ 本地执行：降级额外记录 `degrade_reason`
  （`forced` / `scheduler_unreachable` / `dependency_unhealthy`）。
- 所有聚合旁必须显示样本量 `n`；缺数据一律显示「暂无数据，请先下发测试任务」，
  **不补 0、不推算、不编造图表数据**。

## 对后端返回差异的容错

站点后端与契约文档在某些细节上不同（已在代码中兼容，两者都能读）：

| 位置 | 后端实际返回 | 兼容处理 |
|---|---|---|
| `GET /api/patients` | `{count, patients:[…]}` | 解包信封，也支持裸数组 |
| `GET /api/runs` | spec 字段**平铺**在 run 上，`attempts` 是**数字** | `runSpecLoose()` 统一读取；`asAttemptList()` 判型（否则会对数字调用 `.map()`） |
| `GET /api/runs/{id}` | `{run, progress, summary, attempts:[…]}` | `normalizeRun()` 摊平为 `Run` |
| `GET /api/health` | 降级开关在 `health.config` 里，pods 用 `local_mode_available` | `healthForceDegraded()` / `healthAutoDegrade()`；`local_health.capabilities` 优先 |
| `GET /api/compare` | 聚合嵌在 `client_total_ms:{n,mean,p50,p95,min,max}`，另有 `correctness` | 嵌套与平铺两种都读；`correctness.status` 直接呈现 |
| `GET /api/compare/matrix` | `{rows:[…]}` | 同时接受 `rows` / `matrix` / 裸数组 |
