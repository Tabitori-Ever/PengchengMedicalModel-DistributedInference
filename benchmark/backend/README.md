# benchmark-site backend (v1.0)

FastAPI + stdlib-`sqlite3` service behind the **Kubernetes 云边端协同推理 测试与性能对比站点**.
It implements the `/api/*` contract of
`调度模式与降级-设计方案.md` §6.1 and the attempt record of §6.2, executes experiments
against either the scheduler (`/schedule/*`) or the edge pods (`/local/*`, §4), and measures
the authoritative end-to-end latency itself (wall clock in this process, §3).

```
app.py            FastAPI app: the /api/* endpoints, exports, lifespan wiring
engine.py         run creation, background worker threads, §6.3 degradation routing,
                  wall-clock timing, patient dataset loading (§8.5)
db.py             SQLite storage (runs / attempts / settings / patients), atomic claim
config.py         config resolution: DB overrides > env vars > in-cluster defaults
stats.py          per-group aggregation (n/success/fail, mean/p50/p95/min/max) + correctness
requirements.txt  fastapi, uvicorn, pydantic, requests  (nothing else)
```

Dependencies are deliberately limited to `fastapi`, `uvicorn`, `pydantic`, `requests`.

## Run

```bash
# local (host) development — ClusterIPs because the WSL host cannot resolve k8s DNS
cd benchmark/backend
DB_PATH=/tmp/bench/bench.db \
PATIENTS_PATH=/tmp/bench/patients.json \
SCHEDULER_URL=http://localhost:30080 \
HOSPITAL_A_URL=http://10.50.126.210:8006 HOSPITAL_B_URL=http://10.50.78.57:8006 \
CLINIC_1_URL=http://10.50.193.185:8007 CLINIC_2_URL=http://10.50.168.231:8007 \
CLINIC_3_URL=http://10.50.157.126:8007 CLINIC_4_URL=http://10.50.171.207:8007 \
python -m uvicorn app:app --host 0.0.0.0 --port 8099
```

In the deployed image (port 8090, NodePort 30081) no env is needed: defaults are the
in-cluster DNS names and `/data/bench/bench.db`. `benchmark/tools/extract_patients.py`
regenerates `patients.json` from `test/test_dataset.json` for local testing (writes to
`/tmp` only — the real file is baked into the image, the repo does not ship it).

`pip install -r requirements.txt` (Python 3.11; also runs on 3.10).

## Endpoints (§6.1)

| Method | Path | Notes |
|---|---|---|
| GET | `/api/health` | site + scheduler reachability (short probe) + per-pod reachability/capabilities (`/local/health`), patients source. `?pods=0` skips pod probing. A pod that answers but has no `/local/*` is reported as `reachable=true, local_mode_available=false, error="local endpoint unavailable (404)"`. |
| GET | `/api/config` | flat config object (see below) |
| PUT | `/api/config` | partial update, validated, persisted to `settings` and applied to *subsequent* attempts (e.g. `force_degraded` switch). Unknown keys / bad types → 400. |
| GET | `/api/patients` | `{count, source, path, origins, patients:[{patient_id,bpCR,hospital,origin,has_input,input_keys}]}`; `?format=list` returns the bare array (parity with scheduler `/test/patients`), `?refresh=1` re-reads the file. |
| POST | `/api/runs` | `{kind,source,params,mode,repeats,concurrency,label,force_degraded}` → 201 `{run_id,spec,status,attempts}` |
| GET | `/api/runs?limit=50` | experiments + `progress` + aggregate `summary` |
| GET | `/api/runs/{run_id}` | run + `progress` + `summary` + per-attempt rows (§6.2 fields + raw payloads) |
| POST | `/api/runs/{run_id}/cancel` | cancels **not-yet-started** attempts (running ones finish) |
| DELETE | `/api/runs/{run_id}` | deletes run + attempts |
| GET | `/api/results?limit=100` | recent attempt-level results across runs (filters: `kind,source,mode,run_id,status,degraded,include_pending`) |
| GET | `/api/compare?kind=&source=&mode=` | grouped: `n/success/fail`, `client_total_ms{mean,p50,p95,min,max}`, `stage_means{...}`, `correctness`; `group_by=spec` also splits by parameter fingerprint |
| GET | `/api/compare/matrix` | full (kind × source × mode_used × degraded) aggregate table |
| GET | `/api/export.csv` | attempt-level export, UTF-8 **BOM** + CRLF + Chinese headers, `Content-Disposition: attachment` |
| GET | `/api/export.json` | attempt-level export with all §6.2 fields (+ raw envelope/metrics/result_detail) |

Export/compare filters: `kind`, `source`, `mode` (`collaborative` \| `local` \| `degraded`),
`run_id`, `degraded`, `limit` (`compact=1` for JSON drops raw envelopes).

## Configuration

Resolution order: **SQLite `settings` row > environment variable > built-in default**.
`PUT /api/config` writes the settings row; `GET /api/config` shows the merged result.

| key | env | default | meaning |
|---|---|---|---|
| `scheduler_url` | `SCHEDULER_URL` | `http://scheduler-service:8000` | scheduler base url |
| `pod_urls` | `POD_URLS` (JSON) / `HOSPITAL_A_URL` `HOSPITAL_B_URL` `CLINIC_1_URL`…`CLINIC_4_URL` | in-cluster DNS | per-entity pod base url |
| `force_degraded` | `FORCE_DEGRADED` | `false` | manual forced-degradation switch (skips the scheduler) |
| `auto_degrade` | `AUTO_DEGRADE` | `true` | fall back to the pod when the scheduler is unreachable |
| `probe_timeout_ms` | `PROBE_TIMEOUT_MS` | `1500` | pre-flight probe / health probe timeout |
| `concurrency` | `CONCURRENCY` | `2` | default attempts executed in parallel per run |
| `default_repeats` | `DEFAULT_REPEATS` | `3` | repeats when the request omits it |
| `call_timeout_ms` | `CALL_TIMEOUT_MS` | `300000` | submit POST timeout (scheduler + pod) |
| `poll_http_timeout_ms` | `POLL_HTTP_TIMEOUT_MS` | `30000` | single poll GET timeout |
| `task_timeout_ms` | `TASK_TIMEOUT_MS` | `600000` | budget for one attempt (submit → result) |
| `poll_interval_ms` | `POLL_INTERVAL_MS` | `250` | poll period (first polls are faster: 150 ms, then back off) |
| `local_async` | `LOCAL_ASYNC` | `false` | submit pod tasks with `async=true` and poll `/local/result/{id}` |

Also accepted: `DB_PATH` (default `/data/bench/bench.db`), `PATIENTS_PATH`
(default `/app/patients.json`), `PORT`. The last five keys are extras required to give
**every** HTTP call an explicit timeout and to bound polling; they are not part of §6.1.

## Degradation routing (§6.3)

| condition | path | `mode_used` | `degraded` | `degrade_reason` |
|---|---|---|---|---|
| run `force_degraded` or global switch on | pod `/local/execute` | `local` | true | `forced` |
| `mode=local` | pod `/local/execute` | `local` | false | — |
| `mode=collaborative` | scheduler `/schedule/*` + poll `/task/result/{id}` | `collaborative` | false | — |
| collaborative submit fails with a **connect** error/timeout and `auto_degrade` | pod | `local` | true | `scheduler_unreachable` |
| `mode=auto` probe unreachable | pod (probe result reused, no second probe) | `local` | true | `scheduler_unreachable` |
| `mode=auto` probe reachable | scheduler | `collaborative` | false | — |

Safety rules: the fallback triggers **only** on a connect-level failure of the *submit*
(`requests.ConnectionError`, incl. connect timeout). An HTTP error status or a read timeout
means the scheduler is alive and may already own the task, so the attempt is recorded as
failed instead of re-executed (avoids duplicate `sync` side effects, §8.11). After a
successful submit, a polling failure never re-dispatches. `mode` defaults to `auto`.

## Timing discipline (§3, §8.10)

* `client_total_ms` is **measured with a wall clock in this process** around the exchange
  actually used: scheduler path = `POST /schedule/{kind}` … terminal `/task/result/{id}`
  (so it includes scheduler queue wait and poll granularity); pod path = `POST
  /local/execute` … result (sync response, or `async` + `/local/result/{id}`).
  It is never derived from, or replaced by, numbers reported by the scheduler/pod.
* `degrade_switch_ms` records the routing overhead **excluded** from `client_total_ms`:
  the `auto` pre-flight probe, the failed scheduler submit, and the switch decision
  (§6.3.4 explicitly forbids burning the probe timeout inside the measured latency).
  `0.0` for non-degraded paths. If the envelope reports its own switch time and the site
  measured none, the envelope value is used.
* `queue_wait_ms` / `pipeline_total_ms` / `compute_ms_total` / `network_ms_total` come from
  the returned envelope; when a pipeline does not report them, safe derivations are used
  (Σ `stage.compute_ms`, Σ stage `network_ms`, `pipeline − compute`,
  `result_detail.aggregate_cpu_ms`, and `queue_wait_ms = 0` for local execution per §3).
  Every derivation is recorded in `metrics_source`; the untouched envelope is stored in
  `envelope_json` (CSV column 指标来源, JSON field `metrics_source`).

## Storage

`DB_PATH` (SQLite, WAL, auto-created): `runs`, `attempts` (§6.2 fields + `spec_key`,
raw `params_json`/`metrics_json`/`envelope_json`/`result_detail_json`/`detail_json`),
`settings`, `patients`. Everything survives a restart — attempts/runs left `pending`/`running`
by a crash are marked `failed` with `interrupted by backend restart` at startup, completed
attempts are untouched. Imaging inputs (~2 MB/patient) are **not** duplicated into every
attempt row: `patient_id` inputs are re-read from `patients`, inline `params.input` is kept
in memory for the process lifetime.

`GET /api/patients` uses the bundled `PATIENTS_PATH`; if that file is missing the backend
fetches `/test/patients` + `/test/patient/{id}` from the scheduler and caches them
(`origin=scheduler`) so diagnosis keeps working offline later (§8.5).

## Correctness column

* diagnosis: `bpcr_actual` (probability) vs `bpcr_expected` (dataset label), binarised at
  0.5 when the label is 0/1 → `match` / `mismatch` / `n/a`.
* compute: the attempt's `checksums` vs the collaborative reference checksums for the same
  parameter fingerprint (`spec_key`; same run preferred) → `match` / `mismatch` / `n/a`
  (n/a when no collaborative reference exists, or for the collaborative attempt itself).

## Integration notes / deliberately left to other sides

* `/local/*` on the pods is implemented by another engineer; until it ships, local/degraded
  attempts are recorded as `status=failed` with `local endpoint unavailable (404) at <url>`
  (not a crash), and `/api/health` reports the same for each pod.
* `scheduler/api.py::ComputeRequest` has no `seed` field, so the collaborative compute
  pipeline always derives a **random** seed per task: identical `params` therefore yield
  different `checksums`, and the compute correctness column reports `mismatch`. For
  meaningful cross-mode checksum comparison the scheduler should accept `seed` in
  `/schedule/compute` (the pipeline already honours `input["seed"]`).
* `clinic-3`/`clinic-4` are configurable here but unknown to `scheduler.service_registry.is_source()`;
  collaborative runs from them fail inside the scheduler (recorded as a failed attempt).
* Cancelling cannot interrupt an in-flight HTTP attempt; it prevents not-yet-started
  attempts from running.
* No Kubernetes manifests and no Dockerfile are included here (owned by another change).

## 固定套件（v3.3）

`suites.py` 里的任务集是**冻结**的：同一套件在两种调度策略下跑的是逐字节相同的任务，唯一的差异是被测量的变量。

| 接口 | 说明 |
|---|---|
| `GET /api/suites` | 套件清单（含档位与固定参数、`task_count`、默认发起方与并发） |
| `POST /api/runs` + `{suite_id, mode, source, concurrency, label}` | 把整份套件作为**一次实验**下发（20 个 attempt，各自带自己的参数） |
| `GET /api/compare/suite?suite_id=&source=` | 按档位汇总两种策略的 `n/mean/p50/p95/min/max/success`，并给出总体 `speedup.collaborative_faster_pct` |

`suite-compute-20`：计算类，5 档负载（对照/中载/重载/特重/极重）× 4 次重复，固定 `partition_count=3` 与每档固定 seed
（因此本地与协同的 checksum 可逐档比对）。**修改套件等于更换实验条件**——如需变更请新建 `suite_id`。
