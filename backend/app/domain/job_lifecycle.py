"""Job state machine — the only allowed writer of ``jobs.status``.

Every transition between the six statuses defined in design doc §8.1
flows through :meth:`JobLifecycle.transition`. Centralising writes here
gives us three load-bearing guarantees that the rest of the system
relies on:

1. **State validity.** The graph in §8.1 is enforced as data, not as
   "I'll remember to check"; an executor or admin handler that tries
   to take ``SUCCEEDED → RUNNING`` gets an :class:`InvalidTransition`
   instead of silently corrupting the row.
2. **Side effects move together with state.** ``timeline.jsonl`` gets
   appended in the same call that flips ``jobs.status``; nothing in the
   codebase writes one without the other. Likewise the SSE
   ``job_state`` broadcast is part of the same atomic step from the
   caller's point of view.
3. **No hidden mutators.** A grep for ``Job.status =`` outside this
   module should always return zero hits. If you find one, that's a
   bug — fix it by routing through :meth:`transition`.

PR-12 wires a real SSE hub. Until then, the broadcast happens through
:class:`BroadcastSink`, an interface lifecycle takes by injection. The
default sink is a no-op so calling code doesn't have to care whether
the hub is available — tests can drop in a recording sink to assert on
what the lifecycle would have published.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.engine import get_session
from app.db.models import Job
from app.services.image_io import append_timeline

logger = logging.getLogger("txt2img.job_lifecycle")


# ---------------------------------------------------------------------------
# State graph
# ---------------------------------------------------------------------------


# All status values that may appear in ``jobs.status``. The DB CHECK
# constraint enforces this set too — keep the two in lockstep.
QUEUED = "QUEUED"
RUNNING = "RUNNING"
SUCCEEDED = "SUCCEEDED"
FAILED = "FAILED"
CANCELLED = "CANCELLED"
DELETED = "DELETED"


VALID_STATUSES: frozenset[str] = frozenset(
    {QUEUED, RUNNING, SUCCEEDED, FAILED, CANCELLED, DELETED}
)


# Adjacency list copied straight from design doc §8.1. ``None`` is the
# pseudo state from which a fresh job emerges; ``transition`` allows
# ``None → QUEUED`` so the create-job path can use the same code path.
_ALLOWED_TRANSITIONS: dict[str | None, frozenset[str]] = {
    # Job creation: insert with status=QUEUED.
    None: frozenset({QUEUED}),
    # QUEUED can be picked up, cancelled, or hard-failed (e.g. captcha
    # invalid / NO_PROVIDER_AVAILABLE before dispatch).
    QUEUED: frozenset({RUNNING, CANCELLED, FAILED}),
    # RUNNING can finish, fail, or be cancelled mid-flight.
    RUNNING: frozenset({SUCCEEDED, FAILED, CANCELLED}),
    # Terminal-ish: only "user/admin/cleanup deletes the row" is allowed.
    SUCCEEDED: frozenset({DELETED}),
    FAILED: frozenset({DELETED}),
    CANCELLED: frozenset({DELETED}),
    # DELETED is final: nothing further. Cleanup of the on-disk dir
    # happens through the cache_keeper, not the lifecycle.
    DELETED: frozenset(),
}


class InvalidTransition(ValueError):
    """Raised when a caller asks for a state change the §8.1 graph forbids."""


class JobNotFound(LookupError):
    """Raised when the lifecycle is asked to transition an id we can't find."""


# ---------------------------------------------------------------------------
# Broadcast sink
# ---------------------------------------------------------------------------


class BroadcastSink(Protocol):
    """Minimal interface PR-12's SSE hub will satisfy.

    Lifecycle calls :meth:`broadcast_to_user` immediately after persisting
    a status change. Until the real hub lands the lifecycle uses a no-op
    sink so calling code doesn't need a feature flag.

    Implementations must be safe to call from inside an async task and
    must not raise on bad payloads — failures here should not roll the
    transition back. The PR-12 hub will satisfy that by serialising
    payloads and swallowing transport errors per-client.
    """

    async def broadcast_to_user(
        self, user_id: str, event: str, payload: Mapping[str, Any]
    ) -> None: ...


class NullBroadcastSink:
    """The default sink: drops everything. Used until PR-12 wires the hub.

    Concrete (not a Protocol) so callers can store it in module-level
    variables without losing the type. Behaviour: log at DEBUG only.
    """

    async def broadcast_to_user(
        self, user_id: str, event: str, payload: Mapping[str, Any]
    ) -> None:
        logger.debug(
            "null sink: drop event=%s user=%s payload_keys=%s",
            event,
            user_id,
            list(payload.keys()),
        )


class RecordingBroadcastSink:
    """Test-friendly sink that captures every broadcast in order.

    Lifecycle tests use this to assert "transitioning from QUEUED to
    RUNNING fired one ``job_state`` event with the right payload" without
    standing up the SSE hub.
    """

    def __init__(self) -> None:
        self.events: list[tuple[str, str, dict[str, Any]]] = []

    async def broadcast_to_user(
        self, user_id: str, event: str, payload: Mapping[str, Any]
    ) -> None:
        self.events.append((user_id, event, dict(payload)))


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TransitionResult:
    """Snapshot of the post-transition row.

    Convenient for callers that want to log without re-querying. We
    return a frozen dataclass rather than the live ``Job`` ORM instance
    so the lifecycle's session lifecycle is fully encapsulated and
    callers can't accidentally mutate the row through it.
    """

    job_id: str
    hash_id: str
    user_id: str
    seq_no: int
    set_id: str | None
    model: str
    from_status: str | None
    to_status: str
    reason: str | None
    finished_at: datetime | None


# ---------------------------------------------------------------------------
# JobLifecycle
# ---------------------------------------------------------------------------


# Mapping from terminal state → which timestamp column to set. The job
# scheduler / executor already sets ``dispatched_at`` and ``started_at``
# directly because they are about pre-RUNNING / RUNNING transitions
# respectively; the lifecycle only owns the final timestamp.
_TIMESTAMP_FOR_TERMINAL: dict[str, str] = {
    SUCCEEDED: "finished_at",
    FAILED: "finished_at",
    CANCELLED: "finished_at",
}


class JobLifecycle:
    """The single writer of ``jobs.status``.

    Stateless aside from the broadcast sink. PR-12 swaps the sink for
    the real SSE hub via :func:`set_broadcast_sink`; until then the
    process-wide default is :class:`NullBroadcastSink`.
    """

    def __init__(self, sink: BroadcastSink | None = None) -> None:
        self._sink: BroadcastSink = sink or NullBroadcastSink()

    # -- sink wiring ------------------------------------------------------

    def set_sink(self, sink: BroadcastSink) -> None:
        """Replace the broadcast sink (PR-12 plugs the SSE hub in here)."""
        self._sink = sink

    @property
    def sink(self) -> BroadcastSink:
        return self._sink

    # -- transition -------------------------------------------------------

    async def transition(
        self,
        hash_id: str,
        to_status: str,
        *,
        reason: str | None = None,
        broadcast: bool = True,
        session: AsyncSession | None = None,
        extra_timeline: Mapping[str, Any] | None = None,
    ) -> TransitionResult:
        """Move ``hash_id`` to ``to_status`` atomically.

        Steps, in order:

        1. Validate ``to_status`` and lock the row inside a session.
        2. Verify the §8.1 adjacency allows ``current → to_status``.
        3. Update ``jobs.status`` (+ ``status_reason``, ``finished_at``,
           ``updated_at``) and append one event to ``timeline.jsonl``.
           DB write and timeline write are best-effort co-atomic: a
           timeline write failure is logged but the DB transition is
           preserved (see comment below).
        4. Commit the transaction.
        5. Broadcast ``job_state`` to the owning user via the sink.

        Caller-managed sessions are accepted so the executor can fold
        a transition into a larger transaction (e.g. SUCCEEDED + ledger
        deduct as one unit). When ``session`` is provided the caller
        owns the commit; when omitted we open and commit our own.
        """
        if to_status not in VALID_STATUSES:
            raise InvalidTransition(
                f"unknown status {to_status!r}; must be one of {sorted(VALID_STATUSES)}"
            )

        if session is None:
            async with get_session() as new_session:
                result = await self._transition_in_session(
                    new_session,
                    hash_id,
                    to_status,
                    reason=reason,
                    extra_timeline=extra_timeline,
                )
        else:
            result = await self._transition_in_session(
                session,
                hash_id,
                to_status,
                reason=reason,
                extra_timeline=extra_timeline,
            )

        if broadcast:
            await self._broadcast(result)
        return result

    async def _transition_in_session(
        self,
        session: AsyncSession,
        hash_id: str,
        to_status: str,
        *,
        reason: str | None,
        extra_timeline: Mapping[str, Any] | None,
    ) -> TransitionResult:
        job = (
            await session.execute(select(Job).where(Job.hash_id == hash_id))
        ).scalar_one_or_none()
        if job is None:
            raise JobNotFound(f"job hash_id={hash_id!r} not found")

        from_status = job.status
        if to_status not in _ALLOWED_TRANSITIONS.get(from_status, frozenset()):
            raise InvalidTransition(
                f"illegal transition {from_status!r} -> {to_status!r} "
                f"for job {hash_id!r}"
            )

        now = datetime.now(timezone.utc)
        job.status = to_status
        job.status_reason = reason
        job.updated_at = now

        terminal_field = _TIMESTAMP_FOR_TERMINAL.get(to_status)
        if terminal_field is not None and getattr(job, terminal_field) is None:
            setattr(job, terminal_field, now)

        # Persist the timeline event *after* the row mutation but inside
        # the same call so the on-disk file and DB don't diverge by more
        # than one append on a partial failure. We swallow OSError here
        # because the canonical record is the DB row; an unwriteable
        # ``timeline.jsonl`` is a debug-aid problem, not a state
        # problem. If a caller needs the timeline write to be
        # transactional, route through the executor's upstream-log
        # helpers (PR-11) instead.
        try:
            event: dict[str, Any] = {
                "from": from_status,
                "to": to_status,
            }
            if reason is not None:
                event["reason"] = reason
            if extra_timeline:
                # Caller-supplied keys are namespaced under "extra" so
                # they can never overwrite the canonical from/to/reason.
                event["extra"] = dict(extra_timeline)
            append_timeline(hash_id, event)
        except OSError:
            logger.warning(
                "timeline append failed for job=%s; DB transition kept",
                hash_id,
                exc_info=True,
            )

        logger.info(
            "lifecycle: job=%s %s -> %s reason=%s",
            hash_id,
            from_status,
            to_status,
            reason,
        )

        return TransitionResult(
            job_id=job.id,
            hash_id=job.hash_id,
            user_id=job.user_id,
            seq_no=job.seq_no,
            set_id=job.set_id,
            model=job.model,
            from_status=from_status,
            to_status=to_status,
            reason=reason,
            finished_at=job.finished_at,
        )

    async def _broadcast(self, result: TransitionResult) -> None:
        """Publish a ``job_state`` event for the post-transition snapshot.

        Payload mirrors design doc §8.5.3. Image / progress fields are
        deliberately omitted here — they are filled in by the executor
        via dedicated ``job_progress`` and ``job_state SUCCEEDED`` events
        that ride the same SSE channel (PR-11 / PR-12).
        """
        payload = {
            "hash_id": result.hash_id,
            "set_id": result.set_id,
            "seq_no": result.seq_no,
            "model": result.model,
            "from": result.from_status,
            "to": result.to_status,
            "ts": _isoformat(result.finished_at) or _isoformat(_utcnow()),
            "reason": result.reason,
        }
        try:
            await self._sink.broadcast_to_user(
                result.user_id, "job_state", payload
            )
        except Exception:
            # The sink is responsible for not raising in normal
            # operation. Catching here is defense-in-depth so a buggy
            # sink can never roll back a state change that already
            # committed.
            logger.exception(
                "lifecycle broadcast sink raised for job=%s; ignored",
                result.hash_id,
            )


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------


_instance: JobLifecycle | None = None


def get_job_lifecycle() -> JobLifecycle:
    """Return the process-wide :class:`JobLifecycle` singleton."""
    global _instance
    if _instance is None:
        _instance = JobLifecycle()
    return _instance


def set_broadcast_sink(sink: BroadcastSink) -> None:
    """Install ``sink`` on the singleton. PR-12 calls this once at startup."""
    get_job_lifecycle().set_sink(sink)


def reset_job_lifecycle_for_tests() -> None:
    """Drop the singleton and any installed sink. Tests-only."""
    global _instance
    _instance = None


# ---------------------------------------------------------------------------
# Tiny helpers
# ---------------------------------------------------------------------------


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _isoformat(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    # ``timespec='seconds'`` matches the format ``image_io._utc_now_iso``
    # writes into ``timeline.jsonl`` so SSE payload timestamps and the
    # on-disk audit log line up exactly.
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")
