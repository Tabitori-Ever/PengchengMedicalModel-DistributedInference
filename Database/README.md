# Database · 数据集目录（真实文件夹）

用户平台「＋ 上传」弹出的目录浏览器读的就是这个文件夹（容器内路径 `/app/Database`，
可用环境变量 `DATABASE_DIR` 覆盖）。把数据文件放进这里，界面里立刻可选，无需改前端。

## 目录约定

```
Database/
  计算/计算_规模小_1.json      计算_规模中等_1.json      计算_规模大_1.json
  通信/通信_普通_1.json        通信_省流_1.json          通信_高速_1.json
  日常/日常_任务数3_1.json     日常_任务数5_1.json
  诊断/诊断_患者1404920_1.json …（4 位真实患者）
```

- 子目录名（计算 / 通信 / 日常 / 诊断）决定任务类型；
- 文件名即参数（如 `计算_规模中等_1`），界面上不需要再选类型与参数；
- 每个文件自描述：

```json
{
  "kind": "compute",
  "name": "计算_规模中等_1",
  "description": "4 台仪器 / 256 行 / 强度 40 / 3 个分区",
  "params": { "instruments": 4, "rows": 256, "intensity": 40, "partition_count": 3 }
}
```

`kind` 取 `compute | sync | routine | diagnosis`；`params` 字段名与提交接口一致
（计算 instruments/rows/intensity/partition_count，通信 bandwidth_mbps/concurrency/chunk_kb，
日常 jobs/rows/intensity，诊断 patient_id/target_hospital）。

## 运行时替换

- 改镜像：把文件放进本目录后重新构建 scheduler 镜像；
- 不改镜像：`kubectl cp <本地文件> <scheduler-pod>:/app/Database/...`，或把宿主机目录挂载到
  `/app/Database`（并在 Deployment 里设置 `DATABASE_DIR`）。
