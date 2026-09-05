# 调度器多任务崩溃与高失败率根因分析

> 日期: 2026-07-27
> 现象: scheduler Pod 在多任务并发时崩溃重启; 任务失败率高

---

## 致命问题速查

| # | 问题 | 严重度 | 影响 |
|---|------|:------:|------|
| 1 | **uvicorn 单 worker + 同步阻塞 worker 线程** | 🔴 致命 | liveness probe 超时 → Pod 被 kill |
| 2 | **内存 OOM** — 医学影像张量全量驻留在进程内存 | 🔴 致命 | 1Gi 限制下 4-5 个医疗任务即可触发 OOMKilled |
| 3 | **无连接池** — `requests` 每次新建 TCP 连接 | 🟠 高危 | FD 耗尽、连接泄漏 |
| 4 | **Redis 无连接池** — 默认每次新建连接 | 🟠 高危 | 并发下 Redis 连接打满 |
| 5 | **无重试机制** + 下游服务过载 | 🟡 中等 | 并发请求击垮单副本推理服务 |
| 6 | **`get_tasks()` 全量扫描** — 每次 `update_metrics` 都遍历所有 key | 🟡 中等 | Redis 压力 + CPU 尖刺 |
| 7 | **`resource_monitor` 无锁** — 缓存过期时惊群效应 | 🟡 中等 | 同一时刻 12×4=48 次 Prometheus 查询 |

---

## 根因 1: uvicorn 单 Worker + 阻塞线程 — Pod 被 Liveness Probe 杀死

### 问题代码

```dockerfile
# Dockerfile:33 — 只有 1 个 worker 进程！
CMD ["python", "-m", "uvicorn", "scheduler.api:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
```

```yaml
# scheduler-deployment.yaml:76-83
livenessProbe:
  httpGet:
    path: /
    port: 8000
  initialDelaySeconds: 15
  periodSeconds: 15
  timeoutSeconds: 5       # ← 5秒超时
  failureThreshold: 3     # ← 连续 3 次失败就杀 Pod
```

### 崩溃链路

```
多任务并发到达
  → 4 个 worker 线程同时处理推理（最长 300s 阻塞）
  → Python GIL 在 JSON 序列化大张量时阻塞主线程
  → FastAPI 的 asyncio event loop 被阻塞
  → GET / (health) 请求排队，超过 5 秒无响应
  → 连续 3 次超时（共 15 秒）
  → Kubernetes kill Pod → 重启
  → 所有正在执行的任务丢失
```

**关键**: uvicorn 默认 1 个 worker 进程。4 个 scheduler worker 线程 + health check 共享同一个进程。当 worker 线程在做 CPU 密集操作（序列化医学影像 JSON、Prometheus 指标写入等），asyncio event loop 被 GIL 阻塞，health check 无法及时响应。

---

## 根因 2: 内存 OOM — 任务数据全量驻留内存

### 问题代码

```python
# scheduler.py:70 — 任务对象（含完整 input_data）直接放入内存队列
self.task_queue.put(task, priority=priority)
```

```python
# task_manager.py:27-30 — task 对象包含了所有 input 数据
task = {
    ...
    "input": input_data or {},    # ← DCE图像 + DWI图像 + 临床特征 + 影像组学特征
    ...
}
```

### 内存计算

```
调度器内存限制: 1Gi

一个医疗任务输入（来自 test_dataset）:
  dce_image:  ~[N, H, W] 3D tensor → 序列化后 ~5-50 MB
  dwi_image:  ~[N, H, W] 3D tensor → 序列化后 ~5-50 MB
  clinical_features: vector
  radiomics_features: vector

保守估计: 每个医疗任务 ≈ 10-100 MB (取决于分辨率)

场景: 4 个 worker 正在执行 + 队列堆积 10 个任务
  14 × 50 MB = 700 MB (仅任务数据)
  + Python 进程基础开销 ~100 MB
  + Redis 连接 / requests 连接开销
  + 序列化/反序列化中间内存
  ≈ 900 MB+

→ 逼近 1Gi 限制 → OOMKilled
```

**为什么"一遇到多任务就崩溃"**: 单任务时内存充裕，但 2-3 个并发医疗任务即可触发内存压力。

---

## 根因 3: `requests` 无 Session 连接池 — FD 泄漏

### 问题代码

```python
# scheduler.py:149 — 每次 HTTP 调用都新建连接
response = requests.post(PART1_URL, json={"image": image_data}, timeout=120)
```

```python
# resource_monitor.py:35 — 同样的问题
response = requests.get(url, params={"query": query}, timeout=5)
```

### 影响

- 每个 `requests.post/get()` 创建一个新 TCP 连接
- TCP 连接在 CLOSE_WAIT/TIME_WAIT 状态残留
- 高并发下文件描述符耗尽 → `OSError: [Errno 24] Too many open files`
- 所有后续 HTTP 调用（包括 Redis、推理服务、Prometheus）全部失败

**修复**: 应使用 `requests.Session()` 复用连接池

---

## 根因 4: Redis 无连接池

### 问题代码

```python
# redis_client.py:28 — 每次实例化都新建连接
r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
```

### 影响

- 无 `connection_pool` 参数，每次操作可能创建/关闭连接
- 4 个 worker + API handler 并发访问 → Redis 连接数暴涨
- Redis Deployment 限制 `memory: "512Mi"`，连接数过多会拒绝新连接
- `REDIS_AVAILABLE` 被设为 True 后操作失败走 `memory_store` → 任务状态丢失

---

## 根因 5: 无重试 — 下游服务并发过载

### 问题代码

```python
# scheduler.py:148-158 — 一次失败即放弃，无重试
try:
    response = requests.post(PART1_URL, ...)
    response.raise_for_status()
    ...
except Exception as e:
    fail_task(task_id, f"Part1 inference failed: {e}")
    return                    # ← 直接返回，不重试
```

### 失败链

```
4 个 worker 同时发送请求到 medical-worker-service
  → 只有 2 个 replica（每个 4 CPU / 8Gi 内存）
  → PyTorch 模型推理是 CPU/GPU 密集型
  → 第 3、4 个请求排队等待或超时
  → scheduler 300s timeout 但 worker 可能返回 5xx
  → 任务记为 failed

同样的问题在 medical-server（1 个 replica）
  → 4 个并发请求打到一个 Pod
  → 直接过载
```

---

## 根因 6: `get_tasks()` 全量扫描 Redis

### 问题代码

```python
# redis_client.py:133-143 — 每次全量扫描 + 反序列化
def get_tasks(limit=50):
    keys = redis_keys("inference:*")       # ← KEYS * 全量扫描
    result = []
    for key in keys:
        if key.startswith("inference:result:"):
            continue
        data = redis_get(key)              # ← 逐个 GET
        if data:
            result.append(json.loads(data)) # ← 逐条反序列化
    ...
```

```python
# api.py:166-167 — 每次任务完成后都调用
def update_metrics():
    ...
    tasks = get_tasks()   # ← 全量扫描 Redis！
```

### 影响

- 每完成一个任务，`update_metrics()` 全量扫描 Redis 所有 key
- Redis 中积累大量历史任务时，这个操作越来越慢
- `KEYS *` 在 Redis 中会阻塞其他操作
- 加重 GIL 阻塞问题（根因 1）

---

## 根因 7: `resource_monitor` 缓存无锁 — 惊群

### 问题代码

```python
# resource_monitor.py:63-94 — 缓存读写无锁
def get_all_nodes(self):
    current_time = time.time()
    if current_time - self.cache_time < self.cache_ttl and self.cache:
        return self.cache              # ← 读缓存无锁

    nodes_status = {}
    for node_name, role_info in NODE_ROLES.items():
        cpu = self.get_node_cpu_usage(node_name)     # HTTP 请求
        memory = self.get_node_memory_usage(node_name)
        gpu = self.get_node_gpu_usage(node_name)

    self.cache = nodes_status          # ← 写缓存无锁
    self.cache_time = current_time
    return nodes_status
```

### 惊群效应

```
T=0s 缓存过期
T=0.01s Worker 1,2,3,4 同时调用 get_all_nodes()
  → 4 个线程都发现缓存过期
  → 同时开始查询 Prometheus
  → 4 × 4 nodes × 3 metrics = 48 个并发的 HTTP GET 到 Prometheus
  → Prometheus 可能超时或返回错误
  → 每个线程串行执行（timeout=5s 每个请求）
  → 最坏情况：某个 worker 线程被阻塞 48 × 5 = 240 秒
```

---

## 综合崩溃时序图

```
T+0s    用户提交 5 个医疗推理任务
T+1s    4 个 worker 各取一个任务开始执行
        ├─ 第 5 个任务在队列等待
        ├─ get_all_nodes() 缓存命中 (T+0 已刷新)
        ├─ select_target_node() 选节点
        └─ HTTP POST → medical-worker-service (timeout=300s)

T+5s    get_all_nodes() 缓存过期
        ├─ 4 个 worker 同时查询 Prometheus（惊群）
        ├─ 每次调用 12 个 HTTP GET
        └─ Prometheus 压力大，部分超时

T+10s   Worker 1,2 的任务收到 medical-worker 响应
        ├─ serialize 医学特征 → HTTP POST → medical-server (timeout=300s)
        └─ Worker 3,4 仍在等 medical-worker 响应

T+12s   Worker 1 dispatch_task 完成
        ├─ update_metrics() → get_tasks() 全量扫描 Redis KEYS *
        ├─ 序列化 JSON result → Redis SET
        └─ Worker 1 取下一个任务

T+15s   [关键时刻]
        ├─ 内存: 4 个活跃任务 × 医学影像数据 + 队列积压 ≈ 700MB+
        ├─ 内存压力导致 GC 频繁
        ├─ GIL 被 Worker 线程长时间持有 (JSON 序列化)
        ├─ asyncio event loop 阻塞
        ├─ Liveness probe GET / → 超时 (5s)
        └─ 第 1 次 probe 失败

T+30s   连续 3 次 liveness probe 全部超时
        └─ Kubernetes: liveness probe failed → KILL Pod

T+31s   Pod 重启
        ├─ 所有 in-flight 任务丢失
        ├─ Redis 中有部分状态不一致
        └─ 客户端看到大量 failed 任务
```

---

## 修复方案

### 紧急修复（立即见效）

| # | 修复 | 改动 |
|---|------|------|
| 1 | **uvicorn 多 worker** | `--workers 2` → 主进程不阻塞时 health check 正常响应 |
| 2 | **增大 scheduler 内存 limit** | `memory: 1Gi → 4Gi` → 缓冲医疗任务数据 |
| 3 | **`requests.Session` 连接池** | 单例 Session，复用 TCP 连接 |

```dockerfile
# Dockerfile 修复
CMD ["python", "-m", "uvicorn", "scheduler.api:app", \
     "--host", "0.0.0.0", "--port", "8000", \
     "--workers", "2", "--no-access-log"]
```

```yaml
# scheduler-deployment.yaml 修复
resources:
  requests:
    cpu: "500m"
    memory: "1Gi"
  limits:
    cpu: "2"
    memory: "4Gi"     # 1Gi → 4Gi
```

### 中期修复（根本解决）

| # | 修复 | 改动文件 |
|---|------|---------|
| 4 | **任务数据不存内存队列** — 只存 task_id，worker 从 Redis 取 input | `scheduler.py`, `task_manager.py` |
| 5 | **重试机制** — HTTP 调用加 `urllib3.Retry`（3 次指数退避） | `scheduler.py` |
| 6 | **`get_tasks()` 用 SCAN 替代 KEYS \*** | `redis_client.py` |
| 7 | **`resource_monitor` 缓存加锁** + 过期前刷新 | `resource_monitor.py` |
| 8 | **调大 liveness probe timeout** — 应对 GC 暂停 | `scheduler-deployment.yaml` |

```python
# 重试示例 — scheduler.py
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

_session = None
def _get_session():
    global _session
    if _session is None:
        _session = requests.Session()
        retries = Retry(total=3, backoff_factor=1.0,
                        status_forcelist=[500, 502, 503, 504])
        _session.mount("http://", HTTPAdapter(max_retries=retries, pool_connections=10, pool_maxsize=20))
    return _session
```

### 架构改进（长期）

| # | 方向 | 说明 |
|---|------|------|
| 9 | **推理服务扩容** | medical-server 至少 2 replica（当前仅 1） |
| 10 | **异步调度** | 用 `aiohttp` 替代同步 `requests`，避免 worker 线程阻塞 event loop |
| 11 | **任务持久化优先** | 任务提交时先落 Redis，worker 只持有 task_id，需要时再加载 input |
| 12 | **优雅关闭** | Pod 收到 SIGTERM 时等待 in-flight 任务完成或超时再退出 |

---

## 总结

**崩溃的直接原因**是内存 OOM + liveness probe 超时的组合拳：
- 医学影像数据使每个任务占用 10-100 MB 内存
- uvicorn 单 worker + 同步阻塞线程使 health check 在高负载时超时
- 连续 3 次 probe 失败导致 Kubernetes 杀 Pod

**失败率高的原因**:
- 下游服务（medical-worker 2 副本、medical-server 1 副本）无法承受 4 个并发请求
- HTTP 调用无重试机制，一次失败就丢弃任务
- `requests` 无连接池，并发下连接数耗尽
- Pod 被 kill 导致所有 in-flight 任务丢失
