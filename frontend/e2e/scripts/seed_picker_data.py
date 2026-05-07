"""Direct-DB seeder for picker E2E tests.

Bypasses the provider stack by writing jobs / images directly into the
SQLite DB and on-disk thumbnail files. Same approach as the backend
test fixtures (see backend/tests/test_picker_api.py).

Usage:
    python frontend/e2e/scripts/seed_picker_data.py reset
    python frontend/e2e/scripts/seed_picker_data.py seed deck_mixed

Output (stdout): JSON with created session ids so the JS test can use
them in URLs.
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import string
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from random import choice

# Make backend importable
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "backend"))

# Load backend env vars from backend.env (not committed)
env_path = ROOT / "backend.env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k, v)

from PIL import Image as PILImage  # noqa: E402
from sqlalchemy import delete, select  # noqa: E402

from app.db import engine as db_engine  # noqa: E402
from app.db.engine import get_session  # noqa: E402
from app.db.migrate import upgrade_to_head  # noqa: E402
from app.db.models import (  # noqa: E402
    Image,
    Job,
    Session as SessionRow,
    SessionJob,
    User,
)
from app.services import image_io  # noqa: E402
from app.utils.ids import (  # noqa: E402
    new_image_id,
    new_job_internal_id,
    new_session_id,
)


E2E_USERNAME = "e2e_picker_user"


def _rand_hash(prefix: str = "j_") -> str:
    pool = string.ascii_letters + string.digits
    return prefix + "".join(choice(pool) for _ in range(12))


async def _get_user_id(username: str) -> str | None:
    async with get_session() as session:
        row = (
            await session.execute(select(User).where(User.username == username))
        ).scalar_one_or_none()
    return row.id if row else None


async def _reset_user_data(user_id: str) -> int:
    """Clear every session/job/image owned by the user. Returns count."""
    deleted = 0
    async with get_session() as session:
        sess_ids = [
            r.id
            for r in (
                await session.execute(
                    select(SessionRow).where(SessionRow.user_id == user_id)
                )
            ).scalars().all()
        ]
        for sid in sess_ids:
            await session.execute(
                delete(SessionJob).where(SessionJob.session_id == sid)
            )
            await session.execute(
                delete(SessionRow).where(SessionRow.id == sid)
            )
            deleted += 1
        # Delete jobs + images (cascades) owned by the user
        job_ids = [
            r.id
            for r in (
                await session.execute(
                    select(Job).where(Job.user_id == user_id)
                )
            ).scalars().all()
        ]
        for jid in job_ids:
            await session.execute(delete(Image).where(Image.job_id == jid))
            await session.execute(delete(Job).where(Job.id == jid))
    return deleted


async def _seed_session(
    *,
    user_id: str,
    name: str,
    picker_state: str = "not_started",
) -> str:
    sid = new_session_id()
    now = datetime.now(timezone.utc)
    async with get_session() as session:
        session.add(
            SessionRow(
                id=sid,
                user_id=user_id,
                name=name,
                picker_state=picker_state,
                created_at=now,
                updated_at=now,
            )
        )
    return sid


async def _seed_job_with_images(
    *,
    user_id: str,
    session_id: str,
    seq_no: int,
    image_count: int,
    prompt: str,
    pick_states: list[str] | None = None,
    color: tuple[int, int, int] = (240, 196, 48),
) -> tuple[str, str, list[str]]:
    """Create one job + N images on disk + DB rows. Returns
    (hash_id, job_id, [image_ids]).
    """
    hash_id = _rand_hash("j_")
    job_id = new_job_internal_id()
    now = datetime.now(timezone.utc)
    queued = now - timedelta(minutes=10)

    async with get_session() as session:
        session.add(
            Job(
                id=job_id,
                hash_id=hash_id,
                user_id=user_id,
                tier_at_submit="premium",
                seq_no=seq_no,
                set_id=None,
                session_id=session_id,
                model="gemini-3.1-flash",
                params_json=json.dumps(
                    {"prompt": prompt, "model": "gemini-3.1-flash", "seed": f"e2e-{seq_no}"}
                ),
                flags_json="{}",
                client_request_id=None,
                status="SUCCEEDED",
                status_reason=None,
                provider_used="e2e-mock",
                retries=0,
                cost_cny=0.0,
                created_at=queued,
                queued_at=queued,
                dispatched_at=queued,
                started_at=queued,
                finished_at=now,
                updated_at=now,
            )
        )
        session.add(SessionJob(session_id=session_id, job_id=job_id))

    image_ids: list[str] = []
    image_io.ensure_job_dirs(hash_id)
    for order in range(1, image_count + 1):
        # Vary the colour so distinct thumbnails are visible.
        r = (color[0] + order * 17) % 256
        g = (color[1] + order * 23) % 256
        b = (color[2] + order * 31) % 256
        buf = io.BytesIO()
        PILImage.new("RGB", (256, 256), color=(r, g, b)).save(buf, format="PNG")
        rel_orig = image_io.save_original(
            hash_id, order, buf.getvalue(), "image/png"
        )
        abs_orig = image_io.path_for_output_original(hash_id, order, "png")
        abs_thumb = image_io.path_for_output_thumb(hash_id, order)
        image_io.make_thumbnail(abs_orig, abs_thumb)
        from app.config import get_settings

        data_root = Path(get_settings().DATA_ROOT).resolve()
        rel_thumb = str(abs_thumb.relative_to(data_root))

        image_id = new_image_id()
        pick_state = (
            pick_states[order - 1]
            if pick_states and order - 1 < len(pick_states)
            else "unjudged"
        )
        async with get_session() as session:
            session.add(
                Image(
                    id=image_id,
                    job_id=job_id,
                    img_order=order,
                    original_path=rel_orig,
                    thumb_path=rel_thumb,
                    width=256,
                    height=256,
                    format="png",
                    file_size_bytes=abs_orig.stat().st_size,
                    starred=1 if pick_state in ("picked", "final") else 0,
                    pick_state=pick_state,
                    pick_state_updated_at=now if pick_state != "unjudged" else None,
                )
            )
        image_ids.append(image_id)
    return hash_id, job_id, image_ids


async def _set_session_meta(
    *,
    session_id: str,
    picker_state: str | None = None,
    final_image_id: str | None = None,
    cursor_image_id: str | None = None,
):
    async with get_session() as session:
        sess = (
            await session.execute(
                select(SessionRow).where(SessionRow.id == session_id)
            )
        ).scalar_one()
        if picker_state is not None:
            sess.picker_state = picker_state
        if final_image_id is not None:
            sess.final_image_id = final_image_id
        if cursor_image_id is not None:
            sess.cursor_image_id = cursor_image_id


async def cmd_reset() -> dict:
    upgrade_to_head()
    db_engine.init_engine()
    user_id = await _get_user_id(E2E_USERNAME)
    if not user_id:
        return {"ok": False, "error": f"user {E2E_USERNAME} not found"}
    deleted = await _reset_user_data(user_id)
    return {"ok": True, "user_id": user_id, "deleted_sessions": deleted}


async def cmd_seed_deck_mixed() -> dict:
    """Seed a 4-session deck: not_started / judging / ready / finalized."""
    upgrade_to_head()
    db_engine.init_engine()
    user_id = await _get_user_id(E2E_USERNAME)
    if not user_id:
        return {"ok": False, "error": f"user {E2E_USERNAME} not found"}

    out: dict[str, object] = {"ok": True, "user_id": user_id, "sessions": []}

    # Session 1: not_started (zero images)
    s1 = await _seed_session(
        user_id=user_id, name="Cover slide", picker_state="not_started"
    )
    out["sessions"].append({"id": s1, "name": "Cover slide", "state": "not_started"})

    # Session 2: judging — 6 images, 2 picked, 1 discarded, 3 unjudged
    s2 = await _seed_session(
        user_id=user_id, name="Core problem", picker_state="judging"
    )
    states = ["picked", "picked", "discarded", "unjudged", "unjudged", "unjudged"]
    _, _, ids2 = await _seed_job_with_images(
        user_id=user_id,
        session_id=s2,
        seq_no=2,
        image_count=6,
        prompt="diagram: a tangled cord plugged into nothing, isometric line work",
        pick_states=states,
    )
    await _set_session_meta(session_id=s2, picker_state="judging")
    out["sessions"].append({"id": s2, "name": "Core problem", "state": "judging"})

    # Session 3: ready_to_finalize — 4 picked + 1 final
    s3 = await _seed_session(
        user_id=user_id, name="Our method", picker_state="judging"
    )
    states = ["final", "picked", "picked", "picked", "picked"]
    _, _, ids3 = await _seed_job_with_images(
        user_id=user_id,
        session_id=s3,
        seq_no=3,
        image_count=5,
        prompt="three-panel storyboard, hand-drawn",
        pick_states=states,
        color=(180, 220, 90),
    )
    await _set_session_meta(
        session_id=s3, picker_state="judging", final_image_id=ids3[0]
    )
    out["sessions"].append({"id": s3, "name": "Our method", "state": "ready_to_finalize"})

    # Session 4: finalized
    s4 = await _seed_session(
        user_id=user_id, name="Results", picker_state="finalized"
    )
    states = ["final", "picked", "picked", "discarded", "discarded"]
    _, _, ids4 = await _seed_job_with_images(
        user_id=user_id,
        session_id=s4,
        seq_no=4,
        image_count=5,
        prompt="bar chart in lithograph style",
        pick_states=states,
        color=(120, 140, 200),
    )
    await _set_session_meta(
        session_id=s4,
        picker_state="finalized",
        final_image_id=ids4[0],
    )
    # Set finalized_at
    async with get_session() as session:
        sess = (
            await session.execute(
                select(SessionRow).where(SessionRow.id == s4)
            )
        ).scalar_one()
        sess.finalized_at = datetime.now(timezone.utc)
    out["sessions"].append({"id": s4, "name": "Results", "state": "finalized"})

    # Session 5: judging with full image set for confirm-modal scenario
    s5 = await _seed_session(
        user_id=user_id, name="Team", picker_state="judging"
    )
    states = ["final", "picked", "picked", "unjudged"]
    _, _, ids5 = await _seed_job_with_images(
        user_id=user_id,
        session_id=s5,
        seq_no=5,
        image_count=4,
        prompt="group portrait",
        pick_states=states,
        color=(200, 100, 160),
    )
    await _set_session_meta(
        session_id=s5,
        picker_state="judging",
        final_image_id=ids5[0],
        cursor_image_id=ids5[3],
    )
    out["sessions"].append({"id": s5, "name": "Team", "state": "judging-with-final"})

    return out


COMMANDS = {
    "reset": cmd_reset,
    "seed_deck_mixed": cmd_seed_deck_mixed,
}


async def main():
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(json.dumps({"ok": False, "error": "unknown command"}))
        sys.exit(2)
    result = await COMMANDS[sys.argv[1]]()
    print(json.dumps(result, default=str))


if __name__ == "__main__":
    asyncio.run(main())
