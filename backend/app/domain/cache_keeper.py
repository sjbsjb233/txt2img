"""Disk-usage refresh + (eventually) job retention sweep.

PR-07 ships only ``refresh_disk_usage`` — the function the admin
``GET /api/admin/cleanup/suggestions`` route reads from. The forever
loop, real cleanup task, and SSE broadcasting all arrive in PR-16.

Why this module exists already:

- The ``disk_usage`` table needs *some* writer before PR-16. Without it
  the admin suggestions endpoint would have no data to read on day one,
  and the schema check tests already assert the table exists.
- The cleanup logic depends on reading job rows + on-disk sizes
  together. Centralising that here means PR-16 can build the cleanup
  endpoint by composing the helpers below rather than re-deriving the
  filesystem walk a second time.

Scope categories follow design doc §13.7 — these are the labels the
admin UI presents as cleanup suggestions:

- ``jobs_total``               every job dir on disk
- ``jobs_older_than_7d``       created_at older than 7 days
- ``jobs_older_than_30d``      created_at older than 30 days
- ``jobs_older_than_90d``      created_at older than 90 days
- ``jobs_failed_older_than_1d`` status=FAILED + finished_at > 1 day ago
- ``jobs_cancelled``           status=CANCELLED, any age

Job dirs that exist on disk but have no matching DB row count toward
``jobs_total`` only — we treat them as orphan storage that an operator
can mop up directly. The age-based scopes need ``jobs.created_at``, so
they only count rows we can resolve.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.engine import get_session
from app.db.models import DiskUsage, Job
from app.services.image_io import directory_size_bytes, jobs_root, validate_hash_id

logger = logging.getLogger("txt2img.cache_keeper")


# ---------------------------------------------------------------------------
# Scope keys — kept as constants so the admin layer (PR-16) can refer to
# them without magic strings. Order matches the suggestion UI.
# ---------------------------------------------------------------------------

SCOPE_JOBS_TOTAL = "jobs_total"
SCOPE_JOBS_OLDER_THAN_7D = "jobs_older_than_7d"
SCOPE_JOBS_OLDER_THAN_30D = "jobs_older_than_30d"
SCOPE_JOBS_OLDER_THAN_90D = "jobs_older_than_90d"
SCOPE_JOBS_FAILED_OLDER_THAN_1D = "jobs_failed_older_than_1d"
SCOPE_JOBS_CANCELLED = "jobs_cancelled"

ALL_SCOPES: tuple[str, ...] = (
    SCOPE_JOBS_TOTAL,
    SCOPE_JOBS_OLDER_THAN_7D,
    SCOPE_JOBS_OLDER_THAN_30D,
    SCOPE_JOBS_OLDER_THAN_90D,
    SCOPE_JOBS_FAILED_OLDER_THAN_1D,
    SCOPE_JOBS_CANCELLED,
)


@dataclass(frozen=True)
class ScopeUsage:
    """Aggregated bytes + job count for one scope."""

    bytes: int
    job_count: int


# ---------------------------------------------------------------------------
# The walk
# ---------------------------------------------------------------------------


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _list_job_dirs() -> list[tuple[str, Path]]:
    """Return ``(hash_id, path)`` pairs for every valid job dir on disk.

    Directories that don't match the ``j_<12>`` shape are ignored — we
    don't want stray files (``tmp/``, ``announcements/``, lockfiles, an
    operator's ``debug/`` dir) to be miscounted as jobs.
    """
    root = jobs_root()
    if not root.exists():
        return []
    entries: list[tuple[str, Path]] = []
    for child in root.iterdir():
        if not child.is_dir():
            continue
        try:
            validate_hash_id(child.name)
        except Exception:
            continue
        entries.append((child.name, child))
    return entries


async def _load_jobs_index(
    session: AsyncSession, hash_ids: list[str]
) -> dict[str, Job]:
    """Bulk-load Job rows for the given hash_ids in chunks.

    SQLite has a default ``SQLITE_MAX_VARIABLE_NUMBER`` of 999; chunking
    keeps us well under that even with thousands of jobs on disk.
    """
    if not hash_ids:
        return {}
    out: dict[str, Job] = {}
    chunk = 500
    for i in range(0, len(hash_ids), chunk):
        slice_ids = hash_ids[i : i + chunk]
        rows = (
            await session.execute(
                select(Job).where(Job.hash_id.in_(slice_ids))
            )
        ).scalars().all()
        for row in rows:
            out[row.hash_id] = row
    return out


def _categorise(job: Job | None, now: datetime) -> set[str]:
    """Return the set of age/status scopes a job belongs to.

    Every job is in ``jobs_total``. Rows we couldn't resolve to a DB
    entry stop there — without ``created_at`` and ``status`` we can't
    decide the rest, and an operator looking at ``jobs_total`` minus
    the others can still spot orphan storage.
    """
    scopes: set[str] = {SCOPE_JOBS_TOTAL}
    if job is None:
        return scopes

    created = job.created_at
    if created is not None:
        # SQLite returns naive datetimes. Treat them as UTC since that
        # is how we write them (see _utc_now_iso in image_io and the
        # ``CURRENT_TIMESTAMP`` server defaults which SQLite emits in UTC).
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        age = now - created
        if age >= timedelta(days=7):
            scopes.add(SCOPE_JOBS_OLDER_THAN_7D)
        if age >= timedelta(days=30):
            scopes.add(SCOPE_JOBS_OLDER_THAN_30D)
        if age >= timedelta(days=90):
            scopes.add(SCOPE_JOBS_OLDER_THAN_90D)

    if job.status == "FAILED" and job.finished_at is not None:
        finished = job.finished_at
        if finished.tzinfo is None:
            finished = finished.replace(tzinfo=timezone.utc)
        if (now - finished) >= timedelta(days=1):
            scopes.add(SCOPE_JOBS_FAILED_OLDER_THAN_1D)

    if job.status == "CANCELLED":
        scopes.add(SCOPE_JOBS_CANCELLED)

    return scopes


async def refresh_disk_usage() -> dict[str, ScopeUsage]:
    """Recompute per-scope byte and job-count totals; persist to ``disk_usage``.

    Walks ``data/jobs/`` once, joining each dir against the ``jobs`` table
    by ``hash_id``. The function is idempotent: it always writes one row
    per scope in :data:`ALL_SCOPES` even when totals are zero, so the
    admin endpoint can rely on the table being complete.

    Returns the same totals it wrote, keyed by scope, for convenience.
    """
    now = _utcnow()
    job_dirs = _list_job_dirs()

    async with get_session() as session:
        index = await _load_jobs_index(session, [hid for hid, _ in job_dirs])

        totals: dict[str, ScopeUsage] = {
            scope: ScopeUsage(bytes=0, job_count=0) for scope in ALL_SCOPES
        }

        for hash_id, dir_path in job_dirs:
            size = directory_size_bytes(dir_path)
            scopes = _categorise(index.get(hash_id), now)
            for scope in scopes:
                cur = totals[scope]
                totals[scope] = ScopeUsage(
                    bytes=cur.bytes + size,
                    job_count=cur.job_count + 1,
                )

        # Single transactional write for all scopes. We use SQLite's
        # ``INSERT ... ON CONFLICT(scope) DO UPDATE`` so the routine is
        # an upsert: first call inserts, subsequent calls overwrite.
        for scope, usage in totals.items():
            stmt = sqlite_insert(DiskUsage).values(
                scope=scope,
                bytes=usage.bytes,
                job_count=usage.job_count,
                refreshed_at=now,
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=[DiskUsage.scope],
                set_={
                    "bytes": stmt.excluded.bytes,
                    "job_count": stmt.excluded.job_count,
                    "refreshed_at": stmt.excluded.refreshed_at,
                },
            )
            await session.execute(stmt)

    logger.info(
        "disk usage refreshed: jobs_total=%d bytes=%d",
        totals[SCOPE_JOBS_TOTAL].job_count,
        totals[SCOPE_JOBS_TOTAL].bytes,
    )
    return totals
