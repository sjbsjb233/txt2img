"""Picker state-machine helpers — shared by /api/picker and /api/archive.

Centralises the per-image transition logic so the four-and-a-half write
endpoints (``pick`` / ``discard`` / ``final`` / ``defer`` / ``unjudge``,
plus the legacy ``star`` toggle that mirrors into pick_state) all agree
on the same invariants:

- A session has at most one ``final`` image. Setting a new final
  demotes the old one to ``picked`` (PRD §3.1).
- ``starred`` is kept in sync with ``pick_state`` per PRD §8.5.1:
  ``picked`` and ``final`` star the image; everything else un-stars it.
- ``sessions.picker_state`` is recomputed after every transition so
  the deck overview and drawer don't need to recompute it client-side.

Every helper returns a list of SSE event payloads ready for the route
to broadcast — the caller decides whether to actually call the hub
(tests usually skip it). All helpers run inside an existing
``AsyncSession`` so the route can wrap multiple writes in one
transaction.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Sequence

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Image,
    Job,
    Session as SessionRow,
    SessionJob,
)


# Five legal pick states (PRD v1 §3.1).
PICK_STATES = ("unjudged", "picked", "discarded", "final", "deferred")


# Pick states that should leave ``images.starred = 1`` (PRD §8.5.1).
_STARRED_STATES = frozenset({"picked", "final"})


def _starred_for(pick_state: str) -> int:
    """Return the integer ``starred`` value matching ``pick_state``."""
    return 1 if pick_state in _STARRED_STATES else 0


# ---------------------------------------------------------------------------
# Session pick-state recomputation
# ---------------------------------------------------------------------------


async def recompute_session_picker_state(
    session: AsyncSession,
    *,
    session_id: str,
    user_id: str,
) -> SessionRow | None:
    """Walk the session's images and update ``picker_state`` accordingly.

    Decision matrix:
    - If any image is ``final`` and no image is ``unjudged`` →
      ``finalized`` is *only* set by the explicit Finalize endpoint, so
      we map this case to ``judging`` (the picker frontend computes
      ``ready_to_finalize`` on top of ``judging``).
    - If any image is judged but unjudged remain → ``judging``.
    - Otherwise → ``not_started``.
    - If the row is already ``finalized`` we leave it alone unless a
      brand-new SUCCEEDED image landed (caller is responsible for
      detecting that — see :func:`reopen_session_after_new_image`).
    """
    sess = (
        await session.execute(
            select(SessionRow).where(
                SessionRow.id == session_id, SessionRow.user_id == user_id
            )
        )
    ).scalar_one_or_none()
    if sess is None:
        return None

    # Aggregate counts in one query.
    counts_rows = (
        await session.execute(
            select(Image.pick_state, func.count(Image.id))
            .join(Job, Job.id == Image.job_id)
            .join(SessionJob, SessionJob.job_id == Job.id)
            .where(SessionJob.session_id == session_id)
            .group_by(Image.pick_state)
        )
    ).all()
    counts = {state: int(n) for state, n in counts_rows}
    total = sum(counts.values())
    judged = total - counts.get("unjudged", 0) - counts.get("deferred", 0)
    has_judgement = judged + counts.get("deferred", 0) > 0

    # Don't override finalized — the user explicitly locked it. The
    # exception ("new image landed in a finalized session") is handled
    # by the caller in the SUCCEEDED-broadcast path.
    if sess.picker_state == "finalized":
        return sess

    if total == 0:
        new_state = "not_started"
    elif has_judgement:
        new_state = "judging"
    else:
        new_state = "not_started"

    # Validate / reset final_image_id if its pointee changed state out
    # from under us (e.g. user picked a new final via direct DB poke).
    if sess.final_image_id is not None:
        final_img = (
            await session.execute(
                select(Image).where(Image.id == sess.final_image_id)
            )
        ).scalar_one_or_none()
        if final_img is None or final_img.pick_state != "final":
            sess.final_image_id = None

    if sess.picker_state != new_state:
        sess.picker_state = new_state
    sess.updated_at = datetime.now(timezone.utc)
    return sess


async def reopen_session_after_new_image(
    session: AsyncSession,
    *,
    session_id: str,
    user_id: str,
) -> SessionRow | None:
    """If a finalized session has unjudged images, drop back to ``judging``.

    Called after a job lands SUCCEEDED so the picker page can prompt
    the user "new image arrived; reopen to judge". The session keeps
    ``final_image_id`` set — the user's choice survives — but the
    overall state is no longer "locked".
    """
    sess = (
        await session.execute(
            select(SessionRow).where(
                SessionRow.id == session_id, SessionRow.user_id == user_id
            )
        )
    ).scalar_one_or_none()
    if sess is None or sess.picker_state != "finalized":
        return None

    unjudged_count = (
        await session.execute(
            select(func.count(Image.id))
            .join(Job, Job.id == Image.job_id)
            .join(SessionJob, SessionJob.job_id == Job.id)
            .where(
                SessionJob.session_id == session_id,
                Image.pick_state == "unjudged",
            )
        )
    ).scalar_one()
    if int(unjudged_count or 0) == 0:
        return sess

    sess.picker_state = "judging"
    sess.finalized_at = None
    sess.updated_at = datetime.now(timezone.utc)
    return sess


# ---------------------------------------------------------------------------
# Per-image transitions
# ---------------------------------------------------------------------------


async def _resolve_session_for_image(
    session: AsyncSession, *, job: Job
) -> SessionRow | None:
    """Find the session row that owns this job's image, if any."""
    if job.session_id:
        return (
            await session.execute(
                select(SessionRow).where(SessionRow.id == job.session_id)
            )
        ).scalar_one_or_none()
    # Fallback: the job might be attached only via session_jobs. Walk it.
    row = (
        await session.execute(
            select(SessionRow)
            .join(SessionJob, SessionJob.session_id == SessionRow.id)
            .where(SessionJob.job_id == job.id)
        )
    ).scalar_one_or_none()
    return row


def _build_pick_event(
    *,
    image: Image,
    job: Job,
    session_id: str | None,
    from_state: str,
    to_state: str,
    session_picker_state: str,
    session_final_image_id: str | None,
) -> dict[str, Any]:
    """Shape the ``image_pick_state`` SSE payload."""
    return {
        "image_id": image.id,
        "hash_id": job.hash_id,
        "order": image.img_order,
        "session_id": session_id,
        "from": from_state,
        "to": to_state,
        "ts": (image.pick_state_updated_at or datetime.now(timezone.utc)).isoformat(),
        "session_picker_state": session_picker_state,
        "session_final_image_id": session_final_image_id,
        "starred": bool(image.starred),
    }


async def transition_image(
    session: AsyncSession,
    *,
    user_id: str,
    job: Job,
    image: Image,
    new_state: str,
) -> tuple[Image, SessionRow | None, list[dict[str, Any]], str | None]:
    """Apply ``new_state`` to ``image`` and bookkeep the session.

    Returns ``(image, session_row, broadcast_payloads, prev_final_id)``.
    Caller is responsible for broadcasting (or skipping in tests).

    The ``final`` transition is the only one with a side effect: it
    demotes any sibling that was previously ``final`` to ``picked``.
    Both transitions are emitted as separate events in the returned
    list so the SSE channel mirrors the DB writes.
    """
    if new_state not in PICK_STATES:
        raise ValueError(f"unknown pick state: {new_state!r}")

    sess = await _resolve_session_for_image(session, job=job)

    now = datetime.now(timezone.utc)
    prev_state = image.pick_state or "unjudged"
    events: list[dict[str, Any]] = []
    prev_final_id: str | None = None

    # Step 1: if we're promoting to final, demote any other ``final`` in
    # the same session. (Skip when the session is unbound — picking a
    # final outside of a session is allowed and harmless.)
    if new_state == "final" and sess is not None:
        existing_finals = (
            await session.execute(
                select(Image, Job)
                .join(Job, Job.id == Image.job_id)
                .join(SessionJob, SessionJob.job_id == Job.id)
                .where(
                    SessionJob.session_id == sess.id,
                    Image.pick_state == "final",
                    Image.id != image.id,
                )
            )
        ).all()
        for old_img, old_job in existing_finals:
            old_prev = old_img.pick_state
            old_img.pick_state = "picked"
            old_img.starred = _starred_for("picked")
            old_img.pick_state_updated_at = now
            prev_final_id = old_img.id
            events.append(
                _build_pick_event(
                    image=old_img,
                    job=old_job,
                    session_id=sess.id,
                    from_state=old_prev,
                    to_state="picked",
                    session_picker_state=sess.picker_state,
                    session_final_image_id=image.id,
                )
            )

    # Step 2: write the requested transition.
    image.pick_state = new_state
    image.starred = _starred_for(new_state)
    image.pick_state_updated_at = now

    # Step 3: keep session.final_image_id consistent with reality.
    if sess is not None:
        if new_state == "final":
            sess.final_image_id = image.id
        elif sess.final_image_id == image.id and new_state != "final":
            sess.final_image_id = None

    # Step 4: recompute aggregate state. Skip if no session — orphan
    # images don't roll up anywhere.
    if sess is not None:
        await recompute_session_picker_state(
            session, session_id=sess.id, user_id=user_id
        )

    events.append(
        _build_pick_event(
            image=image,
            job=job,
            session_id=sess.id if sess is not None else None,
            from_state=prev_state,
            to_state=new_state,
            session_picker_state=sess.picker_state if sess else "not_started",
            session_final_image_id=sess.final_image_id if sess else None,
        )
    )

    return image, sess, events, prev_final_id


# ---------------------------------------------------------------------------
# Star <-> pick_state mirror (PRD §8.5.3)
# ---------------------------------------------------------------------------


async def sync_starred_with_pick_state(
    *,
    session: AsyncSession,
    user_id: str,
    job: Job,
    image: Image,
    new_starred: bool,
) -> Sequence[dict[str, Any]] | None:
    """Mirror ``starred`` writes into ``pick_state``.

    Mapping (per PRD §8.5.3):
    - ``starred=true`` and current ``pick_state == 'unjudged'`` →
      transition to ``picked``.
    - ``starred=false`` and current ``pick_state in {'picked','final'}``
      → transition to ``unjudged``.
    - Otherwise: only flip the star bit; ``pick_state`` is preserved.

    Returns the SSE payloads to broadcast (possibly empty), or
    ``None`` if no transition occurred and the caller doesn't need to
    broadcast anything.
    """
    cur = image.pick_state or "unjudged"
    if new_starred and cur == "unjudged":
        _, _, events, _ = await transition_image(
            session,
            user_id=user_id,
            job=job,
            image=image,
            new_state="picked",
        )
        return events
    if (not new_starred) and cur in ("picked", "final"):
        _, _, events, _ = await transition_image(
            session,
            user_id=user_id,
            job=job,
            image=image,
            new_state="unjudged",
        )
        return events
    # No state transition; just flip the star bit.
    image.starred = 1 if new_starred else 0
    return None
