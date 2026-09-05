# 云边协同智能推理平台（医院/诊所 Pod 架构 v2.0）

基于 Kubernetes 的多模型边云协同医疗推理平台。从 v1.x「节点 = 医院实体、worker/part1 独立部署」的架构升级为 **v2.0「医院 / 诊所 Pod 化」**架构：

- **医院 Pod（hospital-a @ node1 / hospital-b @ node2）**：一个 Pod 内合并了原 `medical-worker`（鹏城医疗 DoubleTower 前端：DCE/DWI ResNet conv1→maxpool + 临床/影像组学编码）与 `alexnet-part1`（Conv1-Conv5 + avgpool）两套能力 —— 患者原始数据只在本医院 Pod/节点内处理，不出院。
- **诊所 Pod（clinic-1 @ node1 / clinic-2 @ node2）**：提供「查询指定 Pod 内存占用率」任务 —— 默认返回本 Pod（读容器 cgroup，恒可用），可显式指定目标 Pod（经 Kubernetes metrics-server 查询）。
- **云端（node3 / 控制面）**：`medical-server`（ResNet 骨干 + 融合分类 → bpCR 概率）、`part2`（AlexNet FC → ImageNet 类别）、`scheduler`（统一调度 + 集群编排 API + 前端）、`monitoring / prediction / redis` 保持不变。

控制平面提供全新的 **React 前端**：总览、三类任务提交、任务记录，以及**集群编排地图**（大圆圈=节点并显示 CPU/内存负载环，小圆圈=任务 Pod，可拖动、可新增 clinic Pod，面板编辑副本数/亲和策略/资源/镜像/标签等运维项），**「应用」**真实下发集群、**「重置」**恢复当前版本默认拓扑。

> 数据隐私基线不变：`medical` 流水线中，医院 Pod 只产出中间特征；`alexnet` 流水线的 part1 卷积也由医院 Pod 就地完成，仅特征图上云做 FC 分类。

---

## 架构总览

```
                          ┌──────────────────────────────────────────┐
                          │      Control Plane (scheduler)            │
                          │  FastAPI :8000 + Redis + Prometheus :9000 │
                          │  统一调度 · 集群编排 API(/cluster/*) · 前端  │
                          └───────┬──────────────┬───────────────────┘
                                  │              │
              ┌───────────────────▼───┐   ┌──────▼──────────────────┐
              │ node1 (edge)          │   │ node2 (edge)            │
              │  ● hospital-a Pod     │   │  ● hospital-b Pod       │
              │    ├ medical front-end │   │    ├ medical front-end  │
              │    └ alexnet part1     │   │    └ alexnet part1      │
              │  ● clinic-1 Pod        │   │  ● clinic-2 Pod         │
              │    └ 内存监控任务        │   │    └ 内存监控任务         │
              └──────────┬────────────┘   └──────────┬──────────────┘
                         │ 中间特征 / feature map     │
                         ▼                           ▼
              ┌──────────────────────────────────────────────────────┐
              │ node3 (cloud DC)                                      │
              │  medical-server :9001 (ResNet 骨干+融合+bpCR 分类)       │
              │  part2 :8002 (AlexNet FC) · redis                     │
              └──────────────────────────────────────────────────────┘
```

### 推理流水线（与 v1.x 语义一致，仅执行位置变为 Pod）

**Medical（bpCR 预测）**：`医院 Pod /medical/infer`（前端特征） → `medical-server /infer`（骨干+融合） → bpCR 概率。
**AlexNet（图像分类）**：`医院 Pod /alexnet/infer`（part1 Conv） → `part2 /infer`（FC） → ImageNet 类别。
**Clinic（内存监控）**：`clinic Pod /query/mem` → `{usage_bytes, limit_bytes, usage_percent, source}`。

---

## 目录结构（v2.0 主要变更）

```
├── hospital/                  ★ 新增：医院 Pod（合并 worker + part1）
│   ├── app.py                 # /medical/infer + /alexnet/infer + /health + /metrics
│   ├── Dockerfile             # ~1.8GB（内置 medical 权重与 part1.pt）
│   └── requirements.txt
├── clinic/                    ★ 新增：诊所 Pod（内存监控任务）
│   ├── app.py                 # /query/mem（默认自身 cgroup，可指定目标 Pod）
│   ├── Dockerfile
│   └── requirements.txt
├── scheduler/                 # 控制面（v2.0 改造）
│   ├── api.py                 # REST + /schedule/clinic + /cluster/* + 前端静态托管
│   ├── scheduler.py           # 按实体路由（hospital-a/b、clinic-1/2）的三流水线
│   ├── service_registry.py    ★ 实体服务注册（env 可覆盖，支持运行时新增 clinic）
│   ├── cluster_config.py      ★ 集群默认模型/校验/亲和模板（版本单一事实来源）
│   ├── cluster_api.py         ★ /cluster/status|default|validate|apply|reset|restart
│   ├── policy.py / task_manager.py / metrics.py / redis_client.py / …
│   └── Dockerfile             # 打进 frontend/dist 与 kubernetes python 客户端
├── frontend/                  ★ 全新 React 18 + Vite + TS 前端（HashRouter）
│   ├── src/pages/             # Overview / Submit / History / Cluster
│   ├── src/components/        # ClusterMap(SVG 大/小圆拖拽) PodPanel StatCard …
│   └── package.json / vite.config.ts
├── k8s/
│   ├── hospital-a(-service).yaml / hospital-b(-service).yaml   ★ 新增
│   ├── clinic-1(-service).yaml / clinic-2(-service).yaml       ★ 新增
│   ├── clinic-rbac.yaml                 ★ clinic SA 读取 pods/metrics
│   ├── scheduler-cluster-rbac.yaml      ★ scheduler SA 编辑 hospital/clinic 部署
│   ├── service-monitors.yaml            # hospital/clinic/part2/server 指标抓取
│   ├── scheduler-deployment.yaml        # v2.0（含 HOSPITAL_A/B_URL、CLINIC_1/2_URL）
│   └── legacy/                          # 归档 v1.x：part1、medical-worker 清单
├── part1/  medical-worker/              # 已合并进 hospital/（代码留档，不再部署）
├── medical-server/ part2/ monitoring/ prediction/  # 云端组件（保持不变）
├── model_profile.yaml                   # 增加 clinic.mem
├── build.sh / deploy.sh / start_all.sh / stop_all.sh
├── test/  scripts/  common/  z-鹏城医疗模型代码/   # 权重与原始模型参考代码
└── 版本记录.md                            ★ 版本历史（v1.x → v2.0）
```

---

## 快速开始

### 1. 前置条件
- Kubernetes 集群 ≥4 节点（control-plane + node1/node2 edge + node3 cloud），节点标签见 `k8s/node-labels.yaml`。
- Harbor 镜像仓库 `10.29.182.66:5000/k8s-repo`；节点无 docker.io 出口（全部走内部仓库）。
- 权重文件（`z-鹏城医疗模型代码/…/best_model_fold1.pth`、`common/part1.pt`、`common/part2.pt`）在本地仓库或集群 `/data/medical-model/weights` 中（这些文件体积大，不进 Git，构建前需就位）。

### 2. 构建并推送镜像（v2.0）

```bash
bash build.sh        # 构建并推送 scheduler(v2.0.3)/hospital(v2.0)/clinic(v2.0.1)
```

### 3. 部署

```bash
bash deploy.sh
# 阶段：RBAC → 基础设施 → hospital-a/b → clinic-1/2 → cloud 服务 → scheduler v2.0
#        → 等待 rollout → 清理 legacy（part1 / medical-worker）
```

### 4. 本地开发（无需 Docker/K8s，单机模拟 Pod 拓扑）

```bash
bash start_all.sh    # hospital=8006(clinic=8007) server=9001 part2=8002 scheduler=8000
open http://localhost:8000/app/index.html
bash stop_all.sh
```

> 本地模式下 hospital-b / clinic-2 与 hospital-a / clinic-1 共用同一本地端口（实体一致，仅便于联调）。

### 5. 测试

```bash
python test/submit_task.py --hospital hospital-a --model medical --priority 9
python test/query_result.py <task_id> --wait
python test/e2e_test.py --model medical | alexnet | clinic
# 集群编排 API（真实下发/重置）
curl -s http://<control-plane>:30080/cluster/status
curl -s http://<control-plane>:30080/cluster/default
```

---

## 三类任务（API）

| 任务 | 端点 | 说明 |
|---|---|---|
| 医疗 bpCR | `POST /schedule/preprocessed` `{patient_id, hospital, priority, deadline}` | worker 前端在医院 Pod 执行 |
| 图像分类 | `POST /schedule/task` `{model:"alexnet", hospital, input:{image}, …}` | part1 在医院 Pod，part2 在云端 |
| 内存监控 | `POST /schedule/clinic` `{clinic, target_pod?, priority}` | 目标留空=查询 Clinic Pod 自身 |

任务结果查询 `GET /task/result/{id}`，历史 `GET /tasks`、`GET /tasks/stats`（by_model 含 clinic）。
`GET /health` 现在探测 hospital-a/b、clinic-1/2、medical-server、part2。

## 集群编排 API（前端编辑器后端）

| 端点 | 方法 | 说明 |
|---|---|---|
| `/cluster/status` | GET | 节点(负载) + 可编辑实体(部署/实例实况) + 只读部署 |
| `/cluster/default` | GET | 当前版本(v2.0)默认拓扑模型 |
| `/cluster/validate` | POST | 校验期望模型（拖放/副本/亲和合法性） |
| `/cluster/apply` | PUT | 真实应用（patch 现有 + 创建/删除新增 clinic） |
| `/cluster/reset` | POST | 恢复当前版本默认配置 |
| `/cluster/restart` | POST | 对可编辑实体执行滚动重启 |

- 可编辑实体：`hospital-a/b`、`clinic-1/2`（含运行时新增的 `clinic-N`）；`medical-server`、`part2`、`redis`、`scheduler`、`monitoring`、`prediction` 只读展示。
- 亲和策略：`fixed`（固定节点，replicas=1）、`role-edge`（nodeSelector role=edge）、`spread-edge`（role=edge + 按实体反亲和分散，replicas ≤ 2）。
- 校验规则与默认模型定义在 `scheduler/cluster_config.py`，与 `k8s/*.yaml` 保持一致（改动需同步两者）。

## 镜像与版本

| 镜像 | tag | 说明 |
|---|---|---|
| `k8s-repo/inference-scheduler` | v2.0.3 | 控制面 + /cluster API + React 前端(dist) + 内置测试患者数据 |
| `k8s-repo/hospital` | v2.0 | worker + part1 合并，~1.8GB |
| `k8s-repo/clinic` | v2.0.1 | 内存监控（metrics 用量解析修复） |
| `k8s-repo/medical-server` / `k8s-repo/alexnet-part2` | v1.0 | 云端，未变更 |

> tag 演进：v2.0 架构版 → v2.0.1（clinic metrics 量纲解析、scheduler part2 探测）→
> v2.0.2（scheduler 内置 `/test/patients` 数据集、前端 AlexNet 请求改为 `input.image`、
> 新增 clinic 默认镜像、reset 不再把 clinic 降级回旧版）→
> v2.0.3（节点负载改由 metrics-server 实时计算、提交结果内联展示任务详情）。
> `build.sh` 按各镜像 tag 分别构建。详细演进见 [`版本记录.md`](版本记录.md)。

## 运维要点

- **节点负载显示**：`/cluster/status` 中节点 cpu/memory 来自 Prometheus（`node_memory_*` / `node_cpu_seconds_total`），Prometheus 不可用时自动降级并标记 `ok=false`。
- **clinic 指定 Pod 内存查询**：依赖 metrics-server + `k8s/clinic-rbac.yaml`；自身查询始终可用（cgroup）。
- **数据文件恢复**：`z-鹏城医疗模型代码/`、`common/*.pt` 不进 Git；从本机归档 / Harbor 镜像 / 集群 `/data/medical-model/weights` 恢复后再执行 `build.sh`。

## 技术栈

| 层次 | 技术 |
|---|---|
| 编排 | Kubernetes（Deployment/Service/NodeSelector/Affinity/RBAC） |
| 控制面 | FastAPI + Python kubernetes client + Redis + Prometheus client |
| 推理 | PyTorch / torchvision（AlexNet 拆分）、DoubleTower（ResNet+Transformer 拆分） |
| 前端 | React 18 + Vite + TypeScript + 原生 SVG 集群地图 |
| 镜像 | Harbor 私有仓库，多阶段 python:3.11-slim（CPU-only PyTorch） |
