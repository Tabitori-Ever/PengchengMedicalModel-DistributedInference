# AI诊疗云边端协同应用平台 v3.0（正式版）—— 云 / 边 / 端

基于 Kubernetes 的云边端协同医疗推理平台。**v3.0（正式版）**确立了三层架构：
**Data Center 数据中心（云）= 调度与推理/患者库中枢，hospital 医院（边）= 诊断 worker 与数据主场，
clinic 诊所（端）= 业务发起与轻量终端**；任务收敛为四类，均由 hospital / clinic 发起；
上一版中的 AlexNet(part1/part2) 图像分类能力已整体移除。

> 上一版本历史（v1.x 节点=医院实体 / v2.x 医院诊所 Pod 化）见 [`版本记录.md`](版本记录.md)，
> 本次重构详情见 [`v3.0-重构报告.md`](v3.0-重构报告.md)。

## 架构总览

```
                 ┌────────────────────────────────────────────────┐
                 │        Data Center 数据中心 (node3 · 云)         │
                 │   scheduler（调度功能继承） · medical-server        │
                 │   redis · dc-services(患者库 patient-db+计算/备份)  │
                 └──────▲──────────────────▲──────────────────────┘
                 诊断 worker/云端推理  协同计算/P2P 同步
        ┌───────────────┴──────────┐  ┌──┴────────────────────┐
        │ hospital 医院 · 边         │  │ clinic 诊所 · 端        │
        │  hospital-a@node1         │  │  clinic-1@node1        │
        │  hospital-b@node2         │  │  clinic-2@node2        │
        │  诊断 worker 前端(数据不出院) │  │  发起四类任务             │
        │  协同计算/P2P peer         │  │  协同计算/P2P peer       │
        └──────────────────────────┘  └────────────────────────┘
```

| 层 | 节点 | 实体 | 组件/职责 |
|---|---|---|---|
| 云 | node3 | Data Center | scheduler(调度)、medical-server(诊断 server)、dc-services(患者库+协同计算/备份)、redis |
| 边 | node1/node2 | hospital-a/b | 诊断 worker（DoubleTower 前端）、发起任务、计算分区、P2P peer |
| 端 | node1/node2 | clinic-1/2 | 发起四类任务、本地患者副本、P2P peer、日常 Job 载体 |
| 运维 | desktop | — | monitoring（指标观测，可选） |

## 四类任务（均由 hospital/clinic 发起）

| 任务 | 说明 | 执行路径 |
|---|---|---|
| **诊断 diagnosis** | 医疗模型推理（bpCR 预测） | hospital `worker 前端` → DC `medical-server`；clinic 发起自动/指定转诊 hospital |
| **计算 compute** | 多监护仪器模拟产数，高算力协同 | 发起 Pod + 另一边缘 Pod + `dc-services` 多分区协同计算并聚合 |
| **通信 sync** | 患者数据库云更新/云同步（P2P 原理） | 发起端多 peer 并行拉取缺失分块 → 云上传 → 云端备份 |
| **日常 routine** | 日常计算队列，按节点空闲调度 | DC scheduler 在空闲节点创建一次性 Job Pod，执行后删除 |

每个任务结果包含：`initiator`（发起实体/角色/节点/优先级/时间）、`stages[]`（执行阶段明细）、
以及各类型的 `result_detail`（诊断 bpCR/转诊、计算分区表、P2P 分块/吞吐/备份、日常 Job 清单）。

## 提交接口

```
POST /schedule/diagnosis   {source, patient_id?, target_hospital?, priority?, deadline?}
POST /schedule/compute     {source, instruments, rows, intensity, partition_count, ...}
POST /schedule/sync        {source, bandwidth_mbps, concurrency, chunk_kb, ...}
POST /schedule/routine     {source, jobs, rows, intensity, ...}
source ∈ hospital-a | hospital-b | clinic-1 | clinic-2
```
结果查询 `GET /task/result/{id}`、历史 `GET /tasks`、`GET /tasks/stats`（by_model=四类）。
集群编辑沿用 `GET/POST /cluster/*`（可编辑 hospital/clinic；Data Center 服务只读展示）。

## 目录结构（v3.0 变化）

```
├── hospital/            # 边：诊断 worker + /v3/compute、/v3/sync peer + ★/local/*（v3.2）
├── clinic/              # 端：/v3/compute、/v3/sync peer + /app/run_routine.py + ★/local/*（v3.2）
├── dc/                  # ★ 数据中心：患者库 patient-db(/db/*) + /v3/compute + 备份（端口 8010）
├── common/v3_common.py  # ★ 共享确定性产数/分块逻辑
├── common/local_exec.py # ★ v3.2 共享本地执行运行时（队列/结果落盘/http_json/三类就地流水线）
├── scheduler/           # 调度（迁至 node3）：4 条流水线 + node_usage/kubeops(Job) + ★mode/降级
├── benchmark/           # ★ v3.2 测试/性能对比站点（backend + frontend + Dockerfile）
├── frontend/            # React v3.0：四表单 + 详情 + 综合测试页 + 集群编排 + ★执行模式选择
├── k8s/                 # dc-services.yaml；scheduler→node3；clinic-1..4、benchmark-site
├── test/                # ★ v3.2 四个验证脚本（local_exec_smoke / scheduler_mode_test / site_e2e / degradation_drill）
├── 调度模式与降级-设计方案.md  # ★ v3.2 实现契约（模式语义、结果口径、接口、隐患清单）
└── v3.0-重构报告.md       # 上一版重构报告
```

## 前端（v3.1.8 · 双站点）

三级命名：**数据中心（云，node3）— 医疗中心（原 hospital，node1/2）— 医院（原 clinic，node1/2）**；
术语集中在 `frontend/src/terms.ts`（实体显示名、层级、能力简述、流转标签），便于后续统一调整。

| 入口 | 地址 | 说明 |
|---|---|---|
| 落地页 | `/app/` | 选择「用户调用平台」或「管理平台」 |
| 用户调用平台 | `/app/user/` | 顶部**任务类型横栏**（诊断/计算/通信/日常）+ **居中单列**：选类型即内联列出该文件夹的数据文件，选中即执行，记录与结论按同一列依次排列；左侧历史任务列表；**提交卡新增「执行模式」**（云边端协同 / 本地执行 / 自动 + 强制降级勾选），记录卡显示实际执行模式与「降级」徽标 |
| 管理平台 | `/app/admin/` | `平台总览` / `可视化` / `集群负载` / `综合测试` / `集群编排` |
| 示例数据集 | `/app/datasets/` | `index.json` + 四个中文文件夹（计算 / 通信 / 日常 / 诊断），文件名即参数 |
| **对比站点** | `:30082/` | v3.3 独立站点（四视图）：测试下发（固定套件/单次任务）/ 结果明细 / 性能对比（套件对比优先）/ 实验配置 |

- **用户平台（v3.1.8）**：提交卡**一张卡两部分**——标题行就是四个任务类型按钮（等分四格、46px 高、选中为黄铜实底），卡身紧跟其后（间距仅 1px 发丝线 + 14px 内边距），两者是一体的，不再是一上一下隔开的两块。卡身只有两项：**任务位置** select（医院 1/2、医疗中心 A/B，默认 医院 1）与**「＋ 上传」**入口；未选数据时提示「请先上传数据文件」且执行禁用。点上传打开**数据集目录浏览器**弹窗，**直接落在当前类型对应的文件夹**（诊断→诊断、计算→计算、通信→通信、日常→日常），保留 `数据集 / 计算` 面包屑可回根层再进其它文件夹（根层列出四个文件夹及文件数），文件行给出中文名 + 说明 + 体积，列表下方给出该类型每个参数的**「参数说明」**（如计算的行数 = 每台仪器的数据行数、强度 = 单行运算量倍数、分区数 = 并行份数，本集群最多 3 份），选中即关闭并回填（文件名 + 说明，按钮变「↻ 重新上传」），Esc / 点遮罩 / 关闭按钮均可关闭；目录只在打开时读取一次，文件仅在选中时读取单个。参数写在**文件名**里（`计算_规模中等_1` / `通信_省流_1` / `日常_任务数3_1` / `诊断_患者1404920_1`），用户无需选择类型与参数，也没有本机文件对话框。提交记录与结论按同一列在提交卡下方依次排列（`max-width:880px` 居中）。结果**只给结论**：诊断给 bpCR 概率大字与 pCR/non-pCR 判定、计算给分区数与数据量、通信给拉取/上传与备份与否、日常给完成数；运行中仅进度条，**不显示时延、阶段明细、执行节点与指标表**。
- **平台总览**：三级架构与四类任务说明、节点/实体/实例/任务/组件健康 KPI、任务类型分布与各功能入口。
- **可视化**：逻辑架构总览（数据中心 / 医疗中心 / 医院 / 运维四组实体 + 关系连线，**不显示负载**）→ 任务执行轨迹（类型栏为 **多任务 · 诊断 · 计算 · 通信 · 日常**；选「多任务」时默认**四类任务各取一条**（诊断/计算/通信/日常，同类内运行中/排队优先，候选芯片共 8 个可换选其他任务），在**同一张流程图**上同时绘制这几条任务——拓扑取所选任务节点与连线的**并集**（未被任何任务使用的仍为最淡「无关」色），每条任务一种暖色**色迹**（已执行与当前步骤沿法线平行错开），当前步骤用该任务颜色的**光环**标出，图例列出「同屏任务色迹」与各任务号；单任务模式按执行时间顺序高亮：已执行灰 / 当前橙 / 待执行淡灰虚 / 无关最淡，并行步骤 `∥N` 同时点亮）→ 执行时序 → 图例；一条归一化进度驱动全部轨迹（任务 i 游标 = `round(progress × (步数ᵢ − 1))`），由同一套 `⏮ 重置 / ◀ 上一步 / 下一步 ▶ / ▶ 自动` 控制。页面不再出现说明性小字（回放说明段、卡片右上 meta、`同屏任务 / 图内轨迹 / 基准任务 / 任务总数 / 共享步进` 等单元、图例长句、`循环演示` 与 `基准 … · N 步` 标记、架构总览的 `v3.0 · 12 个逻辑实体 · 最后更新` 均已删除）。
- **集群负载**：节点资源（4 节点 CPU/内存 数值与条）、各节点 Pod 就绪、组件健康（/health）、任务与队列概览。
- **综合测试**：四类任务混合批量执行与逐步结果。
- **集群编排**：`地图视图 / 列表视图` 双视图（拖动 Pod/行改节点、右侧 Pod 详情、校验/应用/重置）。
- 全站中文为主、减少冗余英文与无关信息；正文 16px、标题 32px，重点数值加粗。
- 前端 GET 请求失败自动重试一次；后端 v3.0.12 起 `/cluster/status`、`/cluster/summary` 服务端缓存 4s、新增轻量 `/tasks/recent`、`/app/assets/*` 与 `/app/datasets/*` 长缓存；轮询 10s 且标签页隐藏时暂停。
- 示例数据集为纯静态文件，随前端构建产物发布（`frontend/public/datasets/` → `/app/datasets/`，中文目录与文件名可直接访问），新增数据只需放入对应文件夹并在 `index.json` 登记（`path/name/description/bytes`）；用户平台只在打开浏览器与选中文件时各请求一次。

## 执行模式（v3.2）· 云边端协同 / 本地执行 / 调度降级

任务提交新增 `mode` 字段，**同一个任务可以走三种执行路径**，用于对比研究与容灾：

| mode | 含义 | 编排者 | 执行者 |
|---|---|---|---|
| `collaborative` | 云边端协同（v3.0 行为，默认） | 数据中心 scheduler | 多角色协作（边 + 云 + peer + 一次性 Job） |
| `local` | **本地执行**：发起 Pod 自己担当调度编排、就地优先 | 发起 Pod | 发起 Pod（能力不足时走最短一跳） |
| `auto` | 依赖/调度器不可用时**自动降级**为 `local` 并标记 `degraded` | 视探测结果 | 视探测结果 |

另有两个受控开关：`force_degraded`（提交时强制降级）与站点级「手动强制降级」开关。

**四类任务的本地执行路径**

| 任务 | 协同（现状） | 本地执行 |
|---|---|---|
| 诊断（医院发起） | 医院前端 → 云 medical-server 后端 | **医院就地跑完整 DoubleTower**（镜像内已有全量权重，v3.2 起保留完整模型常驻） |
| 诊断（诊所发起） | 自动转诊医院 → 云后端 | 诊所自己编排，**一跳转诊最近医院**由医院就地完成（诊所无模型，如实标注 `capability=forward`） |
| 计算 | 发起 Pod + 伙伴 + 云**并行**分区 | 发起 Pod **串行**跑完全部分区（不调 peer / 云） |
| 通信 | 调度器编排拉取/上传/备份 | 发起 Pod **自己**完成同一套 P2P 拉取 → 云上传 → 云备份 |
| 日常 | 调度器在空闲节点创建**一次性 K8s Job** | 发起 Pod **内联后台线程**执行（不创建 Job；诊所 SA 无 Job 权限） |

**调度降级**：scheduler 掉线时，边缘 Pod 无需数据中心即可继续完成四类任务。
边缘镜像新增 `/local/execute`、`/local/diagnosis`、`/local/health`、`/local/result/{id}`、
`/local/results`、`/local/queue`（契约见 [`调度模式与降级-设计方案.md`](调度模式与降级-设计方案.md) §4），
本地任务在**有界后台队列**执行并把结果落盘到 hostPath `/data/local-results`；
prometheus 侧新增 `local_exec_requests_total` / `local_exec_latency_seconds`。

### 协同优势实测（v3.3，固定 20 任务套件，本集群）

`test/suite_compare.py` 用**逐字节相同的 20 个计算任务**（5 档负载 × 4 次重复，固定 3 分区）分别跑两种策略：

| 档位 | 仪器 | 行数 | 强度 | 单分区算力 | 协同 mean | 本地 mean | 协同快 |
|---|---|---|---|---|---|---|---|
| 对照 | 4 | 512 | 60 | ~10 ms | 165 ms | 40 ms | **-309%**（本地胜） |
| 中载 | 8 | 2048 | 300 | ~95 ms | 203 ms | 293 ms | **+30.7%** |
| 重载 | 8 | 3072 | 450 | ~200 ms | 334 ms | 606 ms | **+44.8%** |
| 特重 | 8 | 4096 | 600 | ~400 ms | 512 ms | 1035 ms | **+50.5%** |
| 极重 | 16 | 4096 | 900 | ~1300 ms | 1196 ms | 3004 ms | **+60.2%** |
| **总计（n=20）** | | | | | **482 ms** | **996 ms** | **+51.6%（2.07×）** |

> **为什么协同能赢**：协同把 3 个分区**并发**派发给发起端 + 数据中心 + 伙伴边缘端（实测并行度 1.8–2.7×），
> 本地执行只能在发起 Pod 上**串行**跑完 3 份（并行度 ≈0.9）。算力负载越重，协同的多端并行收益越大。
> **对照档位**是诚实的反例：算力小到被通信与编排开销淹没时，本地反而更快（交叉点在单分区算力 ~50–90ms）。

### 四类任务的完整对比（v3.5b，每类固定 20 任务套件 × 两策略，站点实测）

| 任务类 | 负载轴 | 协同 mean | 本地 mean | 结论 |
|---|---|---|---|---|
| **计算** | 算力（仪器×行数×强度），5 档 | **508 ms** | 980 ms | **协同快 48.2%** ✓（分档 +17% … +60%） |
| **通信** | 链路带宽，4 档 | **4 607 ms** | 14 939 ms | **协同快 69.2%** ✓（数据中心持有分块归属表，多源并行流） |
| **日常** | 作业数 2/4/6/8 | **2 607 ms** | 4 758 ms | **协同快 45.2%** ✓（作业并发派发到空闲节点） |
| 诊断 | 批量 1/2/4/8 例 | 见下 | 见下 | **未达标**：端侧空闲时收敛到**持平**（批量 8 例 −0.1%）；端侧弱 6 倍时 **+13.0%** ✅ |

**v3.5c 按算力加权分片 + 公平基线**（`test/diagnosis_ab.py`，两边各自最优，不经站点）：
数据中心每例算力约为医院的一半，**均分分片会让慢的那台决定墙钟** → 改为按「每例成本」加权（批量 2/4/8 → 份额 2/0、3/1、6/2）。
同时发现**本地批量并行反而更慢**（批量 8：920ms → 2117ms，4 路 × 4 线程在 4 核配额上超订），故本地最优为串行。
结果：批量 2/4/8 例差值 **−56.8% / −28.5% / −3.7%**（p50 −67.4% / −28.9% / **−0.1%**）——**诊断收敛到持平，但达不到 +10%**。

**v3.5b 控制面/数据面分离**：诊断新增 `deliver="direct"` —— 调度器只返回「谁执行哪几个患者」的计划，
输入由站点**直投执行者**（医院 `/medical/infer_full`、数据中心 `/infer_full`），结果回传 `/task/{id}/report` 记账。
诊断套件差距 **−353.9% → −251.0%**（改造前 −508%）。端侧空闲时仍未达标，原因已定量：
数据中心 node3（4 核且与调度器/redis/患者库共处）每例算力约为医院的一半，数据并行的墙钟被慢的那台拖住。

**v3.5 数据并行分片**：批量诊断新增 `strategy=data`——把患者分给**医院与数据中心各自跑完整模型**（数据中心新增 `/infer_full`，保留全量权重），
两端同时开工、无中间特征搬运，**bpCR 逐位一致**。它把诊断套件差距从 −429.6% 改善到 **−353.9%**。
剩余瓶颈已定量定位：**输入数据要经控制面搬两趟**（站点→调度器→执行者），归因显示协同侧网络 3 549 ms vs 计算 617 ms。
彻底解决需改造提交接口：调度器只做决策、客户端把输入**直投执行者**（数据面绕开控制面）。

**v3.4 对诊断的三项修复**（`调度模式与降级-设计方案.md` §14）：
① **切分点从 conv1+maxpool 下移到 layer3 之后**——中间特征 1.61MB → **0.401MB**（= 输入大小，此前是输入的 4 倍），
端侧算力份额 8% → 38%，bpCR 逐位不变；② **原始 float32 直传 + 线程本地长连接**——实测每请求固定开销 42ms、
base64 再放大 33%，改后大载荷渐近吞吐 11.3 MB/s；③ **并发修复**——云端端点改用 `run_in_threadpool`（原先阻塞计算卡住事件循环）、
批量前端并发派发（医院 4 核前端与云端 4 核后端真正同时工作，4 并发实测 790ms vs 串行 2763ms）。

**完整报告**：[`benchmark_report.html`](benchmark_report.html)（由 `test/collect_benchmark_data.py` + `test/render_benchmark_report.py` 从实测数据生成，含方法、逐档对比、图表、口径假设与局限）。

**诊断的交叉点（受控实验）**：在端侧 Pod 内制造 CPU 竞争来模拟不同强度的"端"设备，同一批量（4 例）对比：

| 端侧竞争负载 | 端侧有效算力 | 本地 | 协同 | 结论 |
|---|---|---|---|---|
| 0（空闲） | ≈4 核 | **487 ms** | 4 597 ms | 本地快 8.4×（v3.4 后收窄为 367ms vs ~900ms） |
| 14 进程 | ≈0.23 核（1/17） | 8 372 ms | 9 558 ms | 本地快 14.2% |
| 40 进程 | ≈0.16 核（1/25） | 12 200 ms | **9 893 ms** | **协同快 18.9%** ✓ |

即：**诊断类的协同收益有条件成立——端侧算力约为云端同级节点的 1/17–1/20 处是交叉点**，
端侧更弱（如轻量端设备）时协同达标；本集群的"端"是 4 核 Pod，尚未弱到该程度。
复现脚本：`python test/edge_constraint_probe.py --pod hospital-a --load 40 --batch 4 --repeats 5`。

**诊断为什么在算力充裕的端侧不占优（离线切分点分析）**：该模型的中间特征（conv1+maxpool 之后、双视图）是 **1.61MB = 输入的 4 倍**，
而端侧只卸下 **8% 的算力**（21.5ms / 263ms）——拆分几乎纯亏：跨节点要多搬 4 倍于输入的数据，换来的只是省掉 21.5ms 计算。
实测协同诊断的网络耗时为 **5 334ms**（本地为 0）。要在此模型上让协同占优，需把切分点下移到 layer2/layer3
（中间特征降到 2×/1× 输入），或让端侧算力远弱于云端。已做的三项优化（紧凑编码、边端直连云端后端、批量流水线）
把差距从 8× 压到 5×，但**结构上无法翻盘**，故报告中如实标注为未达标。

> **v3.3 修掉的三个实现缺陷**（此前它们把协同的收益全部吃掉）：分区**串行派发**、节点负载查询**阻塞在任务关键路径**（每 3 秒一次 ~4s 的 K8s API 调用）、影像与特征用 **JSON 浮点文本**传输（7.01MB→2.14MB，worker 阶段 2066ms→117ms）。详见设计方案 §11。

## 测试与性能对比站点（benchmark-site v1.2）

**独立部署、不依赖 scheduler 存活**（这是能做掉线演练的前提——平台前端 `/app` 由 scheduler Pod 托管）。

**单页界面**（无侧边栏）：① 选择测试方案 → ② 实验配置 → ③ 运行进度 → ④ 测试结果总览 →
⑤ 统计信息 → ⑥ 性能对比，全部在同一页自上而下排布。

测试方案直接对应《测试大纲》的操作步骤：

| 方案 | 依据 | 任务数 | 设备 | 每类/设备 | 产生窗口 | 测试时长 | 策略 |
|---|---|---|---|---|---|---|---|
| 5.4 协同调度测试 | 大纲 §5.4 | 40 | 2 台（B/C） | 5 | 5 分钟 | 20 分钟 | 本地 → 协同 |
| 5.5 医疗智联专网测试 | 大纲 §5.5 | 200 | 2 台（B/C） | 25 | 10 小时 | 12 小时 | 本地 → 协同 |
| 自定义实验配置 | 自行设定 | 任意 | 任意 | 任意 | 任意 | 任意 | 任选 |

> §5.5 原文写「各设备每类 50 个，共 200 个」：2×4×50 = 400 与明示的 200 不一致，
> 平台取大纲**明示的总数 200**（25 个/设备/类），并在界面与 `plans.py` 里显式标注该矛盾。

方案运行（`POST /api/plans/runs`）会把方案展开成「设备 × 任务类型」子运行，**按调度策略分阶段
顺序执行**：先把本地（未调度）那一遍跑完、记录总用时，再切换协同跑第二遍。阶段内任务的产生
时刻在窗口内按种子随机抽样并写入 `attempts.not_before`，worker 到点才领取——即任务是真的
"随机产生"而不是一次性全发。

**诊断类没有本地执行策略**：医疗中心（hospital）不能执行医疗模型的 server 半段
（layer4 + 融合 + 分类器），只有数据中心能跑。因此 hospital 镜像不再常驻完整模型，
`/local/diagnosis` 与 `/medical/infer_full` 一律 409；诊断的 `mode=local` 与
`force_degraded` 被拒绝（400），`auto` 下云端不可用时诊断**如实失败**而不降级。
方案与固定套件都据此只把诊断排进协同阶段：5.4 本地 30 / 协同 40，5.5 本地 150 / 协同 200；
总用时对比只用两阶段**共有**的任务类型。

**任务数上限（容错，静默截断）**：单类输入 ≤ 1000、每阶段总数 ≤ 1000、设备数 ≤ 8。
超限按比例压缩（每类至少留 1 个），界面显示数与实际下发数一致。设上限的原因不只是崩溃：
阶段/逐类统计查询带 `limit=20000`，任务数超出会被**静默截断**、均值与总量算错，
那比崩溃更难发现。

**任务保障：失败自动重传（成功率要求 100%）**。失败任务自动重传，默认额度 3 次（首次之外，
方案声明、可在实验配置里改），每次重传前有退避。重传代价如实记账：
`client_total_ms` 是成功那次的时延，失败尝试的耗时累计进 `retry_ms_total`、次数进
`retry_count`、原因进 `retry_errors_json`。判定口径为**处理总用时 = Σ(client_total_ms) +
Σ(retry_ms_total)**（含重传代价）；另有"执行总用时"只算成功那次。额度用尽仍失败就如实记失败，
完成率判定 `complete_gte 100%` 随之不通过——平台不隐藏失败。

判定口径：**任务处理总用时**（即大纲的"所有任务处理总用时"）；阶段墙钟含固定产生窗口，
两遍相同，仅作参考。运行中（还有任务未完成）完成率判定保持"待实测"，不会显示"不通过"。

| 入口 | 地址 | 说明 |
|---|---|---|
| 对比站点（单页） | `http://<任一节点IP>:30082/` | 页面只呈现数据（无说明性文案），结论由第三方测试判读 |
| 站点 API | `/api/plans`、`/api/plans/runs`、`/api/plans/runs/{id}`、`/api/plans/runs/{id}/cancel`、`/api/health`、`/api/runs`、`/api/compare`、`/api/compare/matrix`、`/api/export.csv`、`/api/export.json` … | 见设计方案 §6.1 |

- 可下发**单次或批量**实验（`repeats` + `concurrency`），三种模式并列对比；
- 采集 `client_total_ms`（站点侧墙钟，**跨模式唯一可比口径**）与排队/计算/网络/降级耗时分解；
- 统计 `n / 成功率 / mean / p50 / p95 / min / max` 与**正确性列**（诊断 bpCR vs 数据集真值、计算 checksum 是否一致）；
- 默认 `auto` 模式：**先短探测（默认 1.5s）scheduler**，不可达则直接走降级路径，探测耗时单独记入
  `degrade_switch_ms`，**不污染**被测时延；
- 站点自带患者输入（镜像内构建时生成 `/app/patients.json`），因此 scheduler 掉线时诊断仍可执行；
- SQLite 落盘 hostPath `/data/bench/bench.db`，Pod 重建不丢数据。

## 快速开始

```bash
# 构建并推送（本机无 PyPI/npm 外网出口 → hospital/clinic/scheduler 采用增量构建）
bash build.sh

# 部署（RBAC→dc-services→hospital→clinic×4→medical-server→scheduler→对比站点）
bash deploy.sh

# 本地单机联调（hospital 8006 / clinic 8007 / dc 8010 / server 9001 / scheduler 8000）
# 原 start_all.sh / stop_all.sh 已在清理中移除，需要时用 uvicorn 分别启动各服务

# 四类任务综合测试
python test/run_v3_comprehensive.py --base http://localhost:30080

# v3.2 验证脚本
python test/local_exec_smoke.py --hospital http://<pod-ip>:8006 --clinic http://<pod-ip>:8007
python test/scheduler_mode_test.py --base http://localhost:30080 --expect-auto-collab
python test/site_e2e.py --site http://localhost:30082 --with-kubectl
python test/degradation_drill.py --with-kubectl          # 缩容 scheduler 到 0 的掉线演练

# v3.3 协同优势验收（同一套 20 个任务跑两种策略，断言协同快 ≥10%）
python test/suite_compare.py --site http://localhost:30082 --min-gain 10

# 四类任务完整对比 + 生成 HTML 报告（先采集真实数据，再渲染）
python test/collect_benchmark_data.py --site http://localhost:30082 --out benchmark_data.json
python test/render_benchmark_report.py  --data benchmark_data.json --out benchmark_report.html

# 端侧算力约束下的交叉点实验（诊断类）
python test/edge_constraint_probe.py --pod hospital-a --load 40 --batch 4 --repeats 5 --label 端侧受限
python test/edge_constraint_probe.py --pod hospital-a --clean      # 实验后清理竞争进程
```

## 镜像与版本

| 镜像 | tag | 说明 |
|---|---|---|
| `k8s-repo/inference-scheduler` | **v3.3.8** | 控制面 + 前端；并发派发分区 / 批量诊断流水线 / 日常并发派发 / 非阻塞节点负载 / 紧凑编码 |
| `k8s-repo/hospital` | **v3.9** | 边：完整 DoubleTower 常驻 + `/local/*` + 紧凑编码 + **`/medical/infer_forward` 直交云端后端** |
| `k8s-repo/clinic` | v3.3 | 端：`/local/*` 本地执行（诊断=一跳转诊） |
| `k8s-repo/dc` | v3.0 | 数据中心：患者库 + 协同计算/备份（未改动） |
| `k8s-repo/medical-server` | **v1.1** | 诊断 server：接受紧凑特征（兼容旧格式） |
| `k8s-repo/benchmark-site` | **v1.2** | **★ 固定套件下发 / 采集 / 协同-本地对比（独立于 scheduler）** |

> **增量构建**：本机（WSL 控制面）无 PyPI / PyTorch 源 / npm 外网出口，因此
> scheduler / hospital / clinic 的 `Dockerfile` 改为**以上一个发布镜像为基础、只覆盖改动文件**；
> 需要从零构建时用同目录的 `Dockerfile.full`（需外网，且模型权重需另行恢复）。
> **已清理**：part1 / part2 / medical-worker / prediction / k8s legacy 清单 / AlexNet 模型代码与权重
> （`common/model.py`、`common/part*.pt`、`scheduler/predictor.py`）均已删除。
> 详细演进见 [`版本记录.md`](版本记录.md)。

## 技术栈

| 层次 | 技术 |
|---|---|
| 编排 | Kubernetes（Deployment/Service/Job/RBAC/亲和） |
| 控制面 | FastAPI + Python kubernetes client + Redis + Prometheus client |
| 推理 | PyTorch（DoubleTower 拆分：edge worker 前端 + 云 server） |
| 模拟 | 确定性产数/分块（common/v3_common.py）、metrics-server 空闲调度 |
| 前端 | React 18 + Vite + TypeScript |
