# Deployment guide

> 中文 TL;DR：生产环境只需要把 `data/` 整个目录纳入备份策略。`JWT_SECRET`
> 一旦换掉，所有已存的 Provider API key 都解不出来——所以**先备份再换密**。

## Topology

```
┌────────────────────────────┐         ┌─────────────────────────┐
│ frontend container :80     │ ◄─────► │ backend container :8000 │
│  (nginx serving dist/)     │         │  (uvicorn → SQLite WAL) │
└────────────────────────────┘         └─────────────────────────┘
                                              │
                                              ▼
                                    bind-mount ./data → /app/data
                                    (DB, jobs/, announcements/, tmp/)
```

The repo ships with two compose files:

- `docker-compose.dev.yml` — everything builds locally, no Watchtower.
- `docker-compose.prod.yml` — pulls signed images from GHCR, follows
  `:stable`, restarts containers via Watchtower (5-minute poll).

## First-time bring-up

1. `cp backend.env.example backend.env` and fill in:
   - `JWT_SECRET` (≥ 16 chars; rotate **with care**, see below).
   - `ADMIN_USERNAME` / `ADMIN_PASSWORD` (only used at first start; later
     changes via the admin UI).
   - `CORS_ORIGINS` (comma-separated origins, or `*`).
   - `TURNSTILE_SITE_KEY` / `TURNSTILE_SECRET` (real values for prod —
     the example file has Cloudflare's "always-pass" pair).
2. `mkdir -p data` and ensure the user that owns the docker socket has
   read-write access. Inside the container the DB writes as the image's
   default user, so on Linux you may need `chown -R 1000:1000 data`.
3. `docker login ghcr.io -u <github-user>` with a PAT that has
   `read:packages`.
4. `docker compose -f docker-compose.prod.yml up -d`.

The first start runs `alembic upgrade head` and seeds the DB
(`tiers`, `config` defaults, the bootstrap admin row). Subsequent
starts are idempotent.

## Persistence

Everything that must survive a container rebuild lives under `./data/`:

- `data/txt2img.db`, `*.db-wal`, `*.db-shm` — SQLite database in WAL
  mode. The WAL/SHM files grow during writes and shrink at checkpoint;
  back up the trio together.
- `data/jobs/<hash_id>/` — per-job folder with originals, thumbnails,
  the `meta.json` snapshot, and the `timeline.jsonl` audit log.
- `data/announcements/<ann_id>/` — admin-uploaded banner images.
- `data/tmp/` — upload staging, safe to drop.

## Backups

The full backup is `tar -C data .`, but doing it while the backend is
serving traffic risks copying a partial WAL. Two safer options:

1. **Snapshot via SQLite's `.backup`** (preferred; runs against a live DB):
   ```sh
   docker exec txt2img-backend \
       sqlite3 /app/data/txt2img.db ".backup '/app/data/backup.db'"
   tar -C data -czf "txt2img-$(date +%F).tar.gz" backup.db jobs/ announcements/
   ```
   The `.backup` API takes a consistent snapshot without blocking writers.
2. **Stop-and-copy** (simplest, costs a brief outage):
   ```sh
   docker compose -f docker-compose.prod.yml stop backend
   tar -C data -czf "txt2img-$(date +%F).tar.gz" .
   docker compose -f docker-compose.prod.yml start backend
   ```

Either way, store at least the JWT secret alongside the backup — without it
the encrypted `providers.api_key_enc` blobs are unreadable.

## Rolling restarts and graceful shutdown

Watchtower runs with `--rolling-restart`, so it stops the old container
before the new one starts. The backend installs a SIGTERM handler that:

1. Stops the scheduler from accepting new dispatches (`scheduler.drain()`,
   30 s timeout).
2. Waits for in-flight workers to land terminal states (SUCCEEDED / FAILED).
3. Force-fails any leftover ``RUNNING`` rows with reason
   ``SERVER_RESTART`` and **refunds the user's daily quota** (clamped at
   zero). Implemented in `backend/app/domain/graceful_shutdown.py`.
4. Closes the SQLAlchemy engine and exits.

If the host process dies hard (panic, kernel OOM, container kill -9),
the same reconciliation runs at the next startup — see
``force_fail_running_jobs`` in the same module. Quota counters survive
container churn so the user is never billed for a job we crashed.

## Rolling back

The simplest path is to flip the GHCR `:stable` tag back to the previous
`:vX.Y.Z` (Watchtower picks up the change on its next 5-minute poll).
Schema migrations are forward-only: if a release ships a destructive
migration, restore from the most recent backup before the rollback. The
migrations directory is at `backend/app/db/migrations/`.

If a release introduces a bad config default, you can revert without
re-pulling the image by `PATCH /api/admin/config` with the old value;
the live config table wins over the env file's `EmergencyConfig` etc.

## Rotating `JWT_SECRET`

The secret has two jobs and rotating it affects both:

1. **Signing access tokens.** Existing tokens become invalid after a
   rotation; users must log in again. This is normal and acceptable.
2. **Encrypting `providers.api_key_enc`.** This is the dangerous half.
   The current secret derives the AES-GCM key, and a rotation makes
   every existing ciphertext unreadable. The backend will return
   `CRYPTO_ERROR` 500 from any provider that needs decryption.

The safe rotation procedure is:

1. Take a backup (so you can revert).
2. With the **old** secret still in `backend.env`, walk every provider
   row via `PATCH /api/admin/providers/<id>` and re-supply the same
   `api_key`. The PATCH re-encrypts under the *current* secret.
3. Stop the backend; replace `JWT_SECRET` in `backend.env`; start it.
4. Repeat step 2 with the **new** secret to re-encrypt everything once
   more under the new key. Without the second pass, providers added
   between step 2 and step 3 will break.

A future PR can ship a re-encrypt script; today this is a manual flow.

## Scaling out (when you outgrow SQLite)

The architecture intentionally keeps one process and one SQLite file in
v1. When you need horizontal scaling:

- Swap the DB to PostgreSQL (the SQLAlchemy models translate; only the
  PRAGMA noise in `db/engine.py` would need a guard).
- Move the in-memory queue to Redis Streams (the lane abstraction is
  already isolated in `domain/job_queue.py`).
- Move SSE fan-out to Redis Pub/Sub or Kafka (`SSEHub.broadcast_to_user`
  is the seam — every emitter goes through it).

None of these are necessary at v1's scale. Keep the bind-mount + single
container deploy until the §19 ceilings start showing strain.

## Staying inside the SLO

- The four `emergency.*` switches are persistent; flipping them via
  `PATCH /api/admin/config` survives restarts. Keep an operator
  cheatsheet in `docs/runbooks/`.
- Watch `GET /api/admin/metrics/overview` for queue depth and WFQ lane
  starvation. The Free lane should always sit at ≥ 5% of dispatches.
- Cleanup runs on demand from the admin UI; cron it via
  `gh run-once …` on systems with no admin online.

## Frequently asked questions

**Q: Can I run two backend replicas behind a load balancer?**  
Not yet. The 4-lane WFQ queue is in-process. The frontend talks to a
single backend; if you must front-load, run two SQLite copies and a
sticky balancer, but the design doc warns this is a v2 problem.

**Q: How do I onboard a new admin?**  
The bootstrap admin (env-defined) creates additional admins via
`POST /api/admin/users {role: "admin"}`. There's no self-service path —
this is intentional.

**Q: Where do I look when SSE drops?**  
Lower `SSE_HEARTBEAT_SECONDS`, then check `GET /api/admin/sse/clients`
to see the live connection list. PR-12 added per-user buffering with
`Last-Event-ID` replay, so reconnects within 5 minutes recover state.
