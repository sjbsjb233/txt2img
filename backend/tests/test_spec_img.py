"""T-IMG-NN spec cases (文生图平台测试方案 §5.13)."""

from __future__ import annotations

import asyncio
import json
from io import BytesIO
from pathlib import Path

import httpx
import pytest

from PIL import Image as PILImage

from tests.infra.fake_adapter import FakeAdapter, register_fake_adapter
from tests.infra.seeds import auth, install_fake_provider, login_user


pytestmark = [pytest.mark.img]


def _payload(**o):
    base = {"model": "gpt-image-2", "prompt": "p", "n": 1,
            "size": "1024x1024", "output_format": "png"}
    base.update(o)
    return json.dumps(base)


async def _create_and_wait(seeded_app, token):
    register_fake_adapter()
    FakeAdapter.reset()
    # Larger image so the thumbnail actually has work to do
    FakeAdapter.behavior["image_size"] = (1500, 1000)
    await install_fake_provider()
    r = await seeded_app.post(
        "/api/jobs",
        headers=auth(token),
        files={"payload": (None, _payload(), "application/json")},
    )
    assert r.status_code == 200
    h = r.json()["hash_id"]
    for _ in range(60):
        d = await seeded_app.get(f"/api/jobs/{h}", headers=auth(token))
        if d.status_code == 200 and d.json()["status"] == "SUCCEEDED":
            return h, d.json()
        await asyncio.sleep(0.1)
    raise AssertionError("never SUCCEEDED")


# ---------------------------------------------------------------------------
# T-IMG-01 · thumbnail long edge ≤ 720
# T-IMG-02 · thumbnail is webp
# ---------------------------------------------------------------------------
@pytest.mark.p0
async def test_t_img_01_02_thumbnail_caps_and_format(
    seeded_app: httpx.AsyncClient,
):
    user, token = await login_user(seeded_app, tier="vip")
    h, _detail = await _create_and_wait(seeded_app, token)

    r = await seeded_app.get(
        f"/api/jobs/{h}/images/1/thumb", headers=auth(token)
    )
    assert r.status_code == 200, (r.status_code, r.text)
    blob = r.content
    # WebP magic bytes
    assert blob[:4] == b"RIFF" and blob[8:12] == b"WEBP", blob[:16]

    img = PILImage.open(BytesIO(blob))
    assert max(img.size) <= 720, img.size


# ---------------------------------------------------------------------------
# T-IMG-03 · original bytes preserved
# ---------------------------------------------------------------------------
@pytest.mark.p0
async def test_t_img_03_original_bytes_preserved(
    seeded_app: httpx.AsyncClient,
):
    user, token = await login_user(seeded_app, tier="vip")
    h, _ = await _create_and_wait(seeded_app, token)
    r = await seeded_app.get(
        f"/api/jobs/{h}/images/1/original", headers=auth(token)
    )
    assert r.status_code == 200
    assert "attachment" in r.headers.get("content-disposition", "").lower()
    # PNG header
    assert r.content[:8] == b"\x89PNG\r\n\x1a\n"


# ---------------------------------------------------------------------------
# T-IMG-04 · meta.json contains user/model/params snapshot
# ---------------------------------------------------------------------------
@pytest.mark.p1
async def test_t_img_04_meta_json(
    seeded_app: httpx.AsyncClient,
):
    user, token = await login_user(seeded_app, tier="vip")
    h, _ = await _create_and_wait(seeded_app, token)
    from app.config import get_settings

    meta_path = Path(get_settings().DATA_ROOT) / "jobs" / h / "meta.json"
    assert meta_path.exists(), meta_path
    meta = json.loads(meta_path.read_text())
    assert meta.get("model") == "gpt-image-2"
    assert "params" in meta or "request" in meta
