# 测试说明文档

平台测试与性能验收脚本。前置条件：K8s 集群已部署（`bash deploy.sh`），
模型权重随 hospital / medical-server 增量镜像自带（见根 README「镜像与版本」）。

> 说明：`--with-kubectl` 会真实地把 `default/scheduler` 缩容到 0 再恢复，请在允许短暂中断的环境执行。
> 站点与 Pod 的集群外访问需用 **ClusterIP / NodePort**（WSL 主机无法解析 k8s 服务名）。
> `test/output/` 为脚本运行产物（脚本自动创建），不入库。

## 脚本概览

| 脚本 | 用途 | 命令示例 |
|---|---|---|
| `run_v3_comprehensive.py` | 四类任务（诊断/计算/通信/日常）混合批量执行并逐步展示结果；结果写入 `test/output/v3_comprehensive.json` | `python test/run_v3_comprehensive.py --base http://localhost:30080` |
| `local_exec_smoke.py` | 边缘 Pod `/local/*` 冒烟：医院就地完整模型诊断（校验与协同 bpCR 一致）、诊所一跳转诊、本机串行分区计算、内联日常作业、自编排通信同步、结果回查/幂等/降级标记 | `python test/local_exec_smoke.py --hospital http://<ip>:8006 --clinic http://<ip>:8007 --expect-bpcr 0.442733 [--strict-parity]` |
| `scheduler_mode_test.py` | 调度器三种执行模式：`collaborative` 回归、`local` 委派、`auto` 依赖探测降级、`force_degraded`、`/tasks/recent` 模式字段 | `python test/scheduler_mode_test.py --base http://localhost:30080 --expect-auto-collab`（依赖不可用环境用 `--expect-auto-degrade`） |
| `site_e2e.py` | **对比站点端到端**：站点健康、三模式×三类任务对比矩阵与正确性列；`--with-kubectl` 时缩容 scheduler 到 0 验证站点自动降级、再恢复验证回归 | `python test/site_e2e.py --site http://localhost:30082 --with-kubectl --repeats 3` |
| `edge_constraint_probe.py` | **端侧算力约束下的交叉点实验**：在目标 Pod 内制造 CPU 竞争模拟不同强度的端设备，测出协同开始占优的算力门槛 | `python test/edge_constraint_probe.py --pod hospital-a --load 40 --batch 4 --repeats 5 [--clean]` |
| `collect_benchmark_data.py` | **四类任务全量采集**：对每个固定套件先预热、再分别用两种调度策略跑完整套件，汇总输出 `benchmark_data.json`（报告数据源） | `python test/collect_benchmark_data.py --site http://localhost:30082 --out benchmark_data.json` |
| `plan_report_manual.example.json` | 外部仪表项（大纲 5.5 的丢包率）**人工实测值模板**：复制成 `plan_report_manual.json` 填入实测值，用 `collect_plan_data.py --manual` 并入后重新渲染，报告会给出结论并注明测量方与时间 | `cp test/plan_report_manual.example.json plan_report_manual.json` |
| `collect_plan_data.py` | **采集方案运行数据**（大纲 §5.4/§5.5）：从站点 HTTP 接口抓取指定/最近的方案运行（配置快照、两阶段实测、逐类对比、判定、子运行）与镜像能力，存成报告数据源；`--manual` 可并入外部仪表的人工实测值（并写回对应判定项，保证数据自洽） | `python test/collect_plan_data.py --site http://<ip>:30082 --latest 2 --out plan_report_data.json` |
| `plan_report_failure_test.py` | **未达 100% 时的报告渲染回归**：用引擎在临时库里跑一个"部分任务永久失败"的方案运行，把真实输出喂给真实的采集/渲染/校验链路，验证失败数、重传次数、重传耗时如实呈现且完成率判定为**不通过** | `python test/plan_report_failure_test.py` |
| `plan_report_check.py` | **校验方案报告**：结构自包含、章节完整（方案配置/预置条件/判定标准/阶段实测/逐类对比/总体对比/判定/子运行）、外部仪表已渲染、无占位符泄漏、**判定结论与数据文件一致**；未跑完的方案默认拒绝（`--allow-running` 可放宽，用于预览） | `python test/plan_report_check.py --html plan_report.html --data plan_report_data.json --expect-plan-runs 2` |
| `finalize_plan_report.sh` | **自动收尾**：等指定方案运行跑完 → 采集 → 渲染 → 校验 → 写结论标记。用于大纲 §5.5 这种 20+ 小时的运行（人工盯着不现实）；等待期间**周期性给站点库做一致性快照**；`--out-html/--out-json` 可改输出路径（便于演练）、`--manual` 可并入外部仪表实测值（sqlite3 backup API + 拷一份到本机），防止跑到一半库/盘坏了白跑一整天 | `bash test/finalize_plan_report.sh --site http://<ip>:30082 --plans id1,id2` |
| `render_plan_report.py` | **把方案运行数据渲染成自包含 HTML 报告**（纯标准库、手写 SVG 柱状图）。含每方案的「负载画像」（任务数/执行单元/累计任务时间/阶段墙钟/平均并发任务数/单元占用率）与跨方案的「负载敏感度对比」+ 任务比例设计依据 | `python test/render_plan_report.py --data plan_report_data.json --out plan_report.html` |
| `restart_resume_test.py` | **长任务断点续跑回归**：重启只收尾真正执行到一半的任务（放回待执行而非判失败）、等待中的任务原样保留、方案续跑复用已有子 run 不重复创建、worker 与协调器按到期/进度自适应睡眠（按线程分别统计查库次数） | `python test/restart_resume_test.py` |
| `render_benchmark_report.py` | 把采集数据渲染成**自包含 HTML 报告**（纯标准库、手写 SVG 图表、达标判定、口径假设、切分点分析） | `python test/render_benchmark_report.py --data benchmark_data.json --out benchmark_report.html` |
| `plan_run_offline.py` | **离线验证测试方案骨架**：把执行器换成假的 sleep，检查阶段顺序（先本地后协同）、任务按时间窗分散产生、诊断患者轮换、阶段汇总与判定 | `python test/plan_run_offline.py` |
| `db_migration_test.py` | **数据库迁移回归**：用旧版 schema 建库再用当前 `db.init()` 升级，验证老库能平滑升级、老数据完好、新功能可用 | `python test/db_migration_test.py` |
| `worker_resilience_test.py` | **worker 韧性回归**：领取任务短暂失败不得遗弃任务、窗口未到要耐心等、永久故障要如实标记失败收尾（实测踩过：一条 pending 被永久遗弃、run 卡在 running） | `python test/worker_resilience_test.py` |
| `api_route_order_test.py` | **路由顺序回归**：静态路径不得被更早声明的动态路由遮蔽（`/api/plans/runs` 曾被 `/api/plans/{plan_id}` 吞成 404） | `python test/api_route_order_test.py` |
| `task_limits_test.py` | **任务数上限 + 诊断策略回归**：超限静默截断且显示一致、不整类消失、设备数截断；诊断 `local` 被拒（单次 + 套件）、其余三类仍可用、本地阶段无诊断单元 | `python test/task_limits_test.py` |
| `retry_guarantee_test.py` | **任务保障回归**：瞬时失败重传后 100% 完成、重传代价如实记账、额度用尽如实判失败、run 级额度优先于全局、判定为 `complete_gte 100%` | `python test/retry_guarantee_test.py` |
| `frontend_render_check.sh` | **前端渲染自检**：抓站点真实响应，用 `react-dom/server` 渲染各区块并断言关键字段（无浏览器可用的环境下替代人工点检） | `bash test/frontend_render_check.sh http://localhost:30082` |
| `rebuild_report_data.py` | **离线重建报告数据**：从站点 SQLite 库按指定 `run_id` 集合重算逐档/总体统计（与后端 `engine.suite_compare` 同口径），避免历史运行污染统计 | `python test/rebuild_report_data.py --db .bench_ro.db --data benchmark_data.json --latest 2` |
| `suite_compare.py` | **协同优势验收**：同一套固定 20 个计算任务分别用「云边端协同」与「本地执行」下发，逐档位对比 mean/p50/p95，并把「协同快 ≥10%」写成断言 | `python test/suite_compare.py --site http://localhost:30082 --min-gain 10 [--source hospital-a]` |
| `diagnosis_ab.py` | **诊断类脱站点对照**（不经站点，排除测量工具本身）：本地直投医院 `/local/execute`；协同走 `/schedule/diagnosis {deliver:"direct"}` 取计划后按分片直投执行者、`/task/{id}/report` 记账 | `python test/diagnosis_ab.py --scheduler http://localhost:30080 --hospital http://<ip>:8006 --batches 2,4,8 --repeats 5` |
| `degradation_drill.py` | **掉线演练**：基线（协同）→ `scale deploy/scheduler --replicas=0` → 直连发起 Pod `/local/execute` 完成四类任务 → 恢复并确认协同回归 | `python test/degradation_drill.py --with-kubectl` |

## 测试数据

`test/test_dataset.json` 是内置患者数据集（DCE / DWI / 临床 / 影像组学），
被 scheduler 与 benchmark 镜像在构建期拷入镜像内；调度器 `/test/patients`、
站点构建期 `benchmark/tools/make_patients.py` 均以它为数据源。

诊断任务输入字段（形状）：

| 字段 | 形状 |
|---|---|
| `dce_image` / `dwi_image` | [1, 224, 224] float |
| `clinical` | [1, 23] float |
| `radiomics` | [1, 2264] float |
| `patient_ids` | ["..."] |

计算 / 通信 / 日常任务的输入由 `common/v3_common.py` 确定性生成，
数据集文件名即参数（`计算_规模中等_1` / `通信_省流_1` / `日常_任务数3_1` / `诊断_患者1404920_1`）。

## 推荐流程

```bash
# 1. 部署后：四类任务综合冒烟
python test/run_v3_comprehensive.py --base http://<node-ip>:30080

# 2. 执行模式 / 调度降级
python test/scheduler_mode_test.py --base http://<node-ip>:30080 --expect-auto-collab
python test/degradation_drill.py --with-kubectl

# 3. 边缘就地执行冒烟
python test/local_exec_smoke.py --hospital http://<pod-ip>:8006 --clinic http://<pod-ip>:8007

# 4. 协同优势验收 + 采集 + 生成 HTML 报告
python test/suite_compare.py --site http://<node-ip>:30082 --min-gain 10
python test/collect_benchmark_data.py --site http://<node-ip>:30082 --out benchmark_data.json
python test/render_benchmark_report.py --data benchmark_data.json --out benchmark_report.html
```

### 离线重建报告数据（推荐：报告只统计本轮指定运行）

站点的 `GET /api/compare/suite` 默认聚合该套件的**全部历史运行**；跨轮重跑会把旧构建
（例如诊断类早期的切分点、旧传输路径）的结果混进统计。要得到"同一构建、同一轮采集"
的干净口径，用采集时返回的 `run_id` 从站点数据库离线重算：

```bash
# 每个套件取最近 2 次运行（协同 + 本地各一次）
python test/rebuild_report_data.py --db bench.db --data benchmark_data.json --latest 2

# 或显式指定 8 个 run_id（4 套件 × 协同/本地）
python test/rebuild_report_data.py --db bench.db --data benchmark_data.json \
    --runs run-A,run-B,run-C,run-D,run-E,run-F,run-G,run-H
python test/render_benchmark_report.py --data benchmark_data.json --out benchmark_report.html
```

分位数插值、成功/失败判定、档位标签匹配都与站点后端 `engine.suite_compare` 同口径；
重算后 JSON 会带上 `run_ids` 与 `scope: explicit-runs`，报告 §2.4 会把统计范围与
运行 ID 一并列出，便于论文回溯。

如果 `bench.db` 在集群节点上，先取回本地再离线执行（本工具不访问网络、不依赖集群）：

```bash
kubectl -n <ns> exec <benchmark-site-pod> -- cat /data/bench/bench.db > .bench_ro.db
```

也可不重算、直接用 `collect_benchmark_data.py` 的输出；但此时统计范围是该套件的全部
历史运行，报告 §2.4 会显示"数据文件未记录运行 ID"。
