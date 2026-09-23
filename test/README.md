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
| `render_benchmark_report.py` | 把采集数据渲染成**自包含 HTML 报告**（纯标准库、手写 SVG 图表、达标判定、口径假设、切分点分析） | `python test/render_benchmark_report.py --data benchmark_data.json --out benchmark_report.html` |
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
