"""On-disk layout for jobs + thumbnail generation.

Single source of truth for where bytes belonging to a job land on disk and
for the rules that decide whether a path is safe to dereference. The
filesystem layout is defined in design doc §1.2 and §10::

    data/jobs/<hash_id>/
    ├── meta.json
    ├── refs/
    │   ├── 01_<orig_name>.<ext>
    │   └── 02_<orig_name>.<ext>
    ├── outputs/
    │   ├── 01_original.<ext>
    │   ├── 01_thumb.webp
    │   └── ...
    ├── upstream/
    │   ├── attempt_1.json
    │   └── attempt_2.json
    └── timeline.jsonl

Why everything routes through this module:

- ``hash_id`` arrives from request URLs. We must never let
  ``../../etc/passwd`` resolve to a real file (design doc §10.5). The
  regex ``^j_[A-Za-z0-9]{12}$`` is enforced *before* any path join.
- ``order`` is part of the public URL surface for references and output
  images. Constraining it to a positive int guards against negative or
  exotic values that could escape the formatted filename.
- The thumbnail algorithm in §10.2 is small but easy to get wrong (the
  RGBA-vs-other-mode branch matters for non-PNG inputs). Centralising it
  means we get the same dimensions and quality regardless of who calls it.

The module is deliberately split between sync helpers (used directly in
unit tests, blocking I/O is fine) and a couple of async wrappers that the
job executor (PR-11) will call via ``asyncio.to_thread``. PR-07 itself
ships only the helpers; the executor is wired up later.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image

from app.config import get_settings

logger = logging.getLogger("txt2img.image_io")


# ---------------------------------------------------------------------------
# Path helpers — every public function calls one of these first.
# ---------------------------------------------------------------------------

# Job hash_id is the public-facing 14-char id ("j_" + 12 chars).
# See design doc Appendix B + §10.5. Never widen this — the validation is
# the only thing standing between user input and a real filesystem path.
_HASH_ID_RE = re.compile(r"^j_[A-Za-z0-9]{12}$")

# Job-relative subdirs. Kept as constants so tests can introspect the layout.
DIR_OUTPUTS = "outputs"
DIR_REFS = "refs"
DIR_UPSTREAM = "upstream"

META_FILENAME = "meta.json"
TIMELINE_FILENAME = "timeline.jsonl"

# Mapping from MIME type to the on-disk extension we want to use. We cap
# this at the formats Pillow can read/write reliably for the project's
# image pipeline; anything else is rejected by ``mime_to_ext``.
_MIME_TO_EXT: dict[str, str] = {
    "image/png": "png",
    "image/jpeg": "jpeg",
    "image/jpg": "jpeg",
    "image/webp": "webp",
}


class InvalidJobHashId(ValueError):
    """Raised when a hash_id fails the ``^j_[A-Za-z0-9]{12}$`` check."""


class InvalidOrder(ValueError):
    """Raised when ``order`` is not a positive int <= 99."""


class UnsupportedMimeType(ValueError):
    """Raised when a MIME type can't be mapped to a file extension."""


def validate_hash_id(hash_id: str) -> str:
    """Confirm ``hash_id`` matches the public-facing format.

    Returns the input unchanged on success so callers can ``Path(...) /
    validate_hash_id(h)`` in one line.
    """
    if not isinstance(hash_id, str) or not _HASH_ID_RE.match(hash_id):
        # Don't echo the bad value to the client — it could itself be an
        # attempted injection — but log it for ops.
        logger.warning("rejected hash_id with invalid shape")
        raise InvalidJobHashId("hash_id must match ^j_[A-Za-z0-9]{12}$")
    return hash_id


def validate_order(order: int) -> int:
    """Confirm ``order`` is a small positive int.

    1..99 is the practical range: gpt-image-2 caps ``n`` at 10, gemini
    accepts up to 14 references. We allow up to 99 to keep the ``{:02d}``
    filename format intact and leave headroom.
    """
    if not isinstance(order, int) or isinstance(order, bool):
        raise InvalidOrder("order must be a positive int")
    if order < 1 or order > 99:
        raise InvalidOrder("order must be in [1, 99]")
    return order


def mime_to_ext(mime: str) -> str:
    """Map a MIME string to the on-disk extension. Case-insensitive."""
    if not isinstance(mime, str):
        raise UnsupportedMimeType("mime must be a string")
    key = mime.strip().lower()
    if key not in _MIME_TO_EXT:
        raise UnsupportedMimeType(f"unsupported MIME type: {mime!r}")
    return _MIME_TO_EXT[key]


def _data_root() -> Path:
    """Resolve the configured ``DATA_ROOT`` to an absolute Path.

    Resolved each call rather than cached: tests swap ``DATA_ROOT`` between
    cases via the settings fixture, and a module-level cache would freeze
    the first value seen at import time.
    """
    return Path(get_settings().DATA_ROOT).resolve()


def jobs_root() -> Path:
    """Return ``<DATA_ROOT>/jobs``. Does not create it."""
    return _data_root() / "jobs"


def path_for_job(hash_id: str) -> Path:
    """Return the absolute job directory for ``hash_id``.

    Validates the id format before joining; the returned path is also
    re-resolved and confirmed to live under ``jobs_root()``. This second
    check is defense-in-depth: even if the regex were ever loosened, a
    crafted id that happened to contain ``..`` would still be caught here.
    """
    validate_hash_id(hash_id)
    base = jobs_root()
    candidate = (base / hash_id).resolve()
    base_resolved = base.resolve() if base.exists() else base
    # ``Path.is_relative_to`` requires Python 3.9+; we target 3.11.
    if not str(candidate).startswith(str(base_resolved)):
        # Should be impossible after validate_hash_id, but be loud if it
        # ever happens.
        raise InvalidJobHashId("resolved path escapes jobs root")
    return candidate


def path_for_output_dir(hash_id: str) -> Path:
    return path_for_job(hash_id) / DIR_OUTPUTS


def path_for_refs_dir(hash_id: str) -> Path:
    return path_for_job(hash_id) / DIR_REFS


def path_for_upstream_dir(hash_id: str) -> Path:
    return path_for_job(hash_id) / DIR_UPSTREAM


def path_for_reference(hash_id: str, order: int) -> Path:
    """Build the absolute path for a reference image.

    The actual filename is a glob — refs/01_*.* — because the original
    filename and extension depend on the upload. Returns the *directory*
    parent + the formatted prefix; callers should glob for ``<prefix>*``
    to find the concrete file.

    For tests and callers that want a deterministic path we use a stable
    naming scheme described in the design doc: ``NN_<sanitised_name>.<ext>``.
    The full path constructor is ``build_reference_path``.
    """
    validate_hash_id(hash_id)
    validate_order(order)
    return path_for_refs_dir(hash_id)


def build_reference_path(
    hash_id: str, order: int, original_filename: str, mime: str
) -> Path:
    """Compute the deterministic on-disk path for a reference image.

    ``original_filename`` may be anything the browser sent — we strip it
    down to a safe basename (alnum / dot / dash / underscore) so we don't
    leak path separators into the filesystem.
    """
    validate_hash_id(hash_id)
    validate_order(order)
    ext = mime_to_ext(mime)
    safe_name = _safe_basename(original_filename)
    return path_for_refs_dir(hash_id) / f"{order:02d}_{safe_name}.{ext}"


def path_for_output_original(hash_id: str, order: int, ext: str) -> Path:
    validate_hash_id(hash_id)
    validate_order(order)
    if ext not in {"png", "jpeg", "webp"}:
        raise UnsupportedMimeType(f"unsupported output extension: {ext!r}")
    return path_for_output_dir(hash_id) / f"{order:02d}_original.{ext}"


def path_for_output_thumb(hash_id: str, order: int) -> Path:
    validate_hash_id(hash_id)
    validate_order(order)
    return path_for_output_dir(hash_id) / f"{order:02d}_thumb.webp"


def path_for_meta_json(hash_id: str) -> Path:
    return path_for_job(hash_id) / META_FILENAME


def path_for_timeline(hash_id: str) -> Path:
    return path_for_job(hash_id) / TIMELINE_FILENAME


def path_for_upstream_log(hash_id: str, attempt: int) -> Path:
    """Path for the per-attempt upstream log under ``upstream/``."""
    validate_hash_id(hash_id)
    if not isinstance(attempt, int) or attempt < 1 or attempt > 99:
        raise InvalidOrder("attempt must be in [1, 99]")
    return path_for_upstream_dir(hash_id) / f"attempt_{attempt}.json"


def _safe_basename(name: str) -> str:
    """Strip a user-supplied filename down to a conservative safe form.

    Keeps alnum / dot / dash / underscore; drops everything else. Empty
    inputs collapse to ``"file"``. The extension we ultimately use is
    the one derived from MIME, not from this string — this is purely for
    legibility on disk.
    """
    if not name:
        return "file"
    # Take only the basename component first to drop any path bits the
    # client may have included.
    raw = os.path.basename(name)
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", raw).strip("._-")
    return cleaned or "file"


# ---------------------------------------------------------------------------
# Filesystem operations
# ---------------------------------------------------------------------------


def ensure_job_dirs(hash_id: str) -> Path:
    """Create ``jobs/<hash_id>/{outputs,refs,upstream}`` and return job dir.

    Idempotent — ``mkdir(exist_ok=True)`` everywhere. Returns the absolute
    job directory so callers can chain further writes.
    """
    job_dir = path_for_job(hash_id)
    for sub in (DIR_OUTPUTS, DIR_REFS, DIR_UPSTREAM):
        (job_dir / sub).mkdir(parents=True, exist_ok=True)
    return job_dir


def save_original(
    hash_id: str, order: int, image_bytes: bytes, mime: str
) -> str:
    """Write a raw output image to disk. Returns the path relative to DATA_ROOT.

    The caller is responsible for generating the matching thumbnail (call
    ``make_thumbnail`` next). We deliberately keep these as two operations
    rather than one because the executor will run thumbnail generation in
    a worker thread via ``asyncio.to_thread``.
    """
    if not isinstance(image_bytes, (bytes, bytearray)):
        raise TypeError("image_bytes must be bytes")
    ext = mime_to_ext(mime)
    ensure_job_dirs(hash_id)
    target = path_for_output_original(hash_id, order, ext)
    target.write_bytes(image_bytes)
    return str(target.relative_to(_data_root()))


def save_reference(
    hash_id: str, order: int, original_filename: str, mime: str, image_bytes: bytes
) -> str:
    """Persist a user-uploaded reference image. Returns the relative path."""
    if not isinstance(image_bytes, (bytes, bytearray)):
        raise TypeError("image_bytes must be bytes")
    ensure_job_dirs(hash_id)
    target = build_reference_path(hash_id, order, original_filename, mime)
    target.write_bytes(image_bytes)
    return str(target.relative_to(_data_root()))


def make_thumbnail(
    src_path: Path | str,
    dst_path: Path | str,
    *,
    max_edge: int | None = None,
    quality: int | None = None,
) -> tuple[int, int, int]:
    """Generate a WEBP thumbnail per design doc §10.2.

    Implementation is deliberately the literal algorithm from the design
    doc — re-deriving it leads to subtle drift::

        if img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGB")
        img.thumbnail((max_edge, max_edge), Image.LANCZOS)
        img.save(dst_path, "WEBP", quality=q, method=6)

    Returns ``(width, height, file_size_bytes)`` so the caller (the job
    executor) can populate the ``images`` row in one shot.
    """
    settings = get_settings()
    edge = max_edge if max_edge is not None else settings.THUMBNAIL_MAX_LONG_EDGE
    q = quality if quality is not None else settings.THUMBNAIL_QUALITY

    src = Path(src_path)
    dst = Path(dst_path)
    dst.parent.mkdir(parents=True, exist_ok=True)

    with Image.open(src) as img:
        # Pillow loads lazily; force a load before close-on-context-exit
        # so the in-memory copy survives the ``with`` block. The literal
        # design doc snippet doesn't use ``with``, but the resource leak
        # warning that produces in tests isn't worth the deviation.
        img.load()
        if img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGB")
        img.thumbnail((edge, edge), Image.Resampling.LANCZOS)
        img.save(dst, "WEBP", quality=q, method=6)
        out_w, out_h = img.size

    size = dst.stat().st_size
    return out_w, out_h, size


# ---------------------------------------------------------------------------
# Metadata files: meta.json + timeline.jsonl + upstream/*.json
# ---------------------------------------------------------------------------


def _utc_now_iso() -> str:
    """Stable ISO-8601 timestamp with explicit UTC (Z) suffix."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def write_meta_json(hash_id: str, payload: dict[str, Any]) -> Path:
    """Atomically replace ``meta.json`` for the job.

    Atomic via write-tmp + rename so a half-flushed file never gets read
    by an admin debug viewer.
    """
    ensure_job_dirs(hash_id)
    target = path_for_meta_json(hash_id)
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    os.replace(tmp, target)
    return target


def append_timeline(hash_id: str, event: dict[str, Any]) -> Path:
    """Append one JSONL event to ``timeline.jsonl``.

    Each line is a single self-contained JSON object so debug viewers can
    tail the file. Adds a ``ts`` field automatically if the caller didn't
    supply one — the executor and lifecycle are the only intended callers
    and they already know the timestamp, but tests and ad-hoc inserts
    benefit from the default.
    """
    ensure_job_dirs(hash_id)
    target = path_for_timeline(hash_id)
    record = {"ts": _utc_now_iso(), **event}
    line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
    with target.open("a", encoding="utf-8") as f:
        f.write(line + "\n")
    return target


def write_upstream_log(
    hash_id: str, attempt: int, payload: dict[str, Any]
) -> Path:
    """Persist one upstream attempt log under ``upstream/attempt_<n>.json``."""
    ensure_job_dirs(hash_id)
    target = path_for_upstream_log(hash_id, attempt)
    target.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    return target


# ---------------------------------------------------------------------------
# Diagnostics — used by the cleanup keeper to compute disk usage.
# ---------------------------------------------------------------------------


def directory_size_bytes(path: Path) -> int:
    """Recursive sum of file sizes under ``path``.

    Uses ``os.scandir`` instead of ``Path.rglob`` because scandir is
    materially faster on dirs with many entries (it returns stat info as
    part of the iteration), and the cache keeper runs this for every job
    directory on each refresh.

    Symlinks: we follow only file-stat (no recursion across symlinked
    dirs) — defensive against an admin who happens to leave a symlink
    pointing into ``/`` while debugging.
    """
    if not path.exists():
        return 0
    total = 0
    stack: list[Path] = [path]
    while stack:
        d = stack.pop()
        try:
            with os.scandir(d) as it:
                for entry in it:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(Path(entry.path))
                        elif entry.is_file(follow_symlinks=False):
                            total += entry.stat(follow_symlinks=False).st_size
                    except OSError:
                        # Race with concurrent cleanup — ignore the entry.
                        continue
        except FileNotFoundError:
            continue
    return total
