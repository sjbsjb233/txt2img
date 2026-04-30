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

import asyncio
import logging
import os
import shutil
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Awaitable, Callable, Iterable

from sqlalchemy import and_, or_, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.engine import get_session
from app.db.models import DiskUsage, Image, Job
from app.services.image_io import directory_size_bytes, is_valid_hash_id, jobs_root

logger = logging.getLogger("txt2img.cache_keeper")


# Periodic refresh cadence for the disk-usage rollup. Design doc §13.7
# says "5-minute increment"; we keep that in sync here so admin
# suggestions never lag too far behind reality.
DISK_USAGE_REFRESH_INTERVAL_SECONDS = 300


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

    We use ``is_valid_hash_id`` (a silent regex check) rather than
    ``validate_hash_id`` because this routine is called periodically and
    every stray directory under ``jobs/`` would otherwise produce a
    warning log on every refresh.

    Symlinked entries — including a symlinked ``j_<...>`` dir — are
    skipped, which keeps the disk scan inside the ``jobs/`` tree even
    when an operator drops a symlink under ``data/jobs/`` for debug.
    """
    root = jobs_root()
    if not root.exists():
        return []
    entries: list[tuple[str, Path]] = []
    # ``os.scandir`` lets us check ``is_dir(follow_symlinks=False)`` and
    # skip symlinked roots before we ever touch them.
    with os.scandir(root) as it:
        for entry in it:
            if not is_valid_hash_id(entry.name):
                continue
            try:
                if entry.is_symlink():
                    continue
                if not entry.is_dir(follow_symlinks=False):
                    continue
            except OSError:
                continue
            entries.append((entry.name, Path(entry.path)))
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


async def run_disk_usage_loop(
    *,
    interval_seconds: int = DISK_USAGE_REFRESH_INTERVAL_SECONDS,
) -> None:
    """Background task that calls :func:`refresh_disk_usage` on a loop.

    Started by ``app.main`` lifespan; cancelled at shutdown. We swallow
    every non-cancellation exception so a transient I/O failure on one
    refresh doesn't take down the loop — the next tick gets a fresh
    chance.
    """
    while True:
        try:
            await refresh_disk_usage()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("disk usage refresh failed; will retry next tick")
        await asyncio.sleep(interval_seconds)


# ===========================================================================
# Cleanup runner — design doc §13.7
# ===========================================================================


# Job statuses the cleanup endpoint understands. ``DELETED`` is allowed
# so admins can re-run a sweep that left behind orphaned rows the first
# time around (e.g. if rm -rf failed for one job).
_CLEANUP_VALID_STATUSES: tuple[str, ...] = (
    "QUEUED",
    "RUNNING",
    "SUCCEEDED",
    "FAILED",
    "CANCELLED",
    "DELETED",
)


# Which statuses should never be auto-cleaned. Live jobs need to finish.
_CLEANUP_PROTECTED_STATUSES: frozenset[str] = frozenset({"QUEUED", "RUNNING"})


@dataclass(frozen=True)
class CleanupRule:
    """One rule from a cleanup request.

    Two ``kind`` values currently:

    * ``older_than_days`` — match jobs whose ``created_at`` is at least
      ``days`` ago, optionally filtered to specific ``statuses``.
    * ``status_only`` — match jobs by ``statuses`` regardless of age
      (useful for "delete every CANCELLED job").

    The rule is intentionally simple — admins compose multiple rules
    on the request rather than us inventing a more expressive DSL.
    """

    kind: str
    days: int | None = None
    statuses: tuple[str, ...] = ()


@dataclass(frozen=True)
class CleanupSuggestion:
    """A single row in ``GET /api/admin/cleanup/suggestions``."""

    period_label: str
    rule: CleanupRule
    cutoff: str | None
    job_count: int
    image_count: int
    disk_bytes: int


@dataclass
class CleanupTaskState:
    """Server-side state for one in-flight cleanup task.

    Mutable: the runner mutates ``processed_jobs`` / ``deleted_bytes``
    as it makes progress so admins polling
    ``GET /api/admin/cleanup/<task_id>`` see a live counter.
    """

    task_id: str
    dry_run: bool
    rules: tuple[CleanupRule, ...]
    exempt_starred: bool
    status: str = "running"  # running / done / failed / cancelled
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: datetime | None = None
    total_jobs: int = 0
    processed_jobs: int = 0
    deleted_bytes: int = 0
    affected_user_ids: set[str] = field(default_factory=set)
    deleted_hash_ids: list[str] = field(default_factory=list)
    error: str | None = None

    def public(self) -> dict[str, object]:
        return {
            "task_id": self.task_id,
            "status": self.status,
            "dry_run": self.dry_run,
            "started_at": self.started_at.isoformat(),
            "finished_at": (
                self.finished_at.isoformat() if self.finished_at else None
            ),
            "total_jobs": self.total_jobs,
            "processed_jobs": self.processed_jobs,
            "deleted_bytes": self.deleted_bytes,
            "affected_user_count": len(self.affected_user_ids),
            "error": self.error,
        }


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


class CleanupValidationError(ValueError):
    """Raised by :func:`coerce_cleanup_rules` for any malformed rule."""

    def __init__(self, message: str, *, field: str | None = None) -> None:
        super().__init__(message)
        self.field = field


def coerce_cleanup_rules(raw: list[dict] | None) -> tuple[CleanupRule, ...]:
    """Validate and freeze a list of cleanup rules from request JSON.

    Rejects unknown ``kind`` values, ``older_than_days`` without a days
    value, and any status not in the job-status taxonomy. Returns a
    tuple of immutable :class:`CleanupRule` instances; an empty list
    raises (cleanup with no rules is never useful and would be a foot
    gun).
    """
    if not raw:
        raise CleanupValidationError(
            "rules must be a non-empty list", field="rules"
        )
    if not isinstance(raw, list):
        raise CleanupValidationError(
            "rules must be a list of objects", field="rules"
        )
    out: list[CleanupRule] = []
    for i, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise CleanupValidationError(
                f"rules[{i}] must be an object", field="rules"
            )
        kind = entry.get("kind")
        if kind not in ("older_than_days", "status_only"):
            raise CleanupValidationError(
                f"rules[{i}].kind must be 'older_than_days' or 'status_only'",
                field=f"rules[{i}].kind",
            )
        days_raw = entry.get("days")
        statuses_raw = entry.get("statuses") or []
        if kind == "older_than_days":
            if not isinstance(days_raw, int) or isinstance(days_raw, bool):
                raise CleanupValidationError(
                    f"rules[{i}].days must be a positive integer",
                    field=f"rules[{i}].days",
                )
            if days_raw < 0 or days_raw > 365 * 10:
                raise CleanupValidationError(
                    f"rules[{i}].days out of range",
                    field=f"rules[{i}].days",
                )
            days_val: int | None = days_raw
        else:
            days_val = None
            if not statuses_raw:
                raise CleanupValidationError(
                    "status_only rule requires non-empty statuses",
                    field=f"rules[{i}].statuses",
                )
        if not isinstance(statuses_raw, list):
            raise CleanupValidationError(
                f"rules[{i}].statuses must be a list",
                field=f"rules[{i}].statuses",
            )
        statuses_clean: list[str] = []
        for s in statuses_raw:
            if not isinstance(s, str) or s not in _CLEANUP_VALID_STATUSES:
                raise CleanupValidationError(
                    f"rules[{i}].statuses contains invalid status {s!r}",
                    field=f"rules[{i}].statuses",
                )
            statuses_clean.append(s)
            # Reject any rule that would target a protected status (live
            # jobs). Even with ``--force`` we wouldn't want admins to
            # rm -rf a running job's working dir mid-execution.
            if s in _CLEANUP_PROTECTED_STATUSES:
                raise CleanupValidationError(
                    f"rules[{i}].statuses cannot include {s!r} "
                    "(live jobs are never auto-cleaned)",
                    field=f"rules[{i}].statuses",
                )
        out.append(
            CleanupRule(
                kind=kind,
                days=days_val,
                statuses=tuple(statuses_clean),
            )
        )
    return tuple(out)


def _rule_to_suggestion_label(rule: CleanupRule) -> str:
    """Render a human label for the suggestions list."""
    if rule.kind == "older_than_days":
        if rule.statuses:
            return (
                f"{'/'.join(rule.statuses)} jobs older than {rule.days} day(s)"
            )
        return f"Older than {rule.days} day(s)"
    # status_only
    return f"{'/'.join(rule.statuses)} jobs · any age"


def _suggestion_rules() -> tuple[CleanupRule, ...]:
    """The fixed set of suggestions surfaced to admin (design doc §13.7)."""
    return (
        CleanupRule(
            kind="older_than_days",
            days=7,
            statuses=("SUCCEEDED", "FAILED"),
        ),
        CleanupRule(
            kind="older_than_days",
            days=30,
            statuses=("SUCCEEDED", "FAILED"),
        ),
        CleanupRule(
            kind="older_than_days",
            days=90,
            statuses=("SUCCEEDED", "FAILED"),
        ),
        CleanupRule(
            kind="older_than_days",
            days=1,
            statuses=("FAILED",),
        ),
        CleanupRule(
            kind="status_only",
            statuses=("CANCELLED",),
        ),
    )


# ---------------------------------------------------------------------------
# Querying
# ---------------------------------------------------------------------------


def _utc_cutoff_for_rule(rule: CleanupRule, *, now: datetime) -> datetime | None:
    """Return ``now - rule.days`` when applicable, else None."""
    if rule.kind != "older_than_days" or rule.days is None:
        return None
    return now - timedelta(days=rule.days)


async def _select_jobs_for_rule(
    session: AsyncSession,
    rule: CleanupRule,
    *,
    exempt_starred: bool,
    now: datetime,
    limit: int | None = None,
) -> list[Job]:
    """Fetch the ``Job`` rows that match ``rule``.

    ``exempt_starred`` causes a join against ``images.starred=1``: any
    job with at least one starred image is filtered out so the admin
    can preserve favourites across cleanup sweeps (design doc §20
    open-question 1, default-yes).

    Live statuses (``QUEUED``/``RUNNING``) are always excluded. We never
    auto-clean a job that's still in flight.
    """
    where_clauses: list = []

    # Status filter
    if rule.statuses:
        where_clauses.append(Job.status.in_(rule.statuses))
    else:
        # No explicit status filter → exclude live ones at minimum.
        where_clauses.append(Job.status.notin_(_CLEANUP_PROTECTED_STATUSES))

    # Age filter
    cutoff = _utc_cutoff_for_rule(rule, now=now)
    if cutoff is not None:
        where_clauses.append(Job.created_at < cutoff)

    # Defensive: never sweep live jobs even if a rule somehow names them.
    where_clauses.append(Job.status.notin_(_CLEANUP_PROTECTED_STATUSES))

    stmt = select(Job).where(and_(*where_clauses))

    if exempt_starred:
        # Exclude jobs that have at least one starred image. We do this
        # via NOT IN (subquery) rather than LEFT JOIN + WHERE NULL so
        # SQLite can use the ``idx_images_job`` index on the inner side.
        starred_subq = (
            select(Image.job_id).where(Image.starred == 1).subquery()
        )
        stmt = stmt.where(Job.id.notin_(select(starred_subq.c.job_id)))

    if limit is not None:
        stmt = stmt.limit(limit)

    rows = (await session.execute(stmt)).scalars().all()
    return list(rows)


async def _summarise_rule(
    session: AsyncSession,
    rule: CleanupRule,
    *,
    exempt_starred: bool,
    now: datetime,
) -> CleanupSuggestion:
    """Compute job count, image count, and disk bytes for ``rule``."""
    jobs = await _select_jobs_for_rule(
        session, rule, exempt_starred=exempt_starred, now=now
    )
    job_count = len(jobs)
    image_count = 0
    disk_bytes = 0
    if jobs:
        job_ids = [j.id for j in jobs]
        # Image count is exact; cheaper than walking outputs/* on disk.
        # Chunked for SQLITE_MAX_VARIABLE_NUMBER safety.
        chunk = 500
        for i in range(0, len(job_ids), chunk):
            slice_ids = job_ids[i : i + chunk]
            cnt = (
                await session.execute(
                    select(Image)
                    .where(Image.job_id.in_(slice_ids))
                )
            ).scalars().all()
            image_count += len(cnt)
        # Disk size: walk every job dir on disk. This is the slow path;
        # the suggestions endpoint refreshes lazily on each request and
        # also benefits from the periodic ``refresh_disk_usage`` rollup
        # if it's been kept warm.
        for job in jobs:
            disk_bytes += directory_size_bytes(jobs_root() / job.hash_id)

    cutoff = _utc_cutoff_for_rule(rule, now=now)
    return CleanupSuggestion(
        period_label=_rule_to_suggestion_label(rule),
        rule=rule,
        cutoff=cutoff.isoformat() if cutoff else None,
        job_count=job_count,
        image_count=image_count,
        disk_bytes=disk_bytes,
    )


async def list_cleanup_suggestions(
    *, exempt_starred: bool = True
) -> list[CleanupSuggestion]:
    """Return the fixed set of suggestion buckets, each with live counts.

    The buckets are deliberately stable so admin UIs can render them
    with predictable layout. ``exempt_starred=True`` is the default
    because the design doc recommends preserving favourites.
    """
    now = datetime.now(timezone.utc)
    out: list[CleanupSuggestion] = []
    async with get_session() as session:
        for rule in _suggestion_rules():
            out.append(
                await _summarise_rule(
                    session, rule, exempt_starred=exempt_starred, now=now
                )
            )
    return out


async def estimate_cleanup(
    rules: tuple[CleanupRule, ...], *, exempt_starred: bool
) -> CleanupSuggestion:
    """Aggregate one or more rules into a single dry-run estimate.

    Used by the dry-run path: admin sends a custom rule set, we sum up
    the affected jobs / images / bytes without touching anything.

    Jobs matching multiple rules are counted exactly once because we
    union job ids across rules before measuring.
    """
    now = datetime.now(timezone.utc)
    seen: dict[str, Job] = {}
    async with get_session() as session:
        for rule in rules:
            jobs = await _select_jobs_for_rule(
                session, rule, exempt_starred=exempt_starred, now=now
            )
            for job in jobs:
                seen[job.id] = job

        job_count = len(seen)
        image_count = 0
        disk_bytes = 0
        if seen:
            job_ids = list(seen.keys())
            chunk = 500
            for i in range(0, len(job_ids), chunk):
                slice_ids = job_ids[i : i + chunk]
                cnt = (
                    await session.execute(
                        select(Image)
                        .where(Image.job_id.in_(slice_ids))
                    )
                ).scalars().all()
                image_count += len(cnt)
            for job in seen.values():
                disk_bytes += directory_size_bytes(
                    jobs_root() / job.hash_id
                )

    return CleanupSuggestion(
        period_label="custom",
        rule=CleanupRule(kind="custom", days=None, statuses=()),
        cutoff=None,
        job_count=job_count,
        image_count=image_count,
        disk_bytes=disk_bytes,
    )


# ---------------------------------------------------------------------------
# Cleanup task registry
# ---------------------------------------------------------------------------


class _CleanupRegistry:
    """In-memory store of in-flight / recent cleanup tasks.

    A single uvicorn worker (design doc §1.4) makes this safe; if we
    ever go multi-process the cleanup orchestration moves to a real
    queue.

    The registry is bounded — we keep only the most recent N tasks
    so a long-running process doesn't grow without limit. Active
    tasks (``status='running'``) are never evicted.
    """

    _MAX_RECENT = 20

    def __init__(self) -> None:
        self._tasks: dict[str, CleanupTaskState] = {}
        self._lock = threading.Lock()

    def add(self, state: CleanupTaskState) -> None:
        with self._lock:
            self._tasks[state.task_id] = state
            self._evict_locked()

    def get(self, task_id: str) -> CleanupTaskState | None:
        return self._tasks.get(task_id)

    def list(self) -> list[CleanupTaskState]:
        with self._lock:
            return sorted(
                self._tasks.values(),
                key=lambda s: s.started_at,
                reverse=True,
            )

    def _evict_locked(self) -> None:
        """Drop oldest finished tasks until we're under the cap."""
        if len(self._tasks) <= self._MAX_RECENT:
            return
        # Sort by finished_at (None for still-running), evict the oldest
        # finished ones first.
        finished = sorted(
            (s for s in self._tasks.values() if s.status != "running"),
            key=lambda s: s.finished_at or s.started_at,
        )
        excess = len(self._tasks) - self._MAX_RECENT
        for s in finished[:excess]:
            self._tasks.pop(s.task_id, None)

    def reset_for_tests(self) -> None:
        with self._lock:
            self._tasks.clear()


_registry: _CleanupRegistry | None = None


def get_cleanup_registry() -> _CleanupRegistry:
    global _registry
    if _registry is None:
        _registry = _CleanupRegistry()
    return _registry


def reset_cleanup_registry_for_tests() -> None:
    global _registry
    _registry = None


# ---------------------------------------------------------------------------
# Cleanup execution
# ---------------------------------------------------------------------------


# Optional callback signature: ``broadcast_task_deleted(user_id, hash_id)``.
# The admin cleanup route wires this up to the SSE hub so users see
# their archive entries disappear in realtime.
TaskDeletedBroadcaster = Callable[[str, str], Awaitable[None]]


async def execute_cleanup(
    rules: tuple[CleanupRule, ...],
    *,
    exempt_starred: bool,
    broadcaster: TaskDeletedBroadcaster | None = None,
) -> CleanupTaskState:
    """Run one cleanup pass. Synchronous within the task: the caller
    decides whether to await or fire-and-forget.

    Two-phase design:

    1. Mark every matched job ``status='DELETED'`` in DB. Read-then-
       update so we know exactly which job_ids survived the protected-
       status filter.
    2. ``rm -rf`` each ``data/jobs/<hash>/`` directory. Filesystem work
       happens after the DB update so a torn run leaves orphan dirs
       (recoverable) rather than orphan rows (worse).

    Each deletion broadcasts ``task_deleted`` over SSE if a
    ``broadcaster`` callback is provided. The function never raises on
    one bad job — it logs, sets ``state.error`` if every job fails,
    and continues.
    """
    state = CleanupTaskState(
        task_id="cleanup_" + uuid.uuid4().hex[:12],
        dry_run=False,
        rules=rules,
        exempt_starred=exempt_starred,
    )
    get_cleanup_registry().add(state)

    now = datetime.now(timezone.utc)

    # Phase 1: collect target jobs (de-duplicated across rules).
    targets: dict[str, Job] = {}
    try:
        async with get_session() as session:
            for rule in rules:
                jobs = await _select_jobs_for_rule(
                    session, rule, exempt_starred=exempt_starred, now=now
                )
                for j in jobs:
                    targets[j.id] = j
    except Exception as exc:  # pragma: no cover — defensive
        state.status = "failed"
        state.error = f"target collection failed: {exc}"
        state.finished_at = datetime.now(timezone.utc)
        logger.exception("cleanup: target collection failed")
        return state

    state.total_jobs = len(targets)
    if not targets:
        state.status = "done"
        state.finished_at = datetime.now(timezone.utc)
        return state

    # Phase 2: mark + rm. We do it one job at a time to keep the
    # progress counter useful and to avoid a single huge transaction
    # blocking other writers on SQLite. Per-job overhead is negligible
    # at our scale (worst case ~10k jobs).
    for job in targets.values():
        try:
            await _mark_job_deleted(job.id)
            await _rm_job_dir(job.hash_id)
            state.processed_jobs += 1
            state.deleted_hash_ids.append(job.hash_id)
            state.affected_user_ids.add(job.user_id)
            if broadcaster is not None:
                try:
                    await broadcaster(job.user_id, job.hash_id)
                except Exception:
                    # Broadcast failures must not break cleanup —
                    # the DB delete and rm have already happened.
                    logger.exception(
                        "cleanup: broadcast failed for %s", job.hash_id
                    )
        except Exception:
            logger.exception("cleanup: failed to clean job %s", job.hash_id)

    state.status = "done"
    state.finished_at = datetime.now(timezone.utc)
    logger.info(
        "cleanup %s done: jobs=%d/%d users=%d",
        state.task_id,
        state.processed_jobs,
        state.total_jobs,
        len(state.affected_user_ids),
    )
    return state


async def _mark_job_deleted(job_internal_id: str) -> None:
    """Flip a job's status to DELETED. Idempotent."""
    async with get_session() as session:
        await session.execute(
            update(Job)
            .where(Job.id == job_internal_id)
            .values(
                status="DELETED",
                updated_at=datetime.now(timezone.utc),
            )
        )


async def _rm_job_dir(hash_id: str) -> int:
    """``rm -rf data/jobs/<hash_id>/`` and return the bytes freed.

    Runs in a thread because :mod:`shutil` is blocking. Returns 0 if
    the directory was already missing (idempotent).
    """
    target = jobs_root() / hash_id
    if not target.exists():
        return 0
    bytes_freed = directory_size_bytes(target)
    await asyncio.to_thread(shutil.rmtree, target, True)
    return bytes_freed
