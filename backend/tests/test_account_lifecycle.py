"""Tests for the T+7 / T+30 account-deletion lifecycle job.

We don't actually wait 7 days — the test flips ``resolved_at`` back
in time on a real approved deletion-request row, then runs
``run_account_lifecycle_sweep`` and asserts the user transitioned
through the expected statuses (and that the disk dirs went away).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.db.engine import get_session
from app.db.models import (
    AccountDeletionRequest,
    AuthSession,
    Image,
    Job,
    User,
    UserPreference,
)
from app.domain.account_lifecycle import (
    PURGE_DAYS,
    SOFT_DELETE_DAYS,
    run_account_lifecycle_sweep,
)
from app.services.image_io import jobs_root
from app.utils.ids import (
    new_auth_session_id,
    new_deletion_request_id,
    new_image_id,
    new_job_hash_id,
    new_job_internal_id,
    new_user_id,
)
from app.utils.security import hash_password


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def utc_now() -> datetime:
    return datetime.now(timezone.utc)


async def _make_user_with_approved_deletion(
    *,
    username: str,
    days_since_resolved: int,
) -> tuple[str, str, str]:
    """Insert one user + one approved deletion request resolved N days ago.

    Returns ``(user_id, request_id, job_hash_id)``. A single completed
    job is also created so the purge step has something to remove from
    disk.
    """
    user_id = new_user_id()
    job_id = new_job_internal_id()
    job_hash_id = new_job_hash_id()
    image_id = new_image_id()
    request_id = new_deletion_request_id()
    resolved_at = datetime.now(timezone.utc) - timedelta(
        days=days_since_resolved
    )
    requested_at = resolved_at - timedelta(hours=2)

    async with get_session() as session:
        session.add(
            User(
                id=user_id,
                username=username,
                password_hash=hash_password("Userpassword123!"),
                role="user",
                tier="free",
                status="disabled",
                display_name=username,
            )
        )
        # Flush before adding rows that FK back to ``users`` so the
        # parent id is visible at constraint-check time.
        await session.flush()
        session.add(
            UserPreference(user_id=user_id, default_aspect_ratio="1:1")
        )
        session.add(
            AuthSession(
                id=new_auth_session_id(),
                user_id=user_id,
                jti="jti-" + user_id,
                user_agent="pytest",
                ip="127.0.0.x",
            )
        )
        session.add(
            Job(
                id=job_id,
                hash_id=job_hash_id,
                user_id=user_id,
                tier_at_submit="free",
                seq_no=1,
                model="gemini-3-pro-image-preview",
                params_json="{}",
                status="SUCCEEDED",
            )
        )
        # Flush so the Image FK to jobs.id resolves.
        await session.flush()
        session.add(
            Image(
                id=image_id,
                job_id=job_id,
                img_order=0,
                original_path=f"{job_hash_id}/0.png",
                thumb_path=f"{job_hash_id}/0.thumb.webp",
                width=1024,
                height=1024,
                format="png",
                file_size_bytes=12345,
            )
        )
        session.add(
            AccountDeletionRequest(
                id=request_id,
                user_id=user_id,
                reason="lifecycle test",
                requested_at=requested_at,
                status="approved",
                resolved_at=resolved_at,
                resolved_by=user_id,  # placeholder; admin id not validated by FK
                admin_note="auto-approved by fixture",
            )
        )
    return user_id, request_id, job_hash_id


def _stage_disk_files_for(hash_id: str) -> None:
    """Drop a sentinel file under ``DATA_ROOT/jobs/<hash_id>/`` so we
    can assert the purge actually rm -rf'd it."""
    settings = get_settings()
    target = jobs_root() / hash_id
    target.mkdir(parents=True, exist_ok=True)
    (target / "sentinel.txt").write_text("delete me", encoding="utf-8")


# ---------------------------------------------------------------------------
# T+7 — soft delete
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_soft_delete_kicks_in_at_seven_days(
    seeded_app, utc_now: datetime
) -> None:
    user_id, request_id, _hash = await _make_user_with_approved_deletion(
        username="sd_user", days_since_resolved=SOFT_DELETE_DAYS + 1
    )

    report = await run_account_lifecycle_sweep()
    assert report["soft_deleted"] >= 1
    assert report["purged"] == 0

    async with get_session() as session:
        user = (
            await session.execute(select(User).where(User.id == user_id))
        ).scalar_one()
        assert user.status == "deleted"
        # Username has been renamed so a fresh signup can reuse "sd_user".
        assert "__deleted_" in user.username


@pytest.mark.asyncio
async def test_soft_delete_skips_under_seven_days(
    seeded_app,
) -> None:
    user_id, _request_id, _hash = await _make_user_with_approved_deletion(
        username="too_fresh_user", days_since_resolved=SOFT_DELETE_DAYS - 1
    )

    report = await run_account_lifecycle_sweep()
    assert report["soft_deleted"] == 0

    async with get_session() as session:
        user = (
            await session.execute(select(User).where(User.id == user_id))
        ).scalar_one()
        assert user.status == "disabled"
        assert user.username == "too_fresh_user"


@pytest.mark.asyncio
async def test_soft_delete_is_idempotent(
    seeded_app,
) -> None:
    user_id, _request_id, _hash = await _make_user_with_approved_deletion(
        username="idem_user", days_since_resolved=SOFT_DELETE_DAYS + 2
    )

    first = await run_account_lifecycle_sweep()
    second = await run_account_lifecycle_sweep()

    assert first["soft_deleted"] == 1
    # Running again sees user.status='deleted' already → no double-rename.
    assert second["soft_deleted"] == 0
    async with get_session() as session:
        user = (
            await session.execute(select(User).where(User.id == user_id))
        ).scalar_one()
        # Only one __deleted_ suffix.
        assert user.username.count("__deleted_") == 1


# ---------------------------------------------------------------------------
# T+30 — purge
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_purge_after_thirty_days_drops_user_and_files(
    seeded_app,
) -> None:
    """A user who was approved 30+ days ago is purged in one sweep.

    The sweep is allowed to "catch up" — if the soft-delete tick was
    missed (process down for a week, etc.), one run still moves a
    long-overdue account through both transitions.
    """
    user_id, request_id, hash_id = await _make_user_with_approved_deletion(
        username="purge_user", days_since_resolved=PURGE_DAYS + 1
    )
    _stage_disk_files_for(hash_id)

    report = await run_account_lifecycle_sweep()
    # Both transitions land in the same sweep because the windows
    # are anchored to the same ``resolved_at`` timestamp.
    assert report["soft_deleted"] == 1
    assert report["purged"] == 1

    async with get_session() as session:
        user = (
            await session.execute(select(User).where(User.id == user_id))
        ).scalar_one_or_none()
        assert user is None, "user row should be hard-deleted"
        # account_deletion_requests row went with the user via cascade.
        req = (
            await session.execute(
                select(AccountDeletionRequest).where(
                    AccountDeletionRequest.id == request_id
                )
            )
        ).scalar_one_or_none()
        assert req is None
        # auth_sessions and user_preferences also gone.
        assert (
            (
                await session.execute(
                    select(AuthSession).where(AuthSession.user_id == user_id)
                )
            ).first()
            is None
        )
        assert (
            (
                await session.execute(
                    select(UserPreference).where(
                        UserPreference.user_id == user_id
                    )
                )
            ).first()
            is None
        )
        # Jobs + images for this user gone too.
        assert (
            (
                await session.execute(
                    select(Job).where(Job.user_id == user_id)
                )
            ).first()
            is None
        )

    # And the on-disk dir is gone.
    target = jobs_root() / hash_id
    assert not target.exists()


@pytest.mark.asyncio
async def test_purge_only_when_already_soft_deleted_separate_window(
    seeded_app,
) -> None:
    """Purge picks up a row that was soft-deleted in a prior sweep.

    Production cadence: at T+7 the user is soft-deleted (status='deleted'),
    at T+30 the row is purged. This test prepares the row already in
    'deleted' status to confirm the purge step does its work without
    needing the soft-delete step to fire in the same call.
    """
    user_id, request_id, _hash = await _make_user_with_approved_deletion(
        username="already_soft_deleted",
        days_since_resolved=PURGE_DAYS + 2,
    )
    # Pre-set the user to 'deleted' as if a prior sweep had already
    # done the soft-delete step.
    async with get_session() as session:
        user = (
            await session.execute(select(User).where(User.id == user_id))
        ).scalar_one()
        user.status = "deleted"
        user.username = f"{user.username}__deleted_0"

    report = await run_account_lifecycle_sweep()
    # No new soft-delete (the user is already 'deleted'), one purge.
    assert report["soft_deleted"] == 0
    assert report["purged"] == 1
    async with get_session() as session:
        gone = (
            await session.execute(select(User).where(User.id == user_id))
        ).scalar_one_or_none()
        assert gone is None
