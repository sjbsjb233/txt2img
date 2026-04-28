"""Two-stage provider routing.

Implements design doc §7.4. Given a normalised job request, return a
ranked list of providers the executor can try in order. The selector is
the only place that combines:

* DB state (which providers exist, what models / tiers they accept,
  per-(provider, model) capabilities).
* Runtime state (circuit breaker, in-memory metrics, current
  concurrency).
* Configuration (filter thresholds, score weights).

Output: ``list[ScoredCandidate]`` sorted by descending score; the
executor walks the first ``provider_scoring.fallback_top_k`` and stops
on the first success.

Stages
------
1. **Hard filter** — every check below must pass; failure removes the
   provider from the candidate pool with no score:

   - ``providers.enabled = 1``.
   - ``provider_models`` row exists for the requested model with
     ``enabled=1`` (so admin can disable a single model on a provider
     without removing the whole provider).
   - User's tier is allowed by ``provider_tier_access`` *or* by
     ``provider_model_tier_access`` if that finer-grained whitelist
     has any rows for ``(provider, model)``.
   - Capabilities JSON for ``(provider, model)`` matches the request
     (each user-supplied parameter falls inside the provider's allowed
     set).
   - ``balance_cny ≥ provider_filter.balance_min_threshold``. Sub-
     threshold providers are flipped to ``DRAINED`` here so their
     state is durable; admin sees the drained badge until they topup.
   - ``circuit_state == 'healthy'`` (HALF_OPEN is reserved for the
     probe path; OPEN / DRAINED / DISABLED are excluded).
   - ``current_concurrency < max_concurrency``.
   - ``recent_calls_in_60s < rpm_limit``.

2. **Soft score** — five weighted dimensions, each in ``[0, 1]``:

   - ``cost``     — relative to the most expensive remaining
     candidate. Cheaper → higher score.
   - ``success``  — 5-min sliding success rate from the metrics
     engine; defaults to 1.0 when there is no data so a fresh
     provider isn't penalised on a metric it has had no chance to
     populate.
   - ``latency``  — ``1 - min(1, p50_ms / SLO_ms)`` where ``SLO_ms``
     comes from the user's tier (with a fallback for tiers without
     an SLO, e.g. Free).
   - ``load``     — ``1 - current_concurrency / max_concurrency``.
   - ``freshness``— exploration term: increases with seconds since the
     last use, saturating at the configured window. Without this the
     selector locks onto whichever provider scored highest and never
     gives a marginal alternative a chance to gather data.

Importantly, the selector is read-only with respect to ``circuit_state``
*except* for the balance-driven ``mark_drained`` transition. The breaker
owns every other state change.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.engine import get_session
from app.db.models import (
    Provider,
    ProviderModel,
    ProviderModelTierAccess,
    ProviderTierAccess,
    User,
)
from app.domain.circuit_breaker import (
    CircuitBreaker,
    DRAINED,
    HEALTHY,
    get_circuit_breaker,
)
from app.domain.metrics_engine import MetricsEngine, get_metrics_engine
from app.domain.runtime_configs import (
    ProviderFilterConfig,
    ProviderScoringConfig,
)
from app.domain.tier_config import TierConfig, get_tier_config
from app.schemas.normalized import NormalizedRequest

logger = logging.getLogger("txt2img.selector")


# Latency SLO fallback (ms) when the user's tier doesn't define one.
# Free tier in the seed has ``slo_p95_ms = NULL``; rather than skipping
# the latency dimension entirely, we use a generous default so the
# dimension keeps contributing.
_LATENCY_SLO_FALLBACK_MS = 60_000

# Freshness saturation window (seconds): once a provider has been idle
# this long the freshness term is at its max value of 1.0. Picked to
# match the metric window so "fresh" matches "no recent data".
_FRESHNESS_SATURATION_SECONDS = 300


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CandidateProvider:
    """Snapshot of a provider that passed hard filtering.

    Pure data — no live ORM references — so the selector can release
    the DB session and still let the executor (PR-11) hold the
    candidate across the ``await adapter.generate(...)`` call.
    """

    provider_id: str
    label: str
    adapter_type: str
    base_url: str
    api_key_enc: str
    cost_per_image_cny: float
    balance_cny: float
    max_concurrency: int
    rpm_limit: int
    capabilities: dict[str, Any]


@dataclass(frozen=True)
class ScoredCandidate:
    """A candidate plus its score breakdown.

    The breakdown is included so admin debug tooling (PR-16/17) can
    show why a provider was preferred. The executor only uses
    ``provider`` and ``score``.
    """

    provider: CandidateProvider
    score: float
    components: Mapping[str, float]


# ---------------------------------------------------------------------------
# Selector
# ---------------------------------------------------------------------------


class ProviderSelector:
    """Compose DB + breaker + metrics into a ranked candidate list."""

    def __init__(
        self,
        *,
        metrics: MetricsEngine | None = None,
        breaker: CircuitBreaker | None = None,
        scoring: ProviderScoringConfig | None = None,
        filter_config: ProviderFilterConfig | None = None,
        tier_config: TierConfig | None = None,
    ) -> None:
        self._metrics = metrics or get_metrics_engine()
        self._breaker = breaker or get_circuit_breaker()
        self._scoring = scoring or ProviderScoringConfig()
        self._filter = filter_config or ProviderFilterConfig()
        self._tier_config = tier_config or get_tier_config()

    # -- public API -------------------------------------------------------

    async def select(
        self,
        user: User,
        request: NormalizedRequest,
        *,
        top_k: int | None = None,
    ) -> list[ScoredCandidate]:
        """Return providers ranked by score for ``user`` + ``request``.

        Empty list means "no candidate" — the executor maps this to
        503 NO_PROVIDER_AVAILABLE and refunds the user's quota (per
        design doc §7.6 / §7.7). Caller must NOT treat an empty
        result as a routing failure.

        ``top_k`` defaults to ``provider_scoring.fallback_top_k``. We
        compute scores for every passing candidate and only truncate
        at the end so the top-1 winner is the global maximum.
        """
        async with get_session() as session:
            candidates = await self._hard_filter(session, user, request)

        if not candidates:
            return []

        scored = self._score_all(user, request, candidates)
        scored.sort(key=lambda c: c.score, reverse=True)

        k = top_k if top_k is not None else self._fallback_top_k()
        return scored[:k]

    # -- stage 1: hard filter --------------------------------------------

    async def _hard_filter(
        self,
        session: AsyncSession,
        user: User,
        request: NormalizedRequest,
    ) -> list[CandidateProvider]:
        """Run the §7.4 hard checks and return surviving providers.

        Single bulk query for providers + per-model rows + tier-access.
        N+1 here would be a real problem at catalog scale; one
        round-trip per join keeps the path predictable.
        """
        # 1. Providers that are enabled and have a row for the model.
        rows = (
            await session.execute(
                select(Provider, ProviderModel)
                .join(
                    ProviderModel,
                    ProviderModel.provider_id == Provider.id,
                )
                .where(
                    Provider.enabled == 1,
                    ProviderModel.model_id == request.model,
                    ProviderModel.enabled == 1,
                )
            )
        ).all()
        if not rows:
            return []

        provider_ids = [p.id for (p, _m) in rows]

        # 2. Tier-access (provider-level) and finer-grained
        # provider_model_tier_access in two bulk queries.
        tier_rows = (
            await session.execute(
                select(
                    ProviderTierAccess.provider_id,
                    ProviderTierAccess.tier,
                ).where(ProviderTierAccess.provider_id.in_(provider_ids))
            )
        ).all()
        pmta_rows = (
            await session.execute(
                select(
                    ProviderModelTierAccess.provider_id,
                    ProviderModelTierAccess.model_id,
                    ProviderModelTierAccess.tier,
                ).where(
                    ProviderModelTierAccess.provider_id.in_(provider_ids),
                    ProviderModelTierAccess.model_id == request.model,
                )
            )
        ).all()

        provider_tiers: dict[str, set[str]] = {}
        for pid, tier in tier_rows:
            provider_tiers.setdefault(pid, set()).add(tier)
        # If any (provider, model) row exists in pmta, it overrides
        # provider_tier_access for that pair.
        per_model_tiers: dict[tuple[str, str], set[str]] = {}
        for pid, mid, tier in pmta_rows:
            per_model_tiers.setdefault((pid, mid), set()).add(tier)

        # 3. Walk each (provider, model) row through the remaining
        # checks in order. We pull DB-only checks first (cheap), then
        # capabilities (medium), then runtime state (live ones).
        candidates: list[CandidateProvider] = []
        balance_threshold = self._filter.balance_min_threshold

        for provider, model_row in rows:
            # 3a. Tier access. PMTA wins if defined; else fall back to
            # the provider-level whitelist.
            allowed = per_model_tiers.get((provider.id, model_row.model_id))
            if allowed is None:
                allowed = provider_tiers.get(provider.id, set())
            if user.tier not in allowed:
                continue

            # 3b. Capabilities.
            caps = _safe_load_caps(model_row.capabilities_json)
            if not _capabilities_match(caps, request):
                continue

            # 3c. Balance threshold. Sub-threshold → DRAINED via the
            # breaker so the state surfaces in admin views.
            if float(provider.balance_cny) < balance_threshold:
                # Awaited inside the loop because the breaker also
                # writes the DB; for a small number of low-balance
                # providers this is fine.
                await self._breaker.mark_drained(provider.id)
                continue

            # 3d. Circuit state. HEALTHY only — HALF_OPEN is the probe
            # path's domain.
            state = await self._breaker.get_state(provider.id)
            if state != HEALTHY:
                # Belt-and-suspenders: if breaker says DRAINED but the
                # DB still says healthy (e.g. the breaker just flipped
                # in memory), skip.
                if state == DRAINED:
                    continue
                continue

            # 3e. Concurrency cap.
            if (
                self._metrics.current_concurrency(provider.id)
                >= int(provider.max_concurrency)
            ):
                continue

            # 3f. RPM cap (per minute).
            if (
                self._metrics.recent_calls_in_60s(provider.id)
                >= int(provider.rpm_limit)
            ):
                continue

            candidates.append(
                CandidateProvider(
                    provider_id=provider.id,
                    label=provider.label,
                    adapter_type=provider.adapter_type,
                    base_url=provider.base_url,
                    api_key_enc=provider.api_key_enc,
                    cost_per_image_cny=float(provider.cost_per_image_cny),
                    balance_cny=float(provider.balance_cny),
                    max_concurrency=int(provider.max_concurrency),
                    rpm_limit=int(provider.rpm_limit),
                    capabilities=caps,
                )
            )

        return candidates

    # -- stage 2: scoring -------------------------------------------------

    def _score_all(
        self,
        user: User,
        request: NormalizedRequest,
        candidates: Sequence[CandidateProvider],
    ) -> list[ScoredCandidate]:
        if not candidates:
            return []

        weights = self._scoring.weights
        max_cost = max(c.cost_per_image_cny for c in candidates) or 1.0
        slo_ms = self._slo_ms_for_user(user)

        out: list[ScoredCandidate] = []
        for cand in candidates:
            comps = self._score_components(
                cand,
                request.model,
                max_cost=max_cost,
                slo_ms=slo_ms,
            )
            score = (
                weights["cost"] * comps["cost"]
                + weights["success"] * comps["success"]
                + weights["latency"] * comps["latency"]
                + weights["load"] * comps["load"]
                + weights["freshness"] * comps["freshness"]
            )
            out.append(
                ScoredCandidate(
                    provider=cand,
                    score=score,
                    components=comps,
                )
            )
        return out

    def _score_components(
        self,
        cand: CandidateProvider,
        model: str,
        *,
        max_cost: float,
        slo_ms: float,
    ) -> dict[str, float]:
        # Cost: cheaper → higher. With a single-candidate pool this
        # collapses to 1.0 (max_cost == cand.cost), which is the
        # neutral choice — there is nothing to compare against.
        cost = (
            1.0 - (cand.cost_per_image_cny / max_cost)
            if max_cost > 0
            else 1.0
        )

        success = float(self._metrics.success_rate(cand.provider_id, model))

        # Latency: defaults to neutral 1.0 when there's no data yet
        # (consistent with the "be optimistic" stance in metrics_engine).
        p50 = self._metrics.p50_ms(cand.provider_id, model)
        if p50 is None or slo_ms <= 0:
            latency = 1.0
        else:
            latency = 1.0 - min(1.0, p50 / slo_ms)

        # Load: 1 - utilisation. A maxed-out provider already failed
        # the hard filter, so utilisation < 1 here.
        live = self._metrics.current_concurrency(cand.provider_id)
        if cand.max_concurrency > 0:
            load = 1.0 - (live / cand.max_concurrency)
        else:
            load = 1.0

        # Freshness: 0 if just used, 1 if last-used was ≥ saturation.
        # The provider has never been used → maximum freshness, giving
        # new providers a real chance to enter the rotation.
        idle = self._metrics.seconds_since_last_use(cand.provider_id, model)
        if idle is None:
            freshness = 1.0
        else:
            freshness = min(1.0, idle / _FRESHNESS_SATURATION_SECONDS)

        return {
            "cost": _clamp01(cost),
            "success": _clamp01(success),
            "latency": _clamp01(latency),
            "load": _clamp01(load),
            "freshness": _clamp01(freshness),
        }

    # -- helpers ---------------------------------------------------------

    def _fallback_top_k(self) -> int:
        try:
            return int(self._scoring.fallback_top_k)
        except KeyError:
            return 3

    def _slo_ms_for_user(self, user: User) -> float:
        """Resolve an SLO target in ms for the user's tier.

        Tiers without a configured SLO (Free in the default seed) get
        the documented fallback. Tier rows that haven't been loaded
        into ``TierConfig`` yet also fall back rather than crashing.
        """
        try:
            spec = self._tier_config.get(user.tier)
        except KeyError:
            return float(_LATENCY_SLO_FALLBACK_MS)
        slo = spec.slo_p95_ms
        if slo is None or slo <= 0:
            return float(_LATENCY_SLO_FALLBACK_MS)
        return float(slo)


# ---------------------------------------------------------------------------
# Capability matching
# ---------------------------------------------------------------------------


# Map of NormalizedRequest fields → capability JSON keys for "list of
# allowed values" parameters. The capability JSON's value is the list
# of *permitted* values (per design doc §4.3); ``None`` request value
# is always considered compatible.
_LIST_PARAM_KEYS: tuple[tuple[str, str], ...] = (
    ("size", "size"),
    ("aspect_ratio", "aspect_ratio"),
    ("image_size", "image_size"),
    ("quality", "quality"),
    ("output_format", "output_format"),
    ("background", "background"),
    ("moderation", "moderation"),
    ("thinking_level", "thinking_level"),
)


def _capabilities_match(caps: Mapping[str, Any], request: NormalizedRequest) -> bool:
    """Return True iff ``request`` only uses values ``caps`` allows.

    Mirrors design doc §4.3:

    - List-valued capability keys: an explicit empty list ``[]`` means
      "not allowed at all". A list of values means "only these are
      allowed". A missing key (``None``) means "no opinion at this
      layer; fall back to the model's full set" — we accept anything
      in that case.
    - Numeric upper bounds (``n_max``, ``max_reference_images``,
      ``max_prompt_chars``, ``partial_images_max``).
    - Boolean opt-ins: if the user requests a feature the provider
      hasn't enabled, reject.
    """
    # 1. Discrete value lists.
    for req_field, cap_key in _LIST_PARAM_KEYS:
        cap_values = caps.get(cap_key)
        if cap_values is None:
            continue
        if not isinstance(cap_values, list):
            return False
        req_value = getattr(request, req_field, None)
        if req_value is None:
            continue
        if req_value not in cap_values:
            return False

    # 2. n_max
    n_max = caps.get("n_max")
    if isinstance(n_max, int) and request.n > n_max:
        return False

    # 3. Reference image cap.
    max_refs = caps.get("max_reference_images")
    if isinstance(max_refs, int) and len(request.references) > max_refs:
        return False

    # 4. Prompt length cap.
    max_chars = caps.get("max_prompt_chars")
    if isinstance(max_chars, int) and len(request.prompt) > max_chars:
        return False

    # 5. partial_images
    max_partial = caps.get("partial_images_max")
    if isinstance(max_partial, int) and request.partial_images > max_partial:
        return False

    # 6. Booleans: if user requests an opt-in feature, the cap must
    # explicitly say it's allowed (True). False / None reject.
    bool_features = (
        ("include_thoughts", "include_thoughts"),
        ("google_search", "google_search"),
        ("image_search", "image_search"),
        ("stream", "stream"),
    )
    for req_field, cap_key in bool_features:
        if getattr(request, req_field, False):
            cap_value = caps.get(cap_key)
            if cap_value is not True:
                return False

    # 7. Mask requires explicit support.
    if request.mask is not None and caps.get("supports_mask") is not True:
        return False

    # 8. background='transparent' requires explicit transparent support.
    if request.background == "transparent" and (
        caps.get("supports_transparent_bg") is not True
    ):
        return False

    return True


def _safe_load_caps(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        v = json.loads(raw)
        return v if isinstance(v, dict) else {}
    except (ValueError, TypeError):
        logger.warning("provider_models: undecodable capabilities_json")
        return {}


def _clamp01(x: float) -> float:
    if x < 0.0:
        return 0.0
    if x > 1.0:
        return 1.0
    return float(x)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------


_instance: ProviderSelector | None = None


def get_provider_selector() -> ProviderSelector:
    """Return the process-wide provider selector.

    Lightweight: stateless beyond its injected services, but exposed as
    a singleton for symmetry with the rest of ``app.domain``.
    """
    global _instance
    if _instance is None:
        _instance = ProviderSelector()
    return _instance


def reset_provider_selector_for_tests() -> None:
    global _instance
    _instance = None


# Re-export for the test layer that wants the underlying matcher.
__all__ = [
    "CandidateProvider",
    "ScoredCandidate",
    "ProviderSelector",
    "get_provider_selector",
    "reset_provider_selector_for_tests",
]
