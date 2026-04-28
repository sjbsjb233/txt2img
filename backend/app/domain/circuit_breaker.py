"""Per-provider circuit breaker.

Implements the state machine in design doc §7.5: ``HEALTHY → OPEN →
HALF_OPEN → HEALTHY`` (with exponential cooldown backoff and balance-
driven ``DRAINED``). The breaker is the single owner of every
``providers.circuit_state`` transition; the provider selector reads
state but never mutates it, and the executor only calls back through
``observe`` after each upstream attempt.

Transitions (entry → exit conditions are also enumerated in the design
doc table):

* ``HEALTHY → OPEN``  when consecutive failures ≥ ``failure_threshold``.
* ``OPEN → HALF_OPEN`` automatically on ``acquire_probe`` once the
  cooldown timestamp has passed. We don't run a polling loop; the
  selector calls in to see if a probe slot is available, and the
  breaker promotes lazily.
* ``HALF_OPEN → HEALTHY``  on a successful probe.
* ``HALF_OPEN → OPEN``     on a failed probe; cooldown doubles
  (capped at ``max_cooldown_seconds``).
* ``HEALTHY → DRAINED``   when the selector / ledger reports balance
  below threshold (``mark_drained``). Stays drained until a topup or
  admin reset; we never auto-promote out of DRAINED here because the
  topup path is explicitly the recovery surface.
* ``* → DISABLED``        admin only; the breaker treats it as terminal
  for routing purposes (``is_callable`` returns False).

In-memory state vs DB
---------------------
The DB row is the source of truth across restarts: ``circuit_state``
plus ``cooldown_until`` survive a process bounce. To avoid hitting the
DB on every executor call, the breaker maintains an in-memory mirror
keyed by provider id. The mirror is hydrated lazily on first reference
(``_load_state``) and refreshed when admin actions reach the breaker
(``mark_drained``, ``reset_to_healthy``, ``admin_disable``). The mirror
is the only thing the hot path touches.

Probe concurrency
-----------------
HALF_OPEN allows exactly ``half_open_probe_concurrency`` probes (default
1). Each provider has its own ``asyncio.Semaphore`` so two providers
can probe in parallel without contention. ``acquire_probe`` is an
async context manager: ``async with breaker.acquire_probe(pid) as ok:``
yields ``True`` if the caller may probe and is responsible for calling
``observe`` afterward; ``False`` means the slot is taken and the
caller should treat the provider as unavailable for now.
"""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import AsyncIterator

from sqlalchemy import select, update

from app.db.engine import get_session
from app.db.models import Provider
from app.domain.metrics_engine import MetricsEngine, get_metrics_engine
from app.domain.runtime_configs import CircuitBreakerConfig

logger = logging.getLogger("txt2img.breaker")


# Circuit states. Stored in ``providers.circuit_state``; values match
# the CHECK constraint on the column.
HEALTHY = "healthy"
OPEN = "open"
HALF_OPEN = "half_open"
DRAINED = "drained"
DISABLED = "disabled"


# ---------------------------------------------------------------------------
# In-memory state per provider
# ---------------------------------------------------------------------------


@dataclass
class _BreakerState:
    """Live mirror of one provider's breaker fields.

    ``current_cooldown_seconds`` doubles on each successive ``OPEN``
    re-entry, capped at the configured maximum. It resets to the
    initial value when the breaker enters ``HEALTHY``.

    ``probes_inflight`` counts how many HALF_OPEN probes are running
    concurrently. The cap from
    ``circuit_breaker.half_open_probe_concurrency`` (default 1) is
    enforced under the per-provider lock so two callers can't both
    decide there's a free slot.
    """

    state: str = HEALTHY
    consecutive_failures: int = 0
    cooldown_until: float | None = None  # wall-clock seconds; None = no cooldown
    current_cooldown_seconds: int = 0
    probes_inflight: int = 0


# ---------------------------------------------------------------------------
# CircuitBreaker
# ---------------------------------------------------------------------------


def _now() -> float:
    """Wall-clock seconds. Tests monkeypatch via the ``time_source`` ctor arg."""
    return time.time()


class CircuitBreaker:
    """Drives ``providers.circuit_state`` transitions.

    The breaker is the only writer of ``circuit_state`` /
    ``cooldown_until``; everything else (executor, selector, admin)
    calls into it. Centralising writes keeps the state machine
    consistent and lets us emit a single audit log line per transition
    later if needed.
    """

    def __init__(
        self,
        metrics: MetricsEngine | None = None,
        *,
        config: CircuitBreakerConfig | None = None,
        time_source=None,
    ) -> None:
        self._metrics = metrics or get_metrics_engine()
        self._config = config or CircuitBreakerConfig()
        self._states: dict[str, _BreakerState] = {}
        # Per-provider lock guarding the state struct. We avoid one
        # global lock so two providers can transition independently.
        self._locks: dict[str, asyncio.Lock] = {}
        # When set in tests for determinism. Otherwise wall-clock.
        self._time = time_source or _now

    # -- config helpers ---------------------------------------------------

    def _failure_threshold(self) -> int:
        try:
            return int(self._config.failure_threshold)
        except KeyError:
            return 5

    def _initial_cooldown(self) -> int:
        try:
            return int(self._config.initial_cooldown_seconds)
        except KeyError:
            return 30

    def _max_cooldown(self) -> int:
        try:
            return int(self._config.max_cooldown_seconds)
        except KeyError:
            return 600

    def _probe_concurrency(self) -> int:
        try:
            return int(self._config.half_open_probe_concurrency)
        except KeyError:
            return 1

    # -- state hydration --------------------------------------------------

    def _lock_for(self, provider_id: str) -> asyncio.Lock:
        lock = self._locks.get(provider_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[provider_id] = lock
        return lock

    async def _load_state(self, provider_id: str) -> _BreakerState:
        """Populate the in-memory mirror from the DB.

        Called the first time a provider is referenced. Subsequent
        operations stay in memory. If the provider has been deleted in
        the DB we return a default HEALTHY state — the selector will
        skip the provider for other reasons (it's not in the candidate
        pool).
        """
        cached = self._states.get(provider_id)
        if cached is not None:
            return cached

        async with get_session() as session:
            row = (
                await session.execute(
                    select(
                        Provider.circuit_state, Provider.cooldown_until
                    ).where(Provider.id == provider_id)
                )
            ).one_or_none()

        if row is None:
            # Unknown provider — return a default mirror but DON'T
            # cache it so a subsequent ``provider_create`` is observed
            # cleanly the next call.
            return _BreakerState()

        state, cooldown_until = row
        cd_ts: float | None = None
        if cooldown_until is not None:
            # SQLite returns naive datetimes; treat as UTC.
            if cooldown_until.tzinfo is None:
                cooldown_until = cooldown_until.replace(tzinfo=timezone.utc)
            cd_ts = cooldown_until.timestamp()

        st = _BreakerState(
            state=state or HEALTHY,
            consecutive_failures=0,
            cooldown_until=cd_ts,
            current_cooldown_seconds=self._initial_cooldown(),
        )
        self._states[provider_id] = st
        return st

    # -- public read API --------------------------------------------------

    async def get_state(self, provider_id: str) -> str:
        """Return the current breaker state for ``provider_id``.

        Promotes ``OPEN`` to ``HALF_OPEN`` lazily when the cooldown has
        expired. The promotion is in-memory only; the DB row flips on
        the first :meth:`acquire_probe`. This deferral keeps the read
        path cheap.
        """
        st = await self._load_state(provider_id)
        if (
            st.state == OPEN
            and st.cooldown_until is not None
            and self._time() >= st.cooldown_until
        ):
            return HALF_OPEN
        return st.state

    async def is_callable(self, provider_id: str) -> bool:
        """True when the provider may be issued a real (non-probe) call.

        ``HALF_OPEN`` does NOT count as callable here — only the probe
        path may use that provider, via :meth:`acquire_probe`. The
        selector calls this in its hard filter.
        """
        return await self.get_state(provider_id) == HEALTHY

    # -- observation (hot path) -------------------------------------------

    async def observe(self, provider_id: str, *, success: bool) -> str:
        """Record the outcome of one upstream call and update the state.

        Returns the post-observation state (one of HEALTHY / OPEN /
        HALF_OPEN / DRAINED / DISABLED). Callers don't need to inspect
        it; they call observe in a try/finally and trust the breaker to
        drive the state machine.

        Transitions:
          HEALTHY + success          → HEALTHY (reset failure counter)
          HEALTHY + failure (× N)    → OPEN with initial cooldown
          OPEN     + anything        → no change here; cooldown drives
                                       promotion to HALF_OPEN lazily on
                                       the next ``acquire_probe`` /
                                       ``get_state`` read
          HALF_OPEN+ success         → HEALTHY (reset cooldown)
          HALF_OPEN+ failure         → OPEN with doubled cooldown
        """
        async with self._lock_for(provider_id):
            st = await self._load_state(provider_id)

            if st.state == DISABLED or st.state == DRAINED:
                # Admin / balance state machine; outcomes don't change
                # routing decisions here.
                return st.state

            if st.state == HALF_OPEN:
                if success:
                    return await self._transition_to_healthy(provider_id, st)
                return await self._transition_to_open(
                    provider_id, st, doubled=True
                )

            if st.state == OPEN:
                # Caller should not have issued a real call. Tolerate it
                # but ignore the observation as far as failure counting.
                logger.warning(
                    "breaker: observe() while OPEN for %s (success=%s)",
                    provider_id,
                    success,
                )
                return st.state

            # HEALTHY
            if success:
                st.consecutive_failures = 0
                return HEALTHY

            st.consecutive_failures += 1
            threshold = self._failure_threshold()
            if st.consecutive_failures >= threshold:
                return await self._transition_to_open(
                    provider_id, st, doubled=False
                )
            return HEALTHY

    # -- probe -----------------------------------------------------------

    @asynccontextmanager
    async def acquire_probe(
        self, provider_id: str
    ) -> AsyncIterator[bool]:
        """Atomic gate for a HALF_OPEN probe call.

        Yields ``True`` if the caller acquired the probe slot (and must
        therefore issue exactly one upstream call and report the
        outcome via :meth:`observe`). Yields ``False`` when:

        * The provider is not in HALF_OPEN — wrong state to probe.
        * The probe-concurrency cap is already reached.

        The probe slot is released on context exit regardless of
        outcome. The state transition is :meth:`observe`'s job; this
        method only manages the probe slot count.
        """
        acquired = False
        async with self._lock_for(provider_id):
            st = await self._load_state(provider_id)
            # Lazy OPEN→HALF_OPEN promotion at probe time. Persist to
            # the DB here so the admin view sees the current state
            # without waiting for the first observation.
            if (
                st.state == OPEN
                and st.cooldown_until is not None
                and self._time() >= st.cooldown_until
            ):
                st.state = HALF_OPEN
                # Persist HALF_OPEN but keep the original cooldown
                # timestamp on the row. The caller's view (admin UI)
                # then sees "half_open since X" without losing the
                # transition timestamp.
                await self._persist_state(
                    provider_id,
                    HALF_OPEN,
                    datetime.fromtimestamp(
                        st.cooldown_until, tz=timezone.utc
                    ),
                )

            if st.state == HALF_OPEN and (
                st.probes_inflight < self._probe_concurrency()
            ):
                st.probes_inflight += 1
                acquired = True

        try:
            yield acquired
        finally:
            if acquired:
                async with self._lock_for(provider_id):
                    st = self._states.get(provider_id)
                    if st is not None and st.probes_inflight > 0:
                        st.probes_inflight -= 1

    # -- balance-driven transitions ---------------------------------------

    async def mark_drained(self, provider_id: str) -> str:
        """Force the provider into ``DRAINED`` state.

        Called by the selector's hard filter when balance drops below
        ``provider_filter.balance_min_threshold``. Idempotent: a
        provider already in ``DRAINED`` (or ``DISABLED``) returns
        unchanged. Calling on an ``OPEN`` provider keeps the original
        ``OPEN`` state — fault state is more informative for admin
        than balance state.
        """
        async with self._lock_for(provider_id):
            st = await self._load_state(provider_id)
            if st.state in (DRAINED, DISABLED, OPEN, HALF_OPEN):
                return st.state
            st.state = DRAINED
            st.consecutive_failures = 0
            st.cooldown_until = None
            await self._persist_state(provider_id, DRAINED, None)
            logger.info("breaker: %s → DRAINED", provider_id)
            return DRAINED

    async def reset_to_healthy(self, provider_id: str) -> str:
        """Admin/topup-driven hard reset to HEALTHY.

        Used by the topup path (after ledger promotes balance) and by
        the admin "reset-circuit" endpoint (PR-16). Cooldown counter
        also resets so the next OPEN starts fresh at
        ``initial_cooldown_seconds``.
        """
        async with self._lock_for(provider_id):
            st = await self._load_state(provider_id)
            st.state = HEALTHY
            st.consecutive_failures = 0
            st.cooldown_until = None
            st.current_cooldown_seconds = self._initial_cooldown()
            await self._persist_state(provider_id, HEALTHY, None)
            logger.info("breaker: %s reset to HEALTHY", provider_id)
            return HEALTHY

    async def admin_disable(self, provider_id: str) -> str:
        async with self._lock_for(provider_id):
            st = await self._load_state(provider_id)
            st.state = DISABLED
            st.consecutive_failures = 0
            st.cooldown_until = None
            await self._persist_state(provider_id, DISABLED, None)
            return DISABLED

    # -- internal transitions ---------------------------------------------

    async def _transition_to_open(
        self,
        provider_id: str,
        st: _BreakerState,
        *,
        doubled: bool,
    ) -> str:
        """Move state into OPEN; if ``doubled`` is True, double the cooldown.

        ``doubled`` is True on a HALF_OPEN → OPEN re-trip and False on
        a HEALTHY → OPEN first trip. Capped at ``max_cooldown_seconds``.
        """
        if doubled:
            new_cd = min(
                self._max_cooldown(),
                max(
                    self._initial_cooldown(),
                    st.current_cooldown_seconds * 2,
                ),
            )
        else:
            new_cd = self._initial_cooldown()
        st.current_cooldown_seconds = new_cd
        st.state = OPEN
        st.consecutive_failures = 0  # reset; we'll count after re-entry
        st.cooldown_until = self._time() + new_cd
        await self._persist_state(
            provider_id,
            OPEN,
            datetime.fromtimestamp(st.cooldown_until, tz=timezone.utc),
        )
        logger.info(
            "breaker: %s → OPEN cooldown=%ds (doubled=%s)",
            provider_id,
            new_cd,
            doubled,
        )
        return OPEN

    async def _transition_to_healthy(
        self, provider_id: str, st: _BreakerState
    ) -> str:
        st.state = HEALTHY
        st.consecutive_failures = 0
        st.cooldown_until = None
        st.current_cooldown_seconds = self._initial_cooldown()
        await self._persist_state(provider_id, HEALTHY, None)
        logger.info("breaker: %s → HEALTHY", provider_id)
        return HEALTHY

    async def _persist_state(
        self,
        provider_id: str,
        state: str,
        cooldown_until: datetime | None,
    ) -> None:
        """Write circuit_state + cooldown_until to the DB."""
        async with get_session() as session:
            await session.execute(
                update(Provider)
                .where(Provider.id == provider_id)
                .values(
                    circuit_state=state,
                    cooldown_until=cooldown_until,
                    updated_at=datetime.now(timezone.utc),
                )
            )

    # -- maintenance ------------------------------------------------------

    def reset(self) -> None:
        """Drop all in-memory state. Test convenience."""
        self._states.clear()
        self._locks.clear()


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------


_instance: CircuitBreaker | None = None


def get_circuit_breaker() -> CircuitBreaker:
    global _instance
    if _instance is None:
        _instance = CircuitBreaker()
    return _instance


def reset_circuit_breaker_for_tests() -> None:
    global _instance
    _instance = None
