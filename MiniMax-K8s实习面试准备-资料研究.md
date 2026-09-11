# MiniMax（稀宇科技）K8s 集群管理实习生 · 面试准备报告

**标注约定**：✅ = 有来源（附链接，内容已实际抓取核实）；⚠️ = 通用知识/经验法则，无特定来源。
**检索诚实性声明**：① 未找到任何 MiniMax「K8s 集群管理实习生」岗位的面经，也未能抓取该 JD 正文（官方招聘页为 JS 渲染）；② 凡引用均来自实际抓取页面，无捏造引语；③ 牛客/知乎正文多为 JS 空壳，部分经 r.jina.ai 提取成功；④ neituiya.com 本次不可达；⑤ 报告内全部链接已实测——除 [LeetCode DevOps 编程题帖](https://leetcode.com/discuss/post/6321735/coding-related-questions-commonly-asked-2irkg/)（对 curl 返回 403 反爬，正文已通过 r.jina.ai 成功提取核实，浏览器可正常打开）外，其余 41 个链接均返回 HTTP 200。已剔除检索中出现但**无法访问**的 SEO 站点 datasea.cn。

---

## 1. MiniMax 面试流程与风格

| # | 要点 | 来源 |
|---|---|---|
| 1 | **MiniMax AI Infra 实习面试明确三段式：项目经历提问 → 理论问答 → 代码题。** | [AIInfraGuide 实习二面](https://caomaolufei.github.io/AIInfraGuide/interview/minimax-ai-infra-%E5%AE%9E%E4%B9%A0-%E4%BA%8C%E9%9D%A2/) |
| 2 | 项目环节原文要求：「着重描述你遇到的**最大技术难题**以及取得的**优化成果**」→ 必须准备"问题-方案-量化收益"结构。 | [同上](https://caomaolufei.github.io/AIInfraGuide/interview/minimax-ai-infra-%E5%AE%9E%E4%B9%A0-%E4%BA%8C%E9%9D%A2/) |
| 3 | 另一份实习一面同样为「项目深入探讨 → 理论基础 → 代码题」。 | [AIInfraGuide 实习一面(2)](https://caomaolufei.github.io/AIInfraGuide/interview/minimax-ai-infra-%E5%AE%9E%E4%B9%A0-%E4%B8%80%E9%9D%A2-2/) |
| 4 | **MiniMax 确实考手撕算法**，且两份独立面经记录的代码题都是**滑动窗口最大值**（单调队列）。 | [二面](https://caomaolufei.github.io/AIInfraGuide/interview/minimax-ai-infra-%E5%AE%9E%E4%B9%A0-%E4%BA%8C%E9%9D%A2/)、[一面(2)](https://caomaolufei.github.io/AIInfraGuide/interview/minimax-ai-infra-%E5%AE%9E%E4%B9%A0-%E4%B8%80%E9%9D%A2-2/) |
| 5 | 轮次可达 3–4 轮（技术面 + HR），有"前端一二 hr 四面"记录。AI Infra 面经库中 MiniMax 收录 4 篇（一面/实习一面×2/实习二面）。 | [牛客](https://www.nowcoder.com/feed/main/detail/ae9ac0215c25424097f75003a27d01b5)、[AIInfraGuide 目录](https://caomaolufei.github.io/AIInfraGuide/) |
| 6 | MiniMax 服务端/后端线独立存在真实面经：服务端一面凉经、后端开发面筋、Agent 服务端工程师一面。 | [凉经](https://www.nowcoder.com/feed/main/detail/6446c8e3670845ee91cf6560bed0a369)、[后端面筋](https://www.nowcoder.com/discuss/752169418024554496)、[Agent 一面](https://www.nowcoder.com/feed/main/detail/80faa111ac144faa847ae711f1039696) |
| 7 | ⚠️ 未找到任何来源说明 MiniMax 用**哪个平台/ACM 还是核心代码模式**，也未找到语言强制的证据。 | — |

### 最佳可得代理样本（**阿里云云原生实习，非 MiniMax**）

⚠️ 这是检索到的、与"云原生/K8s 实习"最贴近的真实三轮面经，可作为题型风格参照：

- **一面**：八股为主 + 1 道算法（**两数之和**）；含 MySQL 执行计划与索引选择、Spring 生命周期/AOP、epoll、GC 调优；排障工具面到 `uptime/top/pidstat/netstat/vmstat/dstat/sar -n DEV` 及软硬中断、iowait 排查思路。✅ [来源](https://www.nowcoder.com/discuss/353157519931547648)
- **二面（电话面）**：K8s 原理深挖——**Pod 调度到节点的原理**（nodeAffinity、污点、资源 request）、有状态服务与 volumeClaim 挂载、cgroup+namespace 隔离、TCP/UDP；算法为**回溯：矩阵中搜索目标字符串返回 bool**。✅ [同上](https://www.nowcoder.com/discuss/353157519931547648)
- **三面（视频面）**：项目深挖（网关选型、认证流程、秒杀与 Redis/MySQL 一致性）；追问源码级问题 **"kubectl 更新 Pod 配置时 Kubernetes 内部发生了什么"**。✅ [同上](https://www.nowcoder.com/discuss/353157519931547648)

**推断（非事实）**：⚠️ 云原生实习技术面 ≈ 项目深挖 + K8s 原理八股 + 1 道 Medium 算法题，算法题常与岗位无关（两数之和/回溯/滑动窗口）。**建议同时准备"kubectl apply 后的控制面链路"**——这类源码级追问是云原生岗的区分点。

### 常考算法题型小结
✅ 已确认出现：**滑动窗口最大值（单调队列）**、两数之和、矩阵回溯搜索。
⚠️ 通用高频补充：双指针/数组字符串、哈希计数、LRU/LFU、TopK 堆、二叉树遍历、DP（LCS）、并查集、拓扑排序。

---

## 2. 手撕代码（live coding）准备

### 2.1 题目清单（✅ = 有来源，⚠️ = 通用）

| # | 题目 | 难度 | 考察点 | 来源 |
|---|---|---|---|---|
| 1 | **滑动窗口最大值** | Medium | 单调队列/双端队列，O(n) | ✅ [MiniMax 面经](https://caomaolufei.github.io/AIInfraGuide/interview/minimax-ai-infra-%E5%AE%9E%E4%B9%A0-%E4%BA%8C%E9%9D%A2/) |
| 2 | 两数之和 | Easy | 哈希表 | ✅ [阿里云原生面经](https://www.nowcoder.com/discuss/353157519931547648) |
| 3 | 矩阵中搜索目标字符串（回溯） | Medium | DFS+回溯+剪枝 | ✅ [同上](https://www.nowcoder.com/discuss/353157519931547648) |
| 4 | LRU 缓存 | Medium | 哈希+双向链表 | ✅ [2026云计算面试指南](https://www.cnblogs.com/wgwyanfs/p/19963600) |
| 5 | 二叉树遍历（层序/中序迭代） | Easy–Med | 队列/显式栈 | ✅ [同上](https://www.cnblogs.com/wgwyanfs/p/19963600) |
| 6 | 最长公共子序列（DP） | Medium | 二维 DP+滚动数组 | ✅ [同上](https://www.cnblogs.com/wgwyanfs/p/19963600) |
| 7 | TopK（数组/海量日志 TOP N IP） | Medium | 小顶堆 O(n log k)；海量→分治+哈希 | ✅（堆用于TopK）[同上](https://www.cnblogs.com/wgwyanfs/p/19963600) |
| 8 | **手写限流器**（计数器/滑动窗口/漏桶/令牌桶） | Medium | 时间戳+原子计数/锁；并发安全 | ✅ [Go常见限流算法代码](https://www.cnblogs.com/beatle-go/p/17926772.html) |
| 9 | 原子计数器 | Medium | CAS/原子操作，无锁 | ⚠️ |
| 10 | Bash：检查服务是否运行 | Easy | `systemctl is-active`、退出码 | ✅ [LeetCode DevOps 编程题](https://leetcode.com/discuss/post/6321735/coding-related-questions-commonly-asked-2irkg/) |
| 11 | Bash：日志文件轮转 | Easy | 移动+gzip 压缩、日期命名 | ✅ [同上](https://leetcode.com/discuss/post/6321735/coding-related-questions-commonly-asked-2irkg/) |
| 12 | Python：监控 CPU 使用率 | Easy | `psutil`、采样间隔 | ✅ [同上](https://leetcode.com/discuss/post/6321735/coding-related-questions-commonly-asked-2irkg/) |
| 13 | Python：解析日志提取 ERROR 行 | Easy | 流式读取、正则、内存友好 | ✅ [同上](https://leetcode.com/discuss/post/6321735/coding-related-questions-commonly-asked-2irkg/) |
| 14 | 写 Dockerfile + 清理命令 | Easy | 多阶段构建、`docker system prune -a` | ✅ [同上](https://leetcode.com/discuss/post/6321735/coding-related-questions-commonly-asked-2irkg/) |
| 15 | 写 K8s YAML 部署 nginx + 扩缩容 | Easy | Deployment/Service、`kubectl scale` | ✅ [同上](https://leetcode.com/discuss/post/6321735/coding-related-questions-commonly-asked-2irkg/) |
| 16 | LFU 缓存 | Hard | 频率桶+双向链表 O(1) | ⚠️ |
| 17 | 生产者-消费者 / worker pool | Medium | channel+buffer、WaitGroup、优雅退出 | ⚠️ |
| 18 | 并发安全 map / 单例 | Medium | RWMutex、`sync.Map`、`sync.Once` | ⚠️ |
| 19 | 任务依赖 DAG：拓扑排序 + 并发执行 | Med–Hard | 入度表、并发调度、环检测 | ⚠️ |
| 20 | IP/CIDR 处理：判断归属、IP 排序 | Medium | 32位整数化、前缀匹配、位运算 | ⚠️ |
| 21 | 简单调度器：按资源打分选节点 | Medium | 过滤+打分两阶段、多目标权衡 | ⚠️ |

### 2.2 常用模板（⚠️ 通用）
- **Go 并发**：`sync.WaitGroup` 管生命周期；`chan struct{}` 做信号；`context.WithTimeout` 做取消与级联退出；worker pool = N 个 goroutine 抢同一 channel；限流用 `time.Ticker` + `select`。
- **Go 数据结构**：`container/list` 写 LRU 双向链表；`container/heap` 写 TopK；`sync.RWMutex` 保护 map。
- **Python**：`heapq` 写 TopK/合并；`collections.deque` 写单调队列（滑动窗口最大值）；`defaultdict(Counter)` 计数；`threading.Lock`/`asyncio.Semaphore` 做并发控制；`concurrent.futures.ThreadPoolExecutor` 做线程池。

### 2.3 面试官关注的工程细节
✅ 来源提到的高频手撕方向本身即信号：**LRU/遍历/DP**、**Go 的 GMP/Channel/sync 包**、**Operator 开发与可观测性**（[2026云计算面试指南](https://www.cnblogs.com/wgwyanfs/p/19963600)）。
⚠️ 通用经验：先**确认边界与输入规模**→ 说清**复杂度**→ 再写代码；并发题必须主动说**数据竞争、死锁、goroutine 泄漏、优雅退出**；脚本题要说**幂等、超时、错误处理、不吞异常**。

---

## 3. K8s 集群管理实习面试题库（47 题）

> 标 **✅题目** 者为有来源的真实面试问法（阿里云/Prometheus/Etcd/监控类高难题的**答题要点**为⚠️通用知识）。

### A. Pod 生命周期与探针
| # | 题目 | 答题要点 |
|---|---|---|
| 1 | ✅ k8s 为什么不直接跑容器，而引入 Pod？ | Pod 是最小调度单元，多容器共享 Network/UTS/IPC 命名空间（USR/MNT/PID 隔离），便于 sidecar 协作与同生命周期绑定。 |
| 2 | ✅ 三类探针如何选？ | liveness 失败→按 restartPolicy 重启容器；readiness 失败→从 Endpoints 摘除流量；startup 保护慢启动应用，成功前禁用另两者。 |
| 3 | ✅ 为什么 Pod 要"体面终止"？默认宽限期？ | 默认 30s（`terminationGracePeriodSeconds`）；先执行 preStop，再 SIGTERM，超期 SIGKILL。直接拆桥会丢在途请求。 |
| 4 | ✅ preStop 时间超过宽限期会怎样？ | 宽限期到点 kubelet 强制关闭（SIGKILL），preStop 未完成即被中断，属预期行为，需把 preStop 时间设计在宽限期内。 |
| 5 | ✅ 静态 Pod 是什么？有哪些内置？ | 由 kubelet 直接管理、仅存在于本节点、API 不可管理且无健康检查；可"看见"是因为 kubelet 为其在 API 建镜像 Pod（mirror pod）。典型：kube-apiserver、etcd、kube-scheduler、kube-controller-manager。 |
| 6 | ✅ Init 容器与普通容器差异？多 Init 失败怎么办？ | 顺序串行执行、必须成功退出才启动下一个；不支持探针与 lifecycle；失败则按 restartPolicy 重试，Pod 卡在 Init。常用于等待依赖、改权限。 |

### B. 工作负载：Deployment / StatefulSet / 滚动更新
| # | 题目 | 答题要点 |
|---|---|---|
| 7 | ✅ Deployment 滚动更新的 RS/Pod 变化过程？ | 新建 RS → 按 `maxSurge`/`maxUnavailable` 交替扩容新 RS、缩容旧 RS；`kubectl rollout status` 看进度；旧 RS 保留供回滚。 |
| 8 | ✅ 更新时是否关闭所有 Pod？默认比例？ | 不会。默认 `maxUnavailable=25%`、`maxSurge=25%`，保证可用性。 |
| 9 | ✅ 上线停滞如何判定与排查？ | `kubectl rollout status` 超时；常见原因：镜像拉取失败、配额（ResourceQuota）不足、探针失败、PVC 未绑定、调度失败。 |
| 10 | ✅ StatefulSet 与 Deployment 本质区别？ | StatefulSet 提供稳定网络标识（`<name>-<ordinal>`+Headless Service DNS）、稳定存储（volumeClaimTemplates）、有序启停与滚动（默认逆序）；Deployment 管无状态副本。 |
| 11 | ✅ 为什么 StatefulSet 需要 volumeClaimTemplates？ | 每个 Pod 需独占且身份绑定的 PVC；缩容不删 PVC，扩回时复用同序号存储，避免数据错配。 |
| 12 | ✅ 分区滚动更新（partition）用途？ | 只更新序号 ≥ partition 的 Pod，其余保持旧版本，用于金丝雀/灰度分批升级。 |
| 13 | Pod 反亲和怎么用？ | `podAntiAffinity` 的 `requiredDuringSchedulingIgnoredDuringExecution` 按 `topologyKey: kubernetes.io/hostname` 强制打散；软策略用 `preferred`。注意与 `topologySpreadConstraints` 的取舍（后者更适合均匀分布+`whenUnsatisfiable`）。 |

### C. Service / Ingress / kube-proxy
| # | 题目 | 答题要点 |
|---|---|---|
| 14 | Service 四种类型与场景？ | ClusterIP（集群内，默认）、NodePort（节点 IP+静态端口，测试/临时）、LoadBalancer（对接云 LB，生产对外）、ExternalName（DNS CNAME 指向外部）。✅[来源](https://cloud.tencent.cn/developer/article/2503899) |
| 15 | ✅ kube-proxy iptables 与 IPVS 差异？ | 都基于 Netfilter。iptables 为防火墙设计，规则链线性匹配，规模大时性能差；IPVS 为高性能 L4 LB 设计，用 Hash 表，配合 ipset 减少规则数，支持最少连接/加权等算法与健康检查。✅[来源](https://developer.aliyun.com/article/1321832) |
| 16 | Service 不通怎么定位？ | 先 `kubectl get endpoints` 看是否为空（selector 不匹配）；再 `port-forward` 验证 Pod 本身；再查 kube-proxy/CNI；核对 targetPort 与 containerPort。✅[来源](https://dbaplus.cn/news-134-7164-1.html) |
| 17 | Ingress 与 Ingress Controller？ | Ingress 只是 HTTP/HTTPS 路由规则（host/path→Service），必须部署 Controller（如 ingress-nginx）才生效；无 Controller 则规则完全无效。✅[来源](https://dbaplus.cn/news-134-7164-1.html) |

### D. 网络与 CNI
| # | 题目 | 答题要点 |
|---|---|---|
| 18 | CNI 解决什么问题？ | 为 Pod 分配 IP、配置 veth/bridge/路由，实现跨节点 Pod 互通；K8s 只定义接口规范，实现有 Flannel/Calico/Cilium。 |
| 19 | Flannel vs Calico？ | Flannel 走 Overlay（VXLAN/IPIP），部署简单、性能有封装开销、无网络策略；Calico 基于 L3 BGP 路由转发，性能好且**支持 NetworkPolicy**（Pod 级 ACL）。✅[来源](https://cloud.tencent.cn/developer/article/2499890) |
| 20 | VXLAN 原理与开销？ | 把二层帧封装进 UDP（默认 4789），跨三层网络打通大二层；MTU 需下调（通常 1450），有封装/解封装 CPU 成本。✅[来源](https://cloud.tencent.cn/developer/article/2499890) |
| 21 | 同节点 Pod 间通信路径？ | 同一网桥上 veth pair 直连二层转发，不经物理网卡，无 VXLAN 封装。 |

### E. 存储 PV / PVC / CSI
| # | 题目 | 答题要点 |
|---|---|---|
| 22 | PV / PVC / StorageClass 关系？ | PV 是集群侧存储资源，PVC 是用户侧申请，StorageClass 定义动态供给的 provisioner；绑定后一一对应。 |
| 23 | PVC Pending 的典型原因？ | StorageClass provisioner 不存在、云盘 zone 与 Node 不在同可用区、配额/容量不足、`volumeBindingMode` 为 WaitForFirstConsumer 但 Pod 未调度。✅[来源](https://dbaplus.cn/news-134-7164-1.html) |
| 24 | CSI 动态供给与 WaitForFirstConsumer？ | CSI 通过标准接口对接任意存储厂商；`WaitForFirstConsumer` 先调度再建卷，使卷落在 Pod 所在 zone，避免跨区挂载失败。✅（该问题见于 [SRE K8s mock](https://github.com/bregman-arie/devops-sre-interviews/blob/main/interviews/kubernetes/advanced/mock_1_questions.md)） |

### F. 调度器框架与亲和评分
| # | 题目 | 答题要点 |
|---|---|---|
| 25 | 调度两阶段流程？ | Filter（预选，剔除不满足资源/亲和/污点的节点）→ Score（优选，按插件加权打分取最高）→ Reserve/Permit/Bind。✅[调度流程描述](https://developer.aliyun.com/article/1321832) |
| 26 | Scheduling Framework 扩展点有哪些？ | QueueSort、PreFilter、Filter、PostFilter（抢占）、PreScore、Score、NormalizeScore、Reserve、Permit、PreBind、Bind、PostBind；插件经 KubeSchedulerConfiguration 启用。✅[来源](https://caomaolufei.github.io/AIInfraGuide/)／[官方文档](https://kubernetes.io/docs/concepts/scheduling-eviction/scheduling-framework/) |
| 27 | 常用打分插件与权重？ | NodeResourcesFit（LeastAllocated/MostAllocated/RequestedToCapacityRatio）、NodeAffinity、InterPodAffinity、PodTopologySpread、TaintToleration、ImageLocality；`NodeResourcesFit` 默认权重 1，可通过 `pluginConfig` 调权重与打分策略。⚠️ |
| 28 | 抢占（Preemption）机制？ | 高优先级 Pod 调度失败时，PostFilter 在候选节点上找可驱逐的低优先级 Pod，驱逐后重新调度；受 PDB 与 `preemptionPolicy` 约束。 |
| 29 | 节点 NotReady 对调度与 Pod 的影响？ | 新 Pod 不再调度到该节点；其上 Pod 因 node lease 超时被标记，Deployment 触发重建，StatefulSet 需等原 Pod 真正删除（致多副本短暂不可用）。 |

### G. 资源 requests/limits 与 QoS
| # | 题目 | 答题要点 |
|---|---|---|
| 30 | requests 与 limits 的语义？ | requests 用于**调度**（决定节点是否有空间）与 CPU 权重；limits 用于**运行时**约束（CPU 靠 CFS 限流、内存超限触发 OOMKill）。 |
| 31 | 三种 QoS 如何判定？ | Guaranteed：所有容器 CPU/内存 requests==limits；Burstable：至少一个容器设了 request/limit 且不满足 Guaranteed；BestEffort：全都没设。 |
| 32 | 内存超限与 CPU 超限表现差异？ | CPU 可压缩，超限只是被 throttle（`container_cpu_cfs_throttled_periods_total`）；内存不可压缩，cgroup 超限→OOMKilled（exit 137）。 |
| 33 | 如何防止 noisy neighbor？ | 关键服务设 Guaranteed；限制非关键负载 limits；用 LimitRange 兜底默认值、ResourceQuota 限命名空间总量；必要时用 cpu-manager 静态绑核。 |
| 34 | 节点按什么顺序驱逐 Pod？ | kubelet 依 `kubelet-eviction` 软/硬阈值，按 QoS（BestEffort > Burstable > Guaranteed）与超用量排序驱逐；可配 `system-reserved`/`kube-reserved`。 |

### H. RBAC 与安全
| # | 题目 | 答题要点 |
|---|---|---|
| 35 | RBAC 四要素与授权流程？ | Role/ClusterRole（权限）+ RoleBinding/ClusterRoleBinding（绑定）；请求先认证再鉴权，规则**只增不减**、无 deny 规则，匹配到 allow 即通过。 |
| 36 | Role 与 ClusterRole 何时用哪个？ | Role 作用于单一命名空间；ClusterRole 用于集群级资源（nodes/PV/CRD）或跨命名空间复用后按 ns 绑定。 |
| 37 | ServiceAccount 与 default SA 风险？ | Pod 默认挂载 default SA token，若其被授予过宽权限即横向提权风险；应关闭 `automountServiceAccountToken` 并用最小权限 SA + 短期 Token（TokenRequest）。 |

### I. etcd 与控制面
| # | 题目 | 答题要点 |
|---|---|---|
| 38 | etcd 在集群中的角色？ | 唯一持久化数据源（所有对象与状态）；强一致（Raft），写需多数派确认，因此延迟直接影响 apiserver 写入。 |
| 39 | etcd 性能与容量注意点？ | 建议 SSD、控制 `--quota-backend-bytes`（默认 2GB）、定期 `defrag`、避免高频大对象写入（如超大 ConfigMap）；watch 过多会推高内存。✅（etcd 健康/备份/调优为来源问题）[来源](https://github.com/bregman-arie/devops-sre-interviews/blob/main/interviews/kubernetes/advanced/mock_1_questions.md)／[etcd 监控文档](https://etcd.io/docs/v3.5/op-guide/monitoring/) |
| 40 | apiserver 变慢怎么判断是否 etcd 问题？ | 看 etcd `backend_commit_duration_seconds`、`wal_fsync_duration_seconds`、`mvcc` db size、leader 切换次数与磁盘 IO；对比 apiserver 各 verb 的 latency 指标。 |
| 41 | 控制面高可用要点？ | 多 master + 多 etcd 成员（奇数，3/5），前置 LB 统一暴露 6443；etcd 与 apiserver 尽量同机房低延迟，定期做快照备份并演练恢复。 |

### J. 监控 Prometheus
| # | 题目 | 答题要点 |
|---|---|---|
| 42 | Prometheus 拉取模型与生态？ | 主动 pull（`/metrics`）＋服务发现；Pushgateway 只用于短生命周期任务；Alertmanager 负责分组/抑制/静默；Grafana 可视化。✅[官方文档](https://prometheus.io/docs/introduction/overview/) |
| 43 | 四种指标类型与适用？ | Counter（只增，如请求数）、Gauge（可增减，如内存）、Histogram（分桶，可算分位）、Summary（客户端分位）。 |
| 44 | K8s 常用告警怎么设？ | 以 SLO 为锚：Pod 重启率、`container_memory_working_set_bytes` 接近 limit、节点磁盘/inode 水位、apiserver 5xx 与 P99 延迟、etcd fsync 延迟、PDB 违反次数。⚠️ |

### K. 故障排查命令
| # | 题目 | 答题要点 |
|---|---|---|
| 45 | Pod 排障标准命令顺序？ | `get pods -o wide`（状态/节点）→ `describe pod`（Events 是金矿）→ `logs`/`logs --previous` → `exec` 进去 curl/netstat/df → `port-forward` 判断是否 K8s 问题。✅[来源](https://dbaplus.cn/news-134-7164-1.html) |
| 46 | 节点层排障命令？ | `describe node`（Conditions/Allocated resources/Events）、`kubectl top node/pod`、`journalctl -u kubelet`、`crictl ps/pods/logs`、`systemctl status containerd`。 |
| 47 | 系统性排查路径（背下来） | **Pod → 容器 → 应用 → Service → Ingress → 存储 → Node → 网络** 自上而下逐层收敛。✅[来源](https://dbaplus.cn/news-134-7164-1.html) |

### L. Operator / CRD
| # | 题目 | 答题要点 |
|---|---|---|
| 48 | CRD 与 Operator 的关系？ | CRD 只定义新 API 类型与 schema；Operator = CRD + Controller，用调谐循环（reconcile）把期望状态落地。✅[官方 Operator 文档](https://kubernetes.io/docs/concepts/extend-kubernetes/operator/) |
| 49 | Reconcile 设计要点？ | 必须**幂等**、无状态（状态存 etcd）、基于 level-based 而非 edge-based；用 finalizer 做外部资源清理、Status conditions 表达阶段、ownerReference 做级联回收、RateLimiter 退避重试。✅[来源](https://github.com/bregman-arie/devops-sre-interviews/blob/main/interviews/kubernetes/advanced/mock_1_questions.md) |
| 50 | 为什么 List 大量 CR 会超时？ | apiserver 需序列化大对象集合、etcd 需读全量；应使用 `watch` + informer 本地缓存、分页 `limit/continue`、精简 status、必要时拆分为分片 CR。 |

### M. 容器运行时 containerd
| # | 题目 | 答题要点 |
|---|---|---|
| 51 | 容器隔离原理？ | Linux Namespace 做视图隔离（PID/NET/MNT/UTS/IPC/USER），cgroups 做资源限制与统计。✅[来源](https://developer.aliyun.com/article/1052138) |
| 52 | kubelet 与 containerd 的调用链？ | kubelet → CRI（gRPC）→ containerd（`cri` 插件）→ containerd-shim → runc → 容器进程；`crictl` 是调试 CRI 的客户端。 |
| 53 | Docker 与 containerd 关系？ | Docker 曾内置 containerd；K8s 1.24 起移除 dockershim，改用 CRI 直连 containerd/CRI-O，镜像与运行时解耦。✅[运行时文档](https://kubernetes.io/docs/setup/production-environment/container-runtimes/) |

---

## 4. 故障排查场景题（11 个）

✅ 8. 节中的路径框架与多数具体原因来自 [这条排查路径，能解决 99% 的 Kubernetes 故障](https://dbaplus.cn/news-134-7164-1.html)（原有来源：[运维李哥不背锅](https://dbaplus.cn/news-134-7164-1.html)）；命令细节为 ⚠️ 通用。

| 场景 | 排查步骤与常用命令 |
|---|---|
| **Pod Pending** | ① `kubectl describe pod` 看 Events（Insufficient cpu/memory、nodeAffinity 不匹配、taint 未容忍、PVC 未绑定）→ ② `kubectl describe node` 看剩余可分配资源与污点 → ③ `kubectl get pvc` 看存储。**原文强调：90% 的 Pending 最终查在节点上。** |
| **CrashLoopBackOff** | ① `kubectl logs <pod> --previous` 看上次退出栈 → ② `describe pod` 看 Exit Code（137=OOM/被杀、1=应用错误、126/127=入口找不到）→ ③ 检查 command/args、挂载的配置、依赖服务可达性。 |
| **OOMKilled** | ① `describe pod` 确认 `Last State: Terminated, Reason: OOMKilled` → ② `kubectl top pod` / `container_memory_working_set_bytes` 对比 limit → ③ 调 limit 或修内存泄漏；确认是容器 OOM 而非节点驱逐。 |
| **ImagePullBackOff / ErrImagePull** | ① `describe pod` 看具体错误（manifest unknown / unauthorized / no such host）→ ② 校验镜像名与 tag（避免 `latest`）→ ③ 检查 `imagePullSecrets`、镜像仓库网络与 DNS、节点磁盘空间。 |
| **节点 NotReady** | ① `kubectl describe node` 看 Conditions（Ready/MemoryPressure/DiskPressure/PIDPressure）→ ② `journalctl -u kubelet -f` → ③ `systemctl status containerd`、`df -h`、`free -m`、节点到 apiserver 的网络。 |
| **Service 不通** | ① `kubectl get endpoints <svc>`：`<none>` 说明 selector 没选中 Pod → ② `get pod --show-labels` 核对标签 → ③ `port-forward` 验证 Pod 自身 → ④ `systemctl status kube-proxy` / `get po -A | grep kube-proxy` 查代理与 CNI。 |
| **DNS 解析失败** | ① `kubectl exec -it <pod> -- nslookup kubernetes.default` → ② 查 CoreDNS Pod 是否 Running、`kubectl logs -n kube-system <coredns>` → ③ 查 `/etc/resolv.conf` 的 `ndots`/search 域、NetworkPolicy 是否放行 53 端口、conntrack 表是否满。 |
| **探针误杀（Running 但 Ready=false / 反复重启）** | ① `describe pod` 看 Probe 失败原因 → ② 常见：path 写错（`/health` vs `/healthz`）、端口与 containerPort 不一致 → ③ `exec` 进去 `curl localhost:<port>`、`netstat -tlnp` 验证监听地址是否为 `0.0.0.0` → ④ 放宽 `initialDelaySeconds`/`timeoutSeconds`，或改用 startupProbe。 |
| **磁盘压力 / Evicted** | ① `describe node` 查 `DiskPressure` 与 Events 中的 `evicted: ephemeral storage` → ② 清理 `/var/lib/containerd`、镜像与日志 → ③ 为 Pod 设 `ephemeral-storage` request/limit，检查 emptyDir 是否被写爆。 |
| **调度不均衡** | ① `kubectl get pods -o wide` 观察分布 → ② 检查是否有硬性 nodeAffinity/taint 导致集中 → ③ 加 `topologySpreadConstraints` 或软反亲和，调 `NodeResourcesFit` 打分策略（LeastAllocated 更均衡）。 |
| **PVC 挂载失败** | ① `describe pvc` 看 Pending/绑定事件 → ② `describe pod` 找 `MountVolume.WaitForAttach failed` / `Could not mount device` → ③ `exec` 进去 `df -h`、`ls /mnt/path`；核对 zone 一致性、CSI 插件 Pod 状态、Secret（Ceph/RBD 常见缺失）。 |

---

## 5. 复习资料清单（12 条）

**官方文档（全部链接已实测 HTTP 200）**
1. [Pod 生命周期](https://kubernetes.io/docs/concepts/workloads/pods/pod-lifecycle/) ／ [探针配置](https://kubernetes.io/docs/tasks/configure-pod-container/configure-liveness-readiness-startup-probes/)
2. [调度框架 Scheduling Framework](https://kubernetes.io/docs/concepts/scheduling-eviction/scheduling-framework/) ／ [亲和与反亲和](https://kubernetes.io/docs/concepts/scheduling-eviction/assign-pod-node/)
3. [Deployment 滚动更新](https://kubernetes.io/docs/concepts/workloads/controllers/deployment/) ／ [Service](https://kubernetes.io/docs/concepts/services-networking/service/) ／ [PV/PVC](https://kubernetes.io/docs/concepts/storage/persistent-volumes/)
4. [资源 requests/limits](https://kubernetes.io/docs/concepts/configuration/manage-resources-containers/) ／ [节点管理](https://kubernetes.io/docs/concepts/architecture/nodes/)
5. [RBAC](https://kubernetes.io/docs/reference/access-authn-authz/rbac/) ／ [RBAC 最佳实践](https://kubernetes.io/docs/concepts/security/rbac-good-practices/)
6. [容器运行时（containerd）](https://kubernetes.io/docs/setup/production-environment/container-runtimes/) ／ [Operator](https://kubernetes.io/docs/concepts/extend-kubernetes/operator/)
7. [排障总入口](https://kubernetes.io/docs/tasks/debug/) ／ **[kubectl 速查表](https://kubernetes.io/docs/reference/kubectl/quick-reference/)**

**题库与练习仓库**
8. [bregman-arie/devops-sre-interviews](https://github.com/bregman-arie/devops-sre-interviews/blob/main/interviews/kubernetes/advanced/mock_1_questions.md)（含 K8s advanced mock + 答案，适合模拟面试）
9. [bregman-arie/devops-exercises](https://github.com/bregman-arie/devops-exercises)（最全 DevOps 题库）
10. [dgkanatsios/CKAD-exercises](https://github.com/dgkanatsios/CKAD-exercises)（动手练 kubectl）／ [kubernetes-sigs/scheduler-plugins](https://github.com/kubernetes-sigs/scheduler-plugins)（调度插件源码）／ [awesome-kubernetes](https://github.com/ramitsurana/awesome-kubernetes)
11. [21 道题帮你拿捏 Kubernetes 面试](https://developer.aliyun.com/article/1321832)（含 kube-proxy/静态 Pod/创建流程详解）／ [2025 K8s 高频面试题](https://cloud.tencent.cn/developer/article/2503899) ／ [牛客 40 题（真实面试官问法）](https://www.nowcoder.com/discuss/353159323293523968) ／ [SRE 面试对照表](https://cloud.tencent.cn/developer/article/2571263)

**监控 / 书 / 博客**
12. [Prometheus 官方文档](https://prometheus.io/docs/introduction/overview/) ／ [etcd 监控](https://etcd.io/docs/v3.5/op-guide/monitoring/) ／ [这条排查路径能解决 99% 的 K8s 故障](https://dbaplus.cn/news-134-7164-1.html) ／ [2026 云计算面试核心指南](https://www.cnblogs.com/wgwyanfs/p/19963600) ／ [Flannel vs Calico](https://cloud.tencent.cn/developer/article/2499890)
**书单**（⚠️ 通用推荐，未逐一验证版本）：《Kubernetes 权威指南》（龚正等，牛客 40 题帖中亦提及）、《深入剖析 Kubernetes》（张磊/极客时间）、《SRE Google 运维解密》、《Kubernetes Up & Running》、《Kubernetes Patterns》。

---

## 6. 反问与加分项

### 6.1 向面试官提的问题（8 条）
1. 这个实习岗更偏**平台侧建设**（自研 Operator/调度增强/多集群管理）还是**一线集群运维**（容量、告警、发布）？
2. 集群规模大概是什么量级（节点数/GPU 卡数/命名空间数）？自建还是混合云？
3. 训练任务与在线推理是**共用一套 K8s 集群**还是物理隔离？调度上主要矛盾是什么（GPU 拓扑、碎片、抢占）？
4. 目前集群管理里最痛的问题是什么？（例如 GPU 利用率、大规模调度延迟、etcd 压力、多租户隔离）
5. 团队的技术栈与工具链：是否用 Operator/CRD 自研、GitOps（ArgoCD）、可观测性栈（Prometheus+VictoriaMetrics/Loki）？
6. 实习生的预期产出是什么？有没有可能独立负责一个模块（例如一个 Operator 或一套告警体系）？
7. 团队平时怎么做故障复盘与 On-call？实习生是否需要值班？
8. 转正机制与考核标准是什么？实习期一般多长？

### 6.2 回答项目时的加分表达（5 条）
1. **量化收益**（直接回应 MiniMax 面经"最大技术难题 + 优化成果"的原文要求 ✅[来源](https://caomaolufei.github.io/AIInfraGuide/interview/minimax-ai-infra-%E5%AE%9E%E4%B9%A0-%E4%BA%8C%E9%9D%A2/)）："把 Pod 启动 P99 从 45s 降到 12s，靠的是预拉镜像 + 调整探针 initialDelay"。
2. **先结论、再步骤、后运维视角**三层结构（✅[SRE 面试准备](https://gitee.com/shiyq1013/ops-skill-tree/raw/52229ce7af71ec8c670c129dd43d9e67e5aa78c6/99-%E9%9D%A2%E8%AF%95%E5%AE%9D%E5%85%B8/2026-04-10-%E9%9D%A2%E8%AF%95%E5%87%86%E5%A4%87-SRE%E5%B2%97%E4%BD%8DJD%E9%97%AE%E9%A2%98%E6%95%B4%E7%90%86.md.md)）："我的结论是……；具体我分三步做……；线上我会先看 X、必要时升级。"
3. **"先止血，再定位，再复盘"**，并强调**记录时间线与已做操作**（✅[同上](https://gitee.com/shiyq1013/ops-skill-tree/raw/52229ce7af71ec8c670c129dd43d9e67e5aa78c6/99-%E9%9D%A2%E8%AF%95%E5%AE%9D%E5%85%B8/2026-04-10-%E9%9D%A2%E8%AF%95%E5%87%86%E5%A4%87-SRE%E5%B2%97%E4%BD%8DJD%E9%97%AE%E9%A2%98%E6%95%B4%E7%90%86.md.md)）——运维岗特别吃这一点。
4. **主动暴露权衡与失败经验**："我一开始用了 `maxUnavailable=50%` 提速，结果灰度期 SLO 抖动，后来改成 25%+PDB，这个取舍是……"——比"我全做对了"更可信。
5. **把项目接到 K8s 语境**：准备一句"如果重做，我会用 Operator/CRD 把现在的脚本化流程收敛成声明式"，这正是云原生面试官想听的迁移能力（✅ 该追问模式见于阿里云原生三面"如果现在让你用 K8s 来做会怎么做"[来源](https://www.nowcoder.com/discuss/353157519931547648)）。

### 6.3 诚实提示
⚠️ 本次研究**未能找到** MiniMax「K8s 集群管理实习生」岗位的 JD 正文或该岗位面经。官方招聘入口为 [加入 MiniMax](https://vrfi1sk8a0.jobs.feishu.cn/275376/position/)（页面为 JS 渲染，无法抓取正文）。**建议候选人以本文第 3–4 节通用 K8s 题库为主干，用第 1 节的 MiniMax 三段式（项目深挖 → 理论 → 手撕）来安排时间分配**，并在面试中通过第 6.1 节的反问实时校准岗位实际方向。
