"""Admin Job Inspector router (``/api/admin/jobs/*``).

Three endpoints:

1. ``GET /{hash_id}/inspect`` — primary read; aggregates the
   user-visible :class:`JobDetail` payload plus admin-only diagnostics
   (lifecycle, user state, routing, attempts, circuit ripples).
2. ``GET /{hash_id}/upstream/{n}`` — raw-log viewer for one
   ``upstream/attempt_N.json``. Already redacted at write time, but we
   walk it through ``redact_payload`` once more on the read path as
   defence-in-depth.
3. ``POST /{hash_id}/requeue`` — re-enqueue a terminal job with the
   same params (Phase 3).

Every route is admin-gated; every successful read writes one
``audit_log`` row.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Request, Response
from sqlalchemy import desc, func, select

from app.db.engine import get_session
from app.db.models import (
    Image,
    Job,
    JobReference,
    Provider,
    Session as SessionRow,
    User,
)
from app.deps import CurrentAdminContext
from app.domain.tier_config import get_tier_config
from app.schemas.admin_jobs import (
    AdminJobInspect,
    AttemptDetail,
    CircuitRippleNote,
    FilteredProvider,
    JobLifecycleSnapshot,
    ProviderSnapshotAtAttempt,
    RequeueResponse,
    RoutingTrace,
    ScoredProvider,
    UserStateSnapshot,
)
from app.schemas.archive import (
    JobImageSummary,
    JobReferenceSummary,
    JobSessionSummary,
    JobSetSummary,
    JobTiming,
)
from app.services import image_io
from app.utils.audit import write_audit
from app.utils.errors import api_error
from app.utils.redact import excerpt_for_response, redact_payload

logger = logging.getLogger("txt2img.admin.jobs")

router = APIRouter(prefix="/api/admin/jobs", tags=["admin", "jobs"])


_VISIBLE_TERMINAL = {"SUCCEEDED", "FAILED", "CANCELLED"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _client_ip(request: Request) -> str | None:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else None


def _aware_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _safe_load_json(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _seconds_between(a: datetime | None, b: datetime | None) -> float | None:
    if a is None or b is None:
        return None
    return max(0.0, (_aware_utc(b) - _aware_utc(a)).total_seconds())


def _display_name_for(model_id: str) -> str:
    from app.domain.model_catalog import _MODEL_DISPLAY  # type: ignore[attr-defined]

    info = _MODEL_DISPLAY.get(model_id)
    if not info:
        return model_id
    return str(info.get("display_name") or model_id)


def _build_timing(job: Job) -> JobTiming:
    queued = _aware_utc(job.queued_at) if job.queued_at else None
    started = _aware_utc(job.started_at) if job.started_at else None
    finished = _aware_utc(job.finished_at) if job.finished_at else None
    dispatched = (
        _aware_utc(job.dispatched_at) if job.dispatched_at else started
    )
    queue_seconds = _seconds_between(queued, dispatched)
    render_seconds = _seconds_between(started, finished)
    return JobTiming(
        queued_at=queued,
        started_at=started,
        finished_at=finished,
        queue_seconds=queue_seconds,
        render_seconds=render_seconds,
    )


def _derive_lifecycle(job: Job, attempts: list[dict[str, Any]]) -> JobLifecycleSnapshot:
    queued = _aware_utc(job.queued_at) or _aware_utc(job.created_at)
    dispatched = _aware_utc(job.dispatched_at)
    started = _aware_utc(job.started_at)
    finished = _aware_utc(job.finished_at)

    queued_seconds = _seconds_between(queued, dispatched or started)
    admission_seconds = _seconds_between(dispatched, started)
    routing_seconds = None
    attempts_seconds: float | None = None
    finalize_seconds = None

    first_attempt_started = None
    last_attempt_end = None
    if attempts:
        attempts_total_ms = 0.0
        from datetime import timedelta as _td

        for a in attempts:
            ts = a.get("started_at")
            attempt_started: datetime | None = None
            if ts:
                try:
                    attempt_started = datetime.fromisoformat(
                        str(ts).replace("Z", "+00:00")
                    )
                except (TypeError, ValueError):
                    attempt_started = None
            if attempt_started and first_attempt_started is None:
                first_attempt_started = attempt_started

            lat = a.get("latency_ms")
            if isinstance(lat, (int, float)):
                attempts_total_ms += float(lat)
                # Track the *latest* attempt's end time so finalize is
                # measured from the last attempt, not the first. Falling
                # back to ``first_attempt_started + cumulative`` keeps a
                # rough estimate alive when individual attempt timestamps
                # are missing from legacy logs.
                base = attempt_started or first_attempt_started
                if base is not None:
                    candidate = base + _td(milliseconds=float(lat))
                    if last_attempt_end is None or candidate > last_attempt_end:
                        last_attempt_end = candidate
        attempts_seconds = round(attempts_total_ms / 1000.0, 3) if attempts else None

    if started and first_attempt_started:
        routing_seconds = max(
            0.0, (_aware_utc(first_attempt_started) - started).total_seconds()
        )
    elif started and dispatched:
        routing_seconds = 0.0

    if finished and last_attempt_end:
        finalize_seconds = max(
            0.0, (finished - _aware_utc(last_attempt_end)).total_seconds()
        )
    elif finished and started and attempts_seconds is not None:
        # Fall back to whatever wall-clock is left after attempts.
        wall = (finished - started).total_seconds()
        finalize_seconds = max(0.0, wall - (attempts_seconds + (routing_seconds or 0.0)))

    return JobLifecycleSnapshot(
        queued_at=queued,
        dispatched_at=dispatched,
        started_at=started,
        finished_at=finished,
        queued_seconds=queued_seconds,
        admission_seconds=admission_seconds,
        routing_seconds=routing_seconds,
        attempts_seconds=attempts_seconds,
        finalize_seconds=finalize_seconds,
    )


# ---------------------------------------------------------------------------
# Section builders — every one is independent so a failure in one
# section turns into a degraded_sections entry rather than a 500.
# ---------------------------------------------------------------------------


@dataclass
class _TierAtSubmitView:
    """Adapter so :meth:`TierConfig.effective_quotas` can resolve a job's
    historical tier without resolving the user's *current* tier.

    Carries the user's quota-override columns (those don't have a
    historical record either, but they're operator-set values that
    rarely change between submit and inspect). Stops the synthetic
    snapshot from showing ``tier=free`` next to ``hard_quota=200``
    when the user has been promoted in the meantime.
    """

    tier: str
    override_soft_quota: int | None
    override_hard_quota: int | None


def _build_user_state(
    user: User,
    job: Job,
    timeline_rows: list[dict[str, Any]],
    recent_fail_n: int,
    recent_fail_total: int,
    *,
    job_flags: dict[str, Any] | None = None,
) -> tuple[UserStateSnapshot, bool]:
    """Pull state from timeline.jsonl when present; fall back to live row.

    Returns ``(snapshot, is_synthetic)``. ``is_synthetic=True`` means the
    timeline record was not found and the snapshot is reconstructed from
    today's DB row + tier defaults — admins should treat the values as
    "current" rather than "at dispatch".
    """
    snapshot: dict[str, Any] | None = None
    for row in timeline_rows:
        extra = row.get("extra")
        if isinstance(extra, dict):
            us = extra.get("user_state")
            if isinstance(us, dict):
                snapshot = us
                break
        us = row.get("user_state")
        if isinstance(us, dict):
            snapshot = us
            break

    if snapshot is None:
        # Resolve quotas against the *historical* tier so the synthetic
        # snapshot stays internally consistent. ``job.tier_at_submit``
        # is locked at job creation; ``user.tier`` may have changed
        # since. Fall back to the live user when the historical column
        # is missing (legacy schemas).
        synthetic_tier = str(job.tier_at_submit or user.tier)
        view = _TierAtSubmitView(
            tier=synthetic_tier,
            override_soft_quota=user.override_soft_quota,
            override_hard_quota=user.override_hard_quota,
        )
        soft_eff, hard_eff = get_tier_config().effective_quotas(view)
        # Several flags survive on ``jobs.flags_json`` even though the
        # timeline ``user_state`` block was never written. Read them
        # back so soft-quota / captcha-gated jobs don't render as
        # "below" / "n/a".
        flags = job_flags or {}
        soft_triggered = bool(flags.get("SOFT_QUOTA_EXCEEDED") is True)
        captcha_verified = bool(
            flags.get("captcha_verified") is True
            or flags.get("CAPTCHA_VERIFIED") is True
            or flags.get("turnstile_verified") is True
        )
        # ``captcha_required`` was historically derived from the soft
        # penalty plan and not persisted on the row. The closest
        # observable proxy is "the verified bit was set" (proves the
        # gate fired). Surface it as ``required`` only when we have
        # positive evidence.
        captcha_required = captcha_verified or bool(
            flags.get("captcha_required") is True
        )
        return (
            UserStateSnapshot(
                tier=synthetic_tier,
                today_count=int(user.today_count or 0),
                soft_quota_effective=int(soft_eff),
                hard_quota_effective=int(hard_eff),
                soft_quota_triggered=soft_triggered,
                captcha_required=captcha_required,
                captcha_verified=captcha_verified,
                recent_fail_rate_n=recent_fail_n,
                recent_fail_rate_total=recent_fail_total,
            ),
            True,
        )

    # Recorded snapshot path: still resolve the live quotas as a
    # *fallback* in case the recorded row is missing the numeric values.
    soft_eff, hard_eff = get_tier_config().effective_quotas(user)

    # Use a sentinel-aware getter so a recorded ``0`` (e.g. a free-tier
    # user with hard_quota=0) is preserved instead of being silently
    # replaced by the live tier defaults via ``... or fallback``.
    def _pick_int(key: str, fallback: int) -> int:
        if key in snapshot and isinstance(snapshot[key], (int, float, bool)):
            return int(snapshot[key])
        return int(fallback)

    def _pick_bool(key: str) -> bool:
        if key in snapshot:
            return bool(snapshot[key])
        return False

    tier_value = snapshot.get("tier")
    if not isinstance(tier_value, str) or not tier_value:
        tier_value = user.tier

    return (
        UserStateSnapshot(
            tier=str(tier_value),
            today_count=_pick_int("today_count", 0),
            soft_quota_effective=_pick_int("soft_quota_effective", soft_eff),
            hard_quota_effective=_pick_int("hard_quota_effective", hard_eff),
            soft_quota_triggered=_pick_bool("soft_quota_triggered"),
            captcha_required=_pick_bool("captcha_required"),
            captcha_verified=_pick_bool("captcha_verified"),
            recent_fail_rate_n=_pick_int("recent_fail_rate_n", recent_fail_n),
            recent_fail_rate_total=_pick_int(
                "recent_fail_rate_total", recent_fail_total
            ),
        ),
        False,
    )


def _build_routing_trace(payload: dict[str, Any]) -> RoutingTrace:
    filtered_raw = payload.get("filtered_out") or []
    scored_raw = payload.get("scored") or []

    filtered: list[FilteredProvider] = []
    for f in filtered_raw:
        if not isinstance(f, dict):
            continue
        try:
            filtered.append(
                FilteredProvider(
                    provider_id=str(f.get("provider_id") or "?"),
                    label=str(f.get("label") or f.get("provider_id") or "?"),
                    reason=str(f.get("reason") or "other"),  # type: ignore[arg-type]
                    detail=f.get("detail"),
                )
            )
        except Exception:
            continue

    scored: list[ScoredProvider] = []
    for idx, s in enumerate(scored_raw):
        if not isinstance(s, dict):
            continue
        try:
            scored.append(
                ScoredProvider(
                    rank=int(s.get("rank") or (idx + 1)),
                    provider_id=str(s.get("provider_id") or "?"),
                    label=str(s.get("label") or s.get("provider_id") or "?"),
                    components={
                        k: float(v)
                        for k, v in (s.get("components") or {}).items()
                        if isinstance(v, (int, float))
                    },
                    total_score=float(s.get("total_score") or 0.0),
                    chosen=bool(s.get("chosen") or False),
                )
            )
        except Exception:
            continue

    return RoutingTrace(
        pool_total=int(payload.get("pool_total") or 0),
        pool_survived=int(payload.get("pool_survived") or len(scored)),
        pool_scored=int(payload.get("pool_scored") or len(scored)),
        filtered_out=filtered,
        scored=scored,
        selector_config={
            k: float(v)
            for k, v in (payload.get("selector_config") or {}).items()
            if isinstance(v, (int, float))
        },
    )


def _build_attempt_detail(
    hash_id: str,
    n: int,
    raw: dict[str, Any] | None,
    provider_label_by_id: dict[str, str],
    chosen_provider_id: str | None,
    provider_static_by_id: dict[str, dict[str, Any]] | None = None,
) -> AttemptDetail:
    if raw is None:
        return AttemptDetail(
            attempt_no=n,
            provider_id="?",
            provider_label="attempt log unavailable",
            ok=False,
            error_kind="OTHER",
            raw_log_url=f"/api/admin/jobs/{hash_id}/upstream/{n}",
        )
    provider_id = str(raw.get("provider_id") or "?")
    error = raw.get("error") or {}
    snap_raw = raw.get("provider_snapshot") or {}
    static = (
        (provider_static_by_id or {}).get(provider_id) or {}
    )

    def _fallback(key: str) -> Any:
        """Return the snapshotted value if recorded; otherwise the
        provider's current value as a best-effort fill-in."""
        if key in snap_raw and snap_raw[key] is not None:
            return snap_raw[key]
        return static.get(key)

    snap = ProviderSnapshotAtAttempt(
        circuit_state=_fallback("circuit_state"),
        success_rate_5m=snap_raw.get("success_rate_5m"),
        p50_latency_ms=snap_raw.get("p50_latency_ms"),
        current_concurrency=snap_raw.get("current_concurrency"),
        max_concurrency=_fallback("max_concurrency"),
        current_rpm=snap_raw.get("current_rpm"),
        rpm_limit=_fallback("rpm_limit"),
        extra=snap_raw.get("extra") or {},
    )
    # Prefer the upstream body excerpt the executor stored on the error
    # dict; fall back to the normalized message when the adapter never
    # captured a body (timeouts, network errors).
    body_excerpt = None
    if not raw.get("ok"):
        body_value = None
        if isinstance(error, dict):
            body_value = error.get("upstream_body_excerpt") or error.get("message")
        body_excerpt = excerpt_for_response(body_value)

    started_at = None
    ts = raw.get("started_at")
    if ts:
        try:
            started_at = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            started_at = None

    error_kind: Any = None
    if isinstance(error, dict):
        kind_value = error.get("kind")
        if isinstance(kind_value, str):
            error_kind = kind_value

    # ``upstream_status`` lives on the nested error dict (executor
    # writes it through ``StandardError.to_dict``), not at the top
    # level of the attempt log. The top-level fallback covers any
    # future caller that promotes the field.
    upstream_status = None
    if isinstance(error, dict):
        upstream_status = error.get("upstream_status") or error.get("http_status")
    if upstream_status is None:
        upstream_status = raw.get("upstream_status")

    return AttemptDetail(
        attempt_no=n,
        provider_id=provider_id,
        provider_label=provider_label_by_id.get(provider_id, provider_id),
        started_at=started_at,
        latency_ms=raw.get("latency_ms"),
        ok=bool(raw.get("ok")),
        error_kind=error_kind,
        upstream_status=upstream_status,
        upstream_body_excerpt=body_excerpt,
        provider_snapshot=snap,
        raw_log_url=f"/api/admin/jobs/{hash_id}/upstream/{n}",
        chosen=bool(chosen_provider_id and provider_id == chosen_provider_id),
    )


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


async def _recent_fail_count(user_id: str, exclude_job_id: str) -> tuple[int, int]:
    """Count failed jobs in the user's last 10 terminal-state jobs."""
    async with get_session() as session:
        rows = list(
            (
                await session.execute(
                    select(Job.status)
                    .where(
                        Job.user_id == user_id,
                        Job.id != exclude_job_id,
                        Job.status.in_(_VISIBLE_TERMINAL),
                    )
                    .order_by(desc(Job.finished_at), desc(Job.created_at))
                    .limit(10)
                )
            ).all()
        )
    n = sum(1 for (status,) in rows if status == "FAILED")
    return n, len(rows)


@router.get("/{hash_id}/inspect", response_model=AdminJobInspect)
async def inspect_job(
    hash_id: str,
    ctx: CurrentAdminContext,
    request: Request,
    response: Response,
) -> AdminJobInspect:
    """Aggregate every per-job artefact admins need into one payload."""
    if not image_io.is_valid_hash_id(hash_id):
        raise api_error(404, "NOT_FOUND", "Job not found.", field="hash_id")

    degraded: list[str] = []

    async with get_session() as session:
        job_row = (
            await session.execute(select(Job).where(Job.hash_id == hash_id))
        ).scalar_one_or_none()
        if job_row is None:
            raise api_error(404, "NOT_FOUND", "Job not found.", field="hash_id")

        user_row = (
            await session.execute(
                select(User).where(User.id == job_row.user_id)
            )
        ).scalar_one_or_none()
        if user_row is None:  # pragma: no cover - FK guarantees this
            raise api_error(500, "INTERNAL_ERROR", "Job has no user.")

        # Pull child rows + provider labels in parallel-friendly fashion.
        images = list(
            (
                await session.execute(
                    select(Image)
                    .where(Image.job_id == job_row.id)
                    .order_by(Image.img_order)
                )
            ).scalars().all()
        )
        refs = list(
            (
                await session.execute(
                    select(JobReference)
                    .where(JobReference.job_id == job_row.id)
                    .order_by(JobReference.ref_order)
                )
            ).scalars().all()
        )
        session_summary = None
        if job_row.session_id:
            sess_row = (
                await session.execute(
                    select(SessionRow).where(SessionRow.id == job_row.session_id)
                )
            ).scalar_one_or_none()
            if sess_row is not None:
                session_summary = JobSessionSummary(id=sess_row.id, name=sess_row.name)

        set_summary = None
        if job_row.set_id:
            count = (
                await session.execute(
                    select(func.count(Image.id))
                    .select_from(Job)
                    .join(Image, Image.job_id == Job.id)
                    .where(
                        Job.set_id == job_row.set_id,
                        Job.user_id == job_row.user_id,
                    )
                )
            ).scalar_one()
            set_summary = JobSetSummary(
                set_id=job_row.set_id, image_count=int(count or 0)
            )

        # Pull the static + currently-persisted dynamic columns so the
        # inspector can backfill the per-attempt provider snapshot when
        # the recorded ``provider_snapshot`` block is missing fields
        # (legacy jobs that ran before Phase 2 instrumentation, or rows
        # whose write was truncated). ``max_concurrency`` / ``rpm_limit``
        # rarely change, so showing today's value beats a row of em
        # dashes; ``circuit_state`` is the live debugger view of a
        # provider and is informative even when not historical.
        provider_rows = list(
            (
                await session.execute(
                    select(
                        Provider.id,
                        Provider.label,
                        Provider.max_concurrency,
                        Provider.rpm_limit,
                        Provider.circuit_state,
                        Provider.enabled,
                    )
                )
            ).all()
        )
        provider_label_by_id = {row[0]: row[1] for row in provider_rows}
        provider_static_by_id: dict[str, dict[str, Any]] = {
            row[0]: {
                "max_concurrency": int(row[2]) if row[2] is not None else None,
                "rpm_limit": int(row[3]) if row[3] is not None else None,
                "circuit_state": row[4] or None,
            }
            for row in provider_rows
        }
        # Real routing traces define ``pool_total`` as the count of
        # *enabled* providers considered before model filtering. Mirror
        # that contract in the synthetic fallback so the summary line
        # doesn't disagree with current traces for the same catalog.
        enabled_provider_count = sum(1 for row in provider_rows if int(row[5] or 0) == 1)

    # Off the event loop: read the per-job filesystem artefacts in parallel.
    timeline_task = asyncio.to_thread(image_io.read_timeline, hash_id)
    routing_task = asyncio.to_thread(image_io.read_routing_trace, hash_id)
    attempts_idx_task = asyncio.to_thread(image_io.list_upstream_attempts, hash_id)
    timeline_rows, routing_payload, attempt_indices = await asyncio.gather(
        timeline_task, routing_task, attempts_idx_task
    )

    if attempt_indices:
        attempt_payloads = await asyncio.gather(
            *(
                asyncio.to_thread(image_io.read_upstream_log, hash_id, n)
                for n in attempt_indices
            )
        )
    else:
        attempt_payloads = []

    # Build attempt details first so lifecycle can use the latency totals.
    chosen_provider_id = job_row.provider_used
    attempt_dicts: list[dict[str, Any]] = []
    for n, payload in zip(attempt_indices, attempt_payloads):
        if payload is None:
            attempt_dicts.append({"attempt_no": n})
            continue
        d = dict(payload)
        d["attempt_no"] = n
        attempt_dicts.append(d)

    attempts_models: list[AttemptDetail] = []
    attempt_decode_failed = False
    for n, p in zip(attempt_indices, attempt_payloads):
        try:
            attempts_models.append(
                _build_attempt_detail(
                    hash_id,
                    n,
                    p,
                    provider_label_by_id,
                    chosen_provider_id,
                    provider_static_by_id,
                )
            )
        except Exception:
            # Legacy / malformed log → keep the section alive by
            # emitting a placeholder row instead of 500-ing the whole
            # inspector response. The placeholder mirrors the
            # ``raw is None`` branch of ``_build_attempt_detail``.
            logger.exception(
                "inspect: failed to decode attempt %d for job=%s", n, hash_id
            )
            attempt_decode_failed = True
            try:
                attempts_models.append(
                    _build_attempt_detail(
                        hash_id,
                        n,
                        None,
                        provider_label_by_id,
                        chosen_provider_id,
                        provider_static_by_id,
                    )
                )
            except Exception:  # pragma: no cover - extremely defensive
                pass

    if attempt_decode_failed:
        degraded.append("attempts")

    # ---- Lifecycle ----
    try:
        lifecycle = _derive_lifecycle(job_row, attempt_dicts)
    except Exception:
        logger.exception("inspect: lifecycle derivation failed for job=%s", hash_id)
        lifecycle = JobLifecycleSnapshot()
        degraded.append("lifecycle")

    # ---- User state ----
    try:
        recent_n, recent_total = await _recent_fail_count(
            job_row.user_id, job_row.id
        )
        recent_total = recent_total if recent_total > 0 else 10
        # Pre-decode flags so the synthetic branch can read SOFT_QUOTA_*
        # and captcha bits straight off the row (timeline didn't carry
        # them for legacy jobs).
        _job_flags_for_state = _safe_load_json(job_row.flags_json)
        user_state, user_state_synthetic = _build_user_state(
            user_row,
            job_row,
            timeline_rows,
            recent_n,
            recent_total,
            job_flags=_job_flags_for_state,
        )
        if user_state_synthetic:
            # Distinct from the failure flag — the section is *valid*
            # but reconstructed from current DB rows rather than the
            # snapshot taken at dispatch. The frontend uses this flag
            # to render a "current values" disclaimer.
            degraded.append("user_state_synthetic")
    except Exception:
        logger.exception(
            "inspect: user-state derivation failed for job=%s", hash_id
        )
        user_state = None
        degraded.append("user_state")

    # ---- Routing ----
    routing: RoutingTrace | None = None
    if routing_payload:
        try:
            routing = _build_routing_trace(routing_payload)
            # Reflect the executor's chosen provider in case the
            # per-job patch writer didn't run (legacy / failure path).
            if chosen_provider_id and routing.scored:
                for s in routing.scored:
                    s.chosen = s.provider_id == chosen_provider_id
        except Exception:
            # ``routing.json`` exists but the bytes don't decode into a
            # valid trace — distinct from "no file at all". Surface it
            # as a separate degraded marker so the UI can advise on
            # storage corruption rather than telling the operator the
            # trace was never recorded.
            logger.exception(
                "inspect: routing trace decode failed for job=%s", hash_id
            )
            routing = None
            degraded.append("routing_corrupt")
    else:
        # No recorded trace on disk — pre-Phase-2 jobs ran before the
        # selector started persisting the trace, and there is no way to
        # reconstruct the historical filter / score breakdown after the
        # fact. We still synthesise a minimal trace that pins the
        # chosen provider so admins see *something* useful instead of
        # an opaque "trace unavailable" placeholder.
        if chosen_provider_id:
            # Try the live ``providers`` row first for a friendlier
            # label, but fall back to the raw id when the provider has
            # since been deleted from the catalog. ``provider_used`` on
            # the job row is the only piece of routing context we still
            # have for those jobs and we'd rather show it than nothing.
            label = provider_label_by_id.get(
                chosen_provider_id, chosen_provider_id
            )
            routing = RoutingTrace(
                # ``enabled_provider_count`` mirrors the real selector's
                # ``pool_total`` definition (enabled providers, before
                # model filtering); falling back to "at least the chosen
                # one" guarantees a non-zero baseline even for empty
                # catalogs (the chosen provider provably existed at
                # dispatch even if it has since been removed).
                pool_total=max(enabled_provider_count, 1),
                pool_survived=1,
                pool_scored=1,
                filtered_out=[],
                scored=[
                    ScoredProvider(
                        rank=1,
                        provider_id=chosen_provider_id,
                        label=label,
                        components={},
                        total_score=0.0,
                        chosen=True,
                    )
                ],
                selector_config={},
            )
        if job_row.status in _VISIBLE_TERMINAL:
            degraded.append("routing")

    # ---- Circuit ripples ----
    ripples: list[CircuitRippleNote] = []
    try:
        for row in timeline_rows:
            extra = row.get("extra")
            transitions = (
                (extra.get("circuit_transitions") if isinstance(extra, dict) else None)
                or row.get("circuit_transitions")
                or []
            )
            if not isinstance(transitions, list):
                continue
            for t in transitions:
                if not isinstance(t, dict):
                    continue
                pid = str(t.get("provider_id") or "?")
                ripples.append(
                    CircuitRippleNote(
                        provider_id=pid,
                        label=provider_label_by_id.get(pid, pid),
                        transition=str(t.get("transition") or ""),
                        triggered_by_attempt=int(
                            t.get("triggered_by_attempt") or 0
                        ),
                    )
                )
    except Exception:
        logger.exception("inspect: ripple decode failed for job=%s", hash_id)

    # ---- Build user-visible mirrors ----
    params = _safe_load_json(job_row.params_json)
    flags = _safe_load_json(job_row.flags_json)
    params_for_wire = {
        k: v for k, v in params.items() if k not in ("prompt", "model")
    }

    image_summaries = [
        JobImageSummary(
            image_id=img.id,
            order=img.img_order,
            thumb_url=f"/api/jobs/{hash_id}/images/{img.img_order}/thumb",
            download_url=f"/api/jobs/{hash_id}/images/{img.img_order}/original",
            width=img.width,
            height=img.height,
            format=img.format,
            file_size_bytes=img.file_size_bytes,
            starred=bool(img.starred),
        )
        for img in images
    ]
    ref_summaries = [
        JobReferenceSummary(
            order=ref.ref_order,
            filename=ref.filename,
            thumb_url=f"/api/jobs/{hash_id}/refs/{ref.ref_order}/thumb",
        )
        for ref in refs
    ]

    timing = _build_timing(job_row)

    inspect = AdminJobInspect(
        hash_id=job_row.hash_id,
        seq_no=int(job_row.seq_no),
        status=job_row.status,
        status_reason=job_row.status_reason,
        model=job_row.model,
        model_display_name=_display_name_for(job_row.model),
        updated_at=_aware_utc(job_row.updated_at) or _aware_utc(job_row.created_at),
        set_id=job_row.set_id,
        session_id=job_row.session_id,
        prompt=str(params.get("prompt") or ""),
        params=params_for_wire,
        references=ref_summaries,
        set=set_summary,
        images=image_summaries,
        session=session_summary,
        timing=timing,
        error=job_row.status_reason if job_row.status == "FAILED" else None,
        flags=flags,
        user_id=job_row.user_id,
        user_username=user_row.username,
        provider_used=job_row.provider_used,
        retries=int(job_row.retries or 0),
        cost_cny=float(job_row.cost_cny or 0.0),
        balance_after_cny=None,
        lifecycle=lifecycle,
        user_state_at_submit=user_state,
        routing=routing,
        attempts=attempts_models,
        circuit_ripples=ripples,
        degraded_sections=degraded,
    )

    # Audit the inspection. Best-effort; never blocks the response.
    try:
        async with get_session() as session:
            await write_audit(
                session,
                actor_user_id=ctx.user.id,
                action="admin.job.inspect",
                target_kind="job",
                target_id=job_row.hash_id,
                payload={
                    "target_user_id": job_row.user_id,
                    "target_user_username": user_row.username,
                },
                ip=_client_ip(request),
            )
    except Exception:
        logger.exception("inspect: audit write failed for job=%s", hash_id)

    # Cache hint:
    # - Terminal jobs whose payload is fully sourced from immutable
    #   per-job artefacts (timeline.jsonl, attempt logs, routing.json)
    #   may cache for a minute — those bytes don't change after the
    #   job lands. Even partial degradation (a missing attempt log)
    #   doesn't break that contract because the *missing* file won't
    #   reappear.
    # - But once any synthetic / live-DB fallback has been folded into
    #   the response (provider snapshot caps, synthesised routing
    #   trace, synthesised user_state) the payload depends on the
    #   live ``providers`` / ``users`` rows and an admin edit must
    #   show up immediately. Switch those responses to ``no-store``.
    # - Live (non-terminal) jobs always opt out of caching.
    fallback_markers = {"user_state_synthetic", "routing"}
    response_uses_live_data = (
        any(m in degraded for m in fallback_markers)
        # If any attempt actually pulled values from the live Provider
        # row, the response is also live-coupled. Easiest check: was
        # any attempt's snapshot missing the recorded ``rpm_limit`` /
        # ``circuit_state``? We surface that via the same flag as the
        # routing fallback for cache-control purposes.
        or any(
            not (raw_attempt or {}).get("provider_snapshot")
            for raw_attempt in attempt_payloads
        )
    )
    if job_row.status in _VISIBLE_TERMINAL and not response_uses_live_data:
        response.headers["Cache-Control"] = "private, max-age=60"
    else:
        response.headers["Cache-Control"] = "no-store"

    return inspect


# ---------------------------------------------------------------------------
# Raw upstream attempt log
# ---------------------------------------------------------------------------


@router.get("/{hash_id}/upstream/{attempt_no}")
async def get_upstream_log(
    hash_id: str,
    attempt_no: int,
    ctx: CurrentAdminContext,
    request: Request,
) -> Any:
    if not image_io.is_valid_hash_id(hash_id):
        raise api_error(404, "NOT_FOUND", "Job not found.", field="hash_id")
    if attempt_no < 1 or attempt_no > 99:
        raise api_error(404, "NOT_FOUND", "Attempt not found.", field="attempt_no")

    async with get_session() as session:
        row = (
            await session.execute(
                select(Job.id, Job.user_id).where(Job.hash_id == hash_id)
            )
        ).one_or_none()
        if row is None:
            raise api_error(404, "NOT_FOUND", "Job not found.", field="hash_id")
        target_user_id = row[1]

    payload = await asyncio.to_thread(
        image_io.read_upstream_log, hash_id, attempt_no
    )
    if payload is None:
        raise api_error(404, "NOT_FOUND", "Attempt log not found.")

    # Raw upstream payloads are the most sensitive surface in the
    # admin tooling — every successful read leaves an audit row so
    # access is reconstructable after the fact.
    try:
        async with get_session() as session:
            await write_audit(
                session,
                actor_user_id=ctx.user.id,
                action="admin.job.upstream_log",
                target_kind="job",
                target_id=hash_id,
                payload={
                    "attempt_no": int(attempt_no),
                    "target_user_id": target_user_id,
                },
                ip=_client_ip(request),
            )
    except Exception:  # pragma: no cover - best-effort
        logger.exception(
            "inspect: audit write failed for upstream log job=%s attempt=%d",
            hash_id,
            attempt_no,
        )

    return redact_payload(payload)


# ---------------------------------------------------------------------------
# Re-queue
# ---------------------------------------------------------------------------


@router.post("/{hash_id}/requeue", response_model=RequeueResponse)
async def requeue_job(
    hash_id: str,
    ctx: CurrentAdminContext,
    request: Request,
) -> RequeueResponse:
    """Clone a terminal job's params + refs into a fresh QUEUED job."""
    if not image_io.is_valid_hash_id(hash_id):
        raise api_error(404, "NOT_FOUND", "Job not found.", field="hash_id")

    from app.db.jobs_repository import JobsRepository
    from app.domain.job_queue import get_job_queue
    from app.utils.ids import new_job_hash_id, new_job_internal_id

    async with get_session() as session:
        src = (
            await session.execute(select(Job).where(Job.hash_id == hash_id))
        ).scalar_one_or_none()
        if src is None:
            raise api_error(404, "NOT_FOUND", "Job not found.", field="hash_id")
        if src.status not in {"SUCCEEDED", "FAILED", "CANCELLED"}:
            raise api_error(
                422,
                "INVALID_PARAMETER",
                "Only terminal jobs can be requeued.",
                field="status",
            )

        # Atomic seq_no allocation — using ``UPDATE ... RETURNING`` on
        # ``users.last_seq_no`` keeps two concurrent requeues for the
        # same user from colliding on the unique ``(user_id, seq_no)``
        # index that a SELECT MAX(...) approach would race with.
        allocation = await JobsRepository().allocate_seq_no(
            src.user_id, session=session
        )

        now = datetime.now(timezone.utc)
        new_id = new_job_internal_id()
        new_hash = new_job_hash_id()
        new_job = Job(
            id=new_id,
            hash_id=new_hash,
            user_id=src.user_id,
            tier_at_submit=src.tier_at_submit,
            seq_no=allocation.seq_no,
            set_id=None,
            session_id=src.session_id,
            model=src.model,
            params_json=src.params_json,
            flags_json="{}",
            client_request_id=None,
            status="QUEUED",
            status_reason=None,
            provider_used=None,
            retries=0,
            cost_cny=0.0,
            created_at=now,
            queued_at=now,
            updated_at=now,
        )
        session.add(new_job)

        # Clone reference rows AND the underlying files into the new
        # job's data directory. Pointing at the source job's rel_path
        # would break the requeued job once the source is purged
        # (account-deletion T+30 / cleanup keeper). When the file
        # genuinely can't be copied we keep the row alive with the
        # legacy rel_path so the requeue at least carries metadata.
        ref_rows = list(
            (
                await session.execute(
                    select(JobReference).where(JobReference.job_id == src.id)
                )
            ).scalars().all()
        )
        for r in ref_rows:
            new_rel = await asyncio.to_thread(
                image_io.clone_reference,
                src_rel_path=r.rel_path,
                dst_hash_id=new_hash,
                order=int(r.ref_order),
                original_filename=r.filename,
                mime=r.mime,
            )
            session.add(
                JobReference(
                    job_id=new_id,
                    ref_order=r.ref_order,
                    filename=r.filename,
                    mime=r.mime,
                    rel_path=new_rel or r.rel_path,
                )
            )

        await write_audit(
            session,
            actor_user_id=ctx.user.id,
            action="admin.job.requeue",
            target_kind="job",
            target_id=new_hash,
            payload={
                "source_hash_id": src.hash_id,
                "user_id": src.user_id,
            },
            ip=_client_ip(request),
        )

    queue = get_job_queue()
    from app.domain.job_queue import QueuedJob

    await queue.enqueue(
        QueuedJob(
            hash_id=new_hash,
            job_id=new_id,
            user_id=src.user_id,
            tier=src.tier_at_submit,
            model=src.model,
            queued_at=now,
            seq_no=allocation.seq_no,
        )
    )
    return RequeueResponse(new_hash_id=new_hash, source_hash_id=hash_id)


__all__ = ["router"]
