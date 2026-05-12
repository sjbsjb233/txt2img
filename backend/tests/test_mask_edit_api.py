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


# ---------------------------------------------------------------------------
# parent_order plumbing (PRD §6.1)
# ---------------------------------------------------------------------------


async def _make_succeeded_parent(
    client: httpx.AsyncClient,
    token: str,
    *,
    image_count: int = 1,
) -> str:
    """Create a parent job, mark SUCCEEDED, and seed N images on it.

    Returns the parent's hash_id. The synthesised image rows are the only
    thing parent_order validation reads.
    """
    resp = await client.post(
        "/api/jobs",
        headers=_auth(token),
        files=[("payload", (None, _job_payload(n=image_count), "application/json"))],
    )
    assert resp.status_code == 200, resp.text
    parent_hash = resp.json()["hash_id"]

    from app.db.engine import get_session
    from app.db.models import Image as ImageRow, Job
    from sqlalchemy import select, update

    async with get_session() as session:
        await session.execute(
            update(Job).where(Job.hash_id == parent_hash).values(status="SUCCEEDED")
        )
        parent_row = (
            await session.execute(select(Job).where(Job.hash_id == parent_hash))
        ).scalar_one()
        for order in range(1, image_count + 1):
            session.add(
                ImageRow(
                    id=f"img_{parent_row.id[:6]}_{order}",
                    job_id=parent_row.id,
                    img_order=order,
                    original_path=f"data/jobs/{parent_hash}/{order:02d}.png",
                    thumb_path=f"data/jobs/{parent_hash}/{order:02d}_thumb.webp",
                    width=64,
                    height=64,
                    format="png",
                    file_size_bytes=128,
                )
            )
        await session.commit()
    return parent_hash


@pytest.mark.asyncio
async def test_create_derived_job_carries_parent_order(
    seeded_app: httpx.AsyncClient,
) -> None:
    """A mask edit derived from order=3 of a create-set persists parent_order=3
    and surfaces it on both create response and JobDetail."""
    await _seed_gpt_provider(seeded_app)
    token = await _login_user(seeded_app, username="po_happy")
    parent_hash = await _make_succeeded_parent(seeded_app, token, image_count=4)

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
                        parent_order=3,
                        derivation_kind="mask_edit",
                    ),
                    "application/json",
                ),
            ),
            ("ref_0", ("source.png", _ref_png(64, 64), "image/png")),
            ("mask", ("mask.png", _mask_png(64, 64), "image/png")),
        ],
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["parent_hash_id"] == parent_hash
    assert body["parent_order"] == 3
    assert body["derivation_kind"] == "mask_edit"

    # JobDetail should mirror the field too.
    detail = await seeded_app.get(
        f"/api/jobs/{body['hash_id']}", headers=_auth(token)
    )
    assert detail.status_code == 200, detail.text
    dbody = detail.json()
    assert dbody["parent_order"] == 3


@pytest.mark.asyncio
async def test_create_derived_rejects_out_of_range_parent_order(
    seeded_app: httpx.AsyncClient,
) -> None:
    """parent_order beyond the parent's image count is a 422."""
    await _seed_gpt_provider(seeded_app)
    token = await _login_user(seeded_app, username="po_bounds")
    parent_hash = await _make_succeeded_parent(seeded_app, token, image_count=2)

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
                        parent_order=5,  # parent only has 2 images
                        derivation_kind="mask_edit",
                    ),
                    "application/json",
                ),
            ),
            ("ref_0", ("source.png", _ref_png(64, 64), "image/png")),
            ("mask", ("mask.png", _mask_png(64, 64), "image/png")),
        ],
    )
    assert resp.status_code == 422, resp.text
    body = resp.json()
    assert body["detail"]["code"] == "INVALID_PARAMETER"
    assert "parent_order" in body["detail"].get("field", "")


@pytest.mark.asyncio
async def test_parent_order_without_parent_hash_id_is_422(
    seeded_app: httpx.AsyncClient,
) -> None:
    """parent_order alone (no parent_hash_id) is a schema violation."""
    await _seed_gpt_provider(seeded_app)
    token = await _login_user(seeded_app, username="po_orphan")
    resp = await seeded_app.post(
        "/api/jobs",
        headers=_auth(token),
        files=[
            (
                "payload",
                (
                    None,
                    _job_payload(parent_order=1),
                    "application/json",
                ),
            ),
        ],
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["code"] == "INVALID_PARAMETER"


# ---------------------------------------------------------------------------
# Fallback mask-edit shape (no mask blob; bw mask comes as second ref)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_mask_edit_fallback_succeeds_with_two_refs(
    seeded_app: httpx.AsyncClient,
) -> None:
    """Fallback path: no mask blob, but a source + bw-mask reference pair
    is accepted. ``params.mask_method`` should land as ``"fallback"``."""
    await _seed_gpt_provider(seeded_app)
    token = await _login_user(seeded_app, username="fallback_ok")
    parent_hash = await _make_succeeded_parent(seeded_app, token, image_count=1)

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
                        parent_order=1,
                        derivation_kind="mask_edit",
                    ),
                    "application/json",
                ),
            ),
            ("ref_0", ("source.png", _ref_png(64, 64), "image/png")),
            ("ref_1", ("bw_mask.png", _ref_png(64, 64), "image/png")),
        ],
    )
    assert resp.status_code == 200, resp.text
    derived_hash = resp.json()["hash_id"]

    from app.db.engine import get_session
    from app.db.models import Job
    from sqlalchemy import select

    async with get_session() as session:
        row = (
            await session.execute(select(Job).where(Job.hash_id == derived_hash))
        ).scalar_one()
        params = json.loads(row.params_json)
        assert params.get("mask_method") == "fallback"
        assert "mask" not in params  # no native mask carried


@pytest.mark.asyncio
async def test_create_mask_edit_native_records_method(
    seeded_app: httpx.AsyncClient,
) -> None:
    """Native path: mask blob attached → ``params.mask_method == "native"``."""
    await _seed_gpt_provider(seeded_app)
    token = await _login_user(seeded_app, username="native_ok")
    parent_hash = await _make_succeeded_parent(seeded_app, token, image_count=1)

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
                        parent_order=1,
                        derivation_kind="mask_edit",
                    ),
                    "application/json",
                ),
            ),
            ("ref_0", ("source.png", _ref_png(64, 64), "image/png")),
            ("mask", ("mask.png", _mask_png(64, 64), "image/png")),
        ],
    )
    assert resp.status_code == 200, resp.text
    derived_hash = resp.json()["hash_id"]

    from app.db.engine import get_session
    from app.db.models import Job
    from sqlalchemy import select

    async with get_session() as session:
        row = (
            await session.execute(select(Job).where(Job.hash_id == derived_hash))
        ).scalar_one()
        params = json.loads(row.params_json)
        assert params.get("mask_method") == "native"
        assert "mask" in params


@pytest.mark.asyncio
async def test_create_mask_edit_fallback_requires_two_refs(
    seeded_app: httpx.AsyncClient,
) -> None:
    """Fallback with a single ref must be rejected — without the bw mask
    reference the request silently degrades into a plain i2i edit, which
    would burn upstream credit on a malformed pair."""
    await _seed_gpt_provider(seeded_app)
    token = await _login_user(seeded_app, username="fallback_one_ref")
    parent_hash = await _make_succeeded_parent(seeded_app, token, image_count=1)

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
                        parent_order=1,
                        derivation_kind="mask_edit",
                    ),
                    "application/json",
                ),
            ),
            ("ref_0", ("source.png", _ref_png(64, 64), "image/png")),
        ],
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["code"] == "MASK_FALLBACK_REQUIRES_TWO_REFS"


# ---------------------------------------------------------------------------
# POST /api/jobs/<hash>/replace_image
# ---------------------------------------------------------------------------


async def _make_succeeded_mask_edit_with_image(
    client: httpx.AsyncClient,
    token: str,
    *,
    width: int = 64,
    height: int = 64,
) -> tuple[str, str]:
    """Create a parent + a mask_edit derived job, mark derived SUCCEEDED,
    write a real PNG to disk for img_order=1 so replace_image has a file
    to swap. Returns ``(derived_hash, original_rel_path)``."""
    parent_hash = await _make_succeeded_parent(client, token, image_count=1)
    resp = await client.post(
        "/api/jobs",
        headers=_auth(token),
        files=[
            (
                "payload",
                (
                    None,
                    _job_payload(
                        parent_hash_id=parent_hash,
                        parent_order=1,
                        derivation_kind="mask_edit",
                    ),
                    "application/json",
                ),
            ),
            ("ref_0", ("source.png", _ref_png(width, height), "image/png")),
            ("mask", ("mask.png", _mask_png(width, height), "image/png")),
        ],
    )
    assert resp.status_code == 200, resp.text
    derived_hash = resp.json()["hash_id"]

    from app.db.engine import get_session
    from app.db.models import Image as ImageRow, Job
    from sqlalchemy import select, update
    from app.services import image_io as _io
    from pathlib import Path

    job_dir = _io.path_for_job(derived_hash)
    outputs = job_dir / "outputs"
    outputs.mkdir(parents=True, exist_ok=True)
    img_path = outputs / "01_original.png"
    img_path.write_bytes(_ref_png(width, height))
    rel_path = str(img_path.relative_to(_io._data_root()))
    async with get_session() as session:
        await session.execute(
            update(Job).where(Job.hash_id == derived_hash).values(status="SUCCEEDED")
        )
        derived_row = (
            await session.execute(select(Job).where(Job.hash_id == derived_hash))
        ).scalar_one()
        session.add(
            ImageRow(
                id=f"img_{derived_row.id[:6]}_1",
                job_id=derived_row.id,
                img_order=1,
                original_path=rel_path,
                thumb_path=rel_path,
                width=width,
                height=height,
                format="png",
                file_size_bytes=len(_ref_png(width, height)),
            )
        )
        await session.commit()
    return derived_hash, rel_path


@pytest.mark.asyncio
async def test_replace_image_happy_path(seeded_app: httpx.AsyncClient) -> None:
    """Upload composite → original is backed up, file is overwritten,
    meta.json carries the composite sentinel."""
    await _seed_gpt_provider(seeded_app)
    token = await _login_user(seeded_app, username="replace_ok")
    derived_hash, rel_path = await _make_succeeded_mask_edit_with_image(
        seeded_app, token
    )

    composite = _ref_png(64, 64)
    resp = await seeded_app.post(
        f"/api/jobs/{derived_hash}/replace_image",
        headers=_auth(token),
        files=[
            ("composite", ("composite.png", composite, "image/png")),
        ],
        data={"strategy": "mask_only_overlay"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["replaced"] is True
    assert body["composite"] == "mask_only_overlay"

    from app.services import image_io as _io
    from pathlib import Path
    abs_path = _io._data_root() / rel_path
    backup = abs_path.with_suffix(abs_path.suffix + ".original")
    assert backup.exists(), "expected .original backup to be created"
    # meta.json should carry the composite marker.
    meta_path = _io.path_for_meta_json(derived_hash)
    meta = json.loads(meta_path.read_text())
    assert meta["images"][0]["composite"] == "mask_only_overlay"


@pytest.mark.asyncio
async def test_replace_image_idempotency_returns_409(
    seeded_app: httpx.AsyncClient,
) -> None:
    """A second call on the same image returns 409 with IMAGE_ALREADY_REPLACED."""
    await _seed_gpt_provider(seeded_app)
    token = await _login_user(seeded_app, username="replace_dup")
    derived_hash, _ = await _make_succeeded_mask_edit_with_image(seeded_app, token)

    composite = _ref_png(64, 64)
    files = [("composite", ("composite.png", composite, "image/png"))]
    data = {"strategy": "mask_only_overlay"}
    first = await seeded_app.post(
        f"/api/jobs/{derived_hash}/replace_image",
        headers=_auth(token),
        files=files,
        data=data,
    )
    assert first.status_code == 200
    second = await seeded_app.post(
        f"/api/jobs/{derived_hash}/replace_image",
        headers=_auth(token),
        files=[("composite", ("composite.png", composite, "image/png"))],
        data=data,
    )
    assert second.status_code == 409, second.text
    assert second.json()["detail"]["code"] == "IMAGE_ALREADY_REPLACED"


@pytest.mark.asyncio
async def test_replace_image_rejects_dimension_mismatch(
    seeded_app: httpx.AsyncClient,
) -> None:
    """A composite that doesn't match the image dims returns 422
    INVALID_COMPOSITE so we don't silently desync sha/size from pixels."""
    await _seed_gpt_provider(seeded_app)
    token = await _login_user(seeded_app, username="replace_dim")
    derived_hash, _ = await _make_succeeded_mask_edit_with_image(
        seeded_app, token, width=64, height=64
    )

    wrong = _ref_png(32, 32)
    resp = await seeded_app.post(
        f"/api/jobs/{derived_hash}/replace_image",
        headers=_auth(token),
        files=[("composite", ("composite.png", wrong, "image/png"))],
        data={"strategy": "mask_only_overlay"},
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "INVALID_COMPOSITE"


@pytest.mark.asyncio
async def test_replace_image_rejects_running_job(
    seeded_app: httpx.AsyncClient,
) -> None:
    """A QUEUED / RUNNING job can't be composited — only SUCCEEDED."""
    await _seed_gpt_provider(seeded_app)
    token = await _login_user(seeded_app, username="replace_running")
    parent_hash = await _make_succeeded_parent(seeded_app, token, image_count=1)
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
                        parent_order=1,
                        derivation_kind="mask_edit",
                    ),
                    "application/json",
                ),
            ),
            ("ref_0", ("source.png", _ref_png(64, 64), "image/png")),
            ("mask", ("mask.png", _mask_png(64, 64), "image/png")),
        ],
    )
    derived_hash = resp.json()["hash_id"]
    composite = _ref_png(64, 64)
    res = await seeded_app.post(
        f"/api/jobs/{derived_hash}/replace_image",
        headers=_auth(token),
        files=[("composite", ("composite.png", composite, "image/png"))],
        data={"strategy": "mask_only_overlay"},
    )
    assert res.status_code == 422
    assert res.json()["detail"]["code"] == "JOB_NOT_TERMINAL"


@pytest.mark.asyncio
async def test_replace_image_rejects_unknown_strategy(
    seeded_app: httpx.AsyncClient,
) -> None:
    """Unknown strategy → 422 INVALID_COMPOSITE up-front (before file read)."""
    await _seed_gpt_provider(seeded_app)
    token = await _login_user(seeded_app, username="replace_strategy")
    derived_hash, _ = await _make_succeeded_mask_edit_with_image(seeded_app, token)

    composite = _ref_png(64, 64)
    resp = await seeded_app.post(
        f"/api/jobs/{derived_hash}/replace_image",
        headers=_auth(token),
        files=[("composite", ("composite.png", composite, "image/png"))],
        data={"strategy": "garbage"},
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "INVALID_COMPOSITE"


@pytest.mark.asyncio
async def test_jobs_index_includes_parent_order(
    seeded_app: httpx.AsyncClient,
) -> None:
    """The /api/jobs/index entries expose parent_order so the lineage
    selector can resolve which image of a create-set the child came from."""
    await _seed_gpt_provider(seeded_app)
    token = await _login_user(seeded_app, username="po_index")
    parent_hash = await _make_succeeded_parent(seeded_app, token, image_count=4)

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
                        parent_order=2,
                        derivation_kind="mask_edit",
                    ),
                    "application/json",
                ),
            ),
            ("ref_0", ("source.png", _ref_png(64, 64), "image/png")),
            ("mask", ("mask.png", _mask_png(64, 64), "image/png")),
        ],
    )
    assert resp.status_code == 200, resp.text
    derived_hash = resp.json()["hash_id"]

    idx = await seeded_app.get("/api/jobs/index", headers=_auth(token))
    assert idx.status_code == 200, idx.text
    items = idx.json()["items"]
    derived_entry = next(it for it in items if it["hash_id"] == derived_hash)
    assert derived_entry["parent_order"] == 2
    assert derived_entry["derivation_kind"] == "mask_edit"
