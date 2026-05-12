"""Job creation, precheck, cancel, delete — PR-13.

Routes implemented (design doc §6.3 / §6.4 / §16.2):

- ``POST /api/jobs/precheck`` — tells the frontend whether to render
  Turnstile before opening the multipart form. Cheap, JSON-only.
- ``POST /api/jobs`` — multipart endpoint that admits a generation job
  into the queue. Body is ``payload`` (JSON-encoded :class:`JobCreatePayload`)
  plus zero or more ``ref_<order>`` files (design doc §6.5).
- ``POST /api/jobs/<hash>/cancel`` — user-side cancel; only QUEUED or
  RUNNING jobs are cancellable.
- ``DELETE /api/jobs/<hash>`` — soft-delete a terminal job (design doc
  §8.1 / §8.2). Files are kept on disk; the cache keeper handles the
  physical sweep.

Cross-cutting wiring this module owns:

- :mod:`app.domain.access_policy` evaluation, then attach soft-quota /
  captcha flags to ``flags_json`` so the executor can pick them up.
- ``client_request_id`` idempotency window: same user + same id within
  five minutes returns the prior response without re-enqueueing.
- Multipart reference uploads, ordered by the trailing ``_<N>`` field
  name suffix and persisted to ``data/jobs/<hash>/refs/`` (design doc
  §6.5).
- Lifecycle ``QUEUED`` insert + ``CANCELLED`` / ``DELETED`` transitions
  through :class:`app.domain.job_lifecycle.JobLifecycle`.
- Best-effort SSE broadcast (``task_created`` / ``task_deleted``) via
  the configured hub.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from io import BytesIO
from typing import Any

from fastapi import APIRouter, Form, Request
from starlette.datastructures import UploadFile
from pydantic import ValidationError
from sqlalchemy import func, select, update

from app.db.engine import get_session
from app.db.jobs_repository import (
    JobsRepositoryError,
    get_jobs_repository,
    serialise_params,
)
from app.db.models import Batch, Image, Job, Session as SessionRow, SessionJob
from app.deps import CurrentUser
from app.domain.access_policy import AccessDecision, get_access_policy
from app.domain.job_lifecycle import (
    CANCELLED,
    DELETED,
    FAILED,
    InvalidTransition,
    JobNotFound,
    QUEUED,
    RUNNING,
    get_job_lifecycle,
)
from app.domain.job_queue import QueuedJob, get_job_queue
from app.domain.model_catalog import effective_capabilities_for_user
from app.domain.job_validator import validate_against_capabilities
from app.domain.quota_guard import get_quota_guard
from app.domain.runtime_configs import EmergencyConfig
from app.domain.sse_hub import get_sse_hub
from app.schemas.jobs import (
    JobActionResponse,
    JobCreatePayload,
    JobCreateResponse,
    PrecheckRequest,
    PrecheckResponse,
)
from app.services import image_io, turnstile
from app.utils.audit import write_audit  # noqa: F401  -- reserved for cancel audit
from app.utils.errors import api_error
from app.utils.ids import new_set_id

logger = logging.getLogger("txt2img.jobs")

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Wallclock window during which an idempotent re-submit returns the
# previous job instead of enqueueing a fresh one. Five minutes is long
# enough for a network blip + retry, short enough that a user who is
# *trying* to resubmit a near-identical prompt doesn't get silently
# blocked.
_CLIENT_REQUEST_DEDUP_SECONDS = 5 * 60

# Multipart field name → reference order. Matches the contract in design
# doc §6.5: the frontend POSTs ``ref_0``, ``ref_1``, ...
_REF_FIELD_RE = re.compile(r"^ref_(\d+)$")

# Per-reference upload size limit in bytes. 25 MB matches what gpt-image-2
# can practically accept for edits; gemini's inline_data is also fine
# under this. We reject larger uploads up front rather than letting them
# tie up worker memory.
_REF_MAX_BYTES = 25 * 1024 * 1024

# Mask uploads can be larger than refs because they're alpha-channel PNGs
# that compress less effectively at high resolutions; cap at 50 MB.
_MASK_MAX_BYTES = 50 * 1024 * 1024

# Allowed reference MIME types — Pillow can read all of these and the
# adapters' wire formats accept them.
_ALLOWED_REF_MIMES = frozenset({"image/png", "image/jpeg", "image/jpg", "image/webp"})

# Mask must be a PNG with an alpha channel.
_ALLOWED_MASK_MIMES = frozenset({"image/png"})


@dataclass
class JobAttachments:
    """Container for the uploaded files attached to a ``POST /api/jobs``."""

    references: list[dict[str, Any]] = field(default_factory=list)
    mask: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# /api/jobs/precheck
# ---------------------------------------------------------------------------


@router.post("/precheck", response_model=PrecheckResponse)
async def precheck(body: PrecheckRequest, user: CurrentUser) -> PrecheckResponse:
    """Return whether the next ``POST /api/jobs`` will need a captcha.

    Decision (design doc §6.3):

    1. Force-on via emergency switch ``emergency.force_captcha_global``.
    2. User is over the soft quota for today.
    3. User has burst-submitted: ≥ 5 jobs in the last 60 seconds.
    """
    settings = _settings()
    reason: str | None = None

    if EmergencyConfig().force_captcha_global:
        reason = "force_captcha_global"
    elif await get_quota_guard().check_soft_quota_exceeded(user):
        reason = "soft_quota_exceeded"
    elif await _recent_burst(user.id):
        reason = "rate_limit_pre_warn"
    elif settings.FORCE_CAPTCHA:
        reason = "force_captcha"

    if reason is None:
        return PrecheckResponse(captcha_required=False)

    return PrecheckResponse(
        captcha_required=True,
        captcha_provider="turnstile",
        site_key=settings.TURNSTILE_SITE_KEY or None,
        reason=reason,
    )


async def _recent_burst(user_id: str) -> bool:
    """True if the user submitted ≥ 5 jobs in the last 60 seconds.

    Counted against the durable ``jobs`` table — the in-memory queue
    might already have shed dispatched jobs, but the rows are still
    fresh in the DB. Uses ``created_at`` because that's the canonical
    submission time.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=60)
    async with get_session() as session:
        rows = (
            await session.execute(
                select(Job.id)
                .where(Job.user_id == user_id, Job.created_at >= cutoff)
                .limit(5)
            )
        ).all()
    return len(rows) >= 5


# ---------------------------------------------------------------------------
# /api/jobs (create)
# ---------------------------------------------------------------------------


@router.post("", response_model=JobCreateResponse)
async def create_job(
    request: Request,
    user: CurrentUser,
    payload: str = Form(...),
) -> JobCreateResponse:
    """Admit a generation job into the queue.

    Multipart contract (design doc §6.4):

    - ``payload`` (Form) — JSON-encoded :class:`JobCreatePayload`.
    - ``ref_<N>`` (UploadFile) — zero or more reference images,
      ordered by the integer suffix.
    """
    body = _parse_payload(payload)

    # Idempotency: same user + same client_request_id within the
    # dedup window returns the prior response unchanged. Done up
    # front so we don't burn admission budget on a duplicate.
    if body.client_request_id:
        existing = await _find_recent_by_client_id(
            user.id, body.client_request_id
        )
        if existing is not None:
            return existing

    # Walk the entire multipart up front so we have refs in hand for
    # both the validator (counting) and the storage step. We reject
    # if a ref comes in that we can't classify.
    attachments = await _collect_attachments(request)
    refs = attachments.references

    # Derivation: if parent_hash_id is set, validate it before we burn
    # quota on a request that points at a missing / foreign parent.
    parent_job: Job | None = None
    if body.parent_hash_id is not None:
        parent_job = await _validate_parent_for_derivation(body, user.id)
        # Outpaint synthesises canvas + mask from the parent image, so a
        # caller-supplied mask is forbidden in that mode.
        if (
            body.derivation_kind
            and body.derivation_kind.value == "outpaint"
            and attachments.mask is not None
        ):
            raise api_error(
                422,
                "INVALID_PARAMETER",
                "outpaint mode synthesises its own mask; do not upload one.",
                field="mask",
            )

    # Mask + first-reference dimension parity (mask_edit only — outpaint
    # builds its own pair after this point).
    if (
        body.derivation_kind is None
        or body.derivation_kind.value != "outpaint"
    ):
        _validate_mask_consistency(attachments)
    else:
        # Outpaint mode: synthesise the extended canvas + matching mask
        # *here*, before the executor / adapter can see the request. The
        # frontend uploads the original source as ``ref_0`` and provides
        # the (directions, amount) geometry on the payload. We replace
        # ``ref_0`` with a (potentially) wider canvas and inject a
        # synthesised mask so the request takes the same /v1/images/edits
        # code path that mask_edit takes downstream.
        #
        # Without this step, the request reached upstream as a plain
        # i2i regeneration with no mask — see Bug #5 in the design QA
        # report: the executor never read ``flags['outpaint']`` and
        # ``services/outpaint.py`` was effectively dead code.
        if not attachments.references:
            raise api_error(
                422,
                "MASK_REQUIRES_REFERENCE",
                "outpaint requires the source image as ref_0",
                field="references",
            )
        try:
            from app.services.outpaint import synthesize_outpaint
            canvas_bytes, mask_bytes, _new_size = await asyncio.to_thread(
                synthesize_outpaint,
                source_png_bytes=attachments.references[0]["bytes"],
                directions=[d.value for d in (body.outpaint_directions or [])],
                amount=body.outpaint_amount,
            )
        except ValueError as exc:
            raise api_error(
                422,
                "INVALID_PARAMETER",
                f"outpaint synthesis failed: {exc}",
                field="outpaint_amount",
            ) from exc
        # Substitute ref_0 with the extended canvas; preserve metadata.
        attachments.references[0] = {
            **attachments.references[0],
            "bytes": canvas_bytes,
            "mime": "image/png",
        }
        # Synthesise a mask attachment so the executor's NormalizedRequest
        # reconstitutes a real edits call (alpha=255 for the original
        # area, alpha=0 for the extended area).
        new_w, new_h = _new_size
        attachments.mask = {
            "filename": "outpaint_mask.png",
            "mime": "image/png",
            "bytes": mask_bytes,
            "width": new_w,
            "height": new_h,
        }

    decision = await get_access_policy().enforce(user, model=body.model)

    caps = await effective_capabilities_for_user(user, body.model)
    failure = validate_against_capabilities(
        body, caps, reference_count=len(refs)
    )
    if failure is not None:
        raise api_error(
            422, "INVALID_PARAMETER", failure.message, field=failure.field
        )

    captcha_verified = await _verify_captcha_if_needed(
        user=user,
        body=body,
        decision=decision,
        request=request,
    )

    # Quota: increment the user's daily counter. Done before insert so
    # a job-create that fails halfway still counts (we refund on the
    # NO_PROVIDER_AVAILABLE / system-side paths only).
    await get_quota_guard().record_usage(user)

    # ``set_id`` allocation policy:
    # - Frontend (batch flow) may pre-allocate a ``set_<10>`` and pass
    #   it down so multiple sibling jobs share it. We accept that
    #   verbatim if the format checks out (regex via the schema).
    # - Otherwise, the legacy rule kicks in: assign a new id when n > 1,
    #   leave NULL for single-image jobs.
    if body.set_id is not None:
        set_id = body.set_id
    else:
        set_id = new_set_id() if body.n > 1 else None

    flags = _build_flags(decision, captcha_verified=captcha_verified)
    params = body.to_normalized_dict()
    # Strip ``model``/``prompt`` from params — they're top-level columns.
    params.pop("model", None)
    params.pop("prompt", None)
    # Stash the prompt inside params so the executor can rebuild a
    # ``NormalizedRequest``. The job row also carries ``model`` /
    # ``prompt`` separately for direct DB queries.
    params["prompt"] = body.prompt
    params["model"] = body.model

    # If the caller uploaded a mask, fold it into params so the
    # executor's NormalizedRequest can reconstitute it. The mask is
    # base64-encoded inline; size is bounded by _MASK_MAX_BYTES (50 MB).
    if attachments.mask is not None:
        params["mask"] = {
            "order": 1,
            "mime": attachments.mask["mime"],
            "data_b64": base64.b64encode(
                attachments.mask["bytes"]
            ).decode("ascii"),
            "filename": attachments.mask["filename"],
        }

    # Outpaint geometry travels in flags so the executor (and the
    # outpaint synthesizer) can read it without re-parsing params.
    if (
        body.derivation_kind
        and body.derivation_kind.value == "outpaint"
    ):
        flags["outpaint"] = {
            "directions": [d.value for d in (body.outpaint_directions or [])],
            "amount": body.outpaint_amount,
        }

    repo = get_jobs_repository()

    if body.session_id is not None:
        await _verify_session_owned_by_user(body.session_id, user.id)

    try:
        async with get_session() as session:
            if body.batch_id is not None:
                await _bind_to_batch(body.batch_id, user.id, session)
            created = await repo.insert_queued(
                user_id=user.id,
                tier_at_submit=user.tier,
                model=body.model,
                params_json=serialise_params(params),
                flags_json=json.dumps(flags, separators=(",", ":")),
                client_request_id=body.client_request_id,
                set_id=set_id,
                session_id=body.session_id,
                parent_hash_id=body.parent_hash_id,
                derivation_kind=(
                    body.derivation_kind.value
                    if body.derivation_kind is not None
                    else None
                ),
                parent_order=body.parent_order,
                batch_id=body.batch_id,
                session=session,
            )
            # Persist the prompt on the row itself (params already has
            # it; we keep the column ``prompt`` as a future-friendly
            # spot for full-text indexes — but the schema currently
            # only has ``params_json``, so we don't write it here).
            if body.session_id is not None:
                session.add(
                    SessionJob(session_id=body.session_id, job_id=created.job_id)
                )
            # Save references inside the same transaction so a failed
            # write doesn't leave orphan files on disk vs. a missing
            # join row, and vice versa.
            await _persist_references(
                session, job_id=created.job_id, hash_id=created.hash_id, refs=refs
            )

            mask_meta: dict[str, Any] | None = None
            if attachments.mask is not None:
                mask_rel_path = await asyncio.to_thread(
                    _save_mask_to_disk,
                    created.hash_id,
                    attachments.mask["filename"],
                    attachments.mask["mime"],
                    attachments.mask["bytes"],
                )
                mask_meta = {
                    "filename": attachments.mask["filename"],
                    "mime": attachments.mask["mime"],
                    "rel_path": mask_rel_path,
                    "width": attachments.mask["width"],
                    "height": attachments.mask["height"],
                }

            # Persist a lightweight meta.json snapshot for debug viewers.
            try:
                await asyncio.to_thread(
                    image_io.write_meta_json,
                    created.hash_id,
                    {
                        "user_id": user.id,
                        "model": body.model,
                        "prompt": body.prompt,
                        "params": params,
                        "flags": flags,
                        "set_id": set_id,
                        "session_id": body.session_id,
                        "n": body.n,
                        "references": [
                            {
                                "order": r["order"],
                                "filename": r["filename"],
                                "mime": r["mime"],
                                "rel_path": r["rel_path"],
                            }
                            for r in refs
                        ],
                        "mask": mask_meta,
                        "parent_hash_id": body.parent_hash_id,
                        "derivation_kind": (
                            body.derivation_kind.value
                            if body.derivation_kind is not None
                            else None
                        ),
                        "created_at": created.created_at.isoformat(
                            timespec="seconds"
                        ).replace("+00:00", "Z"),
                    },
                )
            except OSError:
                logger.warning(
                    "create_job: failed to write meta.json for %s",
                    created.hash_id,
                    exc_info=True,
                )

    except JobsRepositoryError as exc:
        # User vanished mid-flight (deleted concurrently). Surface as
        # 401 so the frontend bounces to /login per the global
        # interceptor. Refund quota so the counter doesn't leak.
        await get_quota_guard().refund_usage(user)
        raise api_error(401, "UNAUTHORIZED", "Account not found.") from exc

    queue = get_job_queue()
    queued_job = QueuedJob(
        hash_id=created.hash_id,
        job_id=created.job_id,
        user_id=user.id,
        tier=user.tier,
        model=body.model,
        queued_at=_aware_utc(created.created_at),
        seq_no=created.seq_no,
        set_id=set_id,
        flags=dict(flags),
    )
    await queue.enqueue(queued_job)

    position = await queue.position(created.hash_id)
    eta = _estimate_wait_seconds(position)

    response = JobCreateResponse(
        hash_id=created.hash_id,
        seq_no=created.seq_no,
        model=body.model,
        status=QUEUED,
        position=position,
        estimated_wait_seconds=eta,
        queued_at=_aware_utc(created.created_at),
        set_id=set_id,
        client_request_id=body.client_request_id,
        parent_hash_id=body.parent_hash_id,
        parent_order=body.parent_order,
        derivation_kind=(
            body.derivation_kind.value
            if body.derivation_kind is not None
            else None
        ),
        batch_id=body.batch_id,
    )

    # Best-effort: tell every other tab the user has open.
    asyncio.create_task(_broadcast_task_created(user.id, response))
    if body.batch_id is not None:
        asyncio.create_task(_emit_batch_progress_after_bind(body.batch_id))

    return response


# ---------------------------------------------------------------------------
# /api/jobs/<hash>/cancel
# ---------------------------------------------------------------------------


@router.post("/{hash_id}/cancel", response_model=JobActionResponse)
async def cancel_job(hash_id: str, user: CurrentUser) -> JobActionResponse:
    """Cancel a job that hasn't reached a terminal state.

    Behaviour (design doc §16.2):

    - QUEUED jobs are removed from the in-memory queue *and* the row
      is moved to ``CANCELLED``. Quota is refunded — the upstream
      never ran.
    - RUNNING jobs are flipped to ``CANCELLED``; the executor checks
      ``status`` at safe points and bails when it sees the change.
      Quota is *not* refunded for in-flight cancels because we may
      have already paid an upstream attempt.
    - Terminal jobs (SUCCEEDED / FAILED / CANCELLED / DELETED)
      return 409 ``CONFLICT``.
    """
    job_row = await _load_owned_job(hash_id, user.id)
    current_status = job_row.status

    if current_status not in (QUEUED, RUNNING):
        raise api_error(
            409,
            "CONFLICT",
            f"Job in status {current_status} cannot be cancelled.",
        )

    # Pull from the in-memory queue first so the scheduler doesn't
    # immediately try to dispatch what we're about to cancel.
    queue = get_job_queue()
    if current_status == QUEUED:
        await queue.remove(hash_id)

    try:
        await get_job_lifecycle().transition(
            hash_id, CANCELLED, reason="USER_CANCELLED"
        )
    except InvalidTransition:
        # The job moved between our SELECT and our transition — likely
        # SUCCEEDED/FAILED in the same tick. Surface as 409 so the
        # frontend can refresh.
        raise api_error(
            409,
            "CONFLICT",
            "Job state changed; refresh and try again.",
        )
    except JobNotFound:
        raise api_error(404, "NOT_FOUND", "Job not found.")

    if current_status == QUEUED:
        # Only refund for queue-time cancels.
        await get_quota_guard().refund_usage(user)

    return JobActionResponse(hash_id=hash_id, status=CANCELLED)


# ---------------------------------------------------------------------------
# DELETE /api/jobs/<hash>
# ---------------------------------------------------------------------------


@router.delete("/{hash_id}", response_model=JobActionResponse)
async def delete_job(hash_id: str, user: CurrentUser) -> JobActionResponse:
    """Soft-delete a terminal job.

    Allowed only from a terminal state (SUCCEEDED / FAILED /
    CANCELLED). The on-disk directory is left in place — the cache
    keeper sweep eventually removes it. Output rows in ``images``
    survive too; archive synchronisation tracks the deletion via the
    ``DELETED`` lifecycle event.
    """
    job_row = await _load_owned_job(hash_id, user.id)

    if job_row.status not in ("SUCCEEDED", FAILED, CANCELLED):
        raise api_error(
            409,
            "CONFLICT",
            f"Job in status {job_row.status} cannot be deleted yet.",
        )

    try:
        await get_job_lifecycle().transition(
            hash_id, DELETED, reason="USER_DELETED"
        )
    except InvalidTransition:
        raise api_error(
            409, "CONFLICT", "Job state changed; refresh and try again."
        )
    except JobNotFound:
        raise api_error(404, "NOT_FOUND", "Job not found.")

    # Belt-and-suspenders broadcast — lifecycle's own job_state event
    # carries the DELETED transition, but the design doc §9.5 enumerates
    # ``task_deleted`` separately so the frontend reducer can be tiny.
    asyncio.create_task(_broadcast_task_deleted(user.id, hash_id))

    return JobActionResponse(hash_id=hash_id, status=DELETED)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _settings():
    """Lazy import to keep the test fixture's settings cache honest."""
    from app.config import get_settings

    return get_settings()


def _parse_payload(raw: str) -> JobCreatePayload:
    try:
        data = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise api_error(
            400,
            "BAD_REQUEST",
            "payload must be a JSON object encoded in the `payload` form field.",
            field="payload",
        ) from exc
    if not isinstance(data, dict):
        raise api_error(
            400,
            "BAD_REQUEST",
            "payload must decode to a JSON object.",
            field="payload",
        )
    try:
        return JobCreatePayload.model_validate(data)
    except ValidationError as exc:
        first = exc.errors()[0] if exc.errors() else {}
        loc = first.get("loc") or ()
        field = ".".join(str(p) for p in loc) or None
        message = first.get("msg") or "Invalid request payload."
        raise api_error(422, "INVALID_PARAMETER", message, field=field) from exc


async def _collect_references(request: Request) -> list[dict[str, Any]]:
    """Backwards-compatible wrapper for legacy callers.

    Returns just the references list. New code should use
    :func:`_collect_attachments` to also pick up the ``mask`` field.
    """
    attachments = await _collect_attachments(request)
    return attachments.references


async def _collect_attachments(request: Request) -> JobAttachments:
    """Walk multipart fields into a :class:`JobAttachments` container.

    Recognised file fields:

    - ``ref_<int>`` — reference image; collected into ``.references``.
    - ``mask`` — single PNG with alpha; collected into ``.mask``.

    Unknown file fields are ignored quietly to keep the wire contract
    forward-compatible. Each upload is buffered into memory; size limits
    apply per field.
    """
    form = await request.form()
    out_by_order: dict[int, dict[str, Any]] = {}
    out_mask: dict[str, Any] | None = None
    for field_name, value in form.multi_items():
        if not isinstance(value, UploadFile):
            continue
        if field_name == "mask":
            if out_mask is not None:
                raise api_error(
                    422,
                    "INVALID_PARAMETER",
                    "duplicate mask field",
                    field="mask",
                )
            out_mask = await _read_mask_upload(value)
            continue
        match = _REF_FIELD_RE.match(field_name)
        if not match:
            # Unknown file field — ignore quietly. Strict rejection here
            # would prevent forward-compatible additions to the
            # multipart contract.
            continue
        order_idx = int(match.group(1))
        # Frontend sends 0-indexed names but we store 1-indexed orders so
        # they line up with the on-disk ``01_*.ext`` filenames.
        order = order_idx + 1
        if order in out_by_order:
            raise api_error(
                422,
                "INVALID_PARAMETER",
                f"duplicate reference index ref_{order_idx}",
                field="references",
            )
        mime = (value.content_type or "").lower()
        if mime not in _ALLOWED_REF_MIMES:
            raise api_error(
                422,
                "INVALID_PARAMETER",
                f"reference ref_{order_idx} has unsupported MIME {mime!r}; "
                "must be png, jpeg, or webp.",
                field="references",
            )
        data = await value.read()
        if len(data) > _REF_MAX_BYTES:
            raise api_error(
                413,
                "INVALID_PARAMETER",
                f"reference ref_{order_idx} exceeds {_REF_MAX_BYTES // (1024 * 1024)}MB",
                field="references",
            )
        if not data:
            raise api_error(
                422,
                "INVALID_PARAMETER",
                f"reference ref_{order_idx} is empty.",
                field="references",
            )
        out_by_order[order] = {
            "order": order,
            "filename": value.filename or f"ref_{order:02d}",
            "mime": mime,
            "bytes": data,
        }
    refs = [out_by_order[k] for k in sorted(out_by_order.keys())]
    # Refuse a sparse upload — gaps in the order would silently shift
    # references on the wire. Frontend should send 0..N-1 contiguously.
    for idx, ref in enumerate(refs, start=1):
        if ref["order"] != idx:
            raise api_error(
                422,
                "INVALID_PARAMETER",
                "reference indices must be a contiguous 0..N-1 sequence.",
                field="references",
            )
    return JobAttachments(references=refs, mask=out_mask)


async def _read_mask_upload(value: UploadFile) -> dict[str, Any]:
    """Validate + buffer the ``mask`` upload field.

    Mask must be PNG with alpha channel. We probe via Pillow on the
    thread pool; if Pillow can't open it or the mode lacks alpha we
    return ``INVALID_MASK_FORMAT`` so the frontend can show a precise
    error.
    """
    mime = (value.content_type or "").lower()
    if mime not in _ALLOWED_MASK_MIMES:
        raise api_error(
            422,
            "INVALID_MASK_FORMAT",
            f"mask must be PNG, got {mime!r}",
            field="mask",
        )
    data = await value.read()
    if not data:
        raise api_error(
            422,
            "INVALID_MASK_FORMAT",
            "mask file is empty",
            field="mask",
        )
    if len(data) > _MASK_MAX_BYTES:
        raise api_error(
            413,
            "INVALID_MASK_FORMAT",
            f"mask exceeds {_MASK_MAX_BYTES // (1024 * 1024)}MB",
            field="mask",
        )
    width, height = await asyncio.to_thread(_validate_mask_pixels, data)
    return {
        "filename": value.filename or "mask.png",
        "mime": mime,
        "bytes": data,
        "width": width,
        "height": height,
    }


def _validate_mask_pixels(data: bytes) -> tuple[int, int]:
    """Open the mask via Pillow, ensure it has an alpha channel.

    Returns ``(width, height)``. Raises ``INVALID_MASK_FORMAT`` on a
    corrupt PNG or a mode without alpha.
    """
    try:
        from PIL import Image
    except ImportError as exc:  # pragma: no cover — Pillow is required.
        raise api_error(
            500,
            "INTERNAL_ERROR",
            "Pillow not available for mask validation.",
        ) from exc
    try:
        with Image.open(BytesIO(data)) as probe:
            probe.verify()
    except Exception as exc:
        raise api_error(
            422,
            "INVALID_MASK_FORMAT",
            "mask is not a valid PNG",
            field="mask",
        ) from exc
    # Pillow requires re-open after verify().
    img = Image.open(BytesIO(data))
    if img.mode not in ("RGBA", "LA"):
        raise api_error(
            422,
            "INVALID_MASK_FORMAT",
            f"mask must have alpha channel, got mode {img.mode!r}",
            field="mask",
        )
    return img.size


def _peek_image_size(data: bytes) -> tuple[int, int]:
    try:
        from PIL import Image
    except ImportError as exc:  # pragma: no cover.
        raise api_error(
            500, "INTERNAL_ERROR", "Pillow not available."
        ) from exc
    try:
        img = Image.open(BytesIO(data))
        return img.size
    except Exception as exc:
        raise api_error(
            422,
            "INVALID_PARAMETER",
            "reference image is not decodable",
            field="references",
        ) from exc


def _validate_mask_consistency(attachments: JobAttachments) -> None:
    """Ensure mask + first reference agree on dimensions.

    OpenAI's images.edits requires the mask and the first image to be
    the same WxH. We check this once at the boundary so a bad pair
    never reaches the executor.
    """
    if attachments.mask is None:
        return
    if not attachments.references:
        raise api_error(
            422,
            "MASK_REQUIRES_REFERENCE",
            "mask requires at least one reference image",
            field="mask",
        )
    first_ref = attachments.references[0]
    ref_size = _peek_image_size(first_ref["bytes"])
    if (attachments.mask["width"], attachments.mask["height"]) != ref_size:
        raise api_error(
            422,
            "INVALID_MASK_DIMS",
            f"mask {attachments.mask['width']}x{attachments.mask['height']} "
            f"must match first reference {ref_size[0]}x{ref_size[1]}",
            field="mask",
        )


def _save_mask_to_disk(
    hash_id: str, filename: str, mime: str, data: bytes
) -> str:
    """Persist the mask PNG under ``data/jobs/<hash>/mask.png``.

    Returns the rel-path (relative to the data root) so meta.json can
    reference it. The mask isn't tracked in a DB table — it lives
    entirely in ``params_json.mask`` (base64) for the executor and on
    disk for debug/replay.
    """
    image_io.ensure_job_dirs(hash_id)
    target = image_io.path_for_job(hash_id) / "mask.png"
    target.write_bytes(data)
    return str(target.relative_to(image_io._data_root()))


async def _validate_parent_for_derivation(
    body: JobCreatePayload, user_id: str
) -> Job:
    """Check the parent_hash_id points at a SUCCEEDED job owned by user.

    Also enforces the same ``model`` so a user can't accidentally cross
    a mask edit between two providers.
    """
    parent_hash_id = body.parent_hash_id
    assert parent_hash_id is not None  # caller guard
    async with get_session() as session:
        row = (
            await session.execute(
                select(Job).where(Job.hash_id == parent_hash_id)
            )
        ).scalar_one_or_none()
    if row is None:
        raise api_error(
            404,
            "PARENT_JOB_NOT_FOUND",
            f"parent job {parent_hash_id} not found",
            field="parent_hash_id",
        )
    if row.user_id != user_id:
        raise api_error(
            403,
            "PARENT_JOB_NOT_OWNED",
            "parent job belongs to another user",
            field="parent_hash_id",
        )
    if row.status != "SUCCEEDED":
        raise api_error(
            409,
            "PARENT_JOB_NOT_TERMINAL",
            f"parent job status={row.status} cannot be derived from",
            field="parent_hash_id",
        )
    if body.model != row.model:
        raise api_error(
            422,
            "INVALID_PARAMETER",
            f"derivation must use same model as parent ({row.model})",
            field="model",
        )
    # Bounds-check parent_order against the parent's image count so a
    # bogus order can't reference a non-existent image.
    if body.parent_order is not None:
        async with get_session() as session:
            count = (
                await session.execute(
                    select(func.count(Image.id)).where(Image.job_id == row.id)
                )
            ).scalar_one()
        if int(count or 0) < int(body.parent_order):
            raise api_error(
                422,
                "INVALID_PARAMETER",
                f"parent_order={body.parent_order} exceeds parent image count {int(count or 0)}",
                field="parent_order",
            )
    return row


async def _persist_references(
    session,
    *,
    job_id: str,
    hash_id: str,
    refs: list[dict[str, Any]],
) -> None:
    """Save reference uploads to disk + insert ``job_references`` rows."""
    if not refs:
        return
    from app.db.models import JobReference

    for ref in refs:
        rel_path = await asyncio.to_thread(
            image_io.save_reference,
            hash_id,
            ref["order"],
            ref["filename"],
            ref["mime"],
            ref["bytes"],
        )
        ref["rel_path"] = rel_path
        session.add(
            JobReference(
                job_id=job_id,
                ref_order=ref["order"],
                filename=ref["filename"],
                mime=ref["mime"],
                rel_path=rel_path,
            )
        )


async def _verify_captcha_if_needed(
    *,
    user,
    body: JobCreatePayload,
    decision: AccessDecision,
    request: Request,
) -> bool:
    """Validate the captcha token if one was required for this job.

    Returns True when captcha verification succeeded, False when no
    captcha was needed. Raises 412 ``CAPTCHA_REQUIRED`` /
    ``CAPTCHA_INVALID`` on failure.
    """
    settings = _settings()
    require = (
        decision.soft_quota_exceeded
        or settings.FORCE_CAPTCHA
        or EmergencyConfig().force_captcha_global
        or await _recent_burst(user.id)
    )
    if not require:
        return False

    if not body.captcha_token:
        raise api_error(
            412,
            "CAPTCHA_REQUIRED",
            "Captcha verification is required for this submission.",
        )
    ip = _client_ip(request)
    ok = await turnstile.verify(body.captcha_token, remote_ip=ip)
    if not ok:
        raise api_error(412, "CAPTCHA_INVALID", "Captcha verification failed.")
    return True


def _build_flags(
    decision: AccessDecision, *, captcha_verified: bool
) -> dict[str, Any]:
    """Assemble the ``flags_json`` value to persist on the new job row."""
    flags: dict[str, Any] = {}
    if decision.soft_quota_exceeded:
        flags["SOFT_QUOTA_EXCEEDED"] = True
    if captcha_verified:
        flags["captcha_verified"] = True
    return flags


def _client_ip(request: Request) -> str | None:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else None


def _estimate_wait_seconds(position: int | None) -> int | None:
    """Coarse ETA = position × per-job baseline.

    The design doc §8.4 calls for ``position / free_workers × avg_render``.
    We don't have a metrics-engine surface for "average render seconds
    per model" yet; the executor only records call latency at the
    provider level. Use a flat per-position estimate for now — the
    frontend treats this as advisory anyway.
    """
    if position is None:
        return None
    # ~12s per slot ahead. Picked empirically; recalibrate when the
    # metrics engine surfaces a per-model rolling average.
    return max(0, int(position * 12))


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


async def _find_recent_by_client_id(
    user_id: str, client_request_id: str
) -> JobCreateResponse | None:
    """Look up an existing job by ``client_request_id`` within the dedup window.

    We restrict to the last ``_CLIENT_REQUEST_DEDUP_SECONDS`` seconds so
    a long-lived id reuse (someone scripting a daily run with a fixed
    id) doesn't silently silence forever. Returns the same shape as the
    create-time success path.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(
        seconds=_CLIENT_REQUEST_DEDUP_SECONDS
    )
    async with get_session() as session:
        row = (
            await session.execute(
                select(Job)
                .where(
                    Job.user_id == user_id,
                    Job.client_request_id == client_request_id,
                    Job.created_at >= cutoff,
                )
                .order_by(Job.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
    if row is None:
        return None
    queue = get_job_queue()
    position = await queue.position(row.hash_id)
    return JobCreateResponse(
        hash_id=row.hash_id,
        seq_no=row.seq_no,
        model=row.model,
        status=row.status,
        position=position,
        estimated_wait_seconds=_estimate_wait_seconds(position),
        queued_at=_aware_utc(row.queued_at or row.created_at),
        set_id=row.set_id,
        client_request_id=row.client_request_id,
    )


async def _verify_session_owned_by_user(session_id: str, user_id: str) -> None:
    """Reject foreign / missing session ids with 404 (not 403)."""
    async with get_session() as session:
        row = (
            await session.execute(
                select(SessionRow.id).where(
                    SessionRow.id == session_id, SessionRow.user_id == user_id
                )
            )
        ).scalar_one_or_none()
    if row is None:
        raise api_error(
            404, "NOT_FOUND", "Session not found.", field="session_id"
        )


async def _load_owned_job(hash_id: str, user_id: str) -> Job:
    """Fetch a job row owned by ``user_id`` or raise 404.

    We don't differentiate "not yours" from "doesn't exist" — same
    privacy rationale as :func:`app.api.sessions.delete_session`.
    """
    async with get_session() as session:
        row = (
            await session.execute(
                select(Job).where(
                    Job.hash_id == hash_id, Job.user_id == user_id
                )
            )
        ).scalar_one_or_none()
    if row is None:
        raise api_error(404, "NOT_FOUND", "Job not found.", field="hash_id")
    return row


async def _broadcast_task_created(
    user_id: str, response: JobCreateResponse
) -> None:
    payload = {
        "hash_id": response.hash_id,
        "seq_no": response.seq_no,
        "status": response.status,
        "model": response.model,
        "set_id": response.set_id,
        "position": response.position,
        "estimated_wait_seconds": response.estimated_wait_seconds,
        "created_at": _isoformat(response.queued_at),
        "client_request_id": response.client_request_id,
    }
    try:
        await get_sse_hub().broadcast_to_user(user_id, "task_created", payload)
    except Exception:  # pragma: no cover — broadcast is best-effort
        logger.exception("task_created broadcast failed for %s", response.hash_id)


async def _broadcast_task_deleted(user_id: str, hash_id: str) -> None:
    try:
        await get_sse_hub().broadcast_to_user(
            user_id, "task_deleted", {"hash_id": hash_id}
        )
    except Exception:  # pragma: no cover
        logger.exception("task_deleted broadcast failed for %s", hash_id)


def _isoformat(value: datetime) -> str:
    aware = _aware_utc(value)
    return aware.isoformat(timespec="seconds").replace("+00:00", "Z")


async def _bind_to_batch(
    batch_id: str, user_id: str, session
) -> None:
    """Validate + atomically claim a slot in the parent batch.

    Errors:

    - 422 ``INVALID_PARAMETER`` (batch_id) — wrong shape, missing,
      foreign-owned (we do not differentiate to avoid leaking
      existence).
    - 409 ``BATCH_INVALID_STATE`` — batch is not in ``submitting``.
    - 422 ``BATCH_FULL`` — already at ``total_job_count``.

    Implementation note: under burst load (e.g. four sibling jobs from
    the same set fanning out concurrently), an ORM-style read-modify-
    write would race because SQLite silently drops ``with_for_update``.
    The slot claim is therefore expressed as a single conditional
    ``UPDATE … SET submitted_count = submitted_count + 1
       WHERE id = :id AND user_id = :uid AND status = 'submitting'
       AND submitted_count < total_job_count``.
    The rowcount tells us whether we won the slot; if not we re-read to
    decide which precise error to surface.
    """
    now = datetime.now(timezone.utc)
    result = await session.execute(
        update(Batch)
        .where(
            Batch.id == batch_id,
            Batch.user_id == user_id,
            Batch.status == "submitting",
            Batch.submitted_count < Batch.total_job_count,
        )
        .values(
            submitted_count=Batch.submitted_count + 1,
            last_activity_at=now,
            updated_at=now,
        )
        .execution_options(synchronize_session=False)
    )
    if result.rowcount == 1:
        return
    # The atomic claim missed — figure out why so we can return the
    # correct error code (existence / state / full) without leaking
    # cross-tenant info.
    row = (
        await session.execute(
            select(Batch).where(Batch.id == batch_id)
        )
    ).scalar_one_or_none()
    if row is None or row.user_id != user_id:
        raise api_error(
            422,
            "INVALID_PARAMETER",
            "batch_id is invalid or not owned by the caller.",
            field="batch_id",
        )
    if row.status != "submitting":
        raise api_error(
            409,
            "BATCH_INVALID_STATE",
            f"batch is in status {row.status}; cannot bind new jobs.",
            field="batch_id",
        )
    raise api_error(
        422,
        "BATCH_FULL",
        "batch is already at total_job_count; cannot bind more jobs.",
        field="batch_id",
    )


async def _emit_batch_progress_after_bind(batch_id: str) -> None:
    """Best-effort SSE notify after a Job-bind transaction commits.

    The transaction owner releases its lock on commit; we open a fresh
    short-lived session, read the latest counters, and hand them to the
    debounced emitter. The emitter coalesces rapid bursts so the SSE
    fanout is one event regardless of how many ``POST /api/jobs`` calls
    happened in the same tick.
    """
    try:
        from app.domain.batch_service import (
            _snapshot_for_event,
            get_batch_progress_emitter,
        )

        async with get_session() as session:
            row = (
                await session.execute(
                    select(Batch).where(Batch.id == batch_id)
                )
            ).scalar_one_or_none()
            if row is None:
                return
            payload = _snapshot_for_event(row, in_flight_count=None)
        await get_batch_progress_emitter().publish(
            row.id, row.user_id, payload
        )
    except Exception:  # pragma: no cover — best effort
        logger.exception(
            "post-bind batch_progress emission failed batch=%s", batch_id
        )


# Reserved by the dedup logic — silence unused-import lint when the
# module is loaded without the inner helpers being introspected.
_ = uuid.uuid4

# audit hook reserved for future use; suppress unused-import warning.
_ = update
