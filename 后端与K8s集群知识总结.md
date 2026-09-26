# 后端与 K8s 集群知识总结

> 范围：`PengchengMedicalModel-DistributedInference/`（AI 诊疗云边端协同应用平台 v3.x）
> 依据：`k8s/*.yaml`、`scheduler/*.py`、`hospital/`、`clinic/`、`dc/`、`medical-server/`、
> `common/local_exec.py`、`benchmark/`、`deploy.sh` / `build.sh`、README 与设计方案。
> 文中的所有镜像 tag、端口、环境变量均取自当前仓库文件；与文档描述不一致处已在 §12 标出。

---

## 1. 总览：这个集群里"后端"是什么

后端 = **一个云（数据中心）控制面 + 两类边缘执行体 + 一组数据中心服务**，全部以 Pod 形式跑在同一个
K8s 集群的 `default` 命名空间里，靠 ClusterIP Service 互相调用。

```
                        ┌─────────── Data Center 数据中心 (node3 · 云) ───────────┐
                        │ scheduler (控制面/调度/前端宿主, 8000+9000)              │
                        │ medical-server (诊断后端 layer4+分类器, 9001)           │
                        │ dc-services (患者库 patient-db + 协同计算, 8010)        │
                        │ redis (任务队列/结果存储, 6379)                          │
                        └──────▲────────────────────────────▲────────────────────┘
             诊断 worker / 分区  │                            │  分区 / P2P chunk / 上传备份
        ┌───────────────────────┴──────┐        ┌────────────┴──────────────────┐
        │ hospital-a@node1 / b@node2   │        │ clinic-1..4@node1/node2       │
        │ (边) DoubleTower 前端 worker  │        │ (端) 业务发起 + /local/* 本地执行 │
        └──────────────────────────────┘        └───────────────────────────────┘
                    运维平面 (desktop-jm5iec6): monitoring(30090) · benchmark-site(30082)
```

**四类任务**（都由 hospital/clinic 发起，scheduler 编排）：`diagnosis` 诊断、`compute` 计算、
`sync` 通信、`routine` 日常。每类都有 `collaborative`（云边端协同）/ `local`（发起 Pod 就地执行）
/ `auto`（依赖不可用时自动降级为 local）三种执行模式 —— 诊断是唯一例外：**没有本地策略**，
`mode=local` / `force_degraded` 一律 400 拒绝（医院没有模型 server 半段）。

---

## 2. 节点拓扑与标签

| 节点 | 角色 | 标签/约束 | 承载的 Pod |
|---|---|---|---|
| `node1` | 边 edge | `role=edge`, `hospital=hospital-a` | hospital-a、clinic-1、clinic-3 |
| `node2` | 边 edge | `role=edge`, `hospital=hospital-b` | hospital-b、clinic-2、clinic-4 |
| `node3` | 云 cloud | `role=cloud`, `zone=cloud-dc` | scheduler、medical-server、dc-services、redis |
| `desktop-jm5iec6` | 控制面/运维 | 带 `node-role.kubernetes.io/control-plane:NoSchedule` 污点 | monitoring、benchmark-site（用 toleration 落上去） |

- 标签清单在 `k8s/node-labels.yaml`（**该文件只是注释与验证命令，没有可 apply 的资源对象**，
  真实打标靠 `kubectl label node ...`）。
- **没有 PV/StorageClass/PVC**：本项目所有持久化一律 `hostPath`（见 §7），因此带状态的 Pod
  （redis）必须固定节点，不能漂移。
- 推理相关的 Pod 都用 `nodeSelector: kubernetes.io/hostname` 硬绑定，`k8s/node-labels.yaml`
  里提到的 `role=cloud` 只被 medical-server 使用。

---

## 3. 工作负载清单（k8s/）

### 3.1 有状态基础设施

| 资源 | 类型 | 镜像 | 端口 | 调度 | 持久化 |
|---|---|---|---|---|---|
| `redis` | Deployment ×1 | `redis:6-alpine`（Harbor） | 6379 | `node3` 硬绑定 | AOF+RDB → hostPath `/data/redis-data` |
| `redis-service` | **ClusterIP** | — | 6379 | — | 仅集群内可达 |

Redis 启动参数（`k8s/redis-deployment.yaml`）：
`--protected-mode no --bind 0.0.0.0 --appendonly yes --appendfsync everysec --save 60 1 --dir /data`
资源 `requests 100m/256Mi`、`limits 500m/512Mi`。注释里明确：关 protected-mode 是为了恢复旧行为，
可达性仍由 ClusterIP 限制在集群内。

### 3.2 云侧业务服务

| 资源 | 类型 | 镜像 | 端口 | 关键 env |
|---|---|---|---|---|
| `scheduler` | Deployment ×1 | `inference-scheduler:v3.6.0` | 8000(http) / 9000(metrics) | `REDIS_HOST=redis-service`、`MAX_CONCURRENT=4`、`MAX_QUEUE_SIZE=200`、各实体 URL、`ROUTINE_IMAGE` |
| `scheduler-service` | **NodePort** | — | 8000→**30080**，另暴露 9000 | 前端 `/app` 由 scheduler 托管 |
| `medical-server` | Deployment ×1 | `medical-server:v1.4` | 9001 | `MODEL_PATH=/app/weights/best_model_fold1.pth` |
| `medical-server-service` | ClusterIP | — | 9001 | — |
| `dc-services` | Deployment ×1 | `dc:v3.0` | 8010 | `DB_VERSION=v3-db-1`、`DB_ITEM_COUNT=64`、`CHUNK_KB=4`、`NODE_NAME`(fieldRef) |
| `dc-services` | ClusterIP | — | 8010 | — |

- scheduler 只有 **1 个副本**、`--workers 1`，并发靠进程内 4 个后台线程 + 线程池；
  节点选择 `kubernetes.io/hostname: node3`，与 redis/medical-server/dc 同机（这点在性能结论里被反复提到：
  node3 只有 4 核，还要跟调度器/redis/患者库抢 CPU）。
- medical-server 的权重来自 hostPath `/data/medical-model/weights`（**只读挂载**），
  readiness `initialDelaySeconds: 120` / liveness 180，因为 PyTorch 冷加载很慢。
- dc-services 的"患者库"是**纯内存字典**（`_uploaded` / `_backups`，最多保留 20 个备份），
  **没有落盘**，Pod 重建即丢 —— 与 redis 形成对比，是理解"哪里真正持久化"的关键。

### 3.3 边/端执行体

| 资源 | 副本 | 镜像 | 端口 | 资源 | 卷 |
|---|---|---|---|---|---|
| `hospital-a` | 1 @node1 | `hospital:v3.14` | 8006 | req 1500m/3Gi，lim 4/6Gi | `/data/local-results` |
| `hospital-b` | 1 @node2 | `hospital:v3.14` | 8006 | 同上 | 同上 |
| `hospital-*-service` | ClusterIP | — | 8006 | — | — |
| `clinic-1`…`clinic-4` | 各 1（1/3@node1、2/4@node2） | `clinic:v3.4` | 8007 | req 300m/512Mi，lim 1500m/2Gi | `/data/local-results` |
| `clinic-N-service` | ClusterIP | — | 8007 | — | — |

- clinic 使用专用 ServiceAccount `clinic`（`serviceAccountName: clinic`）；hospital 与云侧用默认 SA。
- clinic/hospital 的 env 里有 `SCHEDULER_URL`、`DC_SERVICES_URL`、`MEDICAL_SERVER_URL`、
  `LOCAL_WORKERS=2`、`LOCAL_QUEUE_CAPACITY=8`、`LOCAL_RESULTS_DIR`(默认 `/data/local-results`)、
  `ENTITY_NAME/ENTITY_KIND`、`NODE_NAME`/`POD_NAME`(fieldRef)；hospital 额外有
  `LOCAL_BATCH_PARALLEL=1`（实测本地并行反而更慢，故串行是最优）。
- Clinic 镜像里同时含 `/app/run_routine.py`，它既是常驻 Service，也是 routine 一次性 Job 的载荷。

### 3.4 运维平面

| 资源 | 镜像 | 端口 | 说明 |
|---|---|---|---|
| `monitoring` | `inference-monitoring:v1.0` | 8005 → NodePort **30090** | `PROMETHEUS_URL` 指向 `monitoring-kube-prometheus-prometheus.monitoring.svc.cluster.local:9090` |
| `benchmark-site` | `benchmark-site:v1.29`（来自 `10.29.182.66:5000`） | 8090 → NodePort **30082** | 独立于 scheduler，SQLite hostPath `/data/bench/bench.db`，内置 `patients.json` |

benchmark-site 必须独立的原因写在清单注释里：**"调度器掉线仍能完成任务"的降级演练前提**，
它自己带前端、数据和降级逻辑，不依赖 scheduler Pod 存活；放在控制面节点是为了不跟被测 Pod 抢资源。

### 3.5 临时工作负载（运行时创建，不在 `k8s/` 里）

`routine` 任务的一种执行器 `executor=job` 会由 scheduler 用 K8s API **现场创建一次性 Job**：

```
job 名: routine-<task_id 末12位>-<index>
labels: {app: routine, job: <name>}
nodeSelector: kubernetes.io/hostname = <空闲节点，含 node3>
command: python /app/run_routine.py <json args>
backoffLimit: 0, ttlSecondsAfterFinished: 120, restartPolicy: Never
resources: req 100m/64Mi, lim 1/256Mi
env: POD_NAME / NODE_NAME (fieldRef)
```

默认执行器其实是 `warm`（**不建 Job**，把作业并发 POST 到空闲节点上已运行的 Pod）。
两条路径的取舍写在 `scheduler/scheduler.py::_routine_targets`：Job 每次有 ~4–6s 冷启动，
只对超大批次划算；`warm` 用于常规。

---

## 4. Redis 在集群内是如何运用的

### 4.1 定位：**唯一的有状态控制面存储，且只被 scheduler 使用**

全仓库检索 `REDIS_HOST` / `import redis` 只有两处：
- `scheduler/redis_client.py`（客户端实现，默认 `localhost:6379`，集群内由
  `k8s/scheduler-deployment.yaml` 注入 `REDIS_HOST=redis-service`、`REDIS_PORT=6379`）；
- `scheduler/scheduler.py`、`scheduler/task_manager.py`（使用方）。

hospital / clinic / dc / medical-server / benchmark-site **都不连 Redis**：前两者走 `/local/*`
自己落盘（hostPath），dc 的患者库在内存，站点用 SQLite。所以 **Redis 是调度器的私有依赖**，
scheduler 挂了 Redis 里的未完成任务就不会再被任何进程推进。

### 4.2 连接管理（`redis_client.py`）

| 机制 | 实现 |
|---|---|
| 连接池 | 进程级单例 `redis.ConnectionPool`，`max_connections=20`（4 worker + API 线程） |
| 超时 | `socket_timeout=5`、`socket_connect_timeout=3`、`retry_on_timeout=True` |
| 启动等待 | `wait_for_redis(max_retries=60, interval=1)`，最多等 60s |
| 健康循环 | 每 10s `ping()` 的后台 daemon 线程 `redis_reconnect_loop`，掉线置 `REDIS_AVAILABLE=False`，恢复后置回 True |
| 降级 | 任何单个命令抛异常都回落到进程内 `memory_store: dict`；`_get_pool()` 在 `REDIS_HOST` 为空时返回 None |

⚠️ 重要语义：`memory_store` 是 **per-Pod 内存兜底**，进程重启即丢，且多副本之间不共享。
它让"Redis 挂了"表现为"任务还能跑但不持久/不可跨 worker 共享"，而不是直接 500。
`dequeue_task()` 还会**同时检查 memory_store**，因为 `enqueue_task` 可能写 Redis 失败但写内存成功。

### 4.3 Key 设计与数据结构

| Key | 类型 | 写入方 | 读取方 | 说明 |
|---|---|---|---|---|
| `priority_queue` | **Sorted Set** | `enqueue_task(task_id, priority)` → `ZADD`，score = `-priority` | `dequeue_task()` → `ZPOPMIN`；`get_queue_length()` → `ZCARD` | 数字越小越先出队 = 优先级越高（1..10，默认 5）。用 ZPOPMIN 保证**原子弹出，无竞态** |
| `inference:{task_id}` | String(JSON) | `create_task` / `update_task` / `complete_task` / `fail_task` | `get_task` / `/tasks` / `/tasks/{id}` | 任务主记录：model、source、priority、stage、progress、status、deadline、metrics、result |
| `inference:result:{task_id}` | String(JSON) | `_finish` / `report_diagnosis`，格式 `{"status":"completed","result":{...}}` | `get_task_result` | 结果快路径；`get_task_result` 先读它再回落任务记录 |
| `cluster:node_registry` | String(JSON) | `update_node_status` → 含 `last_updated` 时间戳 | `get_node_registry` | 节点角色/资源注册表（当前主流程未强依赖） |

`get_tasks()` / `redis_keys()` **一律用 `SCAN`（`count=100` 游标循环）而不是 `KEYS`**，
避免在大 key 空间上阻塞 Redis 单线程；`get_tasks` 显式跳过 `inference:result:*` 前缀，
按 `start_time` 倒序取前 `limit`（默认 50）。

### 4.4 两个容易踩的坑（代码里已处理，值得记住）

1. **JSON 解析**：`get_task()` 返回的是字符串，调用方必须自己 `json.loads`；`get_tasks()`
   内部已经 `json.loads` 过了。`get_task_result()` 返回的已是对象。
2. **输入数据清理**：`task_manager.update_task` 在任务进入 `finished/failed/completed` 时
   **主动 `pop("input")`** —— 医疗输入可达数 MB～10MB，不删会让每次 `/tasks`、`/tasks/stats`
   全量传输并解析，是历史上的性能杀手。

### 4.5 Redis 与"降级"的关系

- 队列满（`ZCARD >= MAX_QUEUE_SIZE(200)`）→ `submit_task` 抛 `RuntimeError` → API 层映射 **429**。
- `GET /` 与 `/cluster/status` 都会返回 `redis_available`，是判断控制面健康的关键字段；
  `README` 也把 redis 列进 Data Center 的组件健康页。
- 掉线演练（`test/degradation_drill.py`）把 scheduler 缩容到 0：此时边缘 Pod 走 `/local/*` 继续完成
  计算/通信/日常，**Redis 完全不参与**；诊断则如实失败（不降级）。这就是"Redis 只管协同路径"的直接体现。

---

## 5. 后端服务与接口全景

### 5.1 scheduler（控制面，FastAPI，`scheduler/api.py`）

提交入口：`POST /schedule/{diagnosis|compute|sync|routine}`，通用字段
`source / priority / deadline / mode / force_degraded`。

任务与结果：`GET /task/result/{id}`、`GET /tasks`、`GET /tasks/{id}`、
`GET /tasks/recent?limit=`（精简子集，供前端 10s 轮询）、`GET /tasks/running`、
`GET /tasks/stats`、`DELETE /tasks/{id}`、`DELETE /tasks/clear`。

直投模式（控制面/数据面分离）：`POST /task/{id}/report` 由调用方回传各执行者结果，调度器只记账。

集群编排：`/cluster/status|summary|default|validate|apply|reset|restart|pod_metrics`
（见 §9）。健康：`/`（含 redis/queue/active）、`/health`（探测 6 个下游服务）。
数据集真实目录：`GET /files/tree`、`GET /files/get?path=`（读容器内 `/app/Database`，有路径穿越防护）。
测试数据集：`/test/patients`、`/test/patient/{id}`、`/test/dataset-info`。
前端静态托管：`/app`（`StaticFiles`）+ `GET /ui` 重定向；`/app/assets/*` 打一年 immutable 缓存，
`/app/*` 走 `no-cache`。Prometheus 指标在 **独立端口 9000**，不在 8000 上。

`POST /schedule/diagnosis` 的 `deliver="direct"` 分支不入队：调用 `plan_diagnosis()` 返回
"谁执行哪几个患者"的计划 + `report_url`，输入数据由客户端直投执行者。

### 5.2 边/端本地执行（`/local/*`，契约见 `调度模式与降级-设计方案.md` §4）

hospital:8006 与 clinic:8007 同构地暴露：
`POST /local/execute`、`POST /local/diagnosis`、`GET /local/health`、
`GET /local/result/{id}`、`GET /local/results?limit=`、`GET /local/queue`、`GET /metrics`。

运行时在 `common/local_exec.py`（共享给 hospital/clinic）：
- `ResultStore`：结果先写内存 LRU（默认 500 条、预载 100 条），再 **原子落盘**
  （`.<id>.tmp` → `os.replace`）到 `$LOCAL_RESULTS_DIR/<entity>/<task_id>.json`，Pod 重建不丢。
- `LocalQueue`：有界后台队列，默认 **2 worker / 容量 8**，超限抛 `QueueFull` → API 返回 **429**。
- 四类流水线：`run_compute`（串行分区）、`run_sync`（自编排 P2P 拉取→云上传→云备份）、
  `run_routine`（内联线程，不建 Job）、`forward_diagnosis`（诊断**总是拒绝**，clinic 只做一跳转诊）。
- `probe_scheduler()`：带缓存的 scheduler 可达性探测，用于 `/local/health`。

### 5.3 其余服务

| 服务 | 关键端点 |
|---|---|
| medical-server:9001 | `/infer`（接收边端特征，layer4+融合+分类器）、`/infer_full`（整段执行，供数据并行分片）、`/health` |
| hospital:8006 | `/medical/infer`（前端，兼容旧客户端）、`/medical/infer_forward`（**边端前端就地算 + 直投云端后端，数据面一跳**）、`/medical/infer_full`（已废弃/409）、`/v3/compute`、`/v3/sync/local|accepted|chunk/{id}`、`/query/mem` |
| clinic:8007 | `/v3/compute`、`/v3/sync/local|accepted|chunk/{id}`、`/query/mem`（读 cgroup 内存）+ 全套 `/local/*` |
| dc:8010 | `/v3/compute`、`/db/state`、`/db/item/{id}`、`/db/upload`、`/db/backup`、`/db/backups`、`/metrics` |

`/query/mem`（clinic/hospital）用于显式查询某 Pod 的内存：自查询读本地 cgroup，
查别的 Pod 才需要 `clinic-rbac.yaml` 授权。

---

## 6. 服务发现、端口与镜像

### 6.1 Service 与端口表

| Service | 类型 | port → targetPort | NodePort | 用途 |
|---|---|---|---|---|
| `scheduler-service` | NodePort | 8000 / 9000 | **30080** | 平台前端 + 控制面 API（唯一对外的业务入口） |
| `medical-server-service` | ClusterIP | 9001 | — | 诊断后端 |
| `dc-services` | ClusterIP | 8010 | — | 患者库 + 协同计算 |
| `redis-service` | ClusterIP | 6379 | — | 任务队列/结果 |
| `hospital-a/b-service` | ClusterIP | 8006 | — | 边 worker |
| `clinic-1..4-service` | ClusterIP | 8007 | — | 端 |
| `monitoring-service` | NodePort | 8005 | **30090** | 指标观测页 |
| `benchmark-site-service` | NodePort | 8090 | **30082** | 性能对比站点 |

DNS 规律：`<service>.<namespace>.svc.cluster.local`，同命名空间可直接用短名
（如 `http://redis-service:6379`）。`scheduler/service_registry.py` 把这些地址集中成
`HOSPITAL_*/CLINIC_*/MEDICAL_SERVER_URL/DC_URL`，并支持同名 env 覆盖（本地联调用）。
该模块还**自动把内部服务名与 `.svc.cluster.local` 追加进 `NO_PROXY`/`no_proxy`**，
避免集群内调用被企业代理劫持 —— 这是一个很容易被忽略但很关键的细节。
`clinic-3/4` 曾因为漏注册导致从它们发起的任务全部失败（代码注释里的 v3.2 记录）。

### 6.2 镜像与仓库

所有节点**无 docker.io 外网出口**，统一走内网 Harbor：
- 业务与基础设施：`k8s-master:5000/k8s-repo/*`（redis 也用这个仓库的 `redis:6-alpine`）
- benchmark-site 在 manifest 里写的是 `10.29.182.66:5000/...`（**与其余清单的仓库地址不一致**，
  版本记录里说明这是一次修复：之前写成 `k8s-master:5000` 导致 `ImagePullBackOff`）。

当前 manifest 锁定的 tag：`inference-scheduler:v3.6.0`、`hospital:v3.14`、`clinic:v3.4`、
`medical-server:v1.4`、`dc:v3.0`、`benchmark-site:v1.29`、`inference-monitoring:v1.0`。
`imagePullPolicy: IfNotPresent`。

构建策略（`build.sh`）：本机无 PyPI/PyTorch/npm 外网出口，scheduler/hospital/clinic/
medical-server 的 `Dockerfile` 都是**增量构建**（`FROM 上一个发布镜像`，只 `COPY` 改动文件），
从零构建需外网用 `Dockerfile.full`。前端产物打进 scheduler 镜像的 `/app/frontend/dist`。

---

## 7. 持久化（无 PV，全部 hostPath）

| 卷 | 节点路径 | 挂载点 | 使用者 | 内容 |
|---|---|---|---|---|
| `redis-data` | `/data/redis-data` | `/data` | redis | AOF + RDB |
| `local-results` | `/data/local-results` | `/data/local-results` | hospital-a/b、clinic-1..4 | 本地执行结果 JSON（`<entity>/<task_id>.json`） |
| `model-weights` | `/data/medical-model/weights` | `/app/weights`（只读） | medical-server | `best_model_fold1.pth` |
| `bench-data` | `/data/bench` | `/data/bench` | benchmark-site | SQLite `bench.db` |

要点：
1. 所有 hostPath 都声明 `type: DirectoryOrCreate`（目录不存在会自动创建，但**父目录权限/磁盘在节点上**）。
2. 这些带卷的 Pod 都同时有 `nodeSelector: kubernetes.io/hostname`（redis→node3、hospital-a→node1、
   hospital-b→node2、clinic-N 同理），即**卷和节点是绑死的一对**。**换节点 = 换数据**。
3. `ResultStore` 的磁盘写失败不会让任务失败（`writable=False` 时只留内存），`/local/health`
   会回报 `dir/writable/cached`。
4. dc 的患者库与备份**不在这一层**，是进程内存，属于"故意只做模拟"的设计。

---

## 8. 调度与节点负载感知

### 8.1 调度主循环

`scheduler/api.py` 启动时开 `MAX_CONCURRENT=4` 个 daemon 线程跑 `scheduler_worker`：
循环 `dequeue_task()`（Redis `ZPOPMIN`）→ `InferenceScheduler.dispatch_task()`。
空队列时 `sleep(0.05)`（v3.3 从 0.1s 调小，把异步队列的平均附加时延从 ~100ms 压到 ~25ms）。

`dispatch_task` 先按 `mode` 选路，再按 model 进四条流水线之一：

| 流水线 | 协同实现要点 |
|---|---|
| `_run_diagnosis` | 单例：医院 `/medical/infer_forward`（前端就地→云端后端一跳）；批量：`strategy=data`（默认，按算力加权分片给医院+数据中心）或 `split`（前端/后端两段流水线，`parallel` 并发派发前端） |
| `_run_compute` | 伙伴 = 发起端 + `datacenter` + 另一个医院，截断到 `partition_count`（2..6），用 `ThreadPoolExecutor` **并发派发**（v3.3 修复了此前 for 循环串行派发） |
| `_run_sync` | 从 dc `/db/state` 取清单、与本地 `holds` 求差集，多 stream 轮转不同 peer（云+边）并行拉 chunk，再 `/db/upload` → `/db/backup` |
| `_run_routine` | `warm`（默认，并发 POST 到空闲节点已运行 Pod）或 `job`（并发建/轮询/删 K8s Job，轮询间隔 0.2s） |

`_finish()` 统一收口：计算 `pipeline_total_ms` / `e2e_total_ms` / `queue_wait_ms`，
写入 `inference:result:{id}`、调用 `complete_task`、观测 `inference_latency` 直方图。
所有任务结果都带 `mode/mode_requested/degraded/degrade_reason/orchestrator/executor/initiator/stages[]`。

### 8.2 节点空闲度（`scheduler/node_usage.py`）

- 直接 **urllib 调 K8s API**（`/api/v1/nodes` 取 allocatable、`/apis/metrics.k8s.io/v1beta1/nodes`
  取 usage），需要 metrics-server；allocatable 缓存 300s。
- 空闲分 `free = 0.5*(1-cpu_usage) + 0.5*(1-mem_usage)`，`free_edge_nodes()` 按 free 排序 node1/node2。
- **v3.3 关键修复**：查询改成后台线程刷新（`NODE_USAGE_REFRESH_S=10`），调用方只读快照，
  **永不在请求关键路径上阻塞**。此前 3s TTL 的同步查询每次要 ~4s，把协同侧时延全吃掉了。
- TLS 细节：本集群 API server 证书链与本 Pod 注入的 SA CA 不匹配，代码在
  `CERTIFICATE_VERIFY_FAILED` 时**一次性降级为不校验证书**（集群内网调用）。

### 8.3 调度策略与画像

- `scheduler/policy.py`：model-aware / resource-aware / priority 三级策略，`MODEL_PROFILE`
  可由 `model_profile.yaml` 覆盖（diagnosis worker 允许 `role=edge`、server 允许 `role=cloud` 等）。
- `scheduler/resource_monitor.py`：经 Prometheus 查询节点资源的监控器（`NODE_ROLES` 映射四个节点）。
- `scheduler/scheduler.py::_exec_cost`：**执行者速度画像**（每例完整模型毫秒数的滚动平均，
  初值 hospital-a/b=130ms、datacenter=230ms），`report_diagnosis` 按 `0.7*prev+0.3*sample` 更新，
  用于"按每例成本加权分片"（v3.5c 起，避免慢的那台决定墙钟）。
- 选医院：`pick_hospital()` —— 医院发起用自己，诊所发起先看 `target_hospital`，
  否则用 `most_free_edge()` 映射到 hospital-a/b。

### 8.4 降级判定（`_dependencies_healthy`）

| 任务 | 依赖检查 |
|---|---|
| `routine` | `kubeops.available()`（K8s client 能否加载，in-cluster 优先） |
| `diagnosis` | medical-server `/health` **且** 至少一个 hospital `/health` |
| `compute`/`sync` | dc `/health` |

`auto` 探测不健康时：`routine/compute/sync` 降级为 `local` 并标 `degraded`；
`diagnosis` **直接失败**（`NO_LOCAL_DIAGNOSIS`），绝不把完整模型派到医院。

---

## 9. RBAC 与集群编辑（K8s API 使用面）

### 9.1 集群编辑 API（`scheduler/cluster_api.py`）

- `GET /cluster/status`、`GET /cluster/summary`：**4s TTL 服务端缓存**（`CLUSTER_CACHE_TTL`），
  合并 Deployment/Pod/Node/metrics 多处查询；`/summary` 是给总览页的轻量子集。
- `GET /cluster/pod_metrics`：逐 Pod CPU/内存用量与 limit。
- `GET /cluster/default` → `POST /cluster/validate` → `PUT /cluster/apply` →
  `POST /cluster/reset` → `POST /cluster/restart`。
- 可编辑模型（`cluster_config.py`）：只有 hospital/clinic 实体可改，且**只允许新增 clinic**；
  hospital 固定 hospital-a/b 且不能删；`affinity ∈ {fixed, role-edge, spread-edge}`；
  `fixed` 必须副本=1 且节点∈{node1,node2}，另外两种上限 = 边缘节点数 2。
- `apply` 用 **merge-patch**（`null` 删除字段，`nodeSelector=null` 时直接省略 key）；
  新增 clinic 会同时建 Deployment + Service + `register_clinic()`；删除会级联删 Service 并 `unregister_clinic()`。
- 错误可读性：`_k8s_err()` 解析 `ApiException.body` 里的真实 message（不再截断 200 字符）。
- 只读展示：`READONLY_APPS = [medical-server, dc-services, redis, scheduler, monitoring]`。

### 9.2 ServiceAccount 与权限边界

| SA | 绑定 | 权限 |
|---|---|---|
| `default`（scheduler/hospital/dc 等） | `ClusterRole/cluster-editor` | deployments: get/list/watch/patch/update/create/delete；services,pods: get/list/watch/create/delete；nodes: get/list/watch；metrics.k8s.io nodes/pods: get/list；**batch/jobs: get/list/watch/create/delete**；pods/log: get/list |
| `clinic` | `ClusterRole/pod-metrics-reader` | pods: get/list；metrics.k8s.io pods: get/list |

设计意图（注释里写明）：调度器要能下发 routine Job、做集群编辑；**clinic 故意没有 Job 权限**，
所以日常任务的本地执行只能用内联线程，不能建 Job。RBAC 走 `kubectl apply` 由 `deploy.sh` Phase 1 安装。

### 9.3 kubeops 客户端加载（`scheduler/kubeops.py`）

`load_incluster_config()` 优先，失败回落 `load_kube_config()`；成功后缓存
`BatchV1Api` + `CoreV1Api`；任何异常只记 `_client_error` 并让 `available()` 返回 False
（→ routine 报"Kubernetes Job 能力不可用"，`auto` 降级）。

---

## 10. 可观测性

- **Prometheus 指标**定义在 `scheduler/metrics.py`（`inference_queue_length`、`task_running{model}`、
  `node_available_cpu{node}`、`node_available_gpu{node}`、`inference_requests_total{model}`、
  `inference_failed_requests_total{model}`、`inference_latency_seconds{model}`、
  `inference_stage_latency_seconds{model,stage}`），由 `api.py` 在 **9000 端口**暴露，
  `update_metrics()` 每个任务完成后刷新。
- **ServiceMonitor**（`k8s/service-monitors.yaml`，需 Prometheus Operator）覆盖 5 个服务：
  scheduler（`port: metrics`，interval **5s**）、hospital、clinic、medical-server、dc-services
  （均 `port: http`，interval 30s，path `/metrics`）。注意 label 需匹配 `release: monitoring`。
- 边缘侧另有 `local_exec_requests_total` / `local_exec_latency_seconds`（v3.2 新增）。
- `monitoring` 服务（部署在控制面节点）读 kube-prometheus 的 Prometheus，暴露观测页 30090。
- 组件健康聚合：`GET /health` 依次探测 6 个下游（两医院、两诊所、dc、medical-server）。

---

## 11. 部署、发布与故障排查

### 11.1 部署顺序（`deploy.sh`，顺序有协议原因）

1. RBAC：`clinic-rbac.yaml` + `scheduler-cluster-rbac.yaml`
2. 基础设施：`redis-deployment.yaml`、`monitoring-deployment.yaml`
3. 数据中心：`dc-services.yaml`
4. 边：`hospital-a/b`（deployment+service）
5. 端：`clinic-1..4`（循环 apply deployment+service）
6. **medical-server 必须先于 hospital/scheduler**（v3.3 紧凑特征协议：新调度器向医院要紧凑特征并转发，
   旧 server 会 422，虽然调度器内置回退）
7. scheduler + scheduler-service
8. benchmark-site
9. `kubectl rollout status` 逐项等待（hospital 900s，其余 300s，失败用 `|| true` 不中断）
10. ServiceMonitors（可失败，集群无 Operator 时跳过）

### 11.2 后端视角的排障清单

| 症状 | 先看什么 |
|---|---|
| 任务全部 429 | `GET /` 的 `queue_length` 是否 ≥ `MAX_QUEUE_SIZE`；`redis_available` 是否 false |
| 任务卡在 queued 不动 | scheduler Pod 与 4 个 worker 线程是否存活；Redis 是否有 `priority_queue` 堆积 |
| `redis_available: false` | `redis-service` Endpoints 是否为空（redis Pod 没起 / 探针失败）；检查 `/data/redis-data` 是否可写 |
| `/cluster/*` 慢或 5xx | metrics-server 是否可用、scheduler SA 是否拿到 `cluster-editor`；`CLUSTER_CACHE_TTL` 是否被调成 0 |
| 日常任务报"Job 能力不可用" | in-cluster config 是否加载成功、`ROUTINE_IMAGE` 是否可拉取、RBAC 是否含 batch/jobs |
| 节点负载长期为 null | API server 证书链不匹配（看日志是否有 CERTIFICATE_VERIFY_FAILED）与 metrics-server |
| 诊断跨节点 500/422 | medical-server 版本是否 ≥v1.1（支持紧凑特征）、IMAGE 是否与调度器协议匹配 |
| 本地执行 429 | 边缘 Pod 的 `LOCAL_QUEUE_CAPACITY=8` 已满；看 `/local/queue` |
| Pod 重建后结果丢失 | 是否挂在 hostPath（`/data/local-results`、`/data/bench`、`/data/redis-data`），节点是否被换过 |

### 11.3 验证脚本（`test/`，都是对后端的黑盒验证）

`local_exec_smoke.py`（打 `/local/*`）、`scheduler_mode_test.py`（三种 mode 语义）、
`site_e2e.py`（站点端到端 + kubectl）、`degradation_drill.py`（**把 scheduler 缩到 0 做掉线演练**）、
`suite_compare.py`（同一套 20 任务跑协同/本地并断言协同快 ≥10%）、
`edge_constraint_probe.py`（在 Pod 内造 CPU 竞争找诊断协同的交叉点）、
`restart_resume_test.py`、`retry_guarantee_test.py`、`worker_resilience_test.py`。

---

## 12. 资源账、约束与当前清单中的不一致（排查时务必知道）

### 12.1 资源账（明显超卖，调度是"软"的）

| 节点 | 承载 requests 合计 | 备注 |
|---|---|---|
| node3 | scheduler 500m/1Gi + redis 100m/256Mi + medical-server **1/2Gi** + dc 200m/256Mi ≈ 1.8 核 / 3.5Gi | 4 核节点；medical-server limits 4/8Gi 会独占全机 |
| node1 | hospital-a 1500m/3Gi + clinic-1 300m/512Mi + clinic-3 300m/512Mi ≈ 2.1 核 / 4Gi | 8 核节点，另有 clinic-3 |
| node2 | hospital-b + clinic-2 + clinic-4 ≈ 2.1 核 / 4Gi | 4 核节点，压力更大 |

这解释了性能章节的核心结论：**node3（4 核，与调度器/redis/患者库共处）每例算力约为医院的一半**，
数据并行的墙钟被慢的那台拖住，诊断协同收益因此有条件成立。

### 12.2 需要留意的不一致 / 隐含假设

| 项 | 现状 | 影响 |
|---|---|---|
| 镜像 tag 双份 | `cluster_config.py` 里 `IMAGE_HOSPITAL=v3.0`、`IMAGE_CLINIC=v3.0`、`IMAGE_SCHEDULER=v3.6.0`，但 `k8s/*.yaml` 已是 hospital `v3.14`、clinic `v3.4` | 通过 `/cluster/*` **新增** clinic 时会用 `cluster_config` 的默认镜像（v3.0），需手改或改代码 |
| `ROUTINE_IMAGE` | scheduler env 指向 `routine-runner:v1.0`，`kubeops.py` 默认值是 `clinic:v3.0` | 两个镜像都不是当前 clinic tag；routine `executor=job` 路径依赖该镜像可拉取，默认走 `warm` 不受影响 |
| Redis 单副本 | Deployment `replicas: 1` + hostPath + node3 硬绑定 | 是**单点**：节点/Pod 故障期间协同路径不可用（本地执行不受影响，这是 v3.2 降级设计的价值） |
| 无 Redis 密码/认证 | protected-mode off、无 requirepass | 仅靠 ClusterIP 隔离；同集群任何 Pod 都能读写任务队列 |
| dc 患者库无持久化 | 内存 dict | Pod 重建后上传/备份记录清空（模拟设计，非缺陷） |
| `node-labels.yaml` 不可 apply | 纯注释文件 | 新集群初始化需要手工打标签/Taint |
| `k8s/` 与 `cluster_config.py` 双份真相 | 文件头注释明确要求"手动保持同步" | 改清单时两处都要动 |
| benchmark-site 仓库地址 | `10.29.182.66:5000`，其余为 `k8s-master:5000` | 换环境时容易拉不动，需统一 |
| hospital 无专用 SA | 用 `default` | 若日后收紧 default 权限，hospital 的 `/local/*` 落盘（hostPath）不受影响，但任何 K8s 调用会失败 |

---

## 13. 三十秒速记

1. **集群 = 1 云 + 2 边 + 4 端 + 2 运维**，全部在 `default` 命名空间，ClusterIP 互访，只有
   scheduler(30080)、monitoring(30090)、benchmark-site(30082) 对外。
2. **Redis 只服务 scheduler**：`priority_queue`（ZSet，`-priority` 打分，`ZPOPMIN` 原子出队）
   + `inference:{id}`（任务 JSON）+ `inference:result:{id}`。
   连接池 20、5s 超时、60 次启动重试、10s 重连线程，**任何失败回落 per-Pod `memory_store`**。
3. **持久化全靠 hostPath**（`/data/redis-data`、`/data/local-results`、`/data/medical-model/weights`、
   `/data/bench`），无 PV/PVC，Pod 与节点强绑定。
4. **控制面/数据面分离**：调度器只做决策与记账；边端 `infer_forward` 把特征直投云端后端，
   输入可 `deliver=direct` 由客户端直投执行者。
5. **降级是核心设计**：scheduler 掉线时 `compute/sync/routine` 走边缘 `/local/*`
   （有界队列 2/8、结果落盘、429 背压）；**诊断如实失败**，因医院没有模型 server 半段。
6. **权限最小化**：`cluster-editor` 给调度器（含 Job 创建 + Deployment/Service 编辑），
   `pod-metrics-reader` 给 clinic（**故意不含 Job**，所以本地日常只能内联线程）。
7. **不用 KEYS 用 SCAN、终态任务删 input、节点负载后台刷新**——这三条是历史上最重要的性能修复。
