# 测试说明文档

## 前置条件

K8s 测试（e2e / submit / query / stress）需要 **模型权重已部署到各节点**，否则 medical 任务返回 503。

```bash
# 一次性部署权重
bash deploy_weights.sh

# 验证
kubectl exec deployment/medical-worker -- python3 -c "
import urllib.request,json
print(json.loads(urllib.request.urlopen('http://localhost:8006/health').read())['model_loaded'])
"  # 应输出 True
```

---

## 测试脚本概览

| 脚本 | 用途 | 运行环境 |
|---|---|---|
| `local_integration_test.py` | 本地集成测试，启动所有服务并端到端验证 | 本地 Python，无需 Docker/K8s/Redis |
| `e2e_test.py` | **全链路端到端测试**，submit → wait → validate 一体化 | 需要 K8s 集群已部署 |
| `dispatch_test.py` | **调度机制验证测试**，覆盖 `TASK_DISPATCH_ANALYSIS.md` 中的优先级队列、状态机、流水线分发、节点选择、并发控制等 | Layer A 无需服务器；Layer B 需要 K8s 集群 |
| `submit_task.py` | 单任务提交，验证调度器任务入口 | 需要 K8s 集群已部署 |
| `query_result.py` | 结果轮询，验证任务状态追踪 | 需要 K8s 集群已部署 |
| `stress_test.py` | 并发压力测试，验证优先级调度和延迟分布 | 需要 K8s 集群已部署 |

### 测试数据说明

Medical 和 AlexNet 任务都需要匹配下游服务的 schema 才能通过 422 校验：

| 模型 | 输入字段 | 形状 |
|---|---|---|
| medical | `dce_image`, `dwi_image` | [1, 224, 224] float |
| medical | `clinical` | [1, 23] float |
| medical | `radiomics` | [1, 2264] float |
| medical | `patient_ids` | ["..."] |
| alexnet | `image` | [3, 224, 224] float |

所有脚本默认生成合法的 dummy 数据（全 0.5），也可通过 `--input` 指定 JSON 文件。

---

## 1. e2e_test.py — 全链路端到端测试（推荐）

### 目的

**一键验证完整推理链路**：提交任务 → 轮询等待 → 校验结果。submit_task + query_result 的合体，适合冒烟测试和快速回归。

### 步骤

```bash
# 单个 Medical 任务（默认）
python test/e2e_test.py

# 单个 AlexNet 任务
python test/e2e_test.py --model alexnet

# 高优先级 + 长超时
python test/e2e_test.py --model medical --priority 10 --timeout 120

# 批量：依次提交 5 个任务并汇总
python test/e2e_test.py --count 5 --model alexnet

# 指定调度器地址
python test/e2e_test.py --url http://192.168.1.100:30080
```

### 四步流程

```
[1/4] 提交任务      → POST /schedule/task
[2/4] 等待结果      → 每秒轮询 GET /task/result/{id}，显示状态变化
[3/4] 校验结果      → 检查字段完整性、延迟拆解、预测输出
[4/4] 判定通过/失败  → PASS 或打印具体错误原因
```

### 预期输出

```
============================================================
E2E TEST: medical
============================================================
  Model:    medical
  Hospital: hospital-a
  Priority: 9

[1/4] Submitting task ... ok (task_id=202607240001, status=queued)
[2/4] Waiting for result ... running running running finished
[3/4] Validating result ...
  Worker latency:  450.23ms
  Server latency:  784.33ms
  Prediction:      pCR (prob=0.7300)
  Total:           1234.56ms

[4/4] Result: PASS
  Duration: 15234ms

✓ End-to-end test passed.
```

批量模式末尾打印汇总：

```
BATCH SUMMARY
============================================================
  Total:    5
  Passed:   5
  Failed:   0
  Avg time: 14850ms
```

---

## 2. local_integration_test.py — 本地集成测试

### 目的

在部署到 K8s 之前，本地验证所有微服务能正确启动、通信、处理任务。

### 步骤

```bash
cd PengchengMedicalModel-DistributedInference

# 完整测试
python test/local_integration_test.py

# 跳过需要 PyTorch 的服务（仅测试调度器、预测、监控）
python test/local_integration_test.py --skip-heavy

# 详细输出
python test/local_integration_test.py --verbose --timeout 60
```

### 测试内容（按阶段）

| 阶段 | 测试项 | 验证点 |
|---|---|---|
| Phase 0 | 环境检查 | fastapi, uvicorn, torch, sklearn, redis 等依赖是否安装 |
| Phase 1 | 服务启动 | scheduler(8000), part1(8001), part2(8002), prediction(8003), monitoring(8005), medical-worker(8006), medical-server(9001) |
| Phase 2 | 健康检查 | 每个服务的 health endpoint 返回 200 |
| Phase 3 | 调度器 API | POST /schedule/task（medical + alexnet）、GET /task/result/{id}、GET /tasks、GET /tasks/stats、GET /nodes、POST /predict/image（legacy） |
| Phase 4 | 跨服务通信 | 各 worker/service 的 health 端点可达 |

### 预期结果

```
✓ All tests passed! Code is ready for K8s deployment.
```

缺少 PyTorch 时 PyTorch 相关服务会被跳过（warning），不影响总体通过。

---

## 3. dispatch_test.py — 调度机制验证测试

### 目的

验证 `scheduler/TASK_DISPATCH_ANALYSIS.md` 中分析的调度与分发机制是否正确工作。与 `e2e_test.py` 关注推理结果不同，本测试关注**调度基础设施本身**：

| 测试 | 对应分析章节 | 验证内容 |
|---|---|---|
| A1 优先级队列排序 | §2.2, §3.3 | `PriorityTaskQueue` 按优先级降序出队 |
| A2 同优先级 FIFO | §2.2 | `_counter` 保证同优先级任务先进先出 |
| A3 状态机阶段 | §2.1 | `scheduler → preprocess → worker/part1 → server/part2 → finished` |
| A3b 资源默认值 | §2.1 | `get_default_resources()` 按模型返回正确配置 |
| A4 角色约束 | §2.4, §3.2 | Medical worker 仅允许 edge、server 仅允许 cloud |
| A5 医院亲和性 | §2.4, §3.2 | Medical worker 优先选择同医院 edge 节点 |
| A6 评分一致性 | §5.2 | `policy.py` 和 `resource_monitor.py` 使用相同评分公式 |
| B1 优先级调度 API | §2.2, §3.3 | 高优先级任务先被调度 |
| B2 状态转换 API | §2.1 | 任务 stage 字段按状态机推进 |
| B3 Medical 流水线 | §2.3.3, §4 | worker → server 两阶段流水线 |
| B4 AlexNet 流水线 | §2.3.3, §4 | part1 → part2 两阶段流水线 |
| B5 并发控制 | §3.3 | `MAX_CONCURRENT` 限制活跃任务数 |
| B6 节点角色 | §2.4, §3.2, §6 | `/nodes` 端点返回正确的 edge/cloud 角色和评分 |
| B7 任务元数据 | §3.1 | stage、node、progress 等元数据正确记录 |
| B8 冗余审计 | §5 | 验证分析文档记录的代码冗余现状 |

### 步骤

```bash
cd PengchengMedicalModel-DistributedInference

# 仅运行单元测试（无需服务器、Redis、K8s）
python test/dispatch_test.py --unit

# 仅运行集成测试（需要调度器运行中）
python test/dispatch_test.py --integration --url http://localhost:30080

# 运行全部测试
python test/dispatch_test.py --all --url http://localhost:30080

# 详细输出
python test/dispatch_test.py --all --url http://localhost:30080 --verbose
```

### 测试架构

测试分为两层：

**Layer A — 单元测试（本地模块）**：直接导入 `scheduler.queue`、`scheduler.policy`、`scheduler.task_manager`、`scheduler.resource_monitor` 模块，验证核心逻辑无需外部依赖。

**Layer B — 集成测试（API）**：通过调度器 REST API 验证端到端调度行为，包括优先级排序、状态转换、流水线路由、并发控制和元数据记录。

### 预期输出

```
============================================================
DISPATCH MECHANISM TEST
============================================================
  Mode:    Layer A: Unit Tests
  Server:  http://localhost:30080
  Tests:   8

--- Layer A: Unit Tests (local modules) ---
  [A1_queue_ordering] PriorityTaskQueue ordering (§2.2) ... PASS
  [A2_queue_fifo] PriorityTaskQueue FIFO tie-break (§2.2) ... PASS
  [A3_state_machine] Task state machine stages (§2.1) ... PASS
  [A3b_resources] Task resource defaults (§2.1) ... PASS
  [A4_role_constraints] Policy role constraints (§2.4, §3.2) ... PASS
  [A5_hospital_affinity] Policy hospital affinity (§2.4, §3.2) ... PASS
  [A6_scoring_consistency] Resource scoring consistency (§5.2) ... PASS
  [B8_redundancy_audit] Redundancy audit (§5) ... PASS

============================================================
Results: 8/8 passed
============================================================
```

### 与 e2e_test.py 的关系

| 维度 | `e2e_test.py` | `dispatch_test.py` |
|---|---|---|
| 关注点 | 推理结果正确性 | 调度机制正确性 |
| 验证内容 | 预测输出、延迟指标、字段完整性 | 队列排序、状态机、流水线路由、节点选择 |
| 依赖 | 需要所有推理服务在线 | Layer A 零依赖；Layer B 只需调度器 |
| 典型用途 | 部署后冒烟测试 | 调度逻辑回归测试、代码审查验证 |

---

## 4. submit_task.py — 单任务提交测试

### 目的

验证调度器接受任务请求，正确返回 task_id，支持不同医院、模型、优先级组合。

### 步骤

```bash
# 提交高优先级医疗任务
python test/submit_task.py --hospital hospital-a --model medical --priority 9

# 提交低优先级 AlexNet 任务
python test/submit_task.py --hospital hospital-b --model alexnet --priority 5

# 指定调度器地址
python test/submit_task.py --url http://192.168.1.100:30080 --model medical --priority 10
```

### 预期结果

```
Submitting task to http://localhost:30080/schedule/task
  Hospital:  hospital-a
  Model:     medical
  Priority:  9

Task submitted successfully!
  Task ID:   202607240001
  Status:    scheduled
```

---

## 5. query_result.py — 结果查询测试

### 目的

验证通过 task_id 查询任务状态和结果，支持轮询直到完成。

### 步骤

```bash
# 单次查询
python test/query_result.py <task_id>

# 轮询直到完成（超时 120s）
python test/query_result.py <task_id> --wait

# 指定超时
python test/query_result.py <task_id> --wait --timeout 300
```

### 预期结果

- **running**: 打印 `Progress: N% | Stage: ... | Node: ...`
- **finished**: 打印完整结果 JSON，含模型预测值和延迟指标
- **failed**: 打印错误信息
- **not_found**: 提示任务不存在

---

## 6. stress_test.py — 并发压力测试

### 目的

模拟高并发场景，验证优先级调度有效性、混合负载处理能力和延迟分布。

### 步骤

```bash
# 默认：100 个 AlexNet + 100 个 Medical，10 并发
python test/stress_test.py

# 轻量冒烟
python test/stress_test.py --alexnet 10 --medical 10 --concurrent 5

# 重载测试
python test/stress_test.py --alexnet 500 --medical 500 --concurrent 50
```

### 测试设计

- **任务优先级分布**: Medical 10% 紧急(p=10)、30% 紧急(p=9)、60% 常规(p=5)；AlexNet 80% 低优(p=1)、20% 常规(p=5)
- **医院分布**: hospital-a 和 hospital-b 交替，各 50%

### 三阶段流程

1. **Phase 1 — 提交**: ThreadPoolExecutor 并发提交所有任务
2. **Phase 2 — 收集**: 并发轮询每个 task_id 直到完成或超时(120s)
3. **Phase 3 — 分析**: 输出每个模型的延迟统计（Min/Max/Avg）

### 验证项

1. 高优先级任务先于低优先级完成
2. Medical 和 AlexNet 任务都能正确处理
3. 失败率接近 0
4. 延迟分布符合模型复杂度预期

---

## 推荐测试流程

```bash
cd PengchengMedicalModel-DistributedInference

# 1. 代码审查后：调度机制单元测试（零依赖，秒级完成）
python test/dispatch_test.py --unit

# 2. 部署前：本地集成测试（不依赖 K8s）
python test/local_integration_test.py

# 3. 部署后：调度机制集成测试（验证调度器 API）
python test/dispatch_test.py --integration --url http://<scheduler-ip>:30080

# 4. 部署后：端到端冒烟（Medical + AlexNet 各一次）
python test/e2e_test.py --model medical
python test/e2e_test.py --model alexnet

# 5. 批量验证
python test/e2e_test.py --count 5 --model medical

# 6. 压力测试
python test/stress_test.py --alexnet 50 --medical 50 --concurrent 10
```
