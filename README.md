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

## 前端页面（v3.0.11）

导航：`01 总览` / `02 实时架构` / `03 任务提交` / `04 任务记录` / `05 综合测试` / `06 集群编排`（科研档案终端风：纸灰底、细线、黄铜点缀、编号导航、等宽数字）。

- **实时架构（/architecture）**：5s 自动刷新，按 云 Data Center / 边 hospital / 端 clinic 三层展示组件健康（/health）、节点负载（/cluster/summary）、任务总线（/tasks/stats）与四类任务流程。
  - 顶部**任务执行轨迹地图（TASK TRAJECTORY MAP）**：上中下=云/边/端三层，可按 诊断/计算/通信/日常 切换轨迹模板；依据真实任务数据按**执行时间顺序**逐步高亮节点与连线（已执行=黄铜 / 当前=橙色脉冲+流动光点 / 待执行与无关=分层灰化）。
  - 播放控制：`⏮ 重置 / ◀ 上一步 / 下一步 ▶ / ▶ 自动`——手动模式点一次只走一步；自动模式 **2.5s/步（带进度条）+ 末步停留 3s → 循环演示**，另有 `AUTO / MANUAL / LIVE` 模式标识与可点击的时序芯片（① 提交 · 12ms …）。
  - 节点以**图标徽章**呈现（齿轮/机架/数据库/缓存堆叠/医院十字/听诊器/Job 立方体），云层四个组件被包进 `DATA CENTER · 云数据中心` 整体面板并显示实时组件摘要。
  - 已执行(passed)连线/节点灰化，仅“当前”步橙色高亮；**并行步骤**（分区计算 / P2P 拉取 / Job 执行）同时点亮全部连线并在时序中标记 `∥N`（耗时取最大值）；任务选择改为**前缀优先的下拉列表**。
  - 轨迹上方为 **`当前集群架构总览`（逻辑架构视图）**：按 `云数据中心` / `医院 · 边` / `诊所 · 端` / `运维` 分组面板展示逻辑实体（无边框图标 + 简短中文说明 + 实时健康/负载），连线精简为：边⇄云「协同诊断 / 计算 / 同步 / 日常」、端⇄云「协同计算 / 同步 / 日常」、诊所→医院**单条点线「转诊」**、运维→云点线「指标观测」、云内调度—医疗推理/患者库/队列连接；节点 CPU/内存 不再逐 Pod 重复显示，改由独立「节点资源」小框集中展示 4 个节点。全站文案中文化（轨迹图与架构图标题、模式、图例均无冗余英文），左侧边栏去英文并放大文字。
  - 前端 GET 请求在超时/网络错误时自动重试一次（0.8s 间隔），缓解集群瞬时抖动造成的页面超时。
  - 信息条改为无边框层次化排版（一级信息加粗墨色 / 二级信息小灰标签），全站减少内层框线并加深次级文字、关键数值加粗，重点更醒目。
- **集群编排（/cluster）**：`地图视图 / 列表视图` 双视图——地图为大圆(节点负载)+小圆(Pod)可拖动；列表按节点分组显示实体/副本/亲和/镜像/实例与只读部署，行可拖拽到其它边缘节点改 node；右侧 Pod 详情面板与 校验/应用/重置 两视图共用。
- **任务提交/记录/综合测试**：四类任务提交（可选举发起 Pod；clinic 诊断可选转诊）、四类结果详情（含可展开的详细时延统计）、四类混合综合测试。

## 快速开始

```bash
# 构建并推送（scheduler/hospital/clinic/dc 全部 v3.0）
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
| `k8s-repo/inference-scheduler` | v3.0.11 | 控制面(迁 node3) + /cluster/* + React 前端 + 测试数据 |
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
