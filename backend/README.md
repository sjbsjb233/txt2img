# txt2img backend

FastAPI + SQLAlchemy + SQLite (WAL) image-generation orchestration platform.
Implements the design in [`docs/backend_design.md`](../docs/backend_design.md):
4-lane WFQ scheduler, provider failover with circuit breaker, soft-quota
penalties, daily quota accounting, SSE fan-out, signed JWT auth, and an admin
console behind the `role==admin` flag.

> 中文 TL;DR：这是 txt2img 平台的后端服务。一个进程内跑队列调度、上游中转站
> 选择、SSE 实时下发；数据落到 SQLite（WAL 模式）。

## Layout

```
backend/
├── app/
│   ├── main.py              FastAPI lifespan + router wiring
│   ├── config.py            pydantic-settings loader
│   ├── deps.py              auth + admin guards + emergency policy
│   ├── api/                 route layer (jobs, models, archive, admin/*)
│   ├── domain/              business logic (scheduler, executor, breaker,
│   │                        quota, soft-penalty, sse, lifecycle, …)
│   ├── adapters/            BaseAdapter + openai_v1, gemini_v1beta
│   ├── services/            image_io, turnstile
│   ├── schemas/             pydantic request/response models
│   ├── db/                  ORM + alembic migrations + seed
│   └── utils/               crypto, ids, security helpers
├── tests/
│   ├── test_*.py            unit / per-module integration tests
│   └── e2e/                 cross-cutting flows (login → submit → archive)
├── alembic.ini
├── pytest.ini
├── requirements.txt
└── Dockerfile
```

## Local development

The repository ships with a worktree-aware setup script at `scripts/setup-python-env.sh`.
Run it once per worktree:

```sh
scripts/setup-python-env.sh --skip-playwright
source .venv/bin/activate
```

This installs a project-local Python 3.12 runtime under `.python/`, creates
`.venv/`, and pulls every backend dependency. The bind-mount target for
production data is `data/` at the repo root. Local runs write to `./data/`
by default; tests use a per-test tmp directory.

## Common commands

| Task | Command |
|------|---------|
| Run unit + integration tests | `.venv/bin/python -m pytest backend/tests` |
| Run only the e2e suite | `.venv/bin/python -m pytest backend/tests/e2e` |
| Boot a dev backend | `.venv/bin/python -m uvicorn app.main:app --reload --app-dir backend` |
| Generate alembic migration | `cd backend && alembic revision -m "<short>" --autogenerate` |
| Apply migrations | `cd backend && alembic upgrade head` (also runs at startup) |

## Required environment variables

Copy `backend.env.example` at the repo root, fill in the required keys, and
either source it or pass via `docker compose --env-file`. Minimum set for a
fresh start:

| Var | Why |
|-----|-----|
| `JWT_SECRET` | At least 16 characters. Used to sign access tokens **and** to derive the AES-GCM key that encrypts provider API keys at rest. **Rotating it makes every stored API key unreadable** — see [DEPLOY.md](../docs/DEPLOY.md). |
| `ADMIN_USERNAME`, `ADMIN_PASSWORD` | Bootstrap admin created on first startup if missing. |
| `TURNSTILE_SITE_KEY`, `TURNSTILE_SECRET` | Cloudflare keys for captcha. The example file ships Cloudflare's "always-pass" pair — fine for dev, replace before production. |

Optional knobs (defaults in parens) — all can be overridden at runtime via
`PATCH /api/admin/config` or the env file:

- `SCHEDULER_GLOBAL_MAX_WORKERS` (32)
- `JOB_RETENTION_DAYS` (30)
- `THUMBNAIL_MAX_LONG_EDGE` (720) and `THUMBNAIL_QUALITY` (78)
- `SSE_HEARTBEAT_SECONDS` (15) and `SSE_MAX_CONNECTIONS_PER_USER` (4)
- `CORS_ORIGINS` (`*`)

## Test layout

The unit tests are colocated with the module they exercise — `tests/test_xyz.py`
mirrors `app/.../xyz.py`. The `tests/e2e/` directory is reserved for tests
that compose multiple subsystems (login → submission → SSE → archive). Most
e2e tests register a deterministic stub adapter via the `stub_adapter`
fixture so the upstream calls never hit the network.

PR-18 added seven e2e modules covering the §19 launch checklist:

- `e2e/test_login_create_archive.py` — golden path
- `e2e/test_provider_fallback.py` — fallback chain + NO_PROVIDER refund
- `e2e/test_soft_penalty.py` — turnstile gate + executor delay/fail
- `e2e/test_emergency_switches.py` — four emergency flags end-to-end
- `e2e/test_cross_device_sse.py` — same-user fanout to multiple SSE clients
- `e2e/test_wfq_anti_starvation.py` — Free lane meets the 5% floor under load
- `e2e/test_cleanup_and_admin.py` — dry-run vs real delete, admin tour

`tests/test_graceful_shutdown.py` covers the SIGTERM reconciliation helper
that force-fails any RUNNING jobs at startup or shutdown and refunds quota.

## Troubleshooting

| Symptom | Likely cause |
|---------|--------------|
| `pydantic_core.ValidationError: JWT_SECRET … at least 16 characters` | Set a longer secret in your env file. |
| `CRYPTO_ERROR` 500 from any provider route | `JWT_SECRET` was rotated; re-encrypt API keys via `PATCH /api/admin/providers/<id>` or restore the previous secret. |
| `EmergencyConfig` blocks every request after a config change | Confirm `emergency.pause_generation` / `block_new_member_login` are off via `GET /api/admin/config`. The four switches stick across restarts. |
| `aiosqlite … database is locked` under load | The startup migration must finish before `init_engine()` opens the async connection. If you see this in tests, ensure your conftest uses the `initialized_db` / `seeded_app` fixtures rather than rolling your own. |
| `127.0.0.1:8000 connection refused` from the frontend | The frontend defaults to that base URL. Set `localStorage.api_base` (or click the Backend disclosure on `/login`) to the actual backend address. |
| SSE keeps dropping after exactly N seconds | Heartbeat too long for an intervening proxy. Lower `SSE_HEARTBEAT_SECONDS`. |

## Deploying

See [`docs/DEPLOY.md`](../docs/DEPLOY.md) for the production rollout flow,
backup strategy, rollback recipe, and the SIGTERM behaviour you should expect.
