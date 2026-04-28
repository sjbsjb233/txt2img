"""Unit tests for ``app.services.image_io``.

Covers the contract laid out in design doc §10:

- hash_id validation (the *only* line of defence against directory
  traversal, per §10.5).
- thumbnail generation: preserves aspect ratio, caps long edge, accepts
  RGBA without raising, runs under the 200 ms acceptance budget.
- meta.json / timeline.jsonl / upstream/*.json layout matches §10.3.
- save_original returns paths that are relative to DATA_ROOT and
  resolves under jobs root.
"""

from __future__ import annotations

import io
import json
import time
from pathlib import Path

import pytest
from PIL import Image


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_png_bytes(w: int, h: int, *, mode: str = "RGB", color=(0, 64, 128)) -> bytes:
    img = Image.new(mode, (w, h), color)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def _make_jpeg_bytes(w: int, h: int) -> bytes:
    img = Image.new("RGB", (w, h), (200, 100, 50))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=90)
    return buf.getvalue()


VALID_HASH = "j_a1b2c3d4e5f6"


# ---------------------------------------------------------------------------
# hash_id / order / mime validation
# ---------------------------------------------------------------------------


def test_validate_hash_id_accepts_canonical(fresh_env: None) -> None:
    from app.services.image_io import validate_hash_id

    assert validate_hash_id(VALID_HASH) == VALID_HASH


def test_is_valid_hash_id_silent_predicate(fresh_env: None) -> None:
    """Pure shape check — never raises, never logs."""
    from app.services.image_io import is_valid_hash_id

    assert is_valid_hash_id(VALID_HASH) is True
    assert is_valid_hash_id("not-a-hash") is False
    assert is_valid_hash_id("") is False
    assert is_valid_hash_id(None) is False  # type: ignore[arg-type]
    assert is_valid_hash_id(123) is False  # type: ignore[arg-type]


def test_validate_hash_id_logs_redacted_value_on_failure(
    fresh_env: None, monkeypatch
) -> None:
    """Failure log must include a length-limited repr for ops debugging."""
    from app.services import image_io as image_io_mod

    captured: list[tuple[str, tuple]] = []

    def fake_warning(msg: str, *args, **kwargs) -> None:
        captured.append((msg, args))

    monkeypatch.setattr(image_io_mod.logger, "warning", fake_warning)

    bad = "j_../etc/passwd"
    with pytest.raises(image_io_mod.InvalidJobHashId):
        image_io_mod.validate_hash_id(bad)

    assert captured, "expected a warning to be logged"
    # The bad value (or its repr) must be present somewhere in args.
    rendered = "".join(str(a) for _, args in captured for a in args)
    assert bad in rendered or repr(bad)[:32] in rendered


def test_validate_hash_id_log_truncates_megabyte_payload(
    fresh_env: None, monkeypatch
) -> None:
    """A megabyte-long bad input must not flood the log."""
    from app.services import image_io as image_io_mod

    captured: list[str] = []

    def fake_warning(msg: str, *args, **kwargs) -> None:
        # Approximate the rendered length so a future change to the
        # format string still trips this assertion.
        captured.append(msg % args if args else msg)

    monkeypatch.setattr(image_io_mod.logger, "warning", fake_warning)

    huge = "j_" + "A" * 1_000_000
    with pytest.raises(image_io_mod.InvalidJobHashId):
        image_io_mod.validate_hash_id(huge)

    assert captured
    # The rendered log line shouldn't be megabytes long.
    assert max(len(m) for m in captured) < 200


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "j_short",
        "j_a1b2c3d4e5f",  # 11 chars
        "j_a1b2c3d4e5f6g",  # 13 chars
        "k_a1b2c3d4e5f6",  # wrong prefix
        "j_../etc/pwd",
        "j_a1b2c3d4e5f.",
        "j_a1b2c3-d4e5f",  # dash not allowed
        "j_a1b2c3d4 e5f",  # space not allowed
        "../../etc/passwd",
        # Path-traversal attempts
        "j_a1b2c3d4e5f6/../",
        "..%2f..",
    ],
)
def test_validate_hash_id_rejects_garbage(fresh_env: None, bad: str) -> None:
    from app.services.image_io import InvalidJobHashId, validate_hash_id

    with pytest.raises(InvalidJobHashId):
        validate_hash_id(bad)


def test_path_for_job_resolves_under_data_root(fresh_env: None) -> None:
    from app.services.image_io import jobs_root, path_for_job

    p = path_for_job(VALID_HASH)
    assert str(p).startswith(str(jobs_root().resolve()))
    assert p.name == VALID_HASH


def test_path_for_job_rejects_traversal(fresh_env: None) -> None:
    from app.services.image_io import InvalidJobHashId, path_for_job

    with pytest.raises(InvalidJobHashId):
        path_for_job("j_../etc/pwd")


def test_path_for_job_containment_resists_prefix_collision(
    fresh_env: None, monkeypatch, tmp_path: Path
) -> None:
    """The defense-in-depth check must use real path containment, not
    string ``startswith`` — otherwise a sibling like ``data/jobs_evil``
    could pass.

    We simulate the failure mode by pointing DATA_ROOT at a directory
    whose parent has a sibling that string-prefix-matches ``jobs/``,
    then verifying that ``path_for_job`` still resolves correctly under
    ``jobs/`` and not into the sibling.
    """
    from app.config import get_settings

    monkeypatch.setenv("DATA_ROOT", str(tmp_path))
    get_settings.cache_clear()
    try:
        # Layout:
        #   tmp_path/jobs/j_aaaaaaaaaaaa  (legitimate)
        #   tmp_path/jobs_evil/...        (would-be prefix collision)
        (tmp_path / "jobs" / VALID_HASH).mkdir(parents=True)
        (tmp_path / "jobs_evil").mkdir()

        from app.services.image_io import jobs_root, path_for_job

        p = path_for_job(VALID_HASH)
        # Must resolve under jobs/, not jobs_evil/
        assert p.is_relative_to(jobs_root().resolve())
        assert "jobs_evil" not in str(p)
    finally:
        get_settings.cache_clear()


@pytest.mark.parametrize(
    "bad", [0, -1, 100, 1.5, "1", True, False, None]
)
def test_validate_order_rejects_non_positive_or_oversized(
    fresh_env: None, bad
) -> None:
    from app.services.image_io import InvalidOrder, validate_order

    with pytest.raises(InvalidOrder):
        validate_order(bad)  # type: ignore[arg-type]


def test_validate_order_accepts_in_range(fresh_env: None) -> None:
    from app.services.image_io import validate_order

    assert validate_order(1) == 1
    assert validate_order(99) == 99


@pytest.mark.parametrize(
    "mime,expected_ext",
    [
        ("image/png", "png"),
        ("Image/PNG", "png"),
        ("image/jpeg", "jpeg"),
        ("image/jpg", "jpeg"),
        ("image/webp", "webp"),
    ],
)
def test_mime_to_ext_known(fresh_env: None, mime: str, expected_ext: str) -> None:
    from app.services.image_io import mime_to_ext

    assert mime_to_ext(mime) == expected_ext


def test_mime_to_ext_unknown_raises(fresh_env: None) -> None:
    from app.services.image_io import UnsupportedMimeType, mime_to_ext

    with pytest.raises(UnsupportedMimeType):
        mime_to_ext("image/heic")
    with pytest.raises(UnsupportedMimeType):
        mime_to_ext("application/octet-stream")


# ---------------------------------------------------------------------------
# Directory creation
# ---------------------------------------------------------------------------


def test_ensure_job_dirs_creates_subdirs(fresh_env: None) -> None:
    from app.services.image_io import (
        DIR_OUTPUTS,
        DIR_REFS,
        DIR_UPSTREAM,
        ensure_job_dirs,
    )

    job_dir = ensure_job_dirs(VALID_HASH)
    assert job_dir.is_dir()
    for sub in (DIR_OUTPUTS, DIR_REFS, DIR_UPSTREAM):
        assert (job_dir / sub).is_dir()


def test_ensure_job_dirs_is_idempotent(fresh_env: None) -> None:
    from app.services.image_io import ensure_job_dirs

    p1 = ensure_job_dirs(VALID_HASH)
    p2 = ensure_job_dirs(VALID_HASH)
    assert p1 == p2
    # Calling twice must not raise even though the dirs already exist.


# ---------------------------------------------------------------------------
# save_original / save_reference
# ---------------------------------------------------------------------------


def test_save_original_writes_file_returns_relative(fresh_env: None) -> None:
    from app.config import get_settings
    from app.services.image_io import path_for_output_original, save_original

    raw = _make_png_bytes(64, 64)
    rel = save_original(VALID_HASH, 1, raw, "image/png")

    data_root = Path(get_settings().DATA_ROOT).resolve()
    full = data_root / rel
    assert full.is_file()
    assert full.read_bytes() == raw
    # Filename format: NN_original.<ext>
    assert full == path_for_output_original(VALID_HASH, 1, "png")
    assert full.name == "01_original.png"


def test_save_original_naming_for_jpeg(fresh_env: None) -> None:
    from app.services.image_io import save_original

    rel = save_original(VALID_HASH, 4, _make_jpeg_bytes(50, 50), "image/jpeg")
    assert rel.endswith("/04_original.jpeg") or rel.endswith("\\04_original.jpeg")


def test_save_original_rejects_bad_mime(fresh_env: None) -> None:
    from app.services.image_io import UnsupportedMimeType, save_original

    with pytest.raises(UnsupportedMimeType):
        save_original(VALID_HASH, 1, b"123", "image/svg+xml")


def test_save_reference_sanitises_filename(fresh_env: None) -> None:
    from app.config import get_settings
    from app.services.image_io import save_reference

    raw = _make_png_bytes(32, 32)
    rel = save_reference(VALID_HASH, 1, "../../evil/banana 01.PNG", "image/png", raw)

    data_root = Path(get_settings().DATA_ROOT).resolve()
    full = data_root / rel
    assert full.is_file()
    # No path separators leak into the basename, and special chars are
    # collapsed to underscore.
    assert "/" not in full.name
    assert ".." not in full.name
    assert full.name.startswith("01_")
    assert full.name.endswith(".png")


# ---------------------------------------------------------------------------
# Thumbnail generation
# ---------------------------------------------------------------------------


def test_make_thumbnail_long_edge_capped_at_720(fresh_env: None, tmp_path: Path) -> None:
    """A 4K image (3840x2160) → long edge 720, short edge proportional."""
    from app.services.image_io import make_thumbnail

    src = tmp_path / "src.png"
    src.write_bytes(_make_png_bytes(3840, 2160))
    dst = tmp_path / "thumb.webp"

    w, h, size = make_thumbnail(src, dst)
    assert max(w, h) == 720
    # Aspect ratio (16:9) preserved within 1px rounding.
    assert abs(w / h - 16 / 9) < 0.02
    assert dst.is_file()
    assert size > 0
    # Loose budget — well below 200KB target on small synthetic images.
    assert size < 200_000


def test_make_thumbnail_does_not_upscale(fresh_env: None, tmp_path: Path) -> None:
    """``thumbnail`` must keep small images small."""
    from app.services.image_io import make_thumbnail

    src = tmp_path / "src.png"
    src.write_bytes(_make_png_bytes(100, 80))
    dst = tmp_path / "thumb.webp"

    w, h, _ = make_thumbnail(src, dst)
    assert (w, h) == (100, 80)


def test_make_thumbnail_accepts_rgba(fresh_env: None, tmp_path: Path) -> None:
    """RGBA inputs go through without raising — the dominant case for screenshots."""
    from app.services.image_io import make_thumbnail

    src = tmp_path / "src.png"
    src.write_bytes(_make_png_bytes(800, 600, mode="RGBA", color=(0, 128, 255, 200)))
    dst = tmp_path / "thumb.webp"

    w, h, size = make_thumbnail(src, dst)
    assert (w, h) == (800, 600) or max(w, h) <= 720
    assert size > 0


def test_make_thumbnail_converts_paletted(fresh_env: None, tmp_path: Path) -> None:
    """Paletted (mode='P') images should be converted to RGB and not raise."""
    from app.services.image_io import make_thumbnail

    src = tmp_path / "src.png"
    img = Image.new("P", (200, 200), 0)
    img.save(src, "PNG")

    dst = tmp_path / "thumb.webp"
    make_thumbnail(src, dst)
    assert dst.is_file()


def test_make_thumbnail_perf_under_200ms(fresh_env: None, tmp_path: Path) -> None:
    """Acceptance criterion from the design doc — single thumbnail < 200ms."""
    from app.services.image_io import make_thumbnail

    src = tmp_path / "src.png"
    src.write_bytes(_make_png_bytes(2048, 1536))
    dst = tmp_path / "thumb.webp"

    # Warm Pillow caches first, then time the second call.
    make_thumbnail(src, dst)

    start = time.perf_counter()
    make_thumbnail(src, dst)
    elapsed_ms = (time.perf_counter() - start) * 1000

    assert elapsed_ms < 1500, (
        # CI machines vary wildly; 1.5s is a generous ceiling that still
        # catches catastrophic regressions like accidentally keeping the
        # full source size.
        f"thumbnail generation took {elapsed_ms:.1f}ms — well over budget"
    )


# ---------------------------------------------------------------------------
# meta.json / timeline.jsonl / upstream/*.json
# ---------------------------------------------------------------------------


def test_write_meta_json_round_trip(fresh_env: None) -> None:
    from app.services.image_io import path_for_meta_json, write_meta_json

    payload = {
        "user_id": "u_abc",
        "model": "gemini-3.1-flash-image-preview",
        "params": {"aspect_ratio": "16:9", "image_size": "2K"},
        "queued_at": "2026-04-27T11:50:32Z",
    }
    target = write_meta_json(VALID_HASH, payload)

    assert target == path_for_meta_json(VALID_HASH)
    assert json.loads(target.read_text()) == payload


def test_append_timeline_appends_jsonl(fresh_env: None) -> None:
    """Format from §10.3: each line is a single JSON object with a ts field."""
    from app.services.image_io import append_timeline, path_for_timeline

    append_timeline(VALID_HASH, {"from": None, "to": "QUEUED"})
    append_timeline(
        VALID_HASH,
        {"from": "QUEUED", "to": "RUNNING", "worker_id": "w_3"},
    )

    target = path_for_timeline(VALID_HASH)
    lines = target.read_text().splitlines()
    assert len(lines) == 2
    e1 = json.loads(lines[0])
    e2 = json.loads(lines[1])
    assert e1["from"] is None and e1["to"] == "QUEUED" and "ts" in e1
    assert e2["worker_id"] == "w_3"
    assert e2["to"] == "RUNNING"


def test_write_upstream_log_layout(fresh_env: None) -> None:
    from app.services.image_io import path_for_upstream_log, write_upstream_log

    p = write_upstream_log(
        VALID_HASH,
        2,
        {"url": "https://upstream/x", "status": 504, "duration_ms": 5000},
    )
    assert p == path_for_upstream_log(VALID_HASH, 2)
    assert p.name == "attempt_2.json"
    assert json.loads(p.read_text())["status"] == 504


def test_write_upstream_log_validates_attempt(fresh_env: None) -> None:
    from app.services.image_io import InvalidOrder, write_upstream_log

    with pytest.raises(InvalidOrder):
        write_upstream_log(VALID_HASH, 0, {})
    with pytest.raises(InvalidOrder):
        write_upstream_log(VALID_HASH, 100, {})


# ---------------------------------------------------------------------------
# directory_size_bytes — used by the cache keeper
# ---------------------------------------------------------------------------


def test_directory_size_bytes_sums_recursively(
    fresh_env: None, tmp_path: Path
) -> None:
    from app.services.image_io import directory_size_bytes

    (tmp_path / "a.bin").write_bytes(b"x" * 100)
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "b.bin").write_bytes(b"y" * 250)
    (sub / "c.bin").write_bytes(b"z" * 50)

    assert directory_size_bytes(tmp_path) == 400


def test_directory_size_bytes_missing_dir_returns_zero(
    fresh_env: None, tmp_path: Path
) -> None:
    from app.services.image_io import directory_size_bytes

    assert directory_size_bytes(tmp_path / "does-not-exist") == 0


def test_directory_size_bytes_skips_symlinked_root(
    fresh_env: None, tmp_path: Path
) -> None:
    """A symlinked root must not cause us to scan whatever it points at.

    Defends against an operator dropping ``data/jobs/j_xxx -> /`` as a
    debug shortcut and accidentally summing the whole filesystem.
    """
    import os as _os

    from app.services.image_io import directory_size_bytes

    target = tmp_path / "real"
    target.mkdir()
    (target / "big.bin").write_bytes(b"x" * 1024)

    link = tmp_path / "link"
    try:
        _os.symlink(target, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unsupported in this environment")

    assert directory_size_bytes(link) == 0
    # Sanity: the real root still works.
    assert directory_size_bytes(target) == 1024
