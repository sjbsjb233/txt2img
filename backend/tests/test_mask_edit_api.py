"""Tests for mask edit + outpaint + derivation features.

Covers the new behaviours added in v3 of the mask editor design:

- Multipart ``mask`` field accepted by ``POST /api/jobs`` and validated
  for PNG / alpha / size-match.
- ``parent_hash_id`` + ``derivation_kind`` plumb through to the row +
  response and reject obvious misuse (missing parent, foreign parent,
  non-terminal parent, mismatched model).
- ``GET /api/jobs/{hash}/derived`` lists children for a parent.
- ``JobDetail`` carries ``parent_hash_id`` / ``derivation_kind`` /
  ``derived_count``.
- Outpaint geometry travels via flags; outpaint mode forbids a
  caller-supplied mask.
- ``services/outpaint.py`` synthesizes an extended canvas + mask of
  the right dimensions.
"""

from __future__ import annotations

import io
import json
from typing import Any

import httpx
import pytest
from PIL import Image

from tests.test_jobs_api import (  # type: ignore[import-untyped]
    _auth,
    _job_payload,
    _login_user,
    _png_bytes,
    _seed_gpt_provider,
)


def _mask_png(width: int = 10, height: int = 10) -> bytes:
    """Build a small RGBA PNG with alpha channel — valid mask."""
    buf = io.BytesIO()
    img = Image.new("RGBA", (width, height), color=(255, 0, 0, 128))
    img.save(buf, format="PNG")
    return buf.getvalue()


def _ref_png(width: int = 10, height: int = 10) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), color=(120, 120, 120)).save(buf, format="PNG")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Multipart mask plumbing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_job_with_mask_succeeds(
    seeded_app: httpx.AsyncClient,
) -> None:
    await _seed_gpt_provider(seeded_app)
    token = await _login_user(seeded_app, username="mask_happy")
    resp = await seeded_app.post(
        "/api/jobs",
        headers=_auth(token),
        files=[
            ("payload", (None, _job_payload(), "application/json")),
            ("ref_0", ("source.png", _ref_png(64, 64), "image/png")),
            ("mask", ("mask.png", _mask_png(64, 64), "image/png")),
        ],
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "QUEUED"
    assert body["parent_hash_id"] is None  # no parent in this case
    assert body["derivation_kind"] is None


@pytest.mark.asyncio
async def test_create_job_rejects_non_png_mask(
    seeded_app: httpx.AsyncClient,
) -> None:
    await _seed_gpt_provider(seeded_app)
    token = await _login_user(seeded_app, username="mask_bad_mime")
    # Build a JPEG mask
    buf = io.BytesIO()
    Image.new("RGB", (10, 10)).save(buf, format="JPEG")
    resp = await seeded_app.post(
        "/api/jobs",
        headers=_auth(token),
        files=[
            ("payload", (None, _job_payload(), "application/json")),
            ("ref_0", ("source.png", _ref_png(10, 10), "image/png")),
            ("mask", ("mask.jpg", buf.getvalue(), "image/jpeg")),
        ],
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "INVALID_MASK_FORMAT"


@pytest.mark.asyncio
async def test_create_job_rejects_mask_without_alpha(
    seeded_app: httpx.AsyncClient,
) -> None:
    await _seed_gpt_provider(seeded_app)
    token = await _login_user(seeded_app, username="mask_no_alpha")
    buf = io.BytesIO()
    Image.new("RGB", (10, 10)).save(buf, format="PNG")  # no alpha
    resp = await seeded_app.post(
        "/api/jobs",
        headers=_auth(token),
        files=[
            ("payload", (None, _job_payload(), "application/json")),
            ("ref_0", ("source.png", _ref_png(10, 10), "image/png")),
            ("mask", ("mask.png", buf.getvalue(), "image/png")),
        ],
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "INVALID_MASK_FORMAT"


@pytest.mark.asyncio
async def test_create_job_rejects_dimension_mismatch(
    seeded_app: httpx.AsyncClient,
) -> None:
    await _seed_gpt_provider(seeded_app)
    token = await _login_user(seeded_app, username="mask_dims")
    resp = await seeded_app.post(
        "/api/jobs",
        headers=_auth(token),
        files=[
            ("payload", (None, _job_payload(), "application/json")),
            ("ref_0", ("source.png", _ref_png(64, 64), "image/png")),
            ("mask", ("mask.png", _mask_png(32, 32), "image/png")),
        ],
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "INVALID_MASK_DIMS"


@pytest.mark.asyncio
async def test_create_job_rejects_mask_without_reference(
    seeded_app: httpx.AsyncClient,
) -> None:
    await _seed_gpt_provider(seeded_app)
    token = await _login_user(seeded_app, username="mask_no_ref")
    resp = await seeded_app.post(
        "/api/jobs",
        headers=_auth(token),
        files=[
            ("payload", (None, _job_payload(), "application/json")),
            ("mask", ("mask.png", _mask_png(64, 64), "image/png")),
        ],
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "MASK_REQUIRES_REFERENCE"


# ---------------------------------------------------------------------------
# Derivation field validation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_payload_requires_both_parent_and_kind(
    seeded_app: httpx.AsyncClient,
) -> None:
    await _seed_gpt_provider(seeded_app)
    token = await _login_user(seeded_app, username="derive_validate")
    resp = await seeded_app.post(
        "/api/jobs",
        headers=_auth(token),
        files=[
            (
                "payload",
                (
                    None,
                    _job_payload(parent_hash_id="j_doesnotexis"),
                    "application/json",
                ),
            ),
        ],
    )
    # Pydantic model_validator rejects the half-set state.
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_payload_rejects_missing_parent(
    seeded_app: httpx.AsyncClient,
) -> None:
    await _seed_gpt_provider(seeded_app)
    token = await _login_user(seeded_app, username="derive_missing")
    resp = await seeded_app.post(
        "/api/jobs",
        headers=_auth(token),
        files=[
            (
                "payload",
                (
                    None,
                    _job_payload(
                        parent_hash_id="j_doesnotexis",
                        derivation_kind="mask_edit",
                    ),
                    "application/json",
                ),
            ),
            ("ref_0", ("source.png", _ref_png(10, 10), "image/png")),
            ("mask", ("mask.png", _mask_png(10, 10), "image/png")),
        ],
    )
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "PARENT_JOB_NOT_FOUND"


@pytest.mark.asyncio
async def test_outpaint_synthesizes_canvas_and_mask(
    seeded_app: httpx.AsyncClient,
) -> None:
    """Outpaint should synthesize an extended canvas + mask from the
    source image, attaching the result to the job's params before
    enqueueing. Without this wiring the executor would reach upstream
    with a plain i2i regeneration (Bug #5)."""
    await _seed_gpt_provider(seeded_app)
    token = await _login_user(seeded_app, username="outpaint_synth")

    # Submit a parent job we can derive from
    parent_resp = await seeded_app.post(
        "/api/jobs",
        headers=_auth(token),
        files=[("payload", (None, _job_payload(), "application/json"))],
    )
    assert parent_resp.status_code == 200, parent_resp.text
    parent_hash = parent_resp.json()["hash_id"]

    # Mark parent SUCCEEDED so the derive-validator accepts it.
    from app.db.engine import get_session
    from app.db.models import Job
    from sqlalchemy import update
    async with get_session() as session:
        await session.execute(
            update(Job)
            .where(Job.hash_id == parent_hash)
            .values(status="SUCCEEDED")
        )
        await session.commit()

    # Submit an outpaint derivation. The mask attachment must NOT be
    # passed by the caller (the synthesizer creates one).
    resp = await seeded_app.post(
        "/api/jobs",
        headers=_auth(token),
        files=[
            (
                "payload",
                (
                    None,
                    _job_payload(
                        parent_hash_id=parent_hash,
                        derivation_kind="outpaint",
                        outpaint_directions=["right"],
                        outpaint_amount="25%",
                    ),
                    "application/json",
                ),
            ),
            ("ref_0", ("source.png", _ref_png(64, 64), "image/png")),
        ],
    )
    assert resp.status_code == 200, resp.text

    # Verify the synthesized mask landed in params_json.
    derived_hash = resp.json()["hash_id"]
    async with get_session() as session:
        from sqlalchemy import select
        row = (await session.execute(
            select(Job).where(Job.hash_id == derived_hash)
        )).scalar_one()
        import json as _json
        params = _json.loads(row.params_json)
        assert "mask" in params, "outpaint should synthesize a mask into params"
        assert params["mask"]["filename"] == "outpaint_mask.png"
        # Verify the mask matches the EXTENDED canvas dim, not the
        # source dim. Source 64×64, extend right 25% → 80×64.
        import base64 as _b64
        from io import BytesIO as _BIO
        from PIL import Image as _Img
        mask_img = _Img.open(_BIO(_b64.b64decode(params["mask"]["data_b64"])))
        assert mask_img.size == (80, 64), f"mask size {mask_img.size} != (80,64)"


@pytest.mark.asyncio
async def test_outpaint_rejects_caller_supplied_mask(
    seeded_app: httpx.AsyncClient,
) -> None:
    """Outpaint synthesises its own mask; rejecting a user-supplied one
    avoids confusing semantics (whose mask wins?)."""
    await _seed_gpt_provider(seeded_app)
    token = await _login_user(seeded_app, username="outpaint_no_mask")

    parent_resp = await seeded_app.post(
        "/api/jobs",
        headers=_auth(token),
        files=[("payload", (None, _job_payload(), "application/json"))],
    )
    assert parent_resp.status_code == 200
    parent_hash = parent_resp.json()["hash_id"]
    from app.db.engine import get_session
    from app.db.models import Job
    from sqlalchemy import update
    async with get_session() as session:
        await session.execute(
            update(Job)
            .where(Job.hash_id == parent_hash)
            .values(status="SUCCEEDED")
        )
        await session.commit()

    resp = await seeded_app.post(
        "/api/jobs",
        headers=_auth(token),
        files=[
            (
                "payload",
                (
                    None,
                    _job_payload(
                        parent_hash_id=parent_hash,
                        derivation_kind="outpaint",
                        outpaint_directions=["top"],
                        outpaint_amount="10%",
                    ),
                    "application/json",
                ),
            ),
            ("ref_0", ("source.png", _ref_png(64, 64), "image/png")),
            ("mask", ("mask.png", _mask_png(64, 64), "image/png")),
        ],
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "INVALID_PARAMETER"


@pytest.mark.asyncio
async def test_outpaint_requires_directions(
    seeded_app: httpx.AsyncClient,
) -> None:
    await _seed_gpt_provider(seeded_app)
    token = await _login_user(seeded_app, username="outpaint_dirs")
    resp = await seeded_app.post(
        "/api/jobs",
        headers=_auth(token),
        files=[
            (
                "payload",
                (
                    None,
                    _job_payload(
                        parent_hash_id="j_doesnotexis",
                        derivation_kind="outpaint",
                    ),
                    "application/json",
                ),
            ),
        ],
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Derived endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_derived_endpoint_empty(seeded_app: httpx.AsyncClient) -> None:
    await _seed_gpt_provider(seeded_app)
    token = await _login_user(seeded_app, username="derived_empty")
    # Create a parent job
    resp = await seeded_app.post(
        "/api/jobs",
        headers=_auth(token),
        files=[("payload", (None, _job_payload(), "application/json"))],
    )
    assert resp.status_code == 200, resp.text
    parent_hash = resp.json()["hash_id"]

    derived = await seeded_app.get(
        f"/api/jobs/{parent_hash}/derived",
        headers=_auth(token),
    )
    assert derived.status_code == 200, derived.text
    body = derived.json()
    assert body["items"] == []
    assert body["next_cursor"] is None


@pytest.mark.asyncio
async def test_derived_endpoint_unknown_hash_404(
    seeded_app: httpx.AsyncClient,
) -> None:
    token = await _login_user(seeded_app, username="derived_404")
    resp = await seeded_app.get(
        "/api/jobs/j_doesnotexis/derived",
        headers=_auth(token),
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Outpaint synthesizer
# ---------------------------------------------------------------------------


def test_outpaint_resolve_geometry_percent() -> None:
    from app.services.outpaint import resolve_geometry

    g = resolve_geometry(1024, 1024, ["right"], "25%")
    assert g.right == 256 and g.left == 0 and g.top == 0 and g.bottom == 0


def test_outpaint_resolve_geometry_pixels() -> None:
    from app.services.outpaint import resolve_geometry

    g = resolve_geometry(1024, 768, ["left", "top"], "120px")
    assert g.left == 120 and g.top == 120
    assert g.right == 0 and g.bottom == 0


def test_outpaint_synth_canvas_size() -> None:
    from app.services.outpaint import synthesize_outpaint

    src = io.BytesIO()
    Image.new("RGBA", (256, 256), (200, 50, 50, 255)).save(src, format="PNG")
    canvas, mask, size = synthesize_outpaint(
        source_png_bytes=src.getvalue(),
        directions=["right", "bottom"],
        amount="50%",
    )
    assert size == (384, 384)  # 256 + 128 each side
    canvas_img = Image.open(io.BytesIO(canvas))
    mask_img = Image.open(io.BytesIO(mask))
    assert canvas_img.size == (384, 384)
    assert mask_img.size == (384, 384)
    # Mask: pixel inside original area opaque (=255), outside =0
    assert mask_img.getpixel((0, 0))[3] == 255  # original area
    assert mask_img.getpixel((383, 383))[3] == 0  # extended area
