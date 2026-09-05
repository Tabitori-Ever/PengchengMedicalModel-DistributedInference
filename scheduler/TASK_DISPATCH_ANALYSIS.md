# 任务调度与分发机制分析文档

> 日期: 2026-07-27
> 覆盖文件: `scheduler.py`, `task_manager.py`, `policy.py`, `queue.py`, `resource_monitor.py`, `predictor.py`, `api.py`

---

## 1. 架构全景

```
                              ┌────────────────────┐
                              │    FastAPI Server   │
                              │    (api.py:8000)    │
                              └────────┬───────────┘
                                       │ POST /schedule/task
                              ┌────────▼───────────┐
                              │  InferenceScheduler │
                              │  (scheduler.py)     │
                              │                    │
                              │  submit_task()  ────┼──── PriorityTaskQueue (queue.py)
                              │  dispatch_task() ───┼──── Task CRUD (task_manager.py)
                              │  _run_*_pipeline()──┼──── Node Selection (policy.py)
                              │                    │
                              └────────┬───────────┘
                                       │ HTTP POST
                    ┌──────────────────┼──────────────────┐
                    ▼                  ▼                  ▼
          ┌──────────────┐   ┌──────────────┐   ┌──────────────┐
          │ part1-service │   │ part2-service │   │ med-worker   │
          │    :8001      │   │    :8002      │   │    :8006     │
          └──────────────┘   └──────────────┘   └──────────────┘
```

---

## 2. 各模块职责分配

### 2.1 `task_manager.py` — 任务生命周期管理

**职责**: 任务的 CRUD 操作和状态管理，**不涉及调度决策**。

| 函数 | 功能 |
|------|------|
| `create_task()` | 创建任务对象，生成 ID，初始化状态为 `scheduler`/`running`，持久化到 Redis |
| `update_task()` | 按 stage 名称自动推进 `progress` 百分比（scheduler→0%, worker→30%, server→60%, finished→100%） |
| `complete_task()` | 标记任务完成，记录延迟和结果 |
| `fail_task()` | 标记任务失败，记录错误信息 |
| `get_default_resources()` | 按模型返回默认资源需求 |
| `get_task_stats()` | 聚合统计（按模型/节点/stage 分布） |

**关键设计**: 任务状态机
```
scheduler → preprocess → worker/part1 → server/part2 → finished
   (0%)        (5%)         (30%)          (60%)        (100%)
                                                    ↖ failed (任意阶段)
```

### 2.2 `queue.py` — 优先级任务队列

**职责**: 提供线程安全的优先级队列，**是调度顺序的核心**。

实现细节：
- 基于 `heapq` 的最小堆，通过**对优先级取负**实现高优先级先出队
- 使用 `(_counter, priority, task)` 三元组，`_counter` 保证同优先级 FIFO
- 支持 `put()`/`get()`/`peek()`/`remove()` 操作
- 最大容量 `maxsize=200`，超出抛 `QueueFullError`

**这是实际决定任务执行顺序的唯一机制**：优先级高的任务先被 worker 线程取出执行。

### 2.3 `scheduler.py` — 核心调度编排器

**职责**: 整个系统的核心，**编排任务从入队到完成的完整流程**。

#### 2.3.1 任务提交流程

```
submit_task()
  ├── create_task()              # task_manager: 创建任务对象，持久化
  ├── task_queue.put(task)       # queue: 按优先级入队
  ├── enqueue_task()             # redis: 备份到 Redis 队列
  └── return {"task_id", "queued"}
```

#### 2.3.2 后台 Worker 循环

```python
# api.py 启动 MAX_CONCURRENT 个 worker 线程
for i in range(MAX_CONCURRENT):
    t = threading.Thread(target=scheduler_worker, args=(i,), daemon=True)
    t.start()
```

每个 worker 循环：
```
loop:
  if queue.empty(): sleep(0.1)
  else:
    task = queue.get()           # 取最高优先级任务
    dispatch_task(task)          # 执行推理流水线
```

#### 2.3.3 任务分发与流水线执行

`dispatch_task()` → 根据模型类型分发：

**AlexNet 流水线**:
```
part1 (Conv层) ──HTTP──▶ part2 (FC层)
     │                       │
     ▼                       ▼
part1-service:8001    part2-service:8002
```

**Medical 流水线**:
```
worker (边缘编码) ──HTTP──▶ server (云端推理)
     │                          │
     ▼                          ▼
med-worker:8006          med-server:9001
```

### 2.4 `policy.py` — 节点选择策略

**职责**: 根据三层策略选择"最佳"目标节点。

**调用位置**: 在 `scheduler.py` 的 `_run_alexnet_pipeline()` 和 `_run_medical_pipeline()` 中，每个阶段执行前调用一次。

---

## 3. 关键问题：`policy.py` 在固定集群中的实际作用

### 3.1 核心发现：节点选择 ≠ 请求路由

```python
# scheduler.py:125-127 — AlexNet Part1 阶段
part1_node = self.policy.select_target_node(
    "alexnet", "part1", task.get("source", ""),
    nodes, self.resource_monitor
)
if not part1_node:
    part1_node = "node1"

update_task(task_id, {"stage": "part1", "node": part1_node})  # ← 仅记录元数据

# scheduler.py:141-145 — 实际请求始终发往固定 URL
response = requests.post(
    PART1_URL,   # ← 永远是 http://part1-service:8001/infer
    json={"image": image_data},
    timeout=120
)
```

**`select_target_node()` 返回的节点名仅用于 `update_task()` 记录到 Redis 的元数据中**，实际 HTTP 请求始终发往硬编码的 K8s Service URL。原因在于：

| 架构事实 | 说明 |
|---------|------|
| 固定 K8s Service | `part1-service:8001`、`part2-service:8002`、`medical-worker-service:8006`、`medical-server-service:9001` 是 K8s 集群中的固定 DNS 名称 |
| 每个 Service 对应一个 Deployment | K8s 的 Service 本身已做负载均衡，调度器不需要选择具体 Pod |
| 单副本部署 | 当前每个模型阶段只部署了一个 Service（一个 Deployment），没有多个平行实例可选 |

### 3.2 `policy.py` 当前的实际价值

| 功能 | 是否有实际作用 | 说明 |
|------|:---:|------|
| 角色约束过滤 (`allowed_roles`) | **部分** | Medical worker 必须选 edge 节点，若无 edge 节点会 `fail_task`（但当前固定集群肯定有 edge 节点） |
| 医院亲和性 (`hospital == source_hospital`) | **无** | 选出的节点名只写入了元数据，请求仍然发往同一 Service |
| 资源评分排序 | **无** | 同样只影响元数据记录，不改变请求目标 |
| `should_schedule()` 准入控制 | **未使用** | 该方法在整个代码库中没有任何调用方 |

### 3.3 真正起作用的是什么？

**在当前固定集群中，真正决定任务调度的是：**

1. **`PriorityTaskQueue`** — 决定任务执行顺序（高优先级先执行）
2. **`MAX_CONCURRENT` Worker 线程数** — 决定并发度（默认 4）
3. **流水线编排逻辑** — 决定先调哪个服务、后调哪个服务、中间特征如何传递
4. **K8s Service 的负载均衡** — 如果扩容到多 Pod，Service 自动负载均衡

---

## 4. 完整调用链路图

```
用户请求 (POST /schedule/task)
  │
  ▼
api.py: schedule_task()
  │
  ▼
scheduler.py: submit_task()
  ├── task_manager.create_task()          ← 创建任务，写 Redis
  ├── queue.PriorityTaskQueue.put()       ← 按优先级入队
  └── redis_client.enqueue_task()         ← Redis 备份
  │
  ▼  [返回 {"task_id", "queued"} 给用户]
  
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
后台 Worker 线程 (api.py: scheduler_worker)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  │
  ▼
queue.PriorityTaskQueue.get()             ← 取最高优先级任务
  │
  ▼
scheduler.py: dispatch_task(task)
  │
  ├── model == "alexnet" ──► _run_alexnet_pipeline()
  │     ├── policy.select_target_node("alexnet","part1",...)  ← 选节点（元数据）
  │     ├── update_task(stage="part1", node=selected_node)    ← 记录元数据
  │     ├── HTTP POST → PART1_URL (http://part1-service:8001) ← 实际推理
  │     ├── policy.select_target_node("alexnet","part2",...)
  │     ├── update_task(stage="part2", node=selected_node)
  │     ├── HTTP POST → PART2_URL (http://part2-service:8002)
  │     └── complete_task() + 记录 Prometheus 指标
  │
  └── model == "medical" ──► _run_medical_pipeline()
        ├── policy.select_target_node("medical","worker",...)
        ├── update_task(stage="worker", node=...)
        ├── HTTP POST → MEDICAL_WORKER_URL (:8006)
        ├── policy.select_target_node("medical","server",...)
        ├── update_task(stage="server", node=...)
        ├── HTTP POST → MEDICAL_SERVER_URL (:9001)
        └── complete_task() + 记录 Prometheus 指标
```

---

## 5. 代码冗余与死代码分析

### 5.1 `policy.py` 中未被使用的部分

```python
# policy.py:119-126 — 全代码库无调用方
@staticmethod
def should_schedule(task_priority, queue_length, max_concurrent):
    ...
```

`should_schedule()` 是一个有意义的准入控制函数（紧急任务绕过并发限制），但**从未被调用**。当前的并发控制仅由 `MAX_CONCURRENT` worker 线程数隐式实现。

### 5.2 `resource_monitor.py` 中重复的评分逻辑

`policy.py:113-114` 和 `resource_monitor.py:131-134` 各自维护了一份**完全相同**的评分函数：

```python
# 两份完全相同的代码
score = 0.4 * cpu_free + 0.35 * gpu_free + 0.25 * mem_free
```

### 5.3 `predictor.py` — 延迟预测器未被使用

`LatencyPredictor` 在 `scheduler.py:43` 被实例化：
```python
self.predictor = LatencyPredictor()
```

但在整个 `scheduler.py` 的流水线执行过程中**没有任何地方调用 `self.predictor`**。它只在 API 层被间接使用（如果有外部调用的话）。

### 5.4 `resource_monitor` 参数传了但未用

```python
# scheduler.py:125-127
part1_node = self.policy.select_target_node(
    "alexnet", "part1", task.get("source", ""),
    nodes, self.resource_monitor   # ← resource_monitor 在 select_target_node 内部未使用
)
```

`select_target_node()` 方法签名中包含 `resource_monitor` 参数，但方法体内从未引用它——资源数据完全来自 `nodes` 字典参数。

---

## 6. `policy.py` 设计意图 vs 实际效果

### 6.1 设计意图（面向动态集群）

`policy.py` 的三层策略是为**动态、异构的多节点集群**设计的：

```
                     ┌──────────────┐
                     │   Scheduler  │
                     └──────┬───────┘
                            │ 选择最优节点
            ┌───────────────┼───────────────┐
            ▼               ▼               ▼
      ┌──────────┐   ┌──────────┐   ┌──────────┐
      │ Edge A   │   │ Edge B   │   │ Cloud C  │
      │ (医院1)  │   │ (医院2)  │   │ (数据中心)│
      │ worker1  │   │ worker2  │   │ server1  │
      └──────────┘   └──────────┘   │ server2  │
                                    └──────────┘
```

在这种场景下，调度器需要：
- 从多个 worker 实例中选择一个（同医院优先）
- 从多个 server 实例中选择一个（资源最充裕的）
- 根据实时负载动态调整

### 6.2 实际效果（固定 K8s 集群）

当前架构中，每个阶段只有一个 K8s Service：

```
      ┌──────────────┐
      │   Scheduler  │
      └──────┬───────┘
             │
   ┌─────────┼─────────┐
   ▼         ▼         ▼         ▼
part1-svc  part2-svc  med-wkr  med-svr
 (1个)     (1个)      (1个)    (1个)
```

`policy.py` 的节点选择退化为**仅做角色校验和元数据标注**。

---

## 7. 改进建议

### 7.1 短期（清理冗余）

| 行动 | 说明 |
|------|------|
| 合并评分函数 | `policy.py` 和 `resource_monitor.py` 的评分逻辑统一到一处 |
| 删除或实现 `should_schedule()` | 要么在 Worker 循环中集成，要么删除 |
| 集成或删除 `LatencyPredictor` | 如在流水线中不需要延迟预测，移除实例化 |
| 清理 `resource_monitor` 未用参数 | `select_target_node()` 的 `resource_monitor` 形参要么使用，要么移除 |

### 7.2 中期（让 policy.py 真正起作用）

要让 `policy.py` 的节点选择真正影响请求路由，需要：

1. **多实例部署**: 同一模型阶段部署多个 Service（如 `worker-hospital-a:8006`, `worker-hospital-b:8006`）
2. **动态 URL 构造**: 将 `select_target_node()` 的结果用于构造实际请求 URL
   ```python
   # 改造后
   worker_node = self.policy.select_target_node("medical", "worker", source, nodes, ...)
   worker_url = f"http://medical-worker-{worker_node}:8006/infer"
   response = requests.post(worker_url, ...)
   ```
3. **`should_schedule()` 集成**: 在 worker 取任务前检查准入条件

### 7.3 长期（完整调度能力）

- 支持同一阶段的弹性伸缩（根据负载增减 Pod 数量）
- 引入 `predictor.py` 的延迟预测，综合延迟和资源做调度决策
- 支持跨集群调度（多 K8s 集群联邦）

---

## 8. 总结

| 问题 | 答案 |
|------|------|
| `policy.py` 是 Pod 部署调度吗？ | **不是**。它是推理任务的节点选择策略，不涉及 Pod 的创建或部署。 |
| 对固定集群有实际作用吗？ | **极小**。仅做角色校验（确保 Medical worker 选 edge 节点），选出的节点名仅写入元数据，不改变实际请求路由。 |
| 任务调度在 `task_manager.py` 吗？ | **不完全是**。`task_manager.py` 负责任务生命周期（创建/更新/完成/失败）。调度核心在 **`scheduler.py`**（流水线编排 + 服务调用）和 **`queue.py`**（优先级排序）。 |
| 真正决定执行顺序的是什么？ | **`PriorityTaskQueue`** 按优先级排序 + **`MAX_CONCURRENT`** worker 线程控制并发。 |
