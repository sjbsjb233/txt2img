# 文生图后端 · PR 切片实施方案

> 配套文档:`后端详细设计文档 v1.0`(下文简称「设计文档」)。每条切片只描述边界与验收;具体实现仍要回去对照设计文档。
>
> 切片原则:
> 1. **从底向上**:DB → 领域服务 → API → admin。下层稳定后再做上层,避免上层先写完底层一改全废。
> 2. **每个 PR 可独立 review**:控制在 ~500–1500 行新增代码以内,带最小可运行的测试。
> 3. **依赖显式声明**:每个 PR 列出"必须先合并的 PR",避免乱序。
> 4. **接口/契约优先**:涉及对外契约(API、SSE event、DB Schema)的 PR 优先做完,后续 PR 只填充行为。
> 5. **不在切片里发明新设计**:如果原文档已有定义,切片只引用不重写。

---

## 0. 总览

**18 个 PR / 6 个阶段**

| 阶段 | PR | 标题 | 复杂度 |
|---|---|---|---|
| 一、基础设施 | PR-01 | 项目骨架与配置加载 | 中 |
| | PR-02 | 数据库 Schema、迁移、Seed | 中 |
| | PR-03 | 鉴权登录与 Turnstile | 中 |
| 二、配置与适配器 | PR-04 | 配置中心 + Tier 配置热加载 | 小 |
| | PR-05 | Adapter 抽象 + OpenAI/Gemini 内置实现 | 大 |
| | PR-06 | Provider(中转站)管理 + 余额账本 | 中 |
| 三、任务管线 | PR-07 | 图片存储与文件服务 | 中 |
| | PR-08 | 任务数据模型与生命周期 | 中 |
| | PR-09 | 配额闸门 + 紧急封控 | 中 |
| | PR-10 | Provider 选择器 + 熔断 + 指标 | 大 |
| | PR-11 | 4-lane WFQ 调度 + 执行器 + 软超惩罚 | 大 |
| 四、用户态 API | PR-12 | SSE 中枢与长连接 | 中 |
| | PR-13 | Models API + 任务创建/取消 | 大 |
| | PR-14 | Archive 同步 API + 图片读取 | 中 |
| 五、管理端 | PR-15 | Admin 用户管理(含影子登录、批量) | 中 |
| | PR-16 | Admin Provider/Adapter + 清理 | 中 |
| | PR-17 | Admin 公告 + 指标 + 审计 + 紧急开关 | 中 |
| 六、收尾 | PR-18 | 集成测试、优雅停机、上线 Checklist | 中 |

## 0.1 依赖图

```
PR-01 (骨架)
  └─ PR-02 (DB)
       ├─ PR-03 (Auth) ──────────────────────────┐
       ├─ PR-04 (Config/Tier) ──────┐            │
       ├─ PR-05 (Adapter) ──┐       │            │
       │                    ▼       ▼            │
       │              PR-06 (Provider) ◄─────────┤
       │                    │                    │
       ├─ PR-07 (Image IO)  │                    │
       ├─ PR-08 (Job Model)─┤                    │
       │       │            │                    │
       │       └─ PR-09 (Quota Guard) ◄──────────┤
       │                    │                    │
       │              PR-10 (Selector/Breaker/Metrics)
       │                    │                    │
       │              PR-11 (Scheduler/Executor)
       │                    │                    │
       │              PR-12 (SSE Hub) ◄──────────┤
       │                    │                    │
       │              PR-13 (Models/Jobs API)    │
       │                    │                    │
       │              PR-14 (Archive API)        │
       │                    │                    │
       │              PR-15 (Admin Users) ◄──────┤
       │              PR-16 (Admin Providers + Cleanup)
       │              PR-17 (Announcements + Metrics + Audit)
       │                    │
       │              PR-18 (E2E + Hardening)
```

---

# 阶段一 · 基础设施

## PR-01: 项目骨架与配置加载

**目标**: 搭起后端工程的目录结构、FastAPI app 启动、Docker、env 加载。**不实现任何业务功能**。

**范围**:
- 按设计文档 §1.1 创建 `backend/app/` 目录骨架(空 `__init__.py` 即可,各模块占位)。
- `app/main.py`:FastAPI app + lifespan(暂时只 print "starting/closing")。
- `app/config.py`:用 pydantic-settings 读取 env(参见设计文档 §1.5)。所有新增 env 都纳入。
- `Dockerfile` + `requirements.txt`(FastAPI + uvicorn + SQLAlchemy + alembic + argon2-cffi + Pillow + python-jose + httpx + pydantic + pydantic-settings)。
- `docker-compose.dev.yml` / `docker-compose.prod.yml` 与现有挂载兼容(`./data:/app/data`)。
- `GET /api/health` 返回 `{"status":"ok","version":"v1.0.0"}`。

**关键文件**:
- `backend/Dockerfile`
- `backend/requirements.txt`
- `backend/app/main.py`
- `backend/app/config.py`
- `backend/app/api/health.py`
- `backend/.env.example`(对齐 `backend.env.example`)

**验收**:
- [ ] `docker compose -f docker-compose.dev.yml up backend` 启动成功
- [ ] `curl localhost:<port>/api/health` 返回 200
- [ ] 缺失关键 env 时启动会清晰报错(pydantic 校验)
- [ ] 容器内 `/app/data` 目录存在且可写
- [ ] 没有任何业务路由,没有 DB 连接

**依赖**: 无

---

## PR-02: 数据库 Schema、迁移、Seed

**目标**: 把设计文档 §14 的所有表落到 SQLite,可运行迁移,首次启动自动 seed。

**范围**:
- `app/db/engine.py`:SQLAlchemy async engine,DSN `sqlite+aiosqlite:////app/data/txt2img.db`,启动时执行 `PRAGMA journal_mode=WAL`、`synchronous=NORMAL`、`busy_timeout=5000`。
- `app/db/models.py`:对应 §14 全部表的 ORM 类(users / tiers / providers / provider_models / provider_tier_access / provider_model_tier_access / jobs / job_references / images / sessions / session_jobs / billing_ledger / announcements / announcement_reads / login_attempts / audit_log / config / disk_usage / sets)。
- Alembic 初始化 + `migrations/versions/0001_init.py` 一次性建完所有表与索引。
- `app/db/seed.py`:
  1. 写入默认 4 档 tier(数值见设计文档 §3.1 / 附录 A)。
  2. 写入默认 config 全部 key(设计文档 §13.5)。
  3. 用 `ADMIN_USERNAME` / `ADMIN_PASSWORD`(env)创建 bootstrap admin。
- 启动时 lifespan 调用 `alembic upgrade head` 与 `seed.bootstrap()`。
- `JsonRepository[T]` 基类(设计文档 §10 重构落地建议)— 占位即可,后续 PR 复用。

**验收**:
- [ ] 第一次启动后 `data/txt2img.db` 存在,`PRAGMA journal_mode` 返回 `wal`
- [ ] 所有表都按 §14 DDL 创建,字段、索引、外键、CHECK 约束完整
- [ ] `tiers` 表有 4 行;`config` 表有设计文档列出的全部 key
- [ ] `users` 表有 1 个 admin 行,密码用 argon2id 哈希
- [ ] 第二次启动不会重复 seed,也不会报错(幂等)
- [ ] 单元测试:能创建一个 user 并查回

**依赖**: PR-01

---

## PR-03: 鉴权登录与 Turnstile

**目标**: 完成 §2 全部内容:JWT、登录、captcha-check、依赖注入、密码哈希。

**范围**:
- `app/services/turnstile.py`:调 Cloudflare siteverify。
- `app/utils/security.py`:argon2id hash/verify、JWT sign/verify。
- `app/deps.py`:
  - `get_current_user(token) -> User`(从 DB 实时读 tier/quota,**不信任 JWT 里的字段**)
  - `get_current_admin(...) -> User`
  - `require_not_blocked(...)`(此处先占位 `pass`,真实逻辑在 PR-09 注入)
- `app/api/auth.py`:
  - `POST /api/auth/captcha-check` — 5 分钟内 ≥3 次失败或 `force_captcha_global=true` 即返回 required(后者读 config,§13.5 已有 key)
  - `POST /api/auth/login` — 校验密码、可选 captcha、写 `login_attempts`、签发 JWT
  - `POST /api/auth/logout` — 仅返回 200(后端无 session)
  - `GET /api/me` — 返回 `{ id, username, role, display_name }`,**不含 tier/quota**
- `ACCOUNT_DISABLED` 错误码(§17)。
- 单元测试覆盖:成功/密码错/账户停用/captcha 缺失/captcha 过期。

**验收**:
- [ ] 用 bootstrap admin 能登录拿到 JWT
- [ ] JWT payload 只含 `sub/u/r/iat/exp`
- [ ] 5 分钟内连续 3 次错密码 → 第 4 次 captcha-check 返回 required
- [ ] `force_captcha_global` 翻成 true 后所有 captcha-check 返回 required
- [ ] `disabled` 用户登录返回 403 `ACCOUNT_DISABLED`
- [ ] `/api/me` 不暴露 tier、today_count、quota
- [ ] 除 `/health` `/auth/login` `/auth/captcha-check` 外所有 `/api/*` 无 token 返回 401

**依赖**: PR-02

---

# 阶段二 · 配置与适配器

## PR-04: 配置中心 + Tier 配置热加载

**目标**: 让设计文档 §13.5 列出的所有 config key 都能通过 admin 接口读写,并被领域单例热感知。

**范围**:
- `app/domain/config_center.py`:`ConfigCenter` 单例,启动加载,提供 `get(key)` / `set(key,value)` / `reload()`。`set` 同时写 DB。
- 拆出几个领域专用包装(读 ConfigCenter 的子集):
  - `TierConfig`(读 `tiers` 表 + override)
  - `SchedulerConfig`(读 `scheduler.*`)
  - `SoftPenaltyConfig`(读 `soft_penalty.*`)
  - `ProviderScoringConfig`(读 `provider_scoring.*`)
  - `CircuitBreakerConfig`(读 `circuit_breaker.*`)
  - `EmergencyConfig`(读 `emergency.*`)
- `app/api/admin/config.py`:
  - `GET /api/admin/config` 全量
  - `GET /api/admin/config/<key>` 单 key
  - `PATCH /api/admin/config` body `{key1: v1, key2: v2}`,对每个 key 校验类型/范围,**weights 总和必须为 1**(否则 422)
- `app/api/admin/tiers.py`:
  - `GET /api/admin/tiers`
  - `PATCH /api/admin/tiers/<tier>`,改后调 `TierConfig.reload()`
- 所有 admin 接口走 `Depends(get_current_admin)`,失败 403。

**验收**:
- [ ] 改 `scheduler.global_max_workers` 立即体现在 `ConfigCenter.get(...)` 返回值,无需重启
- [ ] 改 tier weight 后 `TierConfig.get('vip').weight` 立即更新
- [ ] PATCH 包含非法 key 返回 422 不入库
- [ ] PATCH `provider_scoring.weights.cost=0.9` 而其他不变 → 总和不为 1 → 422
- [ ] 普通用户访问 `/api/admin/config` 返回 403
- [ ] 写操作打 audit_log(本 PR 先占位写入,审计列表查询接口在 PR-17)

**依赖**: PR-03

---

## PR-05: Adapter 抽象 + OpenAI / Gemini 内置实现

**目标**: 完成设计文档 §4.4、§5.1、§5.2 的全部 Adapter 体系。

**范围**:
- `app/schemas/normalized.py`:`NormalizedRequest` / `NormalizedResponse` / `StandardError`(§5.2)。
- `app/adapters/base.py`:`BaseAdapter` ABC,`AdapterRegistry` 启动扫描注册。
- `app/adapters/openai_v1.py`:
  - `adapter_type = "openai_v1"`
  - 处理 `gpt-image-2` 全部参数:`model/prompt/n/size/quality/output_format/output_compression/background/moderation/stream/partial_images/user/image/mask`
  - **不发送 `transparent` 背景**,捕到此参数立即抛 INVALID_PARAMETER
  - **不发送 `input_fidelity`**
  - 多文件 edits 时按 `references[].order` 升序构造 multipart
- `app/adapters/gemini_v1beta.py`:
  - `adapter_type = "gemini_v1beta"`
  - 支持 `gemini-3-pro-image-preview` 与 `gemini-3.1-flash-image-preview`
  - 必须显式声明 `responseModalities: ["TEXT","IMAGE"]`
  - `imageSize` 大写 K 保留,`512` 无后缀
  - 多 reference 按 `order` 升序拼 `contents.parts`
  - 接住 `thought_signature`,放入 `NormalizedResponse.metadata`
  - `tools.googleSearch` / `imageSearch` 按 capabilities 透传
- `app/api/admin/adapters.py`:`GET /api/admin/adapters`(§13.8),返回所有已注册适配器及 `in_use_by_providers`(此时为空,等 PR-06 写入数据)。
- 单元测试:对每个 adapter mock httpx,断言请求体格式与上文 §1.3 / §2.4 / §3.4 的 curl 完全一致;断言失败错误能被 `normalize_error` 翻成统一码。

**验收**:
- [ ] 启动时 `AdapterRegistry` 自动收录 2 个适配器
- [ ] mock 上游成功,生成 `NormalizedResponse` 含图像 bytes + 计费信息
- [ ] 给 gpt-image-2 传 `background="transparent"` → 422 INVALID_PARAMETER
- [ ] 给 gemini 传 `imageSize="1k"` → 422
- [ ] 给 gemini 传 14 张以上参考图 → 422
- [ ] reference 顺序在请求体里严格保持 `order` 升序
- [ ] 上游 401 → `normalize_error` 返回 `AUTH`
- [ ] 上游 429 → `RATE_LIMITED`
- [ ] 上游 5xx → `UPSTREAM_ERROR`

**依赖**: PR-02

---

## PR-06: Provider(中转站)管理 + 余额账本

**目标**: 完成设计文档 §4 全部内容,以及 admin provider 管理基础接口(高级运维操作如 `test`、`reset-circuit` 留到 PR-16)。

**范围**:
- `app/domain/provider_ledger.py`:`ProviderLedger`,提供 `deduct(provider, job, image_count)` 与 `topup(provider, amount)`,写 `billing_ledger`,在事务内更新 `providers.balance_cny`。
- `app/utils/crypto.py`:用 `JWT_SECRET` 派生 AES-GCM key,实现 `encrypt(api_key)` / `decrypt(blob)`。
- `app/api/admin/providers.py`(本 PR 仅基础 CRUD):
  - `GET /api/admin/providers` — 列表(此时 `circuit_state` 字段先恒返 `healthy`,实际状态机在 PR-10)
  - `POST /api/admin/providers` — body 参见 §4.2,api_key 加密存,初始 `balance = initial_balance`
  - `PATCH /api/admin/providers/<id>` — 改基础字段;改 api_key 时重新加密
  - `DELETE /api/admin/providers/<id>` — 物理删除,级联清理 `provider_models` / `provider_tier_access`
  - `PATCH /api/admin/providers/<id>/models/<model_id>` — 改 capabilities / enabled
  - `PATCH /api/admin/providers/<id>/tier-access` — 改 tier 白名单
  - `POST /api/admin/providers/<id>/topup` — 充值,余额 +amount,DRAINED 时强制改回 healthy
- 校验:`adapter_type` 必须在 `AdapterRegistry` 中存在;`supported_models` 中每个 model 必须在该 adapter 的 `supported_models()` 内。
- `GET /api/admin/adapters` 的 `in_use_by_providers` 字段此时正确填充。
- API key 在所有响应里**永远脱敏**(`sk-***LAST4`)。

**验收**:
- [ ] 创建 provider 后 `data/txt2img.db` 中 `api_key_enc` 字段是密文,DB dump 看不到原文
- [ ] 给 provider 配 `adapter_type="not_exist"` → 422
- [ ] 给 gemini_v1beta 配 model "gpt-image-2" → 422
- [ ] capabilities 修改后立即可读
- [ ] topup 5 元后 `balance_cny += 5`,且写入 `billing_ledger`(可选:也可以选择 topup 不写 ledger,保持 ledger 只记扣减,需在文档里说明)
- [ ] 普通用户访问任何 admin/providers/* → 403
- [ ] 列表响应里 api_key 全部脱敏

**依赖**: PR-02、PR-05

---

# 阶段三 · 任务管线

## PR-07: 图片存储与文件服务

**目标**: 完成设计文档 §10 全部内容,但**还没有任务**,所以提供库函数 + 单测。

**范围**:
- `app/services/image_io.py`:
  - `save_original(job_hash, idx, bytes, mime) -> rel_path`
  - `make_thumbnail(src, dst, max_edge=720, quality=78)` — 用 Pillow,长边裁剪,WEBP q78,RGBA→RGB
  - `path_for_job(hash_id) -> Path`(强制校验 `^j_[A-Za-z0-9]{12}$`,不匹配抛)
  - `path_for_reference(hash_id, order) -> Path`
  - `write_meta_json(hash_id, payload)` / `append_timeline(hash_id, event)` / `write_upstream_log(hash_id, attempt, log)`
- `app/domain/cache_keeper.py` 占位 + 一个 `refresh_disk_usage()` 函数(扫 `data/jobs/`,按 7d/30d/90d/cancelled/failed 写 `disk_usage` 表)。真正定时调度的清理任务在 PR-16。
- 引入 `Pillow >= 10`(若 CI 环境支持,可加 `pillow-simd` 作为 optional)。
- 单元测试:
  - 给一张 4K PNG → 缩略图长边 = 720,文件大小 < 200KB
  - hash_id `../etc/passwd` → ValueError
  - 一个 RGBA 透明 PNG 缩略后无报错(自动 RGB)
  - `disk_usage` 表能跑通

**验收**:
- [ ] 单张缩略图生成耗时 < 200ms(本机基线)
- [ ] 路径校验拒绝目录穿越
- [ ] `data/jobs/<hash>/{outputs,refs,upstream}` 子目录按需创建
- [ ] `meta.json` / `timeline.jsonl` 格式如设计文档 §10.3
- [ ] `refresh_disk_usage()` 写入 `disk_usage` 表的 `jobs_total / jobs_older_than_7d / ...`

**依赖**: PR-02

---

## PR-08: 任务数据模型与生命周期

**目标**: 把设计文档 §8、§14.4、§6.6 的"任务"层数据流跑通(不含调度,不含上游调用)。

**范围**:
- `app/utils/ids.py`:`new_user_id() / new_job_internal_id() / new_job_hash_id() / new_set_id() / new_image_id() / new_session_id() / new_announcement_id()`(对齐设计文档附录 B)。
- `app/domain/job_lifecycle.py`:`JobLifecycle`,**唯一允许写 `jobs.status` 的入口**,提供:
  - `transition(job, to_status, reason=None)` — 写 DB + 写 `timeline.jsonl` + 调用 `sse_hub.broadcast_to_user(user_id, job_state_event)`(此处 sse_hub 先用占位接口 `BroadcastSink`,真实实现在 PR-12 注入)
  - 状态机校验(只允许设计文档 §8.1 的合法迁移)
- `app/api/sessions.py`(用户态):
  - `GET /api/sessions` — 列表
  - `POST /api/sessions` — 创建 `{name}`
  - `PATCH /api/sessions/<id>` — 改名
  - `DELETE /api/sessions/<id>` — 仅解除 `session_jobs` 关联,任务保留
- 在 `users.last_seq_no` 上的 atomic 自增(`UPDATE ... RETURNING` 或事务内 SELECT FOR UPDATE 替代 — SQLite 用 BEGIN IMMEDIATE)。
- 单元测试:
  - 同一 user 100 次提交 → seq_no 严格 1..100,无重复
  - 状态从 SUCCEEDED → RUNNING 的非法迁移被拒
  - `set_id` 生成规则:`n>1` 才生成

**验收**:
- [ ] `JobLifecycle.transition` 不允许非法状态迁移
- [ ] `seq_no` 在并发 50 提交下仍严格递增、无并发漏号
- [ ] sessions CRUD 完整
- [ ] 删除 session 不会级联删除 job
- [ ] timeline.jsonl 格式与设计文档 §10.3 一致

**依赖**: PR-02、PR-07

---

## PR-09: 配额闸门 + 紧急封控

**目标**: 完成设计文档 §3.4、§7.1、§13.6 全部内容。**仍不实现真实任务创建**,只把"决定能不能进"这一层做对。

**范围**:
- `app/domain/quota_guard.py`:
  - `check_hard_quota_exceeded(user) -> bool` — 比 `today_count` 与 `effective_hard_quota`
  - `check_soft_quota_exceeded(user) -> bool`
  - `record_usage(user)`(在 Job 入队时 +1)、`refund_usage(user)`(NO_PROVIDER_AVAILABLE / CANCELLED 时 -1)
  - 懒重置:写 `today_count` 前比 `today_reset_date`(北京时间) → 不同就清零
- `app/domain/access_policy.py`:`evaluate(user, model) -> AccessDecision`,顺序:
  1. emergency.pause_generation
  2. emergency.block_new_member_login(此处不阻挡,login 时阻挡)
  3. status == 'disabled'
  4. quota_guard.hard
  5. queue_guard.user_busy(用户当前 running + queued >= max_concurrency + max_queue) — 此处先用 `JobRepository.count_active_by_user`,真实队列在 PR-11 后才有,暂时只看 DB 中 status in (QUEUED, RUNNING) 的数量
  6. 通过 → 返回 `AccessDecision(passed=True, soft_quota_exceeded=...)`
- 把 `app/deps.py::require_not_blocked` 接上真实逻辑。
- 一个**裸 endpoint** `POST /api/jobs/_dryrun_access`(仅本 PR 临时验证用,合并前删除或留 admin-only):入参 `{model}`,返回 AccessDecision。
- 每日 00:00 UTC+8 兜底 reset job 通过 `asyncio.create_task` 在 lifespan 启动(简单实现:每分钟扫一次找跨日的 user)。
- 单元测试:覆盖每种闸门分支 → 应得对应 4xx 错误码(HARD_QUOTA_EXCEEDED / USER_BUSY / BLOCKED_BY_EMERGENCY / ACCOUNT_DISABLED)。

**验收**:
- [ ] today_count 跨日自动清零
- [ ] hard 触发返回 429 HARD_QUOTA_EXCEEDED;soft 不阻挡但带 flag
- [ ] emergency.pause_generation 翻 true 后所有非 admin 提交 403 BLOCKED_BY_EMERGENCY
- [ ] running+queued 满 → USER_BUSY
- [ ] 4 种 429/403 错误码与设计文档 §17 完全一致

**依赖**: PR-04、PR-08

---

## PR-10: Provider 选择器 + 熔断 + 指标

**目标**: 完成设计文档 §7.4、§7.5、`MetricsEngine` 5min 滑窗。**仍不调度**,提供库函数。

**范围**:
- `app/domain/metrics_engine.py`:
  - per (provider_id, model) 滑窗 5min
  - 记录:`record_call(p, model, ok, latency_ms, error_code)`
  - 查询:`success_rate(p, model)` / `p50_ms` / `p95_ms` / `qps` / `recent_calls_in_60s`
  - 用环形 buffer + 时间戳过期,内存即可。每 60s 把当前快照序列化进 `providers.recent_calls_json`(给 admin 列表看)。
- `app/domain/circuit_breaker.py`:状态机如 §7.5,字段在 `providers` 表。`observe(p, success)` 触发状态转移;`HALF_OPEN` 用 `asyncio.Semaphore(1)` per provider 控制探测并发。
- `app/domain/provider_selector.py`:
  - 阶段一硬过滤:8 项检查全部实现(§7.4)
  - 阶段二软打分:5 个维度按 §13.5 weight 加权
  - 输出 `select(job) -> list[Provider]`(top_k 候选,空表示 NO_PROVIDER_AVAILABLE)
- 单元测试:
  - 全 OPEN → 候选为空
  - 余额 < threshold → 自动 DRAINED 后被剔除
  - cost 维度:1 元 vs 0.5 元,其他维度相同 → 0.5 元那个排第一
  - success_rate 维度:96% vs 80%,其他相同 → 96% 排第一
  - 5 次连续失败 → HEALTHY → OPEN,冷却到期 → HALF_OPEN
  - HALF_OPEN 期间最多 1 个并发探测请求

**验收**:
- [ ] 单元测试覆盖率 ≥ 85%
- [ ] 选择器在 200 个 provider 时 select 耗时 < 5ms
- [ ] 状态机退避:第二次 OPEN 冷却时间是第一次的 2 倍,封顶 `max_cooldown_seconds`
- [ ] DRAINED 状态在 admin topup 后自动转 HEALTHY

**依赖**: PR-04、PR-06

---

## PR-11: 4-lane WFQ 调度 + 执行器 + 软超惩罚

**目标**: 完成设计文档 §7.2、§7.3、§7.6、§15.2 的运行时主干。

**范围**:
- `app/domain/job_queue.py`:
  - `Lane`(tier, weight, deque, virtual_time as `Fraction`)
  - 4 个 lane 常驻内存
  - `enqueue(job)` / `peek_by_wfq() -> Lane | None` / `pop_from_lane(lane)`
  - `pick_min_vt_lane()`:tier 高的 tie-breaker;反饥饿 5%(在统计窗口内强制选低于 share 的 lane)
- `app/domain/job_scheduler.py`:
  - `run_forever(executor, lifecycle)`:主循环,WFQ pop → 派发到 worker
  - `drain(timeout=30)`:停止 enqueue,等 RUNNING 落地或超时
  - 每 10000 tick `normalize_virtual_times`
  - deadline 升级:Free 等 > 300s 升 Standard;升过的不再升(`flags.promoted=true`)
- `app/domain/job_executor.py`:
  - `Worker.execute(job)`:
    1. 软超惩罚(§7.3):turnstile 校验 → sleep → dice → 若失败回 lifecycle FAIL,不调上游
    2. 调 `provider_selector.select(job)`,得 top_k
    3. 顺序 try,捕异常调 `breaker.observe(p, success=False)` 与 `metrics_engine.record_call`
    4. 任意一次成功:落盘 → ledger.deduct → SSE → lifecycle SUCCEEDED
    5. 全部失败:lifecycle FAILED with reason=`ALL_PROVIDERS_FAILED`(扣配额)或 `NO_PROVIDER_AVAILABLE`(退配额)
  - 每 Worker 有 `provider concurrency lease`(对应 `providers.current_concurrency`)
- `app/domain/soft_penalty.py`:overage_ratio 计算 + delay/fail probability 函数。
- 在 `main.py::lifespan` 按 §15.2 顺序初始化全部单例,挂到 `app.state`。
- 集成测试(纯 Python,mock adapter):
  - 4 个 lane 同时灌入 job → 抽取顺序符合 8:4:2:1
  - 一个 provider 5 次失败 → OPEN → 后续 job 自动跳过
  - 全 provider 都失败 → ALL_PROVIDERS_FAILED 且扣配额
  - 候选为空 → NO_PROVIDER_AVAILABLE 且退配额
  - 软超 + delay=5 + fail_p=0.3,跑 100 个 job 验证统计接近预期

**验收**:
- [ ] 启动后调度循环 run,空队列时 CPU < 5%
- [ ] WFQ 在持续高负载下,Free lane 至少拿到 5%
- [ ] deadline 升级生效但只升一次
- [ ] 优雅停机:SIGTERM 后 30s 内全部 RUNNING 落地或转 FAILED reason=SERVER_RESTART(此项可在 PR-18 完善)
- [ ] 失败 fallback 在同一 Job 内最多 3 次

**依赖**: PR-09、PR-10

---

# 阶段四 · 用户态 API

## PR-12: SSE 中枢与长连接

**目标**: 完成设计文档 §8.5、§12 全部内容。把 PR-08 的 `BroadcastSink` 接上真实 SSE。

**范围**:
- `app/domain/sse_hub.py`:
  - `register(user_id, client_id, last_event_id) -> AsyncIterator[Event]`
  - `broadcast_to_user(user_id, event_kind, payload)`
  - 5 分钟内 in-memory event buffer(按 user_id key,按 event_id 重放)
  - 心跳协程,每 `SSE_HEARTBEAT_SECONDS` 发送 `: ping` + `event: heartbeat`
  - 单用户最多 `SSE_MAX_CONNECTIONS_PER_USER` 个连接,超出踢最老的
- `app/api/sse.py`:`GET /api/sse`,SSE response,带 `Cache-Control: no-cache`、`X-Accel-Buffering: no`
- `JobLifecycle.transition` 改为真实调用 `sse_hub.broadcast_to_user(user_id, "job_state", payload)`。
- 事件类型至少实现:`hello / heartbeat / job_state / job_progress / task_created / task_deleted / announcement / model_capabilities_changed / connection_warning`(payload 见 §8.5.3 / §9.5 / §11.2)。
- 集成测试:
  - 用 `httpx-sse` 客户端建连,收到 hello + 至少一个 heartbeat
  - 同一 user 5 个连接 → 第 5 个建立时第 1 个被关
  - 断连后带 `Last-Event-ID` 重连,buffer 内事件全部重放
  - lifecycle.transition QUEUED→RUNNING 触发 SSE event

**验收**:
- [ ] 100 个并发 SSE 连接稳定 1 小时无 OOM
- [ ] 重连漏掉的事件能重放(限 5min)
- [ ] 单用户连接数上限生效
- [ ] 心跳穿透常见反向代理

**依赖**: PR-11

---

## PR-13: Models API + 任务创建/取消

**目标**: 完成设计文档 §5、§6、§7.1 闸门接入,把"用户提交一次任务"打通。

**范围**:
- `app/api/models.py`:
  - `GET /api/models`:对当前用户可用的 model + 该用户 tier 下可用 provider 的 capabilities **求并集**(§4.3、§5.4、§6.2)
  - `available_reason` 三种:`all_providers_circuit_open` / `no_provider_for_tier` / `no_capable_provider`
- `app/api/jobs.py`:
  - `POST /api/jobs/precheck` — 见 §6.3
  - `POST /api/jobs`(multipart) — 见 §6.4 完整流程:
    - access_policy → 配额计入 → 校验参数 → 校验 captcha → 保存 refs → 写 jobs 表 → enqueue → 返回 200
    - 返回后**异步**广播 SSE `task_created`
    - `client_request_id` 幂等:同 user 同 id 在 5min 内重复提交 → 返回上次的结果不重复入队
  - `POST /api/jobs/<hash_id>/cancel` — 仅 QUEUED/RUNNING 可取消;QUEUED 直接出队;RUNNING 标志 worker 抢占点,真实中断到下一次 await sleep 时;取消退配额
  - `DELETE /api/jobs/<hash_id>` — 软删除(`status='DELETED'`),广播 `task_deleted`,**不**物理删 outputs(归 cleanup 管)
- 参数兼容性校验(§5.4)封装为 `validators.normalized_request(req, effective_capabilities)`。
- 集成测试:
  - 提交一个任务,SSE 立刻收到 task_created;mock provider 成功 → SSE 收到 job_state SUCCEEDED
  - 软超后 precheck 强制 captcha;不带 captcha_token 提交 → 412 CAPTCHA_REQUIRED
  - 给 gpt-image-2 传 `n=20` → 422 INVALID_PARAMETER
  - 同一 client_request_id 提交两次 → 第二次返回第一次的 hash_id

**验收**:
- [ ] `/api/models` 输出与设计文档 §6.2 示例形态一致
- [ ] temperature 字段不出现在任何响应里
- [ ] precheck/jobs 全流程(login → precheck → captcha → jobs → SSE)端到端可跑
- [ ] 4 种 429/403/412 文案对应正确
- [ ] 取消已 RUNNING 任务,worker 在下一个抢占点退出,不调上游

**依赖**: PR-12

---

## PR-14: Archive 同步 API + 图片读取

**目标**: 完成设计文档 §8.6、§9 全部内容。

**范围**:
- `app/api/archive.py`:
  - `GET /api/jobs/<hash_id>` — 详情(§8.6),user 视角不含 provider_used/cost_cny/retries
  - `GET /api/jobs/index?since=&limit=&cursor=` — 列表元数据
  - `POST /api/jobs/details`(批量,最多 50)
  - `POST /api/jobs/states`(批量,最多 100)
  - `GET /api/jobs/<hash_id>/images/<order>/thumb` — webp,带 `Cache-Control: public, max-age=2592000, immutable`、ETag、`If-None-Match` 304
  - `GET /api/jobs/<hash_id>/images/<order>/original` — 原图,`Content-Disposition: attachment`,流式响应,不读入内存
  - `GET /api/jobs/<hash_id>/refs/<order>/thumb`
  - `POST /api/jobs/<hash_id>/images/<order>/star` — 收藏切换
- 严格鉴权:**只能读自己的 job**(admin 例外,在 PR-15 接入)。所有图片读取路径都经 `image_io.path_for_job` 校验。
- 集成测试:
  - 创建一个 SUCCEEDED 任务,`/api/jobs/<hash>` 返回完整详情
  - 读别人的 hash_id → 404(不区分是否存在)
  - 缩略图 ETag/304 工作正常
  - `/index?since=<future>` 返回空

**验收**:
- [ ] 缩略图首请求 200 带 ETag,二次请求 304
- [ ] 跨用户读图返回 404
- [ ] 原图下载流式,大文件不爆内存(本地 50MB 文件可下载)
- [ ] index 分页 cursor 自洽

**依赖**: PR-13

---

# 阶段五 · 管理端

## PR-15: Admin 用户管理(含影子登录、批量)

**目标**: 完成设计文档 §13.2 全部内容。

**范围**:
- `app/api/admin/users.py`:
  - `GET /api/admin/users?q=&tier=&status=&sort=&page=&page_size=`
  - `POST /api/admin/users`
  - `GET /api/admin/users/<id>` — 含近 30d 每日聚合(从 `jobs` + `images` 算)
  - `PATCH /api/admin/users/<id>`
  - `DELETE /api/admin/users/<id>`(软删)
  - `POST /api/admin/users/<id>/disable` / `/enable`
  - `POST /api/admin/users/<id>/reset-password`
  - `POST /api/admin/users/<id>/impersonate` — 签发 30 分钟 token,token 中带 `impersonator: <admin_id>`,所有用 impersonate token 的写操作 audit_log 仍记 admin
  - `POST /api/admin/users/bulk` — 批量 patch
  - `GET /api/admin/users/<id>/jobs` — admin 视角任务列表(含 provider_used / cost_cny / retries / error)
- audit_log 记录所有写操作 + impersonate。
- 集成测试覆盖:列表过滤、impersonate token 能调通用户接口、批量改 tier 一次更新 100 个。

**验收**:
- [ ] 30d 用量聚合性能在 1k 用户下 < 200ms(必要时加临时聚合表)
- [ ] impersonate token 的写操作 audit_log actor=admin
- [ ] 软删除后 username 加后缀,可重用原 username 注册新账号

**依赖**: PR-04

---

## PR-16: Admin Provider/Adapter 高级运维 + 清理

**目标**: 完成设计文档 §13.4 高级运维、§13.7、§13.8 全部内容。

**范围**:
- 在 PR-06 的基础上扩展 `app/api/admin/providers.py`:
  - `POST /api/admin/providers/<id>/test` — 用预设 3 个 prompt(小图、大图、含中文)实测调一次,**不计 ledger**,实时返回结果
  - `POST /api/admin/providers/<id>/reset-circuit` — 强制 circuit_state=healthy + 清 cooldown_until
  - 列表里返回 `metrics_5min`(从 `MetricsEngine` 拉)
- `app/api/admin/cleanup.py`:
  - `GET /api/admin/cleanup/suggestions` — 读 `disk_usage` 表,组装成 §13.7 形态
  - `POST /api/admin/cleanup` `{rules, dry_run}` — dry_run=true 只返回数量与字节;dry_run=false 创建一个 cleanup_task(内存 + DB),异步执行 → 先 DB 标 DELETED → 异步 `rm -rf`,广播 SSE `task_deleted`
  - `GET /api/admin/cleanup/<task_id>` — 进度
- `CacheKeeper.run_forever()`:每 5min 刷 `disk_usage`;每天检查并提示磁盘告警(可选)。
- `GET /api/admin/sse` 推送 `provider_state` / `provider_metrics` / `worker_pool_state` / `cleanup_progress`(实现要点:admin sse hub 与用户 sse hub 分开两个 channel,共享心跳/重连机制)。
- `GET /api/admin/sse/clients` — 当前在线 SSE 列表。

**验收**:
- [ ] test 接口能跑通真实 provider(本地 mock 或真实 key)
- [ ] cleanup dry_run 不动文件
- [ ] cleanup 执行时 starred=true 的图片豁免(如果团队决定豁免,见原文档 §20 问题 1;默认豁免)
- [ ] disk_usage 5min 增量刷新

**依赖**: PR-11、PR-15

---

## PR-17: Admin 公告 + 指标 + 审计 + 紧急开关

**目标**: 完成设计文档 §11、§13.6、§13.9、§13.10。

**范围**:
- `app/api/admin/announcements.py`:
  - `GET /api/admin/announcements` — 列表
  - `POST /api/admin/announcements`(multipart,可带 `cover` 文件) — content_kind 仅支持 `text` 与 `image`(react 留 v1.1)
  - `PATCH /api/admin/announcements/<id>` / `DELETE`
- `app/api/announcements.py`(用户态):
  - `GET /api/announcements/active`
  - `POST /api/announcements/<id>/read`
  - `GET /api/announcements/<id>/cover`
- `app/domain/announcement_bus.py`:`publish(ann)`:按 audience_kind 解析受众 → 对每个用户 SSE broadcast `announcement` event。
- `app/api/admin/metrics.py`:
  - `GET /api/admin/metrics/overview` — 见 §13.9
  - `GET /api/admin/metrics/timeseries?metric=&range=&bucket=&provider_id=&model=`
- `app/api/admin/audit.py`:`GET /api/admin/audit?actor=&action=&since=&until=&page=`
- 紧急开关:确保 `emergency.*` 4 个 key 的 PATCH 在 admin/config 里前端展示成"大开关 + 二次确认"(后端无需关心,前端 PR);后端确保 4 个开关被 access_policy 与 deps 实际尊重(重申 PR-09 已做)。
- 集成测试:发一条 audience=tier=[free, standard] 的公告 → 仅这两档用户的 SSE 收到 announcement event;read 后下次 active 不再返回。

**验收**:
- [ ] 公告 read 持久化,SSE 重连不会重发已读
- [ ] metrics/overview 在 1 万 jobs 数据下 < 200ms(必要时缓存 30s)
- [ ] audit 列表分页 + 过滤
- [ ] 4 个紧急开关全部影响 access path

**依赖**: PR-12、PR-15

---

# 阶段六 · 收尾

## PR-18: 集成测试、优雅停机、上线 Checklist

**目标**: 设计文档 §19 的 Checklist 全部跑通;补齐 SIGTERM 处理与 README。

**范围**:
- 端到端测试套件 `backend/tests/e2e/`:
  - 注册/登录/captcha 全链路
  - 4 lane 反饥饿(VIP 持续灌入 + Free 仍 ≥ 5%)
  - 单 provider 故障 fallback
  - 全 provider 故障 → NO_PROVIDER_AVAILABLE 不扣配额
  - 软超 → Turnstile + delay + 概率失败统计
  - 跨设备 SSE:模拟 2 个 SSE 客户端,A 提交 B 收到
  - 紧急封控四开关
  - 清理 dry_run vs 真删
  - admin 全功能 happy path
- SIGTERM 处理:正在 RUNNING 的 Job 转 FAILED reason=`SERVER_RESTART` 并退配额;调度器 drain;SSE 优雅断开。
- 性能基线脚本:
  - 100 SSE 并发持续 1h(OOM 监控)
  - 100 QUEUED 调度延迟测量
  - 缩略图生成单张 ≤ 200ms
- `backend/README.md`:目录说明、env 列表、本地开发命令、常见排错。
- 部署文档 `docs/DEPLOY.md`:bind-mount、备份(数据库 + data/jobs)、rollback。
- 把设计文档 §19 上线 Checklist 转成 GitHub PR 模板里的勾选列表。

**验收**:
- [ ] §19 全部 checklist 项有对应自动化测试或人工验收记录
- [ ] CI 通过(lint + 单测 + e2e)
- [ ] SIGTERM 30s 内退出,无僵尸 RUNNING
- [ ] README + DEPLOY 文档可独立看懂

**依赖**: PR-17

---

# 附录 A · 给 Claude Code 的提示词模板

每次让 Claude Code 做一个 PR,推荐这样组织上下文:

```
我现在要交付 [PR-XX: 标题]。

**项目背景**:
[这里贴整份"后端详细设计文档 v1.0"]

**这次 PR 的范围**:
[贴本切片方案中对应的 PR 段落]

**前置 PR 已合并**:
- PR-01 项目骨架
- PR-02 数据库 Schema
- ...

**要求**:
1. 严格按设计文档 §X.Y 实现,不要发明文档外的字段或行为
2. 每个新增的 module/function 都要有 type hint 和 docstring
3. 所有 admin/* 路由都过 Depends(get_current_admin)
4. 所有错误响应严格按设计文档 §17 的错误码格式
5. 单元测试覆盖率 ≥ 80%,核心算法路径 100% 覆盖
6. 不要删除/改动其他 PR 的代码,只在本 PR 范围内动
7. 提交前跑 ruff + mypy + pytest 全绿

请先读全部上下文,生成实施计划,我确认后再开始写代码。
```

---

# 附录 B · 切片中刻意没做的事

为了让每个 PR 单一职责,以下事项**故意推迟**或**刻意排除**:

| 事项 | 推迟到 | 理由 |
|---|---|---|
| Postgres / Redis 迁移 | v2 | v1 SQLite + 内存队列够用 |
| 第三方 SSO | v2 | 设计文档 §20 已明确不做 |
| 公告 react 渲染 | v1.1 | 安全风险大,先做 text + image |
| 前端代码改动 | 不在本切片范围 | 切片只切后端;前端契约变更见设计文档 §18 |
| 多语言错误文案 | v2 | code 字段稳定,文案前端 i18n |
| WebSocket 升级 | v2 | SSE 已满足 v1 |
| GPU 自托管模型 | 非本项目目标 | 仅做中转编排 |

---

# 附录 C · 关于"切片粒度"的几条提醒

- **PR-05 / PR-10 / PR-11 / PR-13 是 4 个最重的 PR**。如果 Claude Code 一次跑不完,把它们再切成 a/b 两半:
  - PR-05a:Adapter 抽象 + AdapterRegistry + openai_v1
  - PR-05b:gemini_v1beta + 单测
  - PR-10a:MetricsEngine + CircuitBreaker
  - PR-10b:ProviderSelector
  - PR-11a:JobQueue + JobScheduler(WFQ 算法)
  - PR-11b:JobExecutor + SoftPenalty + Fallback
  - PR-13a:GET /api/models + 参数校验
  - PR-13b:POST /api/jobs(含 multipart、refs、SSE)+ cancel/delete
- **PR-12 SSE Hub 不要拖到后期**。它是 lifecycle 的依赖。如果非要前置,可以把 PR-12 提到 PR-08 之前,让 lifecycle 一开始就用真实 SSE。本方案选择 lifecycle 先用 `BroadcastSink` 占位,是为了 PR-08 不阻塞,任意一种顺序都行。
- **不要让 Claude Code 自己拍 schema 决定**。每个 DB 改动都必须在切片描述里写清新增哪张表的哪个字段,否则它会按自己的理解发明字段。
- **跑测试是硬要求**。每个 PR 合并前 `pytest` 必须全绿,且本 PR 新增模块必须有测试。

---

文档版本:v1.0
