# 云边协同智能推理平台 v3.0（正式版）—— 云 / 边 / 端

基于 Kubernetes 的边云协同医疗推理平台。**v3.0（正式版）**确立了三层架构：
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
| 运维 | desktop | — | monitoring / prediction（可选） |

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
├── hospital/            # 边：诊断 worker + /v3/compute、/v3/sync peer（移除 part1）
├── clinic/              # 端：/v3/compute、/v3/sync peer + /app/run_routine.py（日常 Job 入口）
├── dc/                  # ★ 数据中心：患者库 patient-db(/db/*) + /v3/compute + 备份（端口 8010）
├── common/v3_common.py  # ★ 共享确定性产数/分块逻辑
├── scheduler/           # 调度（迁至 node3）：4 条流水线 + node_usage/kubeops(Job)
├── frontend/            # React v3.0：四表单 + 详情 + 综合测试页 + 集群编排
├── k8s/                 # dc-services.yaml 新增；part2→legacy/ 归档；scheduler→node3
├── test/run_v3_comprehensive.py   # ★ 四类混合综合测试
└── v3.0-重构报告.md       # ★ 本次版本重构报告
```

## 前端（v3.1.6 · 双站点）

三级命名：**数据中心（云，node3）— 医疗中心（原 hospital，node1/2）— 医院（原 clinic，node1/2）**；
术语集中在 `frontend/src/terms.ts`（实体显示名、层级、能力简述、流转标签），便于后续统一调整。

| 入口 | 地址 | 说明 |
|---|---|---|
| 落地页 | `/app/` | 选择「用户调用平台」或「管理平台」 |
| 用户调用平台 | `/app/user/` | 顶部**任务类型横栏**（诊断/计算/通信/日常）+ **居中单列**：选类型即内联列出该文件夹的数据文件，选中即执行，记录与结论按同一列依次排列；左侧历史任务列表 |
| 管理平台 | `/app/admin/` | `平台总览` / `可视化` / `集群负载` / `综合测试` / `集群编排` |
| 示例数据集 | `/app/datasets/` | `index.json` + 四个中文文件夹（计算 / 通信 / 日常 / 诊断），文件名即参数 |

- **用户平台（v3.1.6）**：提交卡**一张卡两部分**——标题行就是四个任务类型按钮（等分四格、46px 高、选中为黄铜实底），卡身紧跟其后（间距仅 1px 发丝线 + 14px 内边距），两者是一体的，不再是一上一下隔开的两块。卡身只有两项：**发起方** select（医院 1/2、医疗中心 A/B，默认 医院 1）与**「＋ 上传」**入口；未选数据时提示「请先上传数据文件」且执行禁用。点上传打开**数据集目录浏览器**弹窗，**直接落在当前类型对应的文件夹**（诊断→诊断、计算→计算、通信→通信、日常→日常），保留 `数据集 / 计算` 面包屑可回根层再进其它文件夹（根层列出四个文件夹及文件数），文件行给出中文名 + 说明 + 体积，选中即关闭并回填（文件名 + 说明，按钮变「↻ 重新上传」），Esc / 点遮罩 / 关闭按钮均可关闭；目录只在打开时读取一次，文件仅在选中时读取单个。参数写在**文件名**里（`计算_规模中等_1` / `通信_省流_1` / `日常_任务数3_1` / `诊断_患者1404920_1`），用户无需选择类型与参数，也没有本机文件对话框。提交记录与结论按同一列在提交卡下方依次排列（`max-width:880px` 居中）。结果**只给结论**：诊断给 bpCR 概率大字与 pCR/non-pCR 判定、计算给分区数与数据量、通信给拉取/上传与备份与否、日常给完成数；运行中仅进度条，**不显示时延、阶段明细、执行节点与指标表**。
- **平台总览**：三级架构与四类任务说明、节点/实体/实例/任务/组件健康 KPI、任务类型分布与各功能入口。
- **可视化**：逻辑架构总览（数据中心 / 医疗中心 / 医院 / 运维四组实体 + 关系连线，**不显示负载**）→ 任务执行轨迹（类型栏为 **多任务 · 诊断 · 计算 · 通信 · 日常**；选「多任务」时在**同一张流程图**上同时绘制多条任务——拓扑取所选任务节点与连线的**并集**（未被任何任务使用的仍为最淡「无关」色），每条任务一种暖色**色迹**（已执行与当前步骤沿法线平行错开），当前步骤用该任务颜色的**光环**标出，图例列出「同屏任务色迹」与各任务号；单任务模式按执行时间顺序高亮：已执行灰 / 当前橙 / 待执行淡灰虚 / 无关最淡，并行步骤 `∥N` 同时点亮）→ 执行时序 → 图例；一条归一化进度驱动全部轨迹（任务 i 游标 = `round(progress × (步数ᵢ − 1))`），由同一套 `⏮ 重置 / ◀ 上一步 / 下一步 ▶ / ▶ 自动` 控制。页面不再出现说明性小字（回放说明段、卡片右上 meta、`同屏任务 / 图内轨迹 / 基准任务 / 任务总数 / 共享步进` 等单元、图例长句、`循环演示` 与 `基准 … · N 步` 标记、架构总览的 `v3.0 · 12 个逻辑实体 · 最后更新` 均已删除）。
- **集群负载**：节点资源（4 节点 CPU/内存 数值与条）、各节点 Pod 就绪、组件健康（/health）、任务与队列概览。
- **综合测试**：四类任务混合批量执行与逐步结果。
- **集群编排**：`地图视图 / 列表视图` 双视图（拖动 Pod/行改节点、右侧 Pod 详情、校验/应用/重置）。
- 全站中文为主、减少冗余英文与无关信息；正文 16px、标题 32px，重点数值加粗。
- 前端 GET 请求失败自动重试一次；后端 v3.0.12 起 `/cluster/status`、`/cluster/summary` 服务端缓存 4s、新增轻量 `/tasks/recent`、`/app/assets/*` 与 `/app/datasets/*` 长缓存；轮询 10s 且标签页隐藏时暂停。
- 示例数据集为纯静态文件，随前端构建产物发布（`frontend/public/datasets/` → `/app/datasets/`，中文目录与文件名可直接访问），新增数据只需放入对应文件夹并在 `index.json` 登记（`path/name/description/bytes`）；用户平台只在打开浏览器与选中文件时各请求一次。

## 快速开始

```bash
# 构建并推送（hospital/clinic/dc 为 v3.0，scheduler 为 v3.1.4）
bash build.sh

# 部署（RBAC→dc-services→hospital→clinic→medical-server→scheduler→清理 part2）
bash deploy.sh

# 本地单机联调（hospital 8006 / clinic 8007 / dc 8010 / server 9001 / scheduler 8000）
bash start_all.sh      # http://localhost:8000/app/

# 四类任务综合测试
python test/run_v3_comprehensive.py --base http://localhost:30080
```

## 镜像与版本

| 镜像 | tag | 说明 |
|---|---|---|
| `k8s-repo/inference-scheduler` | v3.0.12 | 控制面(迁 node3) + /cluster/* + React 前端 + 测试数据 |
| `k8s-repo/hospital` | v3.0 | 边：诊断 worker + v3 worker（无 part1） |
| `k8s-repo/clinic` | v3.0 | 端：v3 worker + 日常 Job 入口 |
| `k8s-repo/dc` | v3.0 | 数据中心：患者库 + 协同计算/备份 |
| `k8s-repo/medical-server` | v1.0 | 诊断 server（沿用） |

> part2 / part1 / AlexNet 镜像与任务不再部署。详细演进与变更见 [`版本记录.md`](版本记录.md) 与
> [`v3.0-重构报告.md`](v3.0-重构报告.md)。

## 技术栈

| 层次 | 技术 |
|---|---|
| 编排 | Kubernetes（Deployment/Service/Job/RBAC/亲和） |
| 控制面 | FastAPI + Python kubernetes client + Redis + Prometheus client |
| 推理 | PyTorch（DoubleTower 拆分：edge worker 前端 + 云 server） |
| 模拟 | 确定性产数/分块（common/v3_common.py）、metrics-server 空闲调度 |
| 前端 | React 18 + Vite + TypeScript |
