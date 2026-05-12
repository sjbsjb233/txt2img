"""Atomic helpers for the ``jobs`` table.

Two operations need to be carefully serialised:

- **Allocating ``seq_no``.** Each user has an independent monotonic
  counter (``users.last_seq_no``). When two requests arrive at once we
  must hand out two distinct sequential numbers, never the same one,
  and never skip ahead by more than 1. Naive read-modify-write on the
  ORM object is racy (two sessions read N, both write N+1, the unique
  index ``idx_jobs_user_seq`` on ``(user_id, seq_no)`` then refuses
  one of them — which manifests to the user as an unexplained 5xx).
- **Inserting the matching ``jobs`` row** with the just-allocated
  ``seq_no``, in the same transaction as the increment so the two
  durable changes are atomic.

This module exists at PR-08 even though the full job-create flow ships
in PR-13 because ``last_seq_no`` is the most subtle invariant in the
job model and the algorithm benefits from being unit-tested in
isolation. PR-13's :class:`POST /api/jobs` handler will call
:meth:`JobsRepository.allocate_seq_no` followed by an
:meth:`JobsRepository.insert_queued` from inside one transaction.

The atomic strategy uses SQL's ``UPDATE ... RETURNING last_seq_no``
form, which on SQLite (with WAL + ``BEGIN IMMEDIATE`` busy-wait
behavior) takes a write lock during the UPDATE. Concurrent allocations
serialise through that lock, so the value returned by ``RETURNING`` is
the post-update value of *this* session — never another session's.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.engine import get_session
from app.db.models import Job, User
from app.utils.ids import new_job_hash_id, new_job_internal_id

logger = logging.getLogger("txt2img.jobs_repo")


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AllocatedSeqNo:
    """Outcome of :meth:`JobsRepository.allocate_seq_no`."""

    user_id: str
    seq_no: int


@dataclass(frozen=True)
class CreatedJob:
    """Snapshot of the freshly inserted ``jobs`` row."""

    job_id: str
    hash_id: str
    user_id: str
    seq_no: int
    set_id: str | None
    model: str
    status: str
    created_at: datetime


class JobsRepositoryError(RuntimeError):
    """Raised on illegal calls (missing user, duplicate hash_id, ...)."""


# ---------------------------------------------------------------------------
# JobsRepository
# ---------------------------------------------------------------------------


class JobsRepository:
    """Stateless helpers for ``jobs`` writes that need atomicity.

    Read paths (``GET /api/jobs/<hash>`` etc.) live in PR-14 and don't
    need a repository — they are plain ``SELECT`` against
    :class:`AsyncSession`.
    """

    async def allocate_seq_no(
        self,
        user_id: str,
        *,
        session: AsyncSession,
    ) -> AllocatedSeqNo:
        """Atomically increment ``users.last_seq_no`` and return it.

        Issues a single SQL ``UPDATE ... SET last_seq_no = last_seq_no + 1
        WHERE id = :uid RETURNING last_seq_no``. The atomic increment
        eliminates the lost-update race a SELECT + UPDATE would have.

        The session is not committed — the caller folds this into the
        same transaction that inserts the ``jobs`` row so seq_no
        allocation and job insert succeed or fail together.
        """
        stmt = (
            update(User)
            .where(User.id == user_id)
            .values(last_seq_no=User.last_seq_no + 1)
            .returning(User.last_seq_no)
        )
        row = (await session.execute(stmt)).one_or_none()
        if row is None:
            raise JobsRepositoryError(f"user {user_id!r} not found")
        seq = int(row[0])
        return AllocatedSeqNo(user_id=user_id, seq_no=seq)

    async def insert_queued(
        self,
        *,
        user_id: str,
        tier_at_submit: str,
        model: str,
        params_json: str,
        flags_json: str = "{}",
        client_request_id: str | None = None,
        set_id: str | None = None,
        session_id: str | None = None,
        parent_hash_id: str | None = None,
        derivation_kind: str | None = None,
        parent_order: int | None = None,
        batch_id: str | None = None,
        session: AsyncSession,
    ) -> CreatedJob:
        """Insert a fresh ``QUEUED`` job and return its identifiers.

        Allocates ``seq_no`` for the user inside the same transaction.
        Generates ``id`` (internal pk) and ``hash_id`` (public id) via
        :mod:`app.utils.ids`. Sets ``queued_at`` to the same value as
        ``created_at`` so admin views see a single timestamp at insert
        time, with later transitions overwriting nothing here.

        Raises :class:`JobsRepositoryError` if the user is missing.
        Hash id collisions are astronomically unlikely (62^12 ≈ 3·10^21)
        so we don't retry — a unique-index violation will propagate to
        the caller and yield a 500 the operator can investigate.
        """
        allocation = await self.allocate_seq_no(user_id, session=session)

        now = datetime.now(timezone.utc)
        job = Job(
            id=new_job_internal_id(),
            hash_id=new_job_hash_id(),
            user_id=user_id,
            tier_at_submit=tier_at_submit,
            seq_no=allocation.seq_no,
            set_id=set_id,
            session_id=session_id,
            batch_id=batch_id,
            model=model,
            params_json=params_json,
            flags_json=flags_json,
            client_request_id=client_request_id,
            status="QUEUED",
            status_reason=None,
            provider_used=None,
            retries=0,
            cost_cny=0.0,
            parent_hash_id=parent_hash_id,
            derivation_kind=derivation_kind,
            parent_order=parent_order,
            cost_dollars=None,
            usage_input_tokens=None,
            usage_output_tokens=None,
            created_at=now,
            queued_at=now,
            dispatched_at=None,
            started_at=None,
            finished_at=None,
            updated_at=now,
        )
        session.add(job)
        await session.flush()

        return CreatedJob(
            job_id=job.id,
            hash_id=job.hash_id,
            user_id=job.user_id,
            seq_no=job.seq_no,
            set_id=job.set_id,
            model=job.model,
            status=job.status,
            created_at=now,
        )

    async def count_active_by_user(
        self,
        user_id: str,
        *,
        session: AsyncSession | None = None,
    ) -> int:
        """Return the number of QUEUED+RUNNING jobs for a user.

        Used by PR-09's queue guard as a stand-in for "in-flight queue
        capacity" until the real :class:`JobQueue` lands in PR-11. We
        place it here rather than in PR-09 because the SELECT pattern
        belongs alongside the other ``jobs`` access helpers.

        Implemented as a single ``SELECT COUNT(*)`` so the DB does the
        counting and only one scalar comes back over the wire — at
        scale a per-row materialisation would dominate this call.
        """
        async def _count(s: AsyncSession) -> int:
            value = (
                await s.execute(
                    select(func.count())
                    .select_from(Job)
                    .where(
                        Job.user_id == user_id,
                        Job.status.in_(("QUEUED", "RUNNING")),
                    )
                )
            ).scalar_one()
            return int(value)

        if session is None:
            async with get_session() as s:
                return await _count(s)
        return await _count(session)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------


_instance: JobsRepository | None = None


def get_jobs_repository() -> JobsRepository:
    global _instance
    if _instance is None:
        _instance = JobsRepository()
    return _instance


# ---------------------------------------------------------------------------
# Convenience helper for pydantic-style payloads
# ---------------------------------------------------------------------------


def serialise_params(params: dict[str, Any]) -> str:
    """Serialise the normalised request params for storage in ``params_json``.

    Centralised here so the create-job handler in PR-13 and the admin
    debug viewer use the same ``json.dumps`` flags. Sort keys so the
    diff between two job rows is stable; reject non-JSON-serialisable
    values up front rather than letting them blow up at SELECT time.
    """
    import json

    return json.dumps(params, separators=(",", ":"), ensure_ascii=False, sort_keys=True)
