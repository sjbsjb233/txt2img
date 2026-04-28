"""Per-(provider, model) sliding-window telemetry.

Implements design doc §7.4 / §15.3 ``MetricsEngine``. Every adapter call
the executor makes lands here as a ``CallRecord``; the selector reads
back ``success_rate``, ``p50_ms``, ``p95_ms``, ``qps`` and
``recent_calls_in_60s`` to feed its score function. The circuit breaker
also reads success / failure counts from the same windowed view.

Design choices
--------------

- **In-memory ring buffer.** A ``deque[CallRecord]`` per
  ``(provider_id, model_id)`` keyed dict. Records older than
  ``window_seconds`` are evicted lazily on every read/write. The window
  is provider-scoring-config driven (default 300s) and is read live so
  an admin who shrinks the window sees it take effect immediately on
  the next eviction.
- **No durable storage of individual records.** The window is a
  process-local view; restart loses it. The
  :meth:`snapshot_for_provider` helper periodically materialises a
  compact summary into ``providers.recent_calls_json`` so admin lists
  can show recent activity even on a freshly restarted process — that
  is the only durability promise.
- **Concurrency + last-used tracking.** Live ``current_concurrency``
  per provider plus ``last_used_at`` per ``(provider, model)`` belong
  next to the call window because they share the same lifetime: they
  exist while the process is up and reset on restart. Putting them
  here lets the selector talk to a single source of truth for runtime
  state instead of plumbing two dependencies.
- **Lock-free reads.** Reads from a ``deque`` with ``len(...)``,
  iteration and ``popleft`` happen on the asyncio event loop's single
  thread; no locking is needed. ``record_call`` is also single-threaded.
  If we ever multi-thread this, swap the deques for ``threading.Lock``-
  guarded structures.

Default behaviour with no data
------------------------------
The selector's score expression treats "no data" optimistically:
``success_rate`` returns 1.0 and ``p50_ms`` returns ``None`` so the
selector can fall back to a neutral latency contribution. Punishing a
freshly added provider for having no history would defeat the
``freshness`` term that exists exactly to give new providers a chance.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Iterable

from sqlalchemy import update

from app.db.engine import get_session
from app.db.models import Provider
from app.domain.runtime_configs import ProviderScoringConfig
from app.schemas.normalized import StandardErrorKind

logger = logging.getLogger("txt2img.metrics")


# Default window when ConfigCenter is not loaded yet (e.g. tests). The
# real value comes from ``provider_scoring.metric_window_seconds``.
_DEFAULT_WINDOW_SECONDS = 300

# Cap on the number of records we keep per (provider, model) bucket as a
# defensive measure: a misbehaving caller spamming record_call cannot
# exhaust memory because the deque is bounded. The cap is comfortably
# above the realistic per-window volume (RPS × window).
_MAX_RECORDS_PER_BUCKET = 50_000


@dataclass(frozen=True)
class CallRecord:
    """A single observation appended to the rolling window.

    Latencies are stored as floats (milliseconds). For failed calls the
    latency value still reflects how long the upstream took before
    erroring; we explicitly exclude failed records from latency
    percentiles so a fast 500 doesn't pretend to be a fast 200.
    """

    ts: float  # unix seconds, time.monotonic-aligned via ``_now``
    ok: bool
    latency_ms: float
    error_kind: str | None = None


# ---------------------------------------------------------------------------
# Time source
# ---------------------------------------------------------------------------


def _now() -> float:
    """Wall-clock seconds. Tests monkeypatch this for determinism.

    Wall-clock (``time.time``) over ``time.monotonic`` is intentional:
    record timestamps need to be comparable across long-lived process
    runs so ``snapshot_for_provider`` can serialise human-readable
    times. Drift from NTP corrections is tolerable here — the deltas
    we care about are seconds-scale.
    """
    return time.time()


# ---------------------------------------------------------------------------
# MetricsEngine
# ---------------------------------------------------------------------------


class MetricsEngine:
    """Process-wide rolling-window statistics.

    All public methods are sync — they manipulate Python data structures
    on the event loop thread without I/O. Async-flavoured snapshot
    helpers exist as ``async def`` only because they touch the DB.
    """

    def __init__(
        self,
        *,
        window_seconds: int | None = None,
        scoring_config: ProviderScoringConfig | None = None,
    ) -> None:
        self._fixed_window = window_seconds
        self._scoring = scoring_config
        # (provider_id, model_id) → deque[CallRecord]
        self._records: dict[tuple[str, str], deque[CallRecord]] = defaultdict(
            deque
        )
        # provider_id → live concurrency (incremented/decremented by the
        # executor on each upstream call).
        self._concurrency: dict[str, int] = defaultdict(int)
        # (provider_id, model_id) → wall-clock seconds of last attempt,
        # for the freshness term in scoring.
        self._last_used: dict[tuple[str, str], float] = {}

    # -- window resolution ------------------------------------------------

    def window_seconds(self) -> int:
        """Resolve the active window size.

        Tests pass an explicit ``window_seconds`` to the constructor for
        determinism. Production reads it from ``ProviderScoringConfig``
        every call so admin edits propagate without restart. If the
        config is unavailable (e.g. very early in a test fixture) we
        fall back to the documented default — we'd rather operate on a
        sensible window than crash.
        """
        if self._fixed_window is not None:
            return self._fixed_window
        try:
            cfg = self._scoring or ProviderScoringConfig()
            return cfg.metric_window_seconds
        except KeyError:
            return _DEFAULT_WINDOW_SECONDS

    # -- writes -----------------------------------------------------------

    def record_call(
        self,
        provider_id: str,
        model: str,
        *,
        ok: bool,
        latency_ms: float,
        error_kind: StandardErrorKind | str | None = None,
    ) -> None:
        """Append one observation to the per-(provider, model) window.

        Eviction of old records is amortised here: every append calls
        ``_evict`` so the deque stays bounded. ``error_kind`` may be
        either an enum or a raw string; we store the string form.
        """
        now = _now()
        if isinstance(error_kind, StandardErrorKind):
            kind_str: str | None = error_kind.value
        else:
            kind_str = error_kind
        key = (provider_id, model)
        deq = self._records[key]
        deq.append(
            CallRecord(
                ts=now,
                ok=ok,
                latency_ms=float(latency_ms),
                error_kind=kind_str,
            )
        )
        self._last_used[key] = now
        # Evict old after appending so the deque cannot grow unbounded
        # over a long-running process.
        self._evict(deq, now=now)
        # Hard cap defensive trim: extreme call rates never accumulate
        # past _MAX_RECORDS_PER_BUCKET regardless of window.
        while len(deq) > _MAX_RECORDS_PER_BUCKET:
            deq.popleft()

    # -- concurrency lease (used by PR-11 executor) -----------------------

    def lease_concurrency(self, provider_id: str) -> int:
        """Increment and return the live concurrency for ``provider_id``.

        Thread-safety: single-thread asyncio. The executor takes one
        lease before issuing the upstream call and releases it in a
        ``finally`` block. The selector's hard filter reads
        :meth:`current_concurrency` to enforce ``max_concurrency`` per
        design doc §7.4.
        """
        self._concurrency[provider_id] += 1
        return self._concurrency[provider_id]

    def release_concurrency(self, provider_id: str) -> int:
        """Decrement and return the live concurrency, clamped at zero.

        Clamping is purely defensive: a balanced executor never under-
        flows, but a buggy ``finally`` could.
        """
        current = self._concurrency.get(provider_id, 0)
        if current <= 0:
            self._concurrency[provider_id] = 0
            return 0
        self._concurrency[provider_id] = current - 1
        return self._concurrency[provider_id]

    def current_concurrency(self, provider_id: str) -> int:
        return int(self._concurrency.get(provider_id, 0))

    # -- reads ------------------------------------------------------------

    def success_rate(self, provider_id: str, model: str) -> float:
        """Return the fraction of successful calls in the window.

        Optimistic default of 1.0 when the window is empty; design doc
        §7.4 freshness rationale: a brand-new provider should not be
        penalised on a metric it has had no opportunity to populate.
        """
        deq = self._records.get((provider_id, model))
        if not deq:
            return 1.0
        self._evict(deq)
        if not deq:
            return 1.0
        ok = sum(1 for r in deq if r.ok)
        return ok / len(deq)

    def consecutive_failures(self, provider_id: str, model: str) -> int:
        """Count successive failures from the right end of the window.

        The circuit breaker uses this to decide HEALTHY → OPEN. We walk
        the deque from the newest record backwards, counting until we
        hit either a success or the window's left edge. A return value
        of N means "the last N calls (and only those) failed".
        """
        deq = self._records.get((provider_id, model))
        if not deq:
            return 0
        self._evict(deq)
        n = 0
        for rec in reversed(deq):
            if rec.ok:
                break
            n += 1
        return n

    def p50_ms(self, provider_id: str, model: str) -> float | None:
        return self._percentile(provider_id, model, 0.50)

    def p95_ms(self, provider_id: str, model: str) -> float | None:
        return self._percentile(provider_id, model, 0.95)

    def qps(self, provider_id: str, model: str) -> float:
        deq = self._records.get((provider_id, model))
        if not deq:
            return 0.0
        self._evict(deq)
        if not deq:
            return 0.0
        return len(deq) / max(1.0, float(self.window_seconds()))

    def recent_calls_in_60s(self, provider_id: str) -> int:
        """Count records across all models for one provider in the last 60s.

        The selector compares this against ``providers.rpm_limit`` (per
        design doc §7.4 step "RPM 未超") so the unit must match — RPM
        is calls per *minute*. We sum across models because the limit
        applies at the provider level.
        """
        now = _now()
        cutoff = now - 60.0
        n = 0
        for (pid, _model), deq in self._records.items():
            if pid != provider_id:
                continue
            self._evict(deq, now=now)
            for rec in deq:
                if rec.ts >= cutoff:
                    n += 1
        return n

    def last_used_at(self, provider_id: str, model: str) -> float | None:
        """Wall-clock seconds of the most recent attempt, or ``None``."""
        return self._last_used.get((provider_id, model))

    def seconds_since_last_use(
        self, provider_id: str, model: str
    ) -> float | None:
        ts = self.last_used_at(provider_id, model)
        if ts is None:
            return None
        return max(0.0, _now() - ts)

    # -- snapshot for admin DB column ------------------------------------

    def snapshot_for_provider(
        self, provider_id: str
    ) -> dict[str, dict[str, float | int | None]]:
        """Return a per-model summary for ``providers.recent_calls_json``.

        The shape is intentionally compact: admin views render a few
        numbers per model, no need for the per-record detail. The
        method is sync because it does not touch I/O; persistence is
        :meth:`flush_provider_snapshot` so the call site can choose
        whether to await.
        """
        models: set[str] = set()
        for pid, model in self._records.keys():
            if pid == provider_id:
                models.add(model)

        out: dict[str, dict[str, float | int | None]] = {}
        for model in models:
            out[model] = {
                "calls": int(self.qps(provider_id, model) * self.window_seconds()),
                "success_rate": float(self.success_rate(provider_id, model)),
                "p50_ms": self.p50_ms(provider_id, model),
                "p95_ms": self.p95_ms(provider_id, model),
                "current_concurrency": self.current_concurrency(provider_id),
            }
        return out

    async def flush_provider_snapshot(self, provider_id: str) -> None:
        """Persist :meth:`snapshot_for_provider` into ``providers.recent_calls_json``.

        Best-effort — a missing provider row (deleted while we were
        snapshotting) silently no-ops. We don't want a flush sweep to
        crash because admin tidied up a provider mid-loop.
        """
        snap = self.snapshot_for_provider(provider_id)
        payload = json.dumps(snap, separators=(",", ":"), ensure_ascii=False)
        async with get_session() as session:
            await session.execute(
                update(Provider)
                .where(Provider.id == provider_id)
                .values(recent_calls_json=payload)
            )

    # -- maintenance ------------------------------------------------------

    def reset(self) -> None:
        """Wipe all in-memory state. Test convenience."""
        self._records.clear()
        self._concurrency.clear()
        self._last_used.clear()

    # -- internals --------------------------------------------------------

    def _percentile(
        self, provider_id: str, model: str, q: float
    ) -> float | None:
        deq = self._records.get((provider_id, model))
        if not deq:
            return None
        self._evict(deq)
        # Latency percentiles only make sense over successful calls;
        # a 50ms 5xx skews "fast latency" downward in a misleading way.
        latencies = sorted(r.latency_ms for r in deq if r.ok)
        if not latencies:
            return None
        # Nearest-rank percentile (no interpolation): for tiny windows
        # interpolation just adds noise.
        idx = int(round(q * (len(latencies) - 1)))
        idx = max(0, min(idx, len(latencies) - 1))
        return latencies[idx]

    def _evict(
        self,
        deq: deque[CallRecord],
        *,
        now: float | None = None,
    ) -> None:
        """Drop records older than the window from ``deq`` in place."""
        if not deq:
            return
        cutoff = (now if now is not None else _now()) - self.window_seconds()
        while deq and deq[0].ts < cutoff:
            deq.popleft()


# ---------------------------------------------------------------------------
# Background snapshot loop
# ---------------------------------------------------------------------------


async def run_metrics_snapshot_loop(
    metrics: "MetricsEngine",
    provider_ids_provider: "ProviderIdsProvider",
    *,
    interval_seconds: int = 60,
) -> None:
    """Periodically flush per-provider snapshots into ``providers.recent_calls_json``.

    Started by ``app.main`` lifespan in PR-11 (when the executor is
    online and metrics actually move). Stays running until cancelled;
    transient DB errors are logged and ignored — a missed flush is
    cheap to recover from on the next cycle.
    """
    while True:
        try:
            ids = await provider_ids_provider()
            for pid in ids:
                try:
                    await metrics.flush_provider_snapshot(pid)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception(
                        "metrics: failed to flush snapshot for %s", pid
                    )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("metrics snapshot loop iteration failed")
        await asyncio.sleep(interval_seconds)


# Type alias used purely for documentation in the loop signature.
class ProviderIdsProvider:
    """Async callable returning the set of provider ids to snapshot."""

    async def __call__(self) -> Iterable[str]:  # pragma: no cover - protocol
        ...


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------


_instance: MetricsEngine | None = None


def get_metrics_engine() -> MetricsEngine:
    """Return the process-wide MetricsEngine.

    Mirrors the singleton pattern used by ProviderLedger / QuotaGuard
    so the executor (PR-11) and admin views (PR-16) can share one
    in-memory window without DI plumbing.
    """
    global _instance
    if _instance is None:
        _instance = MetricsEngine()
    return _instance


def reset_metrics_engine_for_tests() -> None:
    """Drop the singleton between tests."""
    global _instance
    _instance = None
