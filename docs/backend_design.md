# 文生图平台后端详细设计文档

> 版本：v1.0  
> 范围：后端（FastAPI + SQLite）所有功能的设计、数据结构、接口、与前端的契约。  
> 受众：实施开发者 / 后续维护者 / Code reviewer。

---

## 0. 文档约定

- **`@`** 前缀表示一个内部的 Python 服务/单例（例如 `@JobScheduler`）。
- **`POST /api/...`** 表示一个 HTTP 接口。
- **`SSE: <event_name>`** 表示一个服务端推送事件类型。
- **「等级」** 在本文中统一指 `tier`，含 `VIP / Premium / Standard / Free` 四档，详见 §3。
- **「中转站」、「Provider」、「上游」** 同义。
- **「任务（Job）」** 指用户一次 Generate 提交后产生的执行单元；当 `n>1` 时多张图共享同一个 Job，对应 archive 中的 **Set**；`n=1` 时也是一个 Job，仅含 1 张图。
- **「Set」** 与 Create 页的 "session" **不是同一个概念**：
  - **Set** = 一次 Generate 产出的多张图（output count ≥ 2 时形成）。Archive 列表把同一 Set 的图聚合显示。
  - **Session** = 用户跨多次 Create 主动绑定的"项目分组"。一个 Session 包含多个 Set。

---

## 1. 项目结构与部署

### 1.1 顶层目录

```
txt2img/
├── frontend/                  # 已存在，本文档不改其内容
├── backend/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py            # FastAPI app 入口，仅做 router 挂载与启动钩子
│   │   ├── config.py          # 环境变量 / 配置中心读取
│   │   ├── deps.py            # FastAPI 依赖（鉴权、当前用户、当前管理员等）
│   │   ├── db/
│   │   │   ├── __init__.py
│   │   │   ├── engine.py      # SQLAlchemy engine + sessionmaker
│   │   │   ├── models.py      # ORM 模型（与 §14 一一对应）
│   │   │   ├── migrations/    # alembic 迁移脚本
│   │   │   └── seed.py        # 首次启动写入默认 tier、bootstrap admin
│   │   ├── api/               # 路由层，按 domain 拆分，详见 §15
│   │   │   ├── auth.py
│   │   │   ├── jobs.py
│   │   │   ├── models.py      # /api/models 暴露给 Create 页
│   │   │   ├── archive.py
│   │   │   ├── sse.py
│   │   │   ├── admin/
│   │   │   │   ├── users.py
│   │   │   │   ├── tiers.py
│   │   │   │   ├── providers.py
│   │   │   │   ├── adapters.py
│   │   │   │   ├── cleanup.py
│   │   │   │   ├── announcements.py
│   │   │   │   ├── config.py
│   │   │   │   └── metrics.py
│   │   │   └── health.py
│   │   ├── domain/            # 业务逻辑（与传输层解耦）
│   │   │   ├── access_policy.py     # 鉴权 / 配额 / 紧急封控
│   │   │   ├── job_queue.py         # 4 lane WFQ
│   │   │   ├── job_scheduler.py     # 调度器主循环
│   │   │   ├── job_executor.py      # Worker 执行体
│   │   │   ├── job_lifecycle.py     # 状态机
│   │   │   ├── provider_selector.py # 两阶段筛选 + 打分
│   │   │   ├── provider_ledger.py   # 余额账本
│   │   │   ├── circuit_breaker.py   # Provider 熔断
│   │   │   ├── metrics_engine.py    # 5min 滑窗指标
│   │   │   ├── soft_penalty.py      # 软超惩罚（Turnstile/delay/fail）
│   │   │   ├── sse_hub.py           # SSE 多路推送中枢
│   │   │   ├── cache_keeper.py      # 任务清理 / TTL
│   │   │   └── announcement_bus.py  # 公告投放
│   │   ├── adapters/          # 中转站适配器，详见 §4.4
│   │   │   ├── __init__.py
│   │   │   ├── base.py        # 抽象基类 BaseAdapter
│   │   │   ├── openai_v1.py
│   │   │   ├── gemini_v1beta.py
│   │   │   └── ... 自定义适配器可放入此目录，启动时自动发现
│   │   ├── schemas/           # Pydantic 请求/响应模型
│   │   ├── services/          # IO 工具：图像处理、文件落盘
│   │   │   ├── image_io.py    # 原图保存 / 缩略图生成
│   │   │   └── turnstile.py   # Cloudflare Turnstile 校验
│   │   └── utils/
│   └── tests/
├── data/                       # 见 §1.2，挂载持久化
├── docker-compose.dev.yml      # 已存在
├── docker-compose.prod.yml     # 已存在
└── backend.env.example         # 已存在
```

### 1.2 持久化数据目录

宿主机 `./data/` bind-mount 到容器 `/app/data/`，所有需要在容器重建后保留的内容都落到这里。布局：

```
data/
├── txt2img.db                 # SQLite 主库（journal_mode=WAL）
├── txt2img.db-shm
├── txt2img.db-wal
├── jobs/
│   └── <hash_id>/             # 每个任务一个目录，hash_id 见 §8.6
│       ├── meta.json          # 入参快照（user_id, model, params, refs 列表、时间戳…）
│       ├── refs/              # 用户上传的参考图（若有）
│       │   ├── 01_<orig_name>.png
│       │   └── 02_<orig_name>.jpg
│       ├── outputs/
│       │   ├── 01_original.png
│       │   ├── 01_thumb.webp
│       │   ├── 02_original.png
│       │   └── 02_thumb.webp
│       ├── upstream/          # 上游原始返回，便于 debug
│       │   ├── attempt_1.json
│       │   └── attempt_2.json # fallback 时多次记录
│       └── timeline.jsonl     # 状态变迁流水（QUEUED→RUNNING→…）
├── announcements/
│   └── <ann_id>/              # 公告附件（图片）
│       └── cover.png
└── tmp/                       # 上传中转、压缩中间产物，定时清理
```

**为什么按任务分目录而不是按用户分**：清理策略以任务为粒度（按时间、按状态），按任务分目录可以一次 `rm -rf <hash_id>/` 全清；用户视角的聚合通过 DB 索引完成。

### 1.3 部署相关

- **Docker**：沿用现有 `docker-compose.prod.yml`，唯一新增 env 见 §1.5。
- **DB URL**：`sqlite:////app/data/txt2img.db` + `?journal_mode=WAL&synchronous=NORMAL`，并发读写够用。
- **首次启动**：`@db.seed.bootstrap()` 会创建：
  1. 默认 4 档 tier（数值见 §3.1）。
  2. 由 `ADMIN_USERNAME` / `ADMIN_PASSWORD` 创建 bootstrap admin。
  3. 默认 `config` 表条目（见 §13.5）。
- **优雅停机**：收到 `SIGTERM` → `@JobScheduler.drain()` 停止接受新 Job、等当前 RUNNING 全部落地或超时（30s）→ flush WAL → 退出。

### 1.4 进程模型

单进程多协程（uvicorn `--workers 1`，因为 SQLite + 内存队列不易跨 worker 共享）。如需横向扩展，未来可：
- DB 切 PostgreSQL；
- 队列切 Redis Streams；
- SSE 切 Redis Pub/Sub。

但 v1 不需要。

### 1.5 新增环境变量（追加到 `backend.env.example`）

```dotenv
# ===== 调度 =====
SCHEDULER_GLOBAL_MAX_WORKERS=32

# ===== Turnstile =====
TURNSTILE_SITE_KEY=0x4AAAAAAA...     # 公开
TURNSTILE_SECRET=0x4AAAAAAA...       # 服务端校验

# ===== 任务保留 =====
JOB_RETENTION_DAYS=30                # 默认 30 天后任务可被清理
THUMBNAIL_MAX_LONG_EDGE=720          # 缩略图长边
THUMBNAIL_QUALITY=78                 # webp 质量

# ===== SSE =====
SSE_HEARTBEAT_SECONDS=15             # 心跳，防中间代理断连
SSE_MAX_CONNECTIONS_PER_USER=4       # 同一用户最多并发 SSE
```

---

## 2. 鉴权

### 2.1 总原则

- **除以下两个端点外，所有 `/api/*` 都必须带 `Authorization: Bearer <token>`**：
  - `POST /api/auth/login`
  - `POST /api/auth/captcha-check`（登录前判断是否需要人机验证）
  - `GET /api/health`
- Token 类型：JWT，HS256，密钥来自 `JWT_SECRET`。Payload：
  ```json
  {
    "sub": "<user_id>",        // 用户主键
    "u":   "<username>",       // 仅用于审计日志，校验时不依赖
    "r":   "admin" | "user",   // 主分类
    "iat": 1714000000,
    "exp": 1714000000 + JWT_EXPIRES_DAYS * 86400
  }
  ```
- **Tier、配额、是否 admin** 这些敏感字段都从 DB 实时读，**不写入 JWT**。Token 越简洁越不容易被「伪造的过期信息」绕过策略。
- 日常会话用一个长期 token（默认 10 年，沿用现有 `JWT_EXPIRES_DAYS=3650`）足够。如果未来要做"踢下线"，新增一张 `revoked_tokens` 表即可。

### 2.2 FastAPI 依赖

`@deps.py` 提供：

```python
def get_current_user(...) -> User: ...           # 任意已登录用户
def get_current_admin(...) -> User: ...          # 必须 role == 'admin'，否则 403
def require_not_blocked(...) -> User: ...        # 叠加紧急封控开关，详见 §13.6
```

### 2.3 登录流程

```
POST /api/auth/captcha-check  { username }
        ↓
后端检查 (失败次数 ≥ 3) || FORCE_CAPTCHA → required:true
        ↓
前端弹 Turnstile（如果 required） → 获得 cf_token
        ↓
POST /api/auth/login  { username, password, captcha_token }
        ↓
- 校验密码（argon2id）
- 如有 captcha_token：调 Cloudflare siteverify
- 失败 → 写入 failed_login，返回 401
- 成功 → 清零失败计数，签发 JWT，返回
        ↓
{
  "access_token": "...",
  "user": {
    "id": "u_...",
    "username": "alice",
    "role": "admin" | "user",
    "display_name": "Alice"
    // 注意：不回传 tier / 配额。前端不需要、也不应该知道
  }
}
```

> 失败计数走表 `login_attempts(username, attempted_at)`，5 分钟滚动窗口内 ≥ 3 次失败即触发 captcha。

### 2.4 密码存储

argon2id（推荐参数 t=2, m=64MB, p=1）。已有的 admin bootstrap 在第一次启动时把 `ADMIN_PASSWORD` 哈希后写入 `users` 表。

---

## 3. 用户与权益等级（Tier）

### 3.1 默认 4 档 tier

| Tier | weight | concurrency | queue | soft/day | hard/day | SLO P95 |
|---|---|---|---|---|---|---|
| VIP      | 8 | 4 | 10 | 100 | 200 | 30s   |
| Premium  | 4 | 2 | 5  | 50  | 100 | 60s   |
| Standard | 2 | 1 | 3  | 20  | 40  | 180s  |
| Free     | 1 | 1 | 3  | 8   | 10  | null  |

> 全部数值都进入配置表 `tiers`（§14.2），admin 可热更新，无需重启。  
> SLO P95 仅用于告警，**不影响调度**。

### 3.2 用户主键与字段

```sql
-- 见 §14.1 的完整 DDL，这里只列关键字段
users(
  id              TEXT PRIMARY KEY,        -- 'u_' + nanoid(12)
  username        TEXT UNIQUE NOT NULL,
  password_hash   TEXT NOT NULL,
  role            TEXT NOT NULL,           -- 'admin' | 'user'
  tier            TEXT NOT NULL,           -- 'vip' | 'premium' | 'standard' | 'free'
  status          TEXT NOT NULL,           -- 'active' | 'disabled'
  display_name    TEXT,
  -- 当日用量（每日 00:00 UTC+8 由 cleanup job 重置；也可懒重置）
  today_count        INTEGER NOT NULL DEFAULT 0,
  today_reset_date   TEXT NOT NULL,        -- 'YYYY-MM-DD'，比较时若 != 今日则懒重置
  -- 用户级配额覆写（NULL 表示沿用 tier 默认）
  override_soft_quota INTEGER,
  override_hard_quota INTEGER,
  -- 用户级序号
  last_seq_no     INTEGER NOT NULL DEFAULT 0,  -- 见 §8.6
  created_at      TIMESTAMP NOT NULL,
  last_login_at   TIMESTAMP
);
```

### 3.3 前端可见字段

**前端不允许看到**：`tier / today_count / soft_quota / hard_quota / 是否触发软限`。这些字段不会出现在 `/api/auth/login` 与 `/api/me` 的响应里。

**前端允许看到**：`username / display_name / role`（用 `role==='admin'` 决定 admin 入口是否可见，见 §13.1）。

如果用户超过软/硬限，**只通过任务执行结果或 429 错误码间接得知**：
- 硬限：`POST /api/jobs` 返回 429 `HARD_QUOTA_EXCEEDED`，前端可显示通用文案 "Daily limit reached, try again tomorrow."
- 软限：用户感知到的是 Turnstile 弹窗 + 任务变慢 + 偶发的 `SOFT_QUOTA_PENALTY`，前端不解释为什么。

### 3.4 当日用量重置

- 时区：北京时间 UTC+8。
- 重置点：每日 00:00:00 +08:00。
- 实现：**懒重置**优先（每次写 `today_count` 前对比 `today_reset_date`，不同就先清零再 +1）；额外有一个每分钟的兜底任务把所有跨日用户都对齐。

---

## 4. 中转站接入系统（Provider）

### 4.1 数据模型

```sql
providers(
  id                TEXT PRIMARY KEY,       -- 用户传入的 'provider_id'，如 'bltcy'
  label             TEXT NOT NULL,          -- 显示名 'BLTCY'
  adapter_type      TEXT NOT NULL,          -- 适配器键，见 §4.4
  base_url          TEXT NOT NULL,
  api_key_enc       TEXT NOT NULL,          -- AES-GCM 加密后的 base64
  cost_per_image_cny REAL NOT NULL,         -- 计费单价
  initial_balance_cny REAL NOT NULL,        -- 充值时设定的余额
  balance_cny       REAL NOT NULL,          -- 当前余额（每次成功调用扣减）
  enabled           INTEGER NOT NULL DEFAULT 1,
  note              TEXT,
  max_concurrency   INTEGER NOT NULL DEFAULT 10,
  rpm_limit         INTEGER NOT NULL DEFAULT 600,
  -- 运行时状态（也可拆到独立表，但单表更便于 JSON 投影）
  circuit_state     TEXT NOT NULL DEFAULT 'healthy',  -- healthy / open / half_open / drained / disabled
  cooldown_until    TIMESTAMP,
  recent_calls_json TEXT,                   -- 近 5min 调用环形 buffer 的 JSON 序列化
  created_at        TIMESTAMP NOT NULL,
  updated_at        TIMESTAMP NOT NULL
);

provider_models(
  provider_id   TEXT NOT NULL,
  model_id      TEXT NOT NULL,              -- 'gpt-image-2' / 'gemini-3-pro-image-preview' …
  -- 该 provider 在跑这个 model 时的能力裁剪：见 §4.3
  capabilities_json TEXT NOT NULL,
  enabled       INTEGER NOT NULL DEFAULT 1,
  PRIMARY KEY (provider_id, model_id),
  FOREIGN KEY (provider_id) REFERENCES providers(id) ON DELETE CASCADE
);

provider_tier_access(
  provider_id   TEXT NOT NULL,
  tier          TEXT NOT NULL,              -- 'vip'/'premium'/'standard'/'free'
  PRIMARY KEY (provider_id, tier)
);

-- 同一 (provider, model, tier) 的更细粒度白名单（可选，未配置时回退到 provider_tier_access）
provider_model_tier_access(
  provider_id   TEXT NOT NULL,
  model_id      TEXT NOT NULL,
  tier          TEXT NOT NULL,
  PRIMARY KEY (provider_id, model_id, tier)
);
```

> 加密：`api_key_enc` 用 `JWT_SECRET` 派生的 AES-GCM key 加密。备份 `data/txt2img.db` 时 key 不会明文外泄。

### 4.2 admin 创建 provider 的 payload

`POST /api/admin/providers`，body：

```json
{
  "provider_id": "bltcy",
  "label": "BLTCY",
  "adapter_type": "gemini_v1beta",
  "base_url": "https://api.bltcy.ai/v1beta",
  "api_key": "sk-xxxx",
  "cost_per_image_cny": 0.1,
  "initial_balance_cny": 3.88,
  "enabled": true,
  "note": "official-style gemini v1beta upstream",
  "max_concurrency": 70,
  "rpm_limit": 600,
  "supported_models": [
    {
      "model_id": "gemini-2.5-flash-image",
      "capabilities": { /* 见 §4.3 */ }
    },
    {
      "model_id": "gemini-3-pro-image-preview",
      "capabilities": { /* 见 §4.3 */ }
    }
  ],
  "tier_access": ["vip", "premium", "standard"]   // free 不能用此 provider
}
```

### 4.3 Per-(provider, model) 能力裁剪

每个 provider 即使支持某个 model，也可能因为自身实现限制无法透传所有参数。`capabilities_json` 用统一 schema 描述：

```jsonc
{
  // 用户可调的离散参数：列出"该 provider 在这个 model 下允许的取值"。
  // 数组为空 = 该参数不允许用户传（前端不渲染）。null = 不限制（沿用模型本身的全集）。
  "size":            ["1024x1024", "1536x1024", "1024x1536"],   // gpt-image-2 only
  "aspect_ratio":    ["1:1","2:3","3:2","3:4","4:3","16:9","9:16","21:9"],
  "image_size":      ["1K","2K","4K"],                          // gemini only
  "quality":         ["low","medium","high","auto"],            // gpt-image-2 only
  "output_format":   ["png","jpeg","webp"],
  "background":      ["auto","opaque"],
  "moderation":      ["auto","low"],
  "thinking_level":  ["minimal","high"],                        // 3.1-flash only
  // 数值/布尔参数：声明上限/默认。null 表示禁用该参数。
  "n_max":             10,            // gpt-image-2 单次最多张数；gemini 系列设为 1
  "include_thoughts":  true,          // 是否允许用户开启
  "google_search":     false,         // 是否允许调用 google_search 工具
  "image_search":      false,         // 是否允许 imageSearch
  "stream":            true,
  "partial_images_max": 3,
  "max_reference_images": 14,
  "supports_transparent_bg": false,
  "supports_mask":     false,         // gpt-image-2 edits 端点
  // 其它 quirk
  "max_prompt_chars":  32000,
  "extra_notes":       "BLTCY 不透传 thinkingLevel"
}
```

**前端 Create 页**通过 `GET /api/models` 拉到的 schema 就是「所有可用 provider 在该用户 tier 下的 capabilities **求并集**」（见 §6.2）。

### 4.4 适配器（Adapter）

#### 4.4.1 抽象基类

```python
# backend/app/adapters/base.py

class BaseAdapter(ABC):
    adapter_type: ClassVar[str]              # 'openai_v1' / 'gemini_v1beta' / ...
    display_name: ClassVar[str]              # 给 admin 看的名字
    description: ClassVar[str]               # 给 admin 看的描述

    @abstractmethod
    async def generate(
        self,
        provider: ProviderConfig,
        request: NormalizedRequest,
    ) -> NormalizedResponse:
        """发起一次图像生成，返回原始字节 + 计费信息。"""

    @abstractmethod
    def supported_models(self) -> list[str]:
        """这个适配器**理论上**能跑哪些 model id（白名单）。"""

    def normalize_error(self, exc: Exception) -> StandardError:
        """把上游错误标准化为 RATE_LIMITED / AUTH / 5XX 等。"""
```

#### 4.4.2 自动发现

启动时扫描 `backend/app/adapters/*.py`，凡是 `BaseAdapter` 的非抽象子类都注册到 `@AdapterRegistry`。Admin 端 `GET /api/admin/adapters` 会列出所有已注册适配器与各自支持的 `model_id` 集合，并显示在 admin 创建/编辑 provider 时的 `adapter_type` 下拉框中。

#### 4.4.3 内置两个适配器（v1 必须实现）

| adapter_type | 用于 | 关键映射 |
|---|---|---|
| `openai_v1` | OpenAI 官方风格 / `gpt-image-2` | `POST {base_url}/images/generations` |
| `gemini_v1beta` | Google 官方风格 / Gemini 系列 | `POST {base_url}/models/{model}:generateContent` |

#### 4.4.4 自定义适配器接入流程

1. 在 `backend/app/adapters/` 下新建 `myrelay_v3.py`，继承 `BaseAdapter`，设 `adapter_type = "myrelay_v3"`。
2. 重启服务，`@AdapterRegistry` 自动收录。
3. Admin 在前端添加 provider 时下拉选 `myrelay_v3`。

### 4.5 余额账本

任何**成功**返回图像的请求都触发：

```python
provider.balance_cny -= provider.cost_per_image_cny * n_images_returned
billing_ledger.insert(job_id, provider_id, cost_cny, deducted_at=now())
```

失败请求**不扣**。  
当 `balance_cny < provider_filter.balance_min_threshold`（见 §13.5），状态机进入 `DRAINED`，停止参与调度直到 admin 充值（admin 只能改 `balance_cny`，不能改历史 ledger）。

---

## 5. 文生图模型与参数适配

### 5.1 三个模型的"能力上限"（求全集）

> 这是后端能力上限，**实际允许用户传什么参数还要看 (provider, model) 的 capabilities 裁剪**（§4.3）。

| 参数 | gpt-image-2 | gemini-3-pro-image-preview | gemini-3.1-flash-image-preview | 后端统一字段名 |
|---|---|---|---|---|
| `prompt` | ✅ ≤ 32000 字符 | ✅ | ✅ | `prompt` |
| `n`（一次几张） | 1–10 | 1（强制） | 1（强制） | `n` |
| `size` 离散 | `1024x1024`/`1536x1024`/`1024x1536`/`auto` | — | — | `size` |
| `aspect_ratio` | — | 10 种 | 14 种（含 `1:4`/`4:1`/`1:8`/`8:1`） | `aspect_ratio` |
| `image_size` | —（用 size） | `1K`/`2K`/`4K` | `512`/`1K`/`2K`/`4K` | `image_size` |
| `quality` | `low`/`medium`/`high`/`auto` | — | — | `quality` |
| `output_format` | `png`/`jpeg`/`webp` | png（固定） | png（固定） | `output_format` |
| `output_compression` | 0–100（仅 jpeg/webp） | — | — | `output_compression` |
| `background` | `auto`/`opaque`（**无 transparent**） | 不可配 | 不可配 | `background` |
| `moderation` | `auto`/`low` | — | — | `moderation` |
| `stream` | ✅ | ❌ | ❌ | `stream` |
| `partial_images` | 0–3 | — | — | `partial_images` |
| `thinking_level` | — | 强制开启不可关 | `minimal`/`high` | `thinking_level` |
| `include_thoughts` | — | — | true/false | `include_thoughts` |
| `google_search` | ❌ | ✅ | ✅ | `google_search` |
| `image_search` | ❌ | ❌ | ✅ | `image_search` |
| 参考图（refs） | edits 端点多文件 | ≤ 14 | ≤ 14 | `references[]` |
| mask | ✅ edits 端点 | 描述区域代替 | 描述区域代替 | `mask` |

### 5.2 后端"统一请求体" → 适配器映射

前端永远只传统一字段（`NormalizedRequest`）。各 adapter 内部把它翻译成上游格式：

```jsonc
// NormalizedRequest（前端 → 后端 → adapter 入口）
{
  "model": "gemini-3.1-flash-image-preview",
  "prompt": "…",
  "n": 1,
  "aspect_ratio": "16:9",
  "image_size": "2K",
  "thinking_level": "high",
  "include_thoughts": false,
  "google_search": false,
  "image_search": false,
  "references": [
    { "order": 1, "mime": "image/png", "data_b64": "..." },
    { "order": 2, "mime": "image/jpeg", "data_b64": "..." }
  ],
  "mask": null,                  // gpt-image-2 edits 才用
  "stream": false,
  "partial_images": 0,
  "output_format": "png",
  "output_compression": null,
  "background": "auto",
  "moderation": "auto",
  "quality": "auto",
  "size": null
}
```

**关键约束**：`references` 必须是数组，**严格按顺序**透传给上游。adapter 实现里禁止用 dict / set 等无序结构存储引用图。  
`gemini_v1beta` 适配器拼装 `contents.parts` 时按 `order` 升序遍历。  
`openai_v1` 适配器在 `/images/edits` 多文件上传时按 `order` 升序构造 multipart 字段。

### 5.3 每个模型在前端 Create 页的"默认值"

> 现状：前端 Create 页（`frontend/src/pages/CreatePage.jsx`）的 `count=4` / `temp=0.7` / `thinking="mid"` / `aspect="1:1"` / `size="1K"` 是 mockup 数据，与真实 API 对不齐。下面定义每个模型的真实默认。后端 `/api/models` 会把这些下发给前端，前端切换 model 时直接采纳。

| 字段 | gpt-image-2 默认 | gemini-3-pro 默认 | gemini-3.1-flash 默认 |
|---|---|---|---|
| `n` | 4 | 1（锁死） | 1（锁死） |
| `size` | `auto` | — | — |
| `aspect_ratio` | —（用 size） | `1:1` | `1:1` |
| `image_size` | — | `1K` | `1K` |
| `quality` | `auto` | — | — |
| `output_format` | `png` | png | png |
| `background` | `auto` | — | — |
| `moderation` | `auto` | — | — |
| `thinking_level` | — | —（不可配） | `minimal` |
| `include_thoughts` | — | — | `false` |
| `google_search` | — | `false` | `false` |
| `image_search` | — | — | `false` |
| `temperature` | **删除** | **删除** | **删除** |

> **`temperature` 不应出现在前端**：三个模型都没有 temperature 参数。前端现有的 temperature slider 应在用 `/api/models` 渲染时被隐藏（这是 §6.2 的逻辑应有的副作用：能力描述里没有的参数前端不渲染）。

### 5.4 统一参数"是否允许此次提交"的校验

后端 `POST /api/jobs` 在入队前做：

```
1. 检查 model 是否存在且对该用户 tier 开放（至少 1 个 provider 同时开放）
2. 用 effective capabilities = ∪ (provider_models.capabilities for provider where enabled & 该用户 tier 可用)
3. 校验请求中每个字段是否在 effective capabilities 内
4. 不通过 → 422 INVALID_PARAMETER  { field: "thinking_level", reason: "not_supported" }
```

---

## 6. Create 页面流程

### 6.1 概览

```
进入 /create
   ↓
GET /api/models                ← 拉取「我能用什么」
   ↓
渲染 model 列表 + 每个 model 当前可调的参数
   ↓
用户填 prompt / refs / 参数
   ↓
点击 Generate
   ↓
POST /api/jobs/precheck         ← 可选，但默认开启：判断是否需要 captcha
   ↓
若 required → 弹 Turnstile → 拿到 cf_token
   ↓
POST /api/jobs                  ← 实际创建任务
   ↓
拿到 { hash_id, seq_no, status: "QUEUED", position, eta_seconds, set_id? }
   ↓
路由跳转 /archive，archive 立刻渲染该任务（不等 SSE 推第一帧）
   ↓
SSE 推送状态变化，直到 SUCCEEDED / FAILED / CANCELLED
```

### 6.2 `GET /api/models`

#### 请求

需要登录。无 query param（按当前用户 tier 自动过滤）。

#### 响应

```jsonc
{
  "models": [
    {
      "model_id": "gpt-image-2",
      "display_name": "ChatGPT Images 2.0",
      "tag": "RECOMMENDED",            // 给前端做角标用
      "logo": "chatgpt",               // 与前端 ModelLogo kind 对齐
      "blurb": "Best for editorial, photoreal, brand. Strong text & composition control.",
      "available": true,               // false 时灰显但不允许选
      "available_reason": null,        // 不可用时给 'all_providers_circuit_open' / 'no_provider_for_tier' / ...
      // 当前 tier 下所有可用 provider 的能力并集（见 §5.4）
      "capabilities": {
        "n_max": 10,
        "size": ["1024x1024","1536x1024","1024x1536","auto"],
        "quality": ["low","medium","high","auto"],
        "output_format": ["png","jpeg","webp"],
        "background": ["auto","opaque"],
        "moderation": ["auto","low"],
        "stream": true,
        "partial_images_max": 3,
        "max_reference_images": 16,
        "supports_mask": true,
        "max_prompt_chars": 32000
      },
      // 默认值（§5.3）
      "defaults": {
        "n": 4,
        "size": "auto",
        "quality": "auto",
        "output_format": "png",
        "background": "auto",
        "moderation": "auto"
      }
    },
    {
      "model_id": "gemini-3.1-flash-image-preview",
      "display_name": "Gemini 3.1 Flash (Nano Banana 2)",
      "tag": "FAST",
      "logo": "flash",
      "available": true,
      "capabilities": {
        "n_max": 1,
        "aspect_ratio": ["1:1","2:3","3:2","3:4","4:3","4:5","5:4","9:16","16:9","21:9","1:4","4:1","1:8","8:1"],
        "image_size": ["512","1K","2K","4K"],
        "thinking_level": ["minimal","high"],
        "include_thoughts": true,
        "google_search": true,
        "image_search": true,
        "max_reference_images": 14
      },
      "defaults": {
        "n": 1,
        "aspect_ratio": "1:1",
        "image_size": "1K",
        "thinking_level": "minimal",
        "include_thoughts": false,
        "google_search": false,
        "image_search": false
      }
    },
    {
      "model_id": "gemini-3-pro-image-preview",
      "display_name": "Gemini 3 Pro (Nano Banana Pro)",
      "tag": "QUALITY",
      "logo": "flash",
      "available": false,
      "available_reason": "all_providers_circuit_open",
      "capabilities": { /* 同上 omit */ },
      "defaults": { /* 同上 omit */ }
    }
  ],
  // 用户级别的 session 列表，对应 Create 页的 "Bind to session"
  "sessions": [
    { "id": "sess_…", "name": "Editorial Cover", "image_count": 24, "updated_at": "..." }
  ]
}
```

#### 触发时机

- 进入 `/create` 时拉一次。
- 用户在 `/create` 待超过 60 秒、且尝试 Generate 之前**重新拉一次**（避免熔断状态切换后参数过期）。
- 收到 SSE `model_capabilities_changed` 事件时（admin 改了 provider/model 能力，详见 §11.2）。

#### 前端渲染规则

- `capabilities[k]` 是数组 → 渲染该字段的 chip group / select。
- `capabilities[k]` 是 `false` 或 `undefined` → 不渲染该字段。
- `capabilities[k]` 是数字（如 `n_max`）→ 渲染数字 input / chip group，上限即此值。
- `available: false` 的模型卡仍渲染但置灰，hover 提示 `available_reason`。

### 6.3 `POST /api/jobs/precheck`

> 用户点击 Generate 后**立刻**调用。极轻量，只做 captcha 判定。

#### 请求

```json
{ "model": "gemini-3.1-flash-image-preview" }
```

#### 响应

```json
{
  "captcha_required": true,
  "captcha_provider": "turnstile",
  "site_key": "0x4AAAAAAAxxxxxx",
  "reason": "soft_quota_exceeded"   // 或 'rate_limit_pre_warn' / 'forced_by_admin' / null
}
```

判定逻辑：
1. 用户当日用量 ≥ soft_quota → 强制 captcha。
2. 用户最近 60 秒提交 ≥ 5 次 → 强制 captcha。
3. 紧急封控开关 `force_captcha_global` 打开 → 强制。
4. 否则 false。

#### 前端动作

- `captcha_required: false` → 直接 `POST /api/jobs`。
- `captcha_required: true` → 弹 `TurnstileModal` → 拿到 `cf_token` → `POST /api/jobs` 时把 `captcha_token` 一起带上。

### 6.4 `POST /api/jobs`

#### 请求

`multipart/form-data`，因为可能上传参考图：

```
Content-Type: multipart/form-data; boundary=...

--xxx
Content-Disposition: form-data; name="payload"
Content-Type: application/json

{
  "model": "gemini-3.1-flash-image-preview",
  "prompt": "A still-life of three overripe bananas...",
  "n": 1,
  "aspect_ratio": "16:9",
  "image_size": "2K",
  "thinking_level": "high",
  "include_thoughts": false,
  "google_search": false,
  "image_search": false,
  "session_id": "sess_…" | null,
  "captcha_token": "0.4Bx..." | null,
  "client_request_id": "req_xyz"   // 客户端去重幂等
}

--xxx
Content-Disposition: form-data; name="ref_0"; filename="banana_01.jpg"
Content-Type: image/jpeg

<bytes>

--xxx
Content-Disposition: form-data; name="ref_1"; filename="leaf.png"
Content-Type: image/png

<bytes>
```

> `ref_N` 后缀的数字 = 该图在 prompt 中的次序。adapter 严格按数字升序透传给上游。

#### 服务端处理顺序

1. 鉴权 → 拿到 user。
2. 紧急封控检查（§13.6）→ 不通过 403。
3. **配额闸**（§7.1）：硬限 → 429；软限 → 加 flag 不阻塞；容量满 → 429。
4. 参数校验（§5.4）→ 不通过 422。
5. captcha 校验：
   - precheck 说要 captcha → 必须有 `captcha_token` 且 verify 通过；不通过 → 412 `CAPTCHA_INVALID`。
6. 保存参考图到 `data/jobs/<hash_id>/refs/`（顺序文件名带前导零，如 `01_banana_01.jpg`）。
7. 写入 `jobs` 表，状态 `QUEUED`，分配 `seq_no = users.last_seq_no + 1`，更新 `users.last_seq_no`。
8. 入对应 tier lane（§7.2）。
9. 返回：

```json
{
  "hash_id": "j_a1b2c3d4e5f6",
  "seq_no": 1428,
  "model": "gemini-3.1-flash-image-preview",
  "status": "QUEUED",
  "position": 3,
  "estimated_wait_seconds": 28,
  "queued_at": "2026-04-27T11:50:32+08:00",
  "set_id": null            // n>1 时填 'set_…'，n=1 时 null
}
```

> 后端把响应**先返回**给前端，**再去通过 SSE hub 广播 `task_created` 给该用户的所有 SSE 客户端**（避免提交端等待广播完成）。

### 6.5 参考图顺序保证（强约束）

| 阶段 | 顺序保持机制 |
|---|---|
| 浏览器上传 | 前端按用户拖拽顺序构造 `ref_0`、`ref_1`… 的字段名 |
| FastAPI 接收 | `Form` + `UploadFile` 按字段名升序，写入 `data/jobs/<hash>/refs/01_*.ext`、`02_*.ext`… |
| 数据库 | `job_references(job_id, order INT, file_path)` 按 `order` 主键 |
| Adapter 调用 | `for ref in sorted(job.references, key=lambda r: r.order)` |
| 上游响应里的 thought_signature | 按相同顺序回填 |

### 6.6 Session（图片组）

- Session 是用户级的标签集合，只起聚合作用。
- DB 表 `sessions(id, user_id, name, created_at, updated_at)`。
- DB 表 `session_jobs(session_id, job_id)`。
- Create 页 `Bind to session` 选中后，请求里带 `session_id`。后端写入关联表。
- Archive 页可按 session 过滤展示（前端实现，后端只在 `GET /api/jobs` 加上 `?session_id=` query 参数）。
- **Session 与 Set 不冲突**：一个 Set（多张图的同一个 Job）整体被绑入一个 session。

---

## 7. 调度系统

> 本节是对上传文档《调度系统设计.md》的工程化落地。所有阈值进 §13.5 的配置中心，admin 可热更新。

### 7.1 接入闸门（与 §6.4 重叠的部分省略）

```
判断顺序：
  1. require_not_blocked()
  2. quota_guard.check_hard_quota_exceeded()  → 429 HARD_QUOTA_EXCEEDED
  3. quota_guard.check_soft_quota_exceeded()  → 不阻塞，但给 Job 打 flag SOFT_QUOTA_EXCEEDED
  4. queue_guard.check_user_busy()             → 429 USER_BUSY
  5. ok → 入队
```

四种 429 区分：

| HTTP code 字段 | 文案建议（前端）|
|---|---|
| `HARD_QUOTA_EXCEEDED` | "Daily limit reached. Try again tomorrow." |
| `USER_BUSY` | "You have too many jobs in flight. Wait for some to finish." |
| `RATE_LIMITED` | "Too fast. Slow down a bit." |
| `BLOCKED_BY_EMERGENCY` | "Service temporarily paused by admin." |

### 7.2 4 级优先队列 + WFQ 调度器

#### 数据结构

```python
class Lane:
    tier: str
    weight: int
    queue: deque[Job]          # FIFO
    virtual_time: Fraction     # 用 fractions.Fraction 防累积浮点误差
```

四条 lane 常驻内存。**queue 不持久化**（重启即丢，已 RUNNING 的 Job 落库可恢复）；管理员重启前应执行 `drain` 命令。

#### 调度循环

```python
async def schedule_loop():
    while True:
        if has_free_worker_slot() and any_lane_nonempty():
            lane = pick_min_vt_lane()             # tie-breaker: tier 高的优先
            job = lane.queue.popleft()
            lane.virtual_time += Fraction(1, lane.weight)
            normalize_virtual_times_periodically()  # 每 1 万 tick normalize 一次防止 vt 无限增长
            await dispatch_to_worker(job)
        else:
            await asyncio.sleep(0.05)
```

#### 反饥饿兜底

每条 lane 至少保 5%：通过给每条空 lane 设置 `min_share_per_lane=0.05`，在统计窗口内若该 lane 实际 fraction 低于此值，下次抽取时强制选它（即使 vt 不是最小）。

#### Deadline 升级

```
- Free Job 在 lane 等待 > deadline_promotion_seconds（默认 300s）→ 升一级（Free → Standard）
- Standard 等待 > 升级 → Premium
- 已升级过的 Job 不再升（标记 promoted=true 防止无限循环）
- 升级后被该 lane 调度时正常出队，但仍按原 tier 计入 quota / SSE position
```

### 7.3 软超惩罚

Worker 拿到带 `SOFT_QUOTA_EXCEEDED` flag 的 Job 后：

```python
async def execute_with_penalty(job):
    overage_ratio = (today_count - soft_quota) / max(1, hard_quota - soft_quota)
    overage_ratio = min(1.0, overage_ratio)

    # 1. 强制 Turnstile（已在 precheck 让前端弹过；这里再校验一次 token）
    #    若 captcha_token 无效或已过期 → 直接返回 412 给前端，并让 archive 这个 Job 标 FAILED
    if cfg.soft_penalty.require_turnstile and not job.captcha_verified:
        return mark_failed(job, "CAPTCHA_REQUIRED")

    # 2. 强制延迟
    delay = cfg.soft_penalty.base_delay_seconds + \
            (cfg.soft_penalty.max_delay_seconds - cfg.soft_penalty.base_delay_seconds) * overage_ratio
    await asyncio.sleep(delay)

    # 3. 概率失败
    fail_p = cfg.soft_penalty.base_fail_probability + \
             (cfg.soft_penalty.max_fail_probability - cfg.soft_penalty.base_fail_probability) * overage_ratio
    if random.random() < fail_p:
        return mark_failed(job, "SOFT_QUOTA_PENALTY")  # 不调上游、不扣 provider 余额

    # 4. 正常调上游
    return await call_upstream(job)
```

> **为什么不在 precheck 里直接 fail**：让用户感受到「队列里走完了才告诉我失败」的恶心程度，是设计意图（同时也避免泄露具体阈值）。

### 7.4 Provider 选择（两阶段）

#### 阶段一：硬过滤

```python
def filter_candidates(job) -> list[Provider]:
    return [
        p for p in all_providers if all([
            p.enabled,
            job.user.tier in p.tier_access(job.model),     # §4.1 白名单
            p.supports_model(job.model),
            p.balance_cny >= cfg.provider_filter.balance_min_threshold,
            p.circuit_state in ("healthy", "half_open"),
            p.current_concurrency < p.max_concurrency,
            p.recent_calls_in_60s < p.rpm_limit,
            p.capabilities_for(job.model).matches(job.params),  # §5.4 参数兼容
        ])
    ]
```

#### 阶段二：软打分

```python
def score(p: Provider, job) -> float:
    cost     = 1 - (p.cost_per_image_cny / max_cost_in_pool)
    success  = metrics.success_rate(p, job.model, window=300)         # 0..1
    latency  = 1 - min(1, metrics.p50_ms(p, job.model, 300) / SLO_ms)
    load     = 1 - (p.current_concurrency / p.max_concurrency)
    fresh    = freshness_bonus(p, last_used_at)                        # 0..1，久未用→接近1
    w = cfg.provider_scoring.weights
    return w.cost * cost + w.success * success + w.latency * latency + w.load * load + w.freshness * fresh
```

按 score 降序，取 top_k（默认 3）作为 fallback chain。

### 7.5 Provider 状态机

```
                       连续 N 次失败                       
   ┌─────────────┐ ────────────────────► ┌─────────┐        
   │  HEALTHY    │                        │  OPEN   │       
   │             │ ◄──── 探测成功 ────── │         │       
   └─────┬───────┘                        └────┬────┘       
         │ balance ≤ 0                         │ 冷却到期    
         ▼                                     ▼            
   ┌─────────────┐                       ┌─────────────┐    
   │  DRAINED    │                       │ HALF_OPEN   │    
   │ (等充值)     │                       │ (放 1 探测)  │    
   └─────────────┘                       └─────────────┘    
                                                ▼ 失败 → 退避×2 → OPEN
```

| 状态 | 进入条件 | 退出条件 |
|---|---|---|
| `HEALTHY` | 默认 | 失败计数 ≥ N → OPEN；balance ≤ threshold → DRAINED |
| `OPEN` | 滑窗内连续失败 ≥ `failure_threshold` | 冷却到期 → HALF_OPEN |
| `HALF_OPEN` | OPEN 冷却结束 | 探测成功 → HEALTHY；探测失败 → 退避 ×2，回 OPEN |
| `DRAINED` | balance < threshold | admin 充值 + reset → HEALTHY |
| `DISABLED` | admin 切换 | admin 切换 |

**HALF_OPEN 探测并发控制**：用 `asyncio.Semaphore(1)` per provider 包住，确保同一 provider 同时只有 1 个探测请求。

### 7.6 故障转移

Worker 顺序尝试 `top_k` 候选：

```python
for attempt, provider in enumerate(top_k_candidates):
    try:
        result = await adapter.generate(provider, job.normalized_request)
        await ledger.deduct(provider, job, result.image_count)
        await job_lifecycle.transition(job, "SUCCEEDED")
        return result
    except UpstreamFailure as e:
        await metrics.record_failure(provider, e)
        await circuit_breaker.observe(provider, success=False)
        if attempt + 1 >= cfg.max_retries_per_job:
            break
# 所有候选都失败
if no_candidates_available:
    await job_lifecycle.transition(job, "FAILED", reason="NO_PROVIDER_AVAILABLE")
    refund_user_quota(job.user)              # 系统侧故障 → 不扣用户配额
else:
    await job_lifecycle.transition(job, "FAILED", reason="ALL_PROVIDERS_FAILED")
    # 上游失败仍扣配额，避免被恶意 prompt 反复重试耗尽我们的余额
```

### 7.7 与上传文档不一致的取舍

上传的《调度系统设计.md》整体就是 v1 蓝图，本设计严格沿用。两点补充：

1. **配额"系统侧故障免责"边界**：仅当**没有任何 candidate**（全 OPEN / DRAINED / DISABLED）时退还。所有 candidate 都试过且都失败的场景仍扣配额，避免"故意写个上游一定拒绝的 prompt"刷低用量计数。
2. **WFQ 反饥饿**：用 `Fraction` 不用 `float`，每 1 万 tick `normalize_virtual_times`（找最小 vt 做基准 0）防止累积。

---

## 8. 任务生命周期、SSE 与 Archive 同步

### 8.1 状态机

```
   POST /api/jobs                   admin force-cancel
        │                                  │
        ▼                                  ▼
   ┌────────┐  Worker 取走  ┌────────┐  cancel  ┌──────────┐
   │ QUEUED │ ─────────────►│ RUNNING│─────────►│CANCELLED │
   └────┬───┘                └───┬────┘          └──────────┘
        │ admin force-cancel     │ SUCCEEDED       
        ▼                        ▼
   ┌──────────┐             ┌───────────┐                    
   │CANCELLED │             │ SUCCEEDED │                    
   └──────────┘             └───────────┘                    
                                  │ user delete / cleanup    
                                  ▼                          
                            ┌───────────┐                    
                            │ DELETED   │                    
                            └───────────┘                    
                            
   QUEUED 或 RUNNING ─ 上游失败 ─► FAILED
```

| 状态 | 含义 | 是否计入用户配额 |
|---|---|---|
| `QUEUED` | 已入队，等待 Worker | 已计入 |
| `RUNNING` | Worker 正在调上游 | 已计入 |
| `SUCCEEDED` | 至少一张图成功（Set 中部分失败仍归 SUCCEEDED） | 已计入 |
| `FAILED` | 所有 candidate 失败 / 软超惩罚命中 / 参数错误等 | 已计入（除非 NO_PROVIDER_AVAILABLE） |
| `CANCELLED` | 用户或 admin 撤销 | 不计入 |
| `DELETED` | 用户或清理任务删除 | 已计入但前端不再展示 |

### 8.2 关键时间字段

```
created_at        入队时刻
queued_at         = created_at（与 created_at 区分留给未来扩展）
dispatched_at     Worker 取走的时刻
started_at        实际发出第一次上游请求的时刻（软超惩罚 sleep 之后）
finished_at       SUCCEEDED / FAILED / CANCELLED 落地时刻
```

排队时长 = `dispatched_at - queued_at`，渲染时长 = `finished_at - started_at`。

### 8.3 Archive 任务编号体系

| 标识 | 用途 | 谁生成 | 谁可见 |
|---|---|---|---|
| `seq_no`（用户级递增整数） | 前端 `#1428` | 后端，按 `users.last_seq_no` 自增 | 用户 |
| `hash_id`（短哈希） | 跨设备稳定唯一标识 | 后端，`'j_' + base32(sha256(user_id+db_id+ts+salt))[:12]` | 系统内部，前端不强化显示 |
| `set_id`（仅 n>1） | 关联同一 Set 的所有图片 | 后端 | 前端聚合卡用 |
| `image_id`（每张图） | Set 中某张图的稳定标识 | 后端 | 前端用于点开单图详情 |

**`seq_no` 是按用户隔离的**：用户 A 的 1428 与用户 B 的 1428 各自独立。SQL：

```sql
-- 在事务内：
UPDATE users SET last_seq_no = last_seq_no + 1 WHERE id = ? RETURNING last_seq_no;
INSERT INTO jobs(... seq_no, ...) VALUES (... ?, ...);
```

### 8.4 队列位置与 ETA

#### `position`

```
position = 1 + count(其他队列中 vt 比我小的 Job 数 + 比我先入同 lane 的 Job 数)
```

实现上简化：把当前 4 lane 的 deque 按"调度器下一抽取顺序"模拟一遍，找到该 Job 排第几。100 量级队列下完全够用。

#### `estimated_wait_seconds`

```python
avg_render_seconds = metrics.avg_render_seconds(model, window=3600)   # 历史 1h 平均
free_workers_share = global_workers * lane_share[user.tier]           # 该 tier 实际抢到的 worker 数
eta = position / max(1, free_workers_share) * avg_render_seconds
```

允许 ±50% 误差，前端只要求"大致几分几秒"即可。

### 8.5 SSE 长连接

#### 8.5.1 单一长连接通道

每个用户**只用一条** SSE 连接（`GET /api/sse`），承载多种事件：

| event | 何时发出 | payload |
|---|---|---|
| `hello` | 连接建立首帧 | `{ "server_time": "...", "session_id": "..." }` |
| `heartbeat` | 每 `SSE_HEARTBEAT_SECONDS` 秒一次 | `{ "t": 1714... }` |
| `job_state` | 任意 Job 状态变化（QUEUED→RUNNING→SUCCEEDED 等） | 见 §8.5.3 |
| `job_progress` | 队列位置变化 / 渲染进度更新 | 见 §8.5.3 |
| `task_created` | 该用户的**任意一台设备**新建了任务 | 见 §10.4 |
| `task_deleted` | 任务被 admin / 清理任务删除 | `{ "hash_id": "..." }` |
| `announcement` | 公告投放 | 见 §11.2 |
| `model_capabilities_changed` | admin 改了 provider/model 能力 | `{}`（前端收到后重新拉 `/api/models`） |
| `connection_warning` | 连接异常预告（即将关停） | `{ "reason": "...", "retry_after": 5 }` |

> 用一条连接是为了**不让浏览器达到同源 SSE 上限**（Chrome 默认 6）。前端基于事件类型分发到不同 reducer。

#### 8.5.2 客户端订阅过滤

前端不需要"告诉服务端我在关注哪几个 Job"，因为：
- 服务端推送的全是**当前用户拥有**的 Job，量天然不大；
- archive 在前端把没本地缓存的 hash_id 直接忽略 / 拉详情即可。

但服务端要做：**对当前 SSE 客户端，按 `last_event_id` 重放断连后漏掉的事件**（保留最近 5 分钟事件 buffer in-memory，足够覆盖正常重连）。

#### 8.5.3 `job_state` / `job_progress` payload

```json
// job_state
{
  "hash_id": "j_a1b2c3d4e5f6",
  "set_id": "set_x9y8",
  "seq_no": 1428,
  "model": "gemini-3.1-flash-image-preview",
  "from": "QUEUED",
  "to": "RUNNING",
  "ts": "2026-04-27T11:51:02+08:00",
  "error": null,
  "result": null
}

// job_state SUCCEEDED
{
  "hash_id": "j_a1b2c3d4e5f6",
  "from": "RUNNING",
  "to": "SUCCEEDED",
  "ts": "...",
  "result": {
    "images": [
      {
        "image_id": "img_…",
        "order": 1,
        "thumb_url": "/api/jobs/j_a1b2c3d4e5f6/images/1/thumb",
        "width": 2048,
        "height": 1152,
        "format": "webp"
      }
    ],
    "duration_ms": 14200,
    "provider_used": "bltcy"          // 仅 admin 任务可见，普通用户字段省略
  }
}

// job_progress
{
  "hash_id": "j_a1b2c3d4e5f6",
  "position": 2,
  "estimated_wait_seconds": 14,
  "elapsed_ms": 0           // 在 RUNNING 时填已运行毫秒
}
```

#### 8.5.4 不需要前端发查询请求

按需求 8 和 10：archive 中的 RUNNING/QUEUED 任务**不需要前端轮询**也不需要"前端告诉后端我关注哪些"。因为：
- SSE 是按用户推送，**所有该用户的 active Job 状态变化都会推过来**。
- 前端只需在收到事件时对照本地缓存里的 hash_id 应用更新。

如果前端实在需要兜底（例如 SSE 一直没建上但页面要展示），用 `POST /api/jobs/states` **批量 REST 查询**（§9.4），单次请求最多 100 个 hash_id。

### 8.6 任务详情接口

前端 archive 的右抽屉/详情视图需要的元数据：

`GET /api/jobs/<hash_id>` →

```jsonc
{
  "hash_id": "j_a1b2c3d4e5f6",
  "seq_no": 1428,
  "status": "SUCCEEDED",
  "model": "gemini-3.1-flash-image-preview",
  "model_display_name": "Gemini 3.1 Flash",

  "prompt": "A photorealistic close-up of three overripe bananas...",

  "params": {
    "aspect_ratio": "16:9",
    "image_size": "2K",
    "thinking_level": "high",
    "include_thoughts": false,
    "google_search": false
  },

  "references": [
    { "order": 1, "filename": "banana_01.jpg", "thumb_url": "/api/jobs/.../refs/1/thumb" }
  ],

  "set": {
    "set_id": "set_x9y8",
    "image_count": 4
  },
  "images": [
    {
      "image_id": "img_…",
      "order": 1,
      "thumb_url": "/api/jobs/.../images/1/thumb",
      "download_url": "/api/jobs/.../images/1/original",
      "width": 2048,
      "height": 1152,
      "format": "webp",
      "file_size_bytes": 1842340,
      "starred": false
    }
  ],

  "session": { "id": "sess_…", "name": "Editorial Cover" },

  "timing": {
    "queued_at":     "2026-04-27T11:51:00+08:00",
    "started_at":    "2026-04-27T11:51:03+08:00",
    "finished_at":   "2026-04-27T11:51:17+08:00",
    "queue_seconds": 3,
    "render_seconds": 14
  },

  "error": null
}
```

> **不要**在 user 视图返回 `provider_used / cost_cny / retries` 等运维字段，那些只在 admin 接口（§13.2）里给出。  
> **完善但不太技术**地展示：archive 右抽屉里展示 `model_display_name / shape / image_size / queue_seconds / render_seconds / session / 创建时间`，再加一项「used X reference image(s)」。**不展示** thinking_level、moderation 这种偏开发的字段（虽然存了，但默认收起在"More details"里，用户可点开）。

### 8.7 关于"set 的部分失败"

n=4 的 Set 里若有 1 张失败：
- 整个 Job 状态仍记 `SUCCEEDED`（至少有 1 张产出）。
- `images[]` 里失败那张的 `image_id` 仍存在，但 `thumb_url=null`、`error="UPSTREAM_RETURNED_TEXT_ONLY"`。
- 前端 ArchiveSetCard 的 cell `state="fail"`（已支持）。
- 计费：按上游实际返回的图片数扣，不按 `n`。

---

## 9. Archive 同步策略

### 9.1 设计目标

- archive 主要靠**浏览器本地缓存**（IndexedDB）展示历史，**只有缩略图 url 被缓存，原图不缓存**（原图按需 `/original` 端点拉）。
- 后端开放接口让"另一台设备生成的内容"能同步到当前设备。
- 后端定期清理任务，所以"本地有但服务器没有"是允许的（前端不报错）。
- 切换登录账户时，每个账户的本地缓存独立。

### 9.2 本地缓存键名

```
IndexedDB:
  database name: "txt2img"
  object stores:
    - "archive_<user_id>"        # 一个用户一个 store；切账号自动隔离
    - "thumbnails_<user_id>"     # 缩略图 blob 的本地缓存（可选优化）
```

切换账号**不擦除**旧账号的 store；下次登录回来仍能恢复。

### 9.3 一次完整的同步流程

进入 `/archive` 时执行：

```
1. 读 IndexedDB 里 archive_<userId>，渲染。
2. 拉 GET /api/jobs/index?since=<local_max_updated_at>&limit=2000
     → 返回 [{ hash_id, updated_at, status, set_id }, ...]，仅元数据，不含图片
3. 比对：
     a. 本地有 + 服务端有 + status 不同 → 拉 details
     b. 本地无 + 服务端有 → 拉 details（批量，见 §9.4）
     c. 本地有 + 服务端没有 → 不动（可能服务器清理过，沿用本地缓存）
4. SSE 持续推 task_created / job_state / task_deleted，本地实时打补丁
```

> `since` 参数让首次全量后只拉 delta。本地存的 `local_max_updated_at` 来自上次结果的最大值。

### 9.4 接口

#### `GET /api/jobs/index`

```
?since=<ISO8601>     可选；不传 = 全量
?limit=<int>         默认 1000，上限 5000；超出分页
?cursor=<token>      分页游标，下次请求带回来
```

响应：

```jsonc
{
  "items": [
    { "hash_id": "j_…", "set_id": "set_…" | null, "seq_no": 1428,
      "status": "SUCCEEDED", "updated_at": "..." }
  ],
  "next_cursor": null
}
```

#### `POST /api/jobs/details`（批量拉详情）

```json
{ "hash_ids": ["j_aaa", "j_bbb", "j_ccc"] }
```

- 上限：单次最多 50 个 hash_id（超出 422）。
- 响应是 §8.6 的 details 数组（按请求顺序）。
- 不存在的 hash_id 返回 `{ "hash_id": "...", "not_found": true }`，前端据此忽略。

#### `POST /api/jobs/states`（批量查状态，轻量）

```json
{ "hash_ids": ["j_aaa", "j_bbb"] }
```

只返回每个的 `status / position / estimated_wait_seconds / updated_at`。SSE 不可用时的兜底。

#### `GET /api/jobs/<hash_id>/images/<order>/thumb`

返回 webp 缩略图（带 ETag、Cache-Control: max-age=2592000 immutable）。

#### `GET /api/jobs/<hash_id>/images/<order>/original`

返回原图。**唯一一个允许返回大文件的端点**。前端默认不下载，仅在用户点 download 时拉。

### 9.5 跨设备同步：实时通道

按需求 10：用户在设备 B 创建任务后，设备 A 也要看到。

机制：`@SSEHub.broadcast_to_user(user_id, event)` 在以下时刻被调用：

| 事件源 | 广播事件 |
|---|---|
| `POST /api/jobs` 入队成功 | `task_created` |
| Job 状态变化 | `job_state` |
| `POST /api/jobs/<id>/cancel` | `job_state` |
| `DELETE /api/jobs/<id>` | `task_deleted` |
| 清理任务删除 | `task_deleted` |

`task_created` payload：

```json
{
  "hash_id": "j_a1b2c3",
  "seq_no": 1428,
  "status": "QUEUED",
  "model": "gemini-3.1-flash-image-preview",
  "set_id": null,
  "position": 3,
  "estimated_wait_seconds": 28,
  "created_at": "...",
  "client_request_id": "req_xyz"     // 让原始提交端识别"这是我刚刚提交的"，避免双倒入
}
```

> 提交端拿到 `POST /api/jobs` 的 200 之后，再收到 SSE `task_created` 时若 `client_request_id` 一致则忽略。

### 9.6 Create 完成后跳转 archive 的契约

按需求 10：

```
用户点 Generate
  → 前端 POST /api/jobs
  → 后端 200，返回 { hash_id, ... } 同时已写库 + 入队
  → 前端 router.push('/archive')，把刚才的响应直接 prepend 到本地 IndexedDB store
  → archive 渲染该 Job 为 QUEUED 状态
  → SSE 后续推 job_state，前端打补丁
```

**不要让 archive 等 SSE 才显示**：`POST /api/jobs` 200 即视为"已有任务"，本地立即 optimistic 入栈。

### 9.7 服务器清理后的兼容

清理（§13.4）只删 DB 中的 jobs 行 + `data/jobs/<hash>/` 目录。被清理的 hash_id 出现在 SSE `task_deleted` 里，前端按提示删除本地缓存条目。**前端不会主动校验"本地的 hash_id 是否还在服务器"**，避免对每个本地条目反查。所以：
- 用户在 archive 里依然看到本地缓存中的旧任务卡片（缩略图也还在）。
- 点击"download original"时返回 404 → 前端显示"This file is no longer on the server"，让用户自行清掉本地条目。

---

## 10. 图片存储与压缩

### 10.1 落盘流程

Worker 拿到 adapter 返回的图像（base64 或 bytes）后：

```python
async def store_image(job, idx, image_bytes, mime):
    job_dir = data_root / "jobs" / job.hash_id
    out_dir = job_dir / "outputs"
    out_dir.mkdir(parents=True, exist_ok=True)

    ext = mime_to_ext(mime)                     # png / jpeg / webp
    original_path = out_dir / f"{idx:02d}_original.{ext}"
    original_path.write_bytes(image_bytes)

    # 缩略图：长边 <= THUMBNAIL_MAX_LONG_EDGE，webp，q=78
    thumb_path = out_dir / f"{idx:02d}_thumb.webp"
    await asyncio.to_thread(make_thumb, original_path, thumb_path)

    # DB
    db.insert(images, {
        "image_id":   "img_" + nanoid(10),
        "job_id":     job.id,
        "order":      idx,
        "original_path": str(original_path.relative_to(data_root)),
        "thumb_path":    str(thumb_path.relative_to(data_root)),
        "width":         w, "height": h,
        "format":        ext,
        "file_size_bytes": original_path.stat().st_size,
    })
```

### 10.2 缩略图算法

用 `Pillow`（推荐 `pillow-simd` 提升 2-3×）：

```python
def make_thumb(src_path, dst_path, max_edge=720, q=78):
    img = Image.open(src_path)
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGB")
    img.thumbnail((max_edge, max_edge), Image.LANCZOS)
    img.save(dst_path, "WEBP", quality=q, method=6)
```

- 长边 720px 在 1× 屏与 2× 屏之间折中（archive 卡片在 4 列布局下展示 ~ 280px 实宽 → 2× 设备约 560px）。
- 大约把 4MB PNG 压成 80–150KB webp。

### 10.3 文件夹布局回顾

```
data/jobs/j_a1b2c3d4e5f6/
├── meta.json
├── refs/
│   ├── 01_banana_01.jpg
│   └── 02_leaf.png
├── outputs/
│   ├── 01_original.webp
│   ├── 01_thumb.webp
│   ├── 02_original.webp
│   └── 02_thumb.webp
├── upstream/
│   ├── attempt_1.json
│   └── attempt_2.json
└── timeline.jsonl
```

`meta.json` 里包含**完整的 normalized request + 用户 id + 入队时间 + provider 选择决策摘要**。Debug 时只看这个文件就够。

`upstream/attempt_N.json` 记录每次（含 fallback）调用的：
- 请求 url + headers（api key 已脱敏为 `sk-***`）
- 上游返回状态码 + body 摘要（图像 base64 截断为前 200 字符 + size）
- 耗时

`timeline.jsonl` 每行一条状态变迁：

```jsonl
{"ts":"...","from":null,"to":"QUEUED"}
{"ts":"...","from":"QUEUED","to":"RUNNING","worker_id":"w_3"}
{"ts":"...","from":"RUNNING","to":"SUCCEEDED","provider":"bltcy","duration_ms":14200}
```

### 10.4 下载行为

- 缩略图：`/thumb` 端点带 `Cache-Control: public, max-age=2592000, immutable`。前端用 `<img src=... loading=lazy>`。
- 原图：`/original` 端点带 `Content-Disposition: attachment; filename="…"`，前端用 `<a download>` 触发下载。**不在前端用 fetch 拿 blob**，避免大文件占内存。

### 10.5 安全：路径校验

`hash_id` 必须命中正则 `^j_[A-Za-z0-9]{12}$`，`order` 必须是正整数。任何不匹配 → 400。绝对禁止 `Path(data_root) / hash_id / ...` 之前不校验，否则 `..%2f..` 可读到任意文件。

---

## 11. 公告系统

### 11.1 数据模型

```sql
announcements(
  id            TEXT PRIMARY KEY,           -- 'ann_' + nanoid(10)
  title         TEXT,                       -- 可空
  content_kind  TEXT NOT NULL,              -- 'text' | 'image' | 'react'
  content       TEXT NOT NULL,              -- text/react 用 string；image 存相对路径
  -- 投放规则
  audience_kind TEXT NOT NULL,              -- 'all' | 'tier' | 'user_list'
  audience_data TEXT,                       -- tier 列表（JSON）或 user_id 列表（JSON）
  -- 控制
  starts_at     TIMESTAMP NOT NULL,
  ends_at       TIMESTAMP,                  -- 可空 = 永久
  dismissable   INTEGER NOT NULL DEFAULT 1,
  priority      INTEGER NOT NULL DEFAULT 0, -- 同一时刻多个公告时优先级排序
  created_by    TEXT NOT NULL,              -- admin user_id
  created_at    TIMESTAMP NOT NULL,
  updated_at    TIMESTAMP NOT NULL
);

announcement_reads(
  user_id       TEXT NOT NULL,
  announcement_id TEXT NOT NULL,
  read_at       TIMESTAMP NOT NULL,
  PRIMARY KEY (user_id, announcement_id)
);
```

> `content_kind=react`：`content` 字段存一段 JSX 字符串。前端用受限白名单 sanitizer + JSX runtime 渲染；**不允许**直接 `eval`，只允许预定义组件白名单（`<p>`, `<a>`, `<strong>`, `<em>`, `<ul>`, `<li>`, `<h3>`, `<Banner>`, `<Code>` 等）。  
> 这一项实现复杂、风险高，v1 可以先只支持 `text` 与 `image`，把 `react` 留到 v1.1。

### 11.2 投放与已读

#### 接口

```
GET /api/announcements/active
  → 返回当前用户**未读且生效**的公告列表（按 priority 降序、created_at 降序）

POST /api/announcements/<ann_id>/read
  → 标记已读
```

#### SSE 推送

admin 创建/更新公告后，`@AnnouncementBus` 计算受众列表，对每个受众用户的 SSE 通道推：

```json
// SSE event: announcement
{
  "id": "ann_…",
  "title": "Scheduled maintenance",
  "content_kind": "text",
  "content": "We'll be down on Sunday 02:00–03:00 UTC+8.",
  "dismissable": true,
  "priority": 5
}
```

前端弹出 banner / modal，用户关闭即调 `POST /api/announcements/<id>/read`。  
**关闭即永久**：`announcement_reads` 写入后该用户永远不会再次收到此公告（即便 SSE 重连）。

### 11.3 受众选择

| `audience_kind` | `audience_data` 内容 | 含义 |
|---|---|---|
| `all` | null | 所有 active 用户 |
| `tier` | `["vip","premium"]` | 该 tier 列表里的用户 |
| `user_list` | `["u_a","u_b","u_c"]` | 指定 user id 列表 |

### 11.4 上传图片公告

`POST /api/admin/announcements`（multipart）允许带一个 `cover` 文件，落到 `data/announcements/<ann_id>/cover.<ext>`，DB 里的 `content` 存相对路径。前端通过 `GET /api/announcements/<ann_id>/cover` 拉取。

---

## 12. SSE 连接异常处理

### 12.1 心跳

服务端每 `SSE_HEARTBEAT_SECONDS`（默认 15s）写一行 `: ping\n\n`（comment 行）+ 一个 `event: heartbeat`。前端 `lastSeenHeartbeatAt` 计时，超过 `2 * SSE_HEARTBEAT_SECONDS` 未见心跳即认为断连。

### 12.2 重连策略

```
首次断连     → 立即重连
第 2 次失败  → 等 1s
第 3 次失败  → 等 2s
第 4 次失败  → 等 4s
…              （指数退避，cap 30s）
连续 5 次失败 → 触发 "connection lost" UI 提示
```

每次重连带 `Last-Event-ID` header；服务端按 §8.5.2 的 5min in-memory buffer 重放遗漏事件。

### 12.3 "connection lost" UI

按需求 12 居中显示，与设计稿风格一致。位置在主 content 区中央，**英文文案**。

> **示意 HTML/CSS**（前端实现，本设计仅给出建议）：
> ```jsx
> <div className="conn-warning">
>   <div className="conn-warning-box">
>     <span className="conn-warning-glyph" />
>     <div className="display" style={{fontSize:24, fontWeight:900, letterSpacing:'-0.02em'}}>
>       Connection lost
>     </div>
>     <div className="mono" style={{fontSize:11, color:'var(--ink-3)', marginTop:6}}>
>       trying to reconnect · attempt {attempts}
>     </div>
>     <button className="btn sm" onClick={retryNow}>retry now</button>
>   </div>
> </div>
> ```
> 样式沿用 `--ink / --paper / --banana` 调色板，box 用 1px ink border + 5px ink shadow（沿用项目里的 `boxShadow: "5px 5px 0 var(--ink)"`），符合现有 archive popover 风格。

恢复连接后立即拉一次 `GET /api/jobs/index?since=...` 补齐期间的状态变化，并隐藏警告。

---

## 13. 管理员后台

### 13.1 入口可见性

- 前端 `Sidebar.jsx` 的 BOTTOM 区里已有 `Admin` 按钮，但目前所有人都能看到。需改为 `if (currentUser.role === 'admin')` 才渲染。
- 后端所有 `/api/admin/**` 端点用 `Depends(get_current_admin)` 守门；非 admin 调用一律 403。
- 即使前端被改掉显示了 admin 链接，没有 admin role 的 token 任何 admin 接口都会 403，安全边界在后端。

### 13.2 用户管理

#### 列表

`GET /api/admin/users`

```
?q=<keyword>            按 username / display_name 模糊搜
?tier=<tier>            过滤
?status=active|disabled
?sort=created_at:desc | last_login_at:desc | today_count:desc
?page=1&page_size=50
```

返回每行：

```json
{
  "id": "u_…",
  "username": "alice",
  "display_name": "Alice",
  "role": "user",
  "tier": "premium",
  "status": "active",
  "today_count": 23,
  "soft_quota_effective": 50,
  "hard_quota_effective": 100,
  "created_at": "...",
  "last_login_at": "...",
  "30d_total_jobs": 412,
  "30d_total_success": 401,
  "30d_total_failed": 11
}
```

#### 创建用户

`POST /api/admin/users`

```json
{
  "username": "bob",
  "password": "init-password",
  "display_name": "Bob",
  "role": "user",
  "tier": "free",
  "override_soft_quota": null,
  "override_hard_quota": null
}
```

#### 修改用户

`PATCH /api/admin/users/<id>` — 任意子集字段。改 password 时强制新值不少于 8 字符。

#### 停用 / 启用

`POST /api/admin/users/<id>/disable` / `/enable`：把 `status` 翻转。停用后该用户的 token 仍有效但所有非 login 端点返回 403 `ACCOUNT_DISABLED`。

#### 删除

`DELETE /api/admin/users/<id>` — 软删除：`status='deleted'`，username 加 `__deleted_<ts>` 后缀以便释放命名。任务**保留**（只解除 user 引用，便于审计），可单独跑清理。

#### 单个用户 详情视图

`GET /api/admin/users/<id>` 返回：
- 基本字段（同列表）
- 近 30 天每日生成数（图表用）：`{date, jobs_count, images_count, success_rate, avg_render_seconds}`
- 近 30 天最常用模型 top 3
- 近 30 天最常用 provider top 3
- 当前 active session 数量
- 最近 50 条任务（含 `provider_used / cost_cny / status`）

#### 用户的任务列表

`GET /api/admin/users/<id>/jobs?page=...&status=...` —— 字段比 `/api/jobs/index` 多 `provider_used / cost_cny / retries / error`。

### 13.3 等级配额管理

#### 当前配置

`GET /api/admin/tiers` 返回所有 tier 的当前配置。

#### 修改某个 tier

`PATCH /api/admin/tiers/<tier>` body 子集：

```json
{
  "weight": 8,
  "max_concurrency": 4,
  "max_queue": 10,
  "soft_quota": 100,
  "hard_quota": 200,
  "slo_p95_ms": 30000
}
```

修改后立即生效（不重启）：调度器读的是单例 `@TierConfig`，admin patch 触发 `@TierConfig.reload()`。

> 单用户的 quota 覆写在 `users.override_soft_quota / override_hard_quota`，不在 tier 表里改。

### 13.4 中转站管理

#### 列表

`GET /api/admin/providers`

```jsonc
[
  {
    "id": "bltcy",
    "label": "BLTCY",
    "adapter_type": "gemini_v1beta",
    "base_url": "https://api.bltcy.ai/v1beta",
    "api_key_masked": "sk-***FQBR",
    "cost_per_image_cny": 0.1,
    "balance_cny": 3.42,
    "initial_balance_cny": 3.88,
    "enabled": true,
    "max_concurrency": 70,
    "rpm_limit": 600,
    "circuit_state": "healthy",
    "cooldown_until": null,
    // 实时指标，从 @MetricsEngine 拉
    "metrics_5min": {
      "calls": 142,
      "success_rate": 0.986,
      "p50_ms": 8200,
      "p95_ms": 14500,
      "current_concurrency": 12
    },
    "supported_models": [
      {
        "model_id": "gemini-3-pro-image-preview",
        "enabled": true,
        "capabilities": { /* §4.3 */ }
      }
    ],
    "tier_access": ["vip","premium","standard"]
  }
]
```

#### CRUD

| 方法 | 路径 |
|---|---|
| POST | `/api/admin/providers` |
| PATCH | `/api/admin/providers/<id>` |
| DELETE | `/api/admin/providers/<id>` |
| POST | `/api/admin/providers/<id>/test` 触发一次最小 ping（用预设 prompt 调一张 256×256），不计入 ledger，结果实时返回 |
| POST | `/api/admin/providers/<id>/reset-circuit` 强制把状态改回 healthy（清 cooldown） |
| POST | `/api/admin/providers/<id>/topup` body `{"amount_cny": 10.0}` 余额加值并把 DRAINED→HEALTHY |
| PATCH | `/api/admin/providers/<id>/models/<model_id>` 修改单 (provider, model) 的 `capabilities` 与 `enabled` |
| PATCH | `/api/admin/providers/<id>/tier-access` body `{"tiers": ["vip","premium"]}` |

#### 实时状态 SSE（admin 专用）

`GET /api/admin/sse` 推送 `provider_state` / `provider_metrics` / `worker_pool_state` 等事件，给 admin 监控面板用。普通用户 SSE 不带这些。

### 13.5 配置中心

所有调度阈值进 `config` 表，单条单 key：

```sql
config(key TEXT PRIMARY KEY, value_json TEXT NOT NULL, updated_at TIMESTAMP, updated_by TEXT);
```

key 列表（默认值与上传文档一致）：

```
scheduler.global_max_workers              32
scheduler.min_share_per_lane              0.05
scheduler.deadline_promotion_seconds      300

soft_penalty.base_delay_seconds           5
soft_penalty.max_delay_seconds            15
soft_penalty.base_fail_probability        0.3
soft_penalty.max_fail_probability         0.7
soft_penalty.require_turnstile            true

provider_scoring.weights.cost             0.30
provider_scoring.weights.success          0.25
provider_scoring.weights.latency          0.20
provider_scoring.weights.load             0.15
provider_scoring.weights.freshness        0.10
provider_scoring.metric_window_seconds    300
provider_scoring.fallback_top_k           3
provider_scoring.max_retries_per_job      3

circuit_breaker.failure_threshold         5
circuit_breaker.initial_cooldown_seconds  30
circuit_breaker.max_cooldown_seconds      600
circuit_breaker.half_open_probe_concurrency 1

provider_filter.balance_min_threshold     0.5

retention.job_retention_days              30
thumbnail.max_long_edge                   720
thumbnail.quality                         78

emergency.pause_generation                false
emergency.pause_image_access              false
emergency.block_new_member_login          false
emergency.force_captcha_global            false
```

#### 接口

```
GET   /api/admin/config                       全量
GET   /api/admin/config/<key>                 单 key
PATCH /api/admin/config                       body: { "key1": value1, ... }
```

更新后 `@ConfigCenter.reload()`，对应单例（`@TierConfig` / `@SchedulerConfig` / `@ProviderScoring` …）热加载。**weights 总和必须为 1**，验证不通过 422。

### 13.6 紧急封控

- `emergency.pause_generation = true` → `POST /api/jobs` 全部返回 403 `BLOCKED_BY_EMERGENCY`。已 RUNNING 不打断。
- `emergency.pause_image_access = true` → `/thumb` `/original` 全 403（仅 admin 能 bypass）。
- `emergency.block_new_member_login = true` → 仅 admin 能登录，其他用户 401 `BLOCKED_BY_EMERGENCY`。
- `emergency.force_captcha_global = true` → `precheck` 一律 require_captcha。

四个开关在 admin 的"系统"页有醒目大开关 + 二次确认。

### 13.7 历史任务清理

#### 智能推荐

`GET /api/admin/cleanup/suggestions` 返回多个备选清理周期及预计释放空间：

```jsonc
[
  { "period_label": "Older than 7 days",
    "cutoff": "2026-04-20T...",
    "job_count": 421,
    "image_count": 1380,
    "disk_bytes": 2_140_000_000,    // 2.0 GB
    "disk_human": "2.0 GB"
  },
  { "period_label": "Older than 30 days",
    "cutoff": "2026-03-28T...",
    "job_count": 1820,
    "image_count": 6210,
    "disk_bytes": 9_420_000_000,
    "disk_human": "8.8 GB"
  },
  { "period_label": "Older than 90 days", ... },
  { "period_label": "Failed jobs older than 1 day", ... },
  { "period_label": "Cancelled jobs (any age)", ... }
]
```

实现：扫 `data/jobs/<hash>/` 目录的 `du`（缓存到 `disk_usage` 表，5min 增量更新）。

#### 执行清理

`POST /api/admin/cleanup` body：

```json
{
  "rules": [
    { "kind": "older_than_days", "days": 30, "statuses": ["SUCCEEDED","FAILED"] },
    { "kind": "status_only", "statuses": ["CANCELLED"] }
  ],
  "dry_run": false
}
```

dry_run=true 只返回会删多少不实际执行。  
dry_run=false 异步删除（避免阻塞），返回 `{ "task_id": "cleanup_…" }`，admin 端可轮询 `GET /api/admin/cleanup/<task_id>` 看进度。

清理动作：
1. 先在 DB 把对应 jobs 标记 `status='DELETED'`。
2. 异步 `rm -rf data/jobs/<hash>/`。
3. `images` 表对应行删除。
4. SSE 广播 `task_deleted` 给受影响用户。

### 13.8 Adapter 管理

#### 列表所有已注册适配器

`GET /api/admin/adapters`

```json
[
  {
    "adapter_type": "openai_v1",
    "display_name": "OpenAI Compatible v1",
    "description": "OpenAI 官方 /images/generations 端点风格",
    "supported_models": ["gpt-image-2", "gpt-image-1.5", "dall-e-3"],
    "in_use_by_providers": ["openai_official", "azure_openai_eu"]
  },
  {
    "adapter_type": "gemini_v1beta",
    "display_name": "Gemini v1beta",
    "description": "Google Gemini 原生 :generateContent 协议",
    "supported_models": ["gemini-3-pro-image-preview", "gemini-3.1-flash-image-preview", "gemini-2.5-flash-image"],
    "in_use_by_providers": ["bltcy"]
  }
]
```

> `in_use_by_providers` 让 admin 看到删除某 adapter 之前已经有谁在用。Adapter 是磁盘上的 .py 文件，不通过接口删除；如果想"禁用"某 adapter 直接物理移除文件 + 重启即可。Admin 接口只暴露**只读列表**。

### 13.9 系统度量与可视化

#### `GET /api/admin/metrics/overview`

```json
{
  "active_users_today": 42,
  "jobs_today": 1240,
  "images_today": 3812,
  "queue_state": {
    "vip":      { "queued": 0,  "running": 1 },
    "premium":  { "queued": 2,  "running": 4 },
    "standard": { "queued": 12, "running": 8 },
    "free":     { "queued": 31, "running": 4 }
  },
  "worker_pool": { "max": 32, "in_use": 17 },
  "disk_usage": {
    "data_total_bytes": 14_280_000_000,
    "data_jobs_bytes":  13_200_000_000,
    "free_bytes":       180_000_000_000
  },
  "providers_summary": [
    { "id":"bltcy","circuit_state":"healthy","balance_cny":3.42,"calls_5min":142,"success_5min":0.986 }
  ]
}
```

#### `GET /api/admin/metrics/timeseries`

```
?metric=jobs_count|success_rate|p50_latency|provider_balance
?provider_id=...           （可选）
?model=...                 （可选）
?range=24h|7d|30d
?bucket=1m|5m|1h|1d
```

返回 `{ points: [{ts, value}, ...] }`，给 admin 仪表盘画线图用。

### 13.10 审计日志

`audit_log(id, actor_user_id, action, target, payload_json, ts)`，记录所有 admin 写操作（创建/修改/删除用户、改配置、清理、改 provider 等）。`GET /api/admin/audit?actor=&action=&since=&until=&page=` 列表。

### 13.11 推荐增加的几个能力

1. **影子用户**：admin 用 `POST /api/admin/users/<id>/impersonate` 拿到一个有效期 30 分钟的临时 token，模拟该用户登录，便于复现问题。所有 impersonate 期间的操作在 audit_log 里 actor 仍记 admin。
2. **批量调整**：管理多个用户时一次切 tier，一次封禁。`POST /api/admin/users/bulk` `{ "ids": [...], "patch": { "tier": "premium" } }`。
3. **Provider 上线测试模板**：`POST /api/admin/providers/<id>/test` 走预设 3 个 prompt（小图、大图、含中文），把延迟/成功率展示给 admin。
4. **导出/导入配置**：`GET /api/admin/config/export` 全量配置 + tier + provider（api_key 不导出）打包 JSON，方便迁移。
5. **公告草稿**：公告发出前可保存草稿（`announcements.starts_at` 设未来时刻 + 状态 `draft`）。
6. **SSE 在线连接数监控**：`GET /api/admin/sse/clients` 当前连接列表（user_id, ip, connected_at, last_event_id），便于排查"为什么 A 用户没收到推送"。
7. **任务详细溯源（debug 模式）**：admin 看任意 Job 的 `meta.json + upstream/*.json + timeline.jsonl` 内容，前端给一个"Debug Trace"抽屉。

---

## 14. 数据库 Schema（SQLite, 完整 DDL）

> 全部 DDL 走 alembic 迁移；`migrations/0001_init.py` 一次性建完。

### 14.1 `users`

```sql
CREATE TABLE users (
  id                  TEXT PRIMARY KEY,
  username            TEXT NOT NULL UNIQUE,
  password_hash       TEXT NOT NULL,
  role                TEXT NOT NULL CHECK (role IN ('admin','user')),
  tier                TEXT NOT NULL,
  status              TEXT NOT NULL CHECK (status IN ('active','disabled','deleted')) DEFAULT 'active',
  display_name        TEXT,
  today_count         INTEGER NOT NULL DEFAULT 0,
  today_reset_date    TEXT NOT NULL DEFAULT (date('now','+8 hours')),
  override_soft_quota INTEGER,
  override_hard_quota INTEGER,
  last_seq_no         INTEGER NOT NULL DEFAULT 0,
  created_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  last_login_at       TIMESTAMP
);
CREATE INDEX idx_users_status_tier ON users(status, tier);
```

### 14.2 `tiers`

```sql
CREATE TABLE tiers (
  tier            TEXT PRIMARY KEY CHECK (tier IN ('vip','premium','standard','free')),
  weight          INTEGER NOT NULL,
  max_concurrency INTEGER NOT NULL,
  max_queue       INTEGER NOT NULL,
  soft_quota      INTEGER NOT NULL,
  hard_quota      INTEGER NOT NULL,
  slo_p95_ms      INTEGER,
  updated_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

### 14.3 `providers`、`provider_models`、`provider_tier_access`

见 §4.1 的 DDL。

### 14.4 `jobs`、`job_references`、`images`、`sets`、`session_jobs`

```sql
CREATE TABLE jobs (
  id              TEXT PRIMARY KEY,            -- 内部 'job_' + nanoid
  hash_id         TEXT NOT NULL UNIQUE,        -- 'j_' + 12 字符
  user_id         TEXT NOT NULL,
  tier_at_submit  TEXT NOT NULL,
  seq_no          INTEGER NOT NULL,            -- 用户级递增
  set_id          TEXT,                        -- 'set_' + 10 字符；n=1 为 NULL
  session_id     TEXT,

  model           TEXT NOT NULL,
  params_json     TEXT NOT NULL,
  flags_json      TEXT NOT NULL DEFAULT '{}',  -- soft_quota_exceeded / promoted / ...
  client_request_id TEXT,

  status          TEXT NOT NULL CHECK (status IN ('QUEUED','RUNNING','SUCCEEDED','FAILED','CANCELLED','DELETED')),
  status_reason   TEXT,
  provider_used   TEXT,
  retries         INTEGER NOT NULL DEFAULT 0,
  cost_cny        REAL NOT NULL DEFAULT 0,

  created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  queued_at       TIMESTAMP,
  dispatched_at   TIMESTAMP,
  started_at      TIMESTAMP,
  finished_at     TIMESTAMP,
  updated_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

  FOREIGN KEY (user_id) REFERENCES users(id)
);
CREATE UNIQUE INDEX idx_jobs_user_seq ON jobs(user_id, seq_no);
CREATE INDEX idx_jobs_user_updated   ON jobs(user_id, updated_at DESC);
CREATE INDEX idx_jobs_user_status    ON jobs(user_id, status);
CREATE INDEX idx_jobs_status_created ON jobs(status, created_at);
CREATE INDEX idx_jobs_set            ON jobs(set_id);

CREATE TABLE job_references (
  job_id          TEXT NOT NULL,
  ref_order       INTEGER NOT NULL,            -- 1..14
  filename        TEXT NOT NULL,
  mime            TEXT NOT NULL,
  rel_path        TEXT NOT NULL,
  PRIMARY KEY (job_id, ref_order),
  FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE
);

CREATE TABLE images (
  id              TEXT PRIMARY KEY,
  job_id          TEXT NOT NULL,
  img_order       INTEGER NOT NULL,
  original_path   TEXT NOT NULL,
  thumb_path      TEXT NOT NULL,
  width           INTEGER NOT NULL,
  height          INTEGER NOT NULL,
  format          TEXT NOT NULL,
  file_size_bytes INTEGER NOT NULL,
  starred         INTEGER NOT NULL DEFAULT 0,
  created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE
);
CREATE INDEX idx_images_job ON images(job_id, img_order);

CREATE TABLE sessions (
  id          TEXT PRIMARY KEY,                -- 'sess_' + nanoid
  user_id     TEXT NOT NULL,
  name        TEXT NOT NULL,
  created_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (user_id) REFERENCES users(id)
);
CREATE INDEX idx_sessions_user ON sessions(user_id, updated_at DESC);
```

### 14.5 `billing_ledger`

```sql
CREATE TABLE billing_ledger (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id          TEXT NOT NULL,
  provider_id     TEXT NOT NULL,
  cost_cny        REAL NOT NULL,
  image_count     INTEGER NOT NULL,
  deducted_at     TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_ledger_provider_time ON billing_ledger(provider_id, deducted_at);
```

### 14.6 `announcements`、`announcement_reads`

见 §11.1 的 DDL。

### 14.7 `login_attempts`、`audit_log`、`config`

```sql
CREATE TABLE login_attempts (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  username     TEXT NOT NULL,
  ip           TEXT,
  success      INTEGER NOT NULL,
  attempted_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_login_attempts_user_time ON login_attempts(username, attempted_at);

CREATE TABLE audit_log (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  actor_user_id TEXT NOT NULL,
  action        TEXT NOT NULL,
  target_kind   TEXT,
  target_id     TEXT,
  payload_json  TEXT,
  ip            TEXT,
  ts            TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_audit_actor_ts ON audit_log(actor_user_id, ts);

CREATE TABLE config (
  key           TEXT PRIMARY KEY,
  value_json    TEXT NOT NULL,
  updated_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_by    TEXT
);
```

### 14.8 `disk_usage`（缓存表）

```sql
CREATE TABLE disk_usage (
  scope        TEXT PRIMARY KEY,        -- 'jobs_total' / 'jobs_older_than_7d' / ...
  bytes        INTEGER NOT NULL,
  job_count    INTEGER NOT NULL,
  refreshed_at TIMESTAMP NOT NULL
);
```

每 5 分钟由清理子系统刷新。

---

## 15. 后端代码组织

### 15.1 模块边界与依赖方向

```
api/  ──► domain/  ──► db/ ╲
   │         │              ▶  共同依赖底层
   │         └──► adapters/ ╱
   │
   └──► schemas/ (Pydantic)
   └──► services/ (image_io, turnstile)
```

**禁止反向依赖**：`db/` 不能 import `domain/`；`domain/` 不能 import `api/`；`adapters/` 只依赖 `schemas/` 与 `domain/provider_*` 的接口（不依赖具体实现）。

### 15.2 单例服务的初始化顺序

`main.py` 的 `lifespan`：

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1. DB
    await db.engine.init_engine()
    await db.seed.bootstrap()

    # 2. 配置中心
    await ConfigCenter.load_from_db()

    # 3. 服务实例（按依赖顺序）
    metrics      = MetricsEngine()
    breaker      = CircuitBreaker(metrics)
    selector     = ProviderSelector(metrics, breaker)
    ledger       = ProviderLedger()
    sse_hub      = SSEHub()
    quota        = QuotaGuard()
    queue        = JobQueue()                      # 4 lane
    scheduler    = JobScheduler(queue)             # WFQ
    executor     = JobExecutor(selector, ledger, sse_hub, image_io)
    lifecycle    = JobLifecycle(sse_hub)
    cleanup      = CacheKeeper()

    # 4. 启动后台 task
    app.state.scheduler_task = asyncio.create_task(scheduler.run_forever(executor, lifecycle))
    app.state.metrics_task   = asyncio.create_task(metrics.run_forever())
    app.state.cleanup_task   = asyncio.create_task(cleanup.run_forever())
    app.state.heartbeat_task = asyncio.create_task(sse_hub.heartbeat_loop())

    # 注入到 FastAPI 状态
    app.state.queue = queue
    app.state.sse_hub = sse_hub
    # ...

    yield

    # 关闭：drain + 落库
    await scheduler.drain(timeout=30)
    for t in (app.state.scheduler_task, app.state.metrics_task,
              app.state.cleanup_task, app.state.heartbeat_task):
        t.cancel()
    await db.engine.close()
```

### 15.3 关键文件的职责

| 文件 | 单一职责 |
|---|---|
| `domain/access_policy.py` | 把 §7.1 的闸门检查封成一个函数，按顺序调 quota / busy / emergency。 |
| `domain/quota_guard.py` | `check_hard / check_soft / record_usage / refund`. |
| `domain/job_queue.py` | 4 lane FIFO；提供 `enqueue / pop_by_wfq` |
| `domain/job_scheduler.py` | WFQ 算法主循环；不知道任何 provider 细节 |
| `domain/job_executor.py` | 拿 Job 后选 provider、调 adapter、落盘、更新 lifecycle |
| `domain/job_lifecycle.py` | 状态机；唯一允许写 `jobs.status` 的地方；写入后通过 sse_hub 广播 |
| `domain/provider_selector.py` | §7.4 两阶段筛选 |
| `domain/circuit_breaker.py` | §7.5 状态机；输出"是否允许调用"，外部调用方负责真正放过 |
| `domain/metrics_engine.py` | 5min 滑窗指标；按 (provider, model) 汇总 success/p50/p95/qps |
| `domain/sse_hub.py` | per-user 多客户端推送、event buffer、心跳 |
| `domain/cache_keeper.py` | 清理任务、disk_usage 刷新 |
| `services/image_io.py` | 落原图、生成缩略图、安全路径校验 |
| `services/turnstile.py` | 调 Cloudflare siteverify |

### 15.4 SQLite 并发注意

- 必须 `PRAGMA journal_mode=WAL`、`PRAGMA synchronous=NORMAL`、`PRAGMA busy_timeout=5000`。
- 写操作集中到 `db/` 的 repository，避免长事务。
- 查询热点（`/api/jobs/index`、provider 列表）加索引覆盖（见 §14）。

---

## 16. 全部接口清单（速查表）

### 16.1 公共 / 鉴权

| Method | Path | 鉴权 | 说明 |
|---|---|---|---|
| GET | `/api/health` | 无 | 健康检查 |
| POST | `/api/auth/captcha-check` | 无 | 登录前 captcha 判定 |
| POST | `/api/auth/login` | 无 | 登录获取 token |
| POST | `/api/auth/logout` | user | 登出（仅清前端 token；后端无 session） |
| GET | `/api/me` | user | 当前用户基本信息（不含 tier/quota） |

### 16.2 模型与生成

| Method | Path | 鉴权 | 说明 |
|---|---|---|---|
| GET | `/api/models` | user | 当前用户可用 model + capabilities + defaults |
| POST | `/api/jobs/precheck` | user | 是否需要 captcha |
| POST | `/api/jobs` | user | 创建任务（multipart） |
| POST | `/api/jobs/<hash_id>/cancel` | user | 取消（仅 QUEUED/RUNNING） |
| DELETE | `/api/jobs/<hash_id>` | user | 软删除任务 |

### 16.3 archive 同步

| Method | Path | 鉴权 | 说明 |
|---|---|---|---|
| GET | `/api/jobs/<hash_id>` | user | 单任务详情 |
| GET | `/api/jobs/index` | user | 索引（hash_id 列表 + updated_at + status） |
| POST | `/api/jobs/details` | user | 批量详情，最多 50 |
| POST | `/api/jobs/states` | user | 批量状态，最多 100 |
| GET | `/api/jobs/<hash_id>/images/<order>/thumb` | user | 缩略图 |
| GET | `/api/jobs/<hash_id>/images/<order>/original` | user | 原图（下载） |
| GET | `/api/jobs/<hash_id>/refs/<order>/thumb` | user | 参考图缩略图 |
| POST | `/api/jobs/<hash_id>/images/<order>/star` | user | 收藏 / 取消收藏 |

### 16.4 Session

| Method | Path | 鉴权 | 说明 |
|---|---|---|---|
| GET | `/api/sessions` | user | 列表 |
| POST | `/api/sessions` | user | 创建 |
| PATCH | `/api/sessions/<id>` | user | 改名 |
| DELETE | `/api/sessions/<id>` | user | 删除 session（任务保留，仅解除关联） |

### 16.5 SSE & 公告

| Method | Path | 鉴权 | 说明 |
|---|---|---|---|
| GET | `/api/sse` | user | 单一长连接通道 |
| GET | `/api/announcements/active` | user | 我未读的活动公告 |
| POST | `/api/announcements/<id>/read` | user | 标记已读 |
| GET | `/api/announcements/<id>/cover` | user | 公告封面图 |

### 16.6 Admin

#### 用户

| Method | Path | 说明 |
|---|---|---|
| GET | `/api/admin/users` | 列表 |
| POST | `/api/admin/users` | 创建 |
| GET | `/api/admin/users/<id>` | 详情含 30d 用量 |
| PATCH | `/api/admin/users/<id>` | 修改 |
| DELETE | `/api/admin/users/<id>` | 软删除 |
| POST | `/api/admin/users/<id>/disable` | 停用 |
| POST | `/api/admin/users/<id>/enable` | 启用 |
| POST | `/api/admin/users/<id>/reset-password` | 重置密码 |
| POST | `/api/admin/users/<id>/impersonate` | 影子登录 |
| POST | `/api/admin/users/bulk` | 批量调整 |
| GET | `/api/admin/users/<id>/jobs` | 该用户全部任务 |

#### Tier

| Method | Path | 说明 |
|---|---|---|
| GET | `/api/admin/tiers` | 全部 tier 配置 |
| PATCH | `/api/admin/tiers/<tier>` | 改单 tier |

#### Provider

| Method | Path | 说明 |
|---|---|---|
| GET | `/api/admin/providers` | 列表 + 实时状态 |
| POST | `/api/admin/providers` | 创建 |
| PATCH | `/api/admin/providers/<id>` | 改基础字段 |
| DELETE | `/api/admin/providers/<id>` | 删除 |
| POST | `/api/admin/providers/<id>/test` | 实测 |
| POST | `/api/admin/providers/<id>/reset-circuit` | 重置熔断 |
| POST | `/api/admin/providers/<id>/topup` | 充值 |
| PATCH | `/api/admin/providers/<id>/models/<model_id>` | 改模型能力 |
| PATCH | `/api/admin/providers/<id>/tier-access` | 改 tier 白名单 |

#### Adapter / Config / Cleanup / Announcements / Audit / Metrics

| Method | Path | 说明 |
|---|---|---|
| GET | `/api/admin/adapters` | 已注册适配器列表 |
| GET | `/api/admin/config` | 全配置 |
| GET | `/api/admin/config/<key>` | 单配置 |
| PATCH | `/api/admin/config` | 改配置（多 key） |
| GET | `/api/admin/cleanup/suggestions` | 智能清理建议 |
| POST | `/api/admin/cleanup` | 执行清理 |
| GET | `/api/admin/cleanup/<task_id>` | 清理进度 |
| GET | `/api/admin/announcements` | 公告列表 |
| POST | `/api/admin/announcements` | 创建（multipart） |
| PATCH | `/api/admin/announcements/<id>` | 修改 |
| DELETE | `/api/admin/announcements/<id>` | 删除 |
| GET | `/api/admin/audit` | 审计日志 |
| GET | `/api/admin/metrics/overview` | 概览 |
| GET | `/api/admin/metrics/timeseries` | 时序 |
| GET | `/api/admin/sse` | admin 专用 SSE 通道 |
| GET | `/api/admin/sse/clients` | 当前 SSE 在线连接 |

---

## 17. 错误码统一约定

所有 4xx/5xx 响应统一格式：

```json
{
  "detail": {
    "code": "HARD_QUOTA_EXCEEDED",
    "message": "Daily limit reached. Try again tomorrow.",
    "field": null,
    "extra": null
  }
}
```

| HTTP | code | 出现位置 |
|---|---|---|
| 400 | `BAD_REQUEST` | 参数缺失/格式错 |
| 401 | `UNAUTHORIZED` | token 无效 |
| 401 | `BLOCKED_BY_EMERGENCY` | 紧急封控 + 非 admin |
| 403 | `FORBIDDEN` | 权限不足（如 user 调 admin 接口） |
| 403 | `ACCOUNT_DISABLED` | 用户被停用 |
| 403 | `BLOCKED_BY_EMERGENCY` | 创建任务时紧急封控 |
| 404 | `NOT_FOUND` | 资源不存在 |
| 412 | `CAPTCHA_INVALID` | captcha_token 校验失败 |
| 412 | `CAPTCHA_REQUIRED` | 软超 + 没有 captcha_token |
| 422 | `INVALID_PARAMETER` | 参数语义错误（带 `field`） |
| 429 | `HARD_QUOTA_EXCEEDED` | 当日硬限 |
| 429 | `USER_BUSY` | 并发+队列已满 |
| 429 | `RATE_LIMITED` | IP 级速率限制 |
| 502 | `ALL_PROVIDERS_FAILED` | 所有 candidate 都跑完失败 |
| 503 | `NO_PROVIDER_AVAILABLE` | 没有任何 candidate（不扣配额） |
| 504 | `UPSTREAM_TIMEOUT` | 上游超时（Worker 内部正常重试） |

---

## 18. 与前端契约的关键变更点

1. **前端 Create 页**目前的硬编码 MODELS/SIZES/ASPECTS/temperature 都要替换成 `GET /api/models` 返回的内容。`temperature` slider 删除。`thinking_level` 三档改为 `minimal`/`high` 两档（仅在 gemini-3.1-flash-image-preview 选中时渲染）。
2. **前端 Sidebar.jsx**：`Admin` 菜单需基于 `currentUser.role === 'admin'` 条件渲染。
3. **前端 archive**：所有 mock 数据 (`HX_IMGS`) 替换成本地缓存 + `/api/jobs/index` 同步。
4. **新增 `/admin` 路由组**：v1 至少要 `Users / Tiers / Providers / Config / Cleanup / Announcements / Metrics` 七个子页。
5. **TurnstileModal**：保留，但 `site_key` 从 `precheck` 响应里读，不再写死。
6. **api/client.js**：增加 SSE 客户端封装（独立 `sse.js`），自动重连与 `Last-Event-ID` 处理。

---

## 19. 上线前 Checklist（合并自《调度系统设计.md》§11，加增项）

### 调度
- [ ] 4 lane 反饥饿测试：VIP 持续灌入时 Free 仍能拿到 ≥ 5%
- [ ] WFQ 用 `Fraction`，且每 10000 tick normalize 一次
- [ ] deadline 升级机制：升一级后 `promoted=true` 不再升
- [ ] Provider 熔断 HALF_OPEN 探测全局并发 = 1
- [ ] balance < threshold 时进入 DRAINED，绝不允许真扣到 0
- [ ] 全 provider 故障返回 503 NO_PROVIDER_AVAILABLE 时不扣配额

### 配额与软超
- [ ] 三种 429 (`HARD_QUOTA_EXCEEDED` / `USER_BUSY` / `RATE_LIMITED`) 前端文案分别正确
- [ ] 软超惩罚强度按 overage_ratio 线性
- [ ] 软超的 captcha 校验失败 → 412 而不是 200 + 不调用上游

### 紧急封控
- [ ] `pause_generation` / `pause_image_access` / `block_new_member_login` / `force_captcha_global` 分别测过
- [ ] 紧急封控开启后已 RUNNING 的任务**不被打断**

### 鉴权
- [ ] 除 `/health`、`/auth/login`、`/auth/captcha-check` 外所有 `/api/*` 都拒绝无 token 调用
- [ ] admin 接口被 user 调用 → 403
- [ ] tier / quota / today_count 不出现在任何 user 视角的响应里

### 文件存储
- [ ] hash_id 路径校验（^j_[A-Za-z0-9]{12}$），目录穿越尝试都返回 400
- [ ] 缩略图 webp 长边 ≤ 720
- [ ] 原图按需下载，不被 fetch 到内存
- [ ] 清理任务删除前先在 DB 标 DELETED，再异步 rm -rf
- [ ] `data/jobs/<hash>/` 在容器重建后仍存在（bind-mount 验证）

### SSE
- [ ] 心跳间隔 ≤ 30s，能穿透常见反代
- [ ] 重连带 `Last-Event-ID`，5min buffer 重放
- [ ] 单用户 SSE 上限 (`SSE_MAX_CONNECTIONS_PER_USER`) 验证
- [ ] 一个用户打开 5 个标签页 → 服务端只对最早的 4 个推（或踢最旧那个）

### 多设备同步
- [ ] 设备 A 创建任务，设备 B 立即收到 `task_created`
- [ ] 设备 A 删除任务，设备 B 立即收到 `task_deleted`
- [ ] 切换登录 → 旧账号 store 不被擦，新账号读自己的 store

### Admin
- [ ] 公告 `react` 渲染走白名单，不允许任意 JS
- [ ] 配置项 weights 总和必须为 1
- [ ] tiers 修改后调度立即生效，无需重启
- [ ] 影子登录在 audit_log 里记录正确的 actor

### 性能基线
- [ ] 100 个并发 SSE 连接稳定 1 小时无 OOM
- [ ] 100 个 QUEUED Job 调度无明显延迟（< 50ms 选 lane）
- [ ] 缩略图生成单张 ≤ 200ms（pillow-simd）

---

## 20. 余下可讨论的开放问题（提请决策）

1. **图片有效期**：默认 30 天清理，是否对 `starred=true` 的图片豁免？建议是。
2. **公告 react 渲染**：v1 是否包含？建议先延后，只做 text + image。
3. **失败任务的"自动重试"**：上游 502/504 是否在用户无感的情况下自动重试？建议**仅在同一 Job 内部的 fallback chain** 重试（已在 §7.6），跨 Job 不自动重发。
4. **SSE vs WebSocket**：v1 用 SSE 已足够，未来若引入"用户对模型说话"等双向场景再升级。
5. **多语言**：当前所有错误 message 都是英文。是否后端按 `Accept-Language` 返回中文？建议先固定英文，前端看 `code` 字段做 i18n。
6. **第三方 SSO**：GitHub / Google 登录是否要规划在 v1？建议不做，v1 仅 username+password。
7. **生产环境的 Watchtower 滚动重启**：重启时正在 RUNNING 的 Job 状态如何处理？建议在 SIGTERM 时把 RUNNING 一律转 FAILED with `reason=SERVER_RESTART`，并退还配额。

---

## 附录 A · 默认 tier 与配额一览（再列一次方便速查）

| Tier | weight | concurrency | queue | soft/day | hard/day |
|---|---|---|---|---|---|
| VIP | 8 | 4 | 10 | 100 | 200 |
| Premium | 4 | 2 | 5 | 50 | 100 |
| Standard | 2 | 1 | 3 | 20 | 40 |
| Free | 1 | 1 | 3 | 8 | 10 |

## 附录 B · 文件命名约定

| 标识 | 前缀 | 长度 | 示例 |
|---|---|---|---|
| user id | `u_` | nanoid 12 | `u_K7n9ZxQ2vPmL` |
| job id（内部） | `job_` | nanoid 12 | `job_3rQ9ZxK2pNmW` |
| job hash_id（前端可见） | `j_` | base32 12 | `j_a1b2c3d4e5f6` |
| set id | `set_` | nanoid 10 | `set_x9y8z7w6v5` |
| image id | `img_` | nanoid 10 | `img_K7Q9PmLn3v` |
| session id | `sess_` | nanoid 10 | `sess_3rZxK2pNmW` |
| announcement id | `ann_` | nanoid 10 | `ann_aBcD1234ef` |
| provider id | 用户输入 | 任意 | `bltcy` |

文档版本：v1.0 · 末尾。
