"""Tests for ``app.domain.metrics_engine.MetricsEngine``.

The engine is in-memory, so most tests don't need a DB. The
:func:`test_flush_provider_snapshot_writes_db` case is the exception —
that one verifies the periodic snapshot path and uses ``initialized_db``.

Time is injected via ``time_module.time`` monkeypatching in a couple of
tests; the engine reads ``_now()`` which delegates to ``time.time``,
giving us deterministic eviction without sleeping.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import select


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _engine(window_seconds: int = 300):
    from app.domain.metrics_engine import MetricsEngine

    return MetricsEngine(window_seconds=window_seconds)


# ---------------------------------------------------------------------------
# Recording + reads
# ---------------------------------------------------------------------------


def test_record_and_success_rate() -> None:
    m = _engine()
    for _ in range(8):
        m.record_call("p", "model", ok=True, latency_ms=100)
    for _ in range(2):
        m.record_call("p", "model", ok=False, latency_ms=80)
    assert m.success_rate("p", "model") == pytest.approx(0.8)


def test_success_rate_defaults_to_one_with_no_data() -> None:
    """Empty bucket = 1.0 so a fresh provider isn't penalised."""
    m = _engine()
    assert m.success_rate("p", "model") == 1.0


def test_p50_p95_ignore_failed_records() -> None:
    """Failed-call latencies do not get counted in percentiles.

    A 5xx that returns instantly would otherwise drag p50/p95 down
    misleadingly; the metric we actually care about is "how fast are
    successful generations".
    """
    m = _engine()
    for ms in (50, 100, 200, 400, 800):
        m.record_call("p", "m", ok=True, latency_ms=ms)
    # Add a fast failure that should not appear in percentiles.
    m.record_call("p", "m", ok=False, latency_ms=1)
    p50 = m.p50_ms("p", "m")
    p95 = m.p95_ms("p", "m")
    assert p50 == 200.0
    assert p95 == 800.0


def test_p50_p95_none_when_no_successes() -> None:
    m = _engine()
    m.record_call("p", "m", ok=False, latency_ms=10)
    assert m.p50_ms("p", "m") is None
    assert m.p95_ms("p", "m") is None


# ---------------------------------------------------------------------------
# Consecutive failures (used by the circuit breaker)
# ---------------------------------------------------------------------------


def test_consecutive_failures_counts_from_tail() -> None:
    m = _engine()
    m.record_call("p", "m", ok=True, latency_ms=10)
    m.record_call("p", "m", ok=False, latency_ms=10)
    m.record_call("p", "m", ok=False, latency_ms=10)
    m.record_call("p", "m", ok=False, latency_ms=10)
    assert m.consecutive_failures("p", "m") == 3


def test_consecutive_failures_resets_on_success() -> None:
    m = _engine()
    m.record_call("p", "m", ok=False, latency_ms=10)
    m.record_call("p", "m", ok=False, latency_ms=10)
    m.record_call("p", "m", ok=True, latency_ms=10)
    assert m.consecutive_failures("p", "m") == 0


# ---------------------------------------------------------------------------
# Window eviction
# ---------------------------------------------------------------------------


def test_eviction_drops_records_outside_window(monkeypatch: pytest.MonkeyPatch) -> None:
    """Records older than ``window_seconds`` are gone from every read."""
    from app.domain import metrics_engine as me

    fake_t = [1_000_000.0]
    monkeypatch.setattr(me, "_now", lambda: fake_t[0])

    m = _engine(window_seconds=60)
    m.record_call("p", "m", ok=True, latency_ms=100)
    m.record_call("p", "m", ok=False, latency_ms=200)

    # Advance time past the window — both records should evict on
    # the next read.
    fake_t[0] = 1_000_000.0 + 90.0
    assert m.success_rate("p", "m") == 1.0  # back to default
    assert m.p50_ms("p", "m") is None
    assert m.qps("p", "m") == 0.0


def test_recent_calls_in_60s_sums_across_models(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RPM is enforced at provider granularity per design doc §7.4."""
    from app.domain import metrics_engine as me

    fake_t = [1_000_000.0]
    monkeypatch.setattr(me, "_now", lambda: fake_t[0])

    m = _engine(window_seconds=300)
    m.record_call("p", "m1", ok=True, latency_ms=10)
    m.record_call("p", "m2", ok=True, latency_ms=10)
    m.record_call("p", "m1", ok=True, latency_ms=10)
    assert m.recent_calls_in_60s("p") == 3

    fake_t[0] = 1_000_000.0 + 70.0
    # All records older than 60s now.
    assert m.recent_calls_in_60s("p") == 0


def test_recent_calls_in_60s_by_provider_one_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bulk method returns the same per-provider count in one scan.

    The selector calls this once per ``select()``; the per-provider
    wrapper :meth:`recent_calls_in_60s` is now a thin lookup over the
    same dict, so the two paths must agree.
    """
    from app.domain import metrics_engine as me

    fake_t = [1_000_000.0]
    monkeypatch.setattr(me, "_now", lambda: fake_t[0])

    m = _engine(window_seconds=300)
    m.record_call("a", "m1", ok=True, latency_ms=10)
    m.record_call("a", "m2", ok=True, latency_ms=10)
    m.record_call("b", "m1", ok=True, latency_ms=10)

    bulk = m.recent_calls_in_60s_by_provider()
    assert bulk == {"a": 2, "b": 1}
    assert m.recent_calls_in_60s("a") == bulk["a"]
    assert m.recent_calls_in_60s("b") == bulk["b"]
    # Provider with no records doesn't appear in the dict.
    assert "c" not in bulk


def test_eviction_prunes_empty_buckets(monkeypatch: pytest.MonkeyPatch) -> None:
    """When the window fully drains a bucket, the dict entry is removed.

    Long-running processes shouldn't accumulate empty deques for
    providers/models that haven't been used recently. We assert on the
    private ``_records`` dict because there is no public observable
    for it.
    """
    from app.domain import metrics_engine as me

    fake_t = [1_000_000.0]
    monkeypatch.setattr(me, "_now", lambda: fake_t[0])

    m = _engine(window_seconds=60)
    m.record_call("p", "m", ok=True, latency_ms=10)
    assert ("p", "m") in m._records  # type: ignore[attr-defined]

    fake_t[0] = 1_000_000.0 + 90.0
    # Trigger a read that goes through ``_evict``; bucket should be cleaned.
    assert m.success_rate("p", "m") == 1.0  # default for empty
    assert ("p", "m") not in m._records  # type: ignore[attr-defined]
    assert ("p", "m") not in m._last_used  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Concurrency tracking
# ---------------------------------------------------------------------------


def test_lease_and_release_concurrency() -> None:
    m = _engine()
    assert m.current_concurrency("p") == 0
    assert m.lease_concurrency("p") == 1
    assert m.lease_concurrency("p") == 2
    assert m.current_concurrency("p") == 2
    assert m.release_concurrency("p") == 1
    assert m.release_concurrency("p") == 0
    assert m.release_concurrency("p") == 0  # clamped


# ---------------------------------------------------------------------------
# Freshness / last-used
# ---------------------------------------------------------------------------


def test_last_used_at_tracks_per_model(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.domain import metrics_engine as me

    fake_t = [100.0]
    monkeypatch.setattr(me, "_now", lambda: fake_t[0])

    m = _engine()
    assert m.last_used_at("p", "m") is None
    m.record_call("p", "m", ok=True, latency_ms=10)
    assert m.last_used_at("p", "m") == 100.0

    fake_t[0] = 250.0
    assert m.seconds_since_last_use("p", "m") == pytest.approx(150.0)


# ---------------------------------------------------------------------------
# Snapshot / DB flush
# ---------------------------------------------------------------------------


def test_snapshot_for_provider_per_model() -> None:
    m = _engine()
    m.record_call("p", "m1", ok=True, latency_ms=100)
    m.record_call("p", "m1", ok=True, latency_ms=200)
    m.record_call("p", "m2", ok=False, latency_ms=50)

    snap = m.snapshot_for_provider("p")
    assert set(snap.keys()) == {"m1", "m2"}
    assert snap["m1"]["success_rate"] == pytest.approx(1.0)
    assert snap["m2"]["success_rate"] == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_flush_provider_snapshot_writes_db(initialized_db: None) -> None:
    """End-to-end: snapshot serialises into ``providers.recent_calls_json``."""
    from app.db.engine import get_session
    from app.db.models import Provider
    from app.domain.metrics_engine import MetricsEngine

    async with get_session() as session:
        session.add(
            Provider(
                id="p_flush",
                label="P",
                adapter_type="openai_v1",
                base_url="https://example.com",
                api_key_enc="v1:fake",
                cost_per_image_cny=0.1,
                initial_balance_cny=1.0,
                balance_cny=1.0,
                circuit_state="healthy",
            )
        )

    m = MetricsEngine(window_seconds=300)
    m.record_call("p_flush", "model_a", ok=True, latency_ms=42)
    await m.flush_provider_snapshot("p_flush")

    async with get_session() as session:
        row = (
            await session.execute(
                select(Provider.recent_calls_json).where(Provider.id == "p_flush")
            )
        ).scalar_one()

    payload = json.loads(row)
    assert "model_a" in payload
    assert payload["model_a"]["success_rate"] == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_flush_provider_snapshot_missing_provider_noop(
    initialized_db: None,
) -> None:
    """Flushing a deleted provider must not crash the loop."""
    from app.domain.metrics_engine import MetricsEngine

    m = MetricsEngine(window_seconds=300)
    m.record_call("ghost", "model", ok=True, latency_ms=10)
    # No row exists; flush should silently match zero rows.
    await m.flush_provider_snapshot("ghost")


# ---------------------------------------------------------------------------
# Singleton behaviour
# ---------------------------------------------------------------------------


def test_singleton_is_lazy_and_resettable() -> None:
    from app.domain.metrics_engine import (
        get_metrics_engine,
        reset_metrics_engine_for_tests,
    )

    a = get_metrics_engine()
    b = get_metrics_engine()
    assert a is b
    reset_metrics_engine_for_tests()
    c = get_metrics_engine()
    assert c is not a
