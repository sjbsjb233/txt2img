"""Tests for ``app.domain.provider_ledger.ProviderLedger``.

Backed by a real SQLite-on-disk DB via ``initialized_db`` so the
balance arithmetic and ``billing_ledger`` inserts are exercised against
the live schema. We seed exactly one provider per test rather than
relying on the bootstrap seed (the seed creates none).
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.db.engine import get_session
from app.db.models import BillingLedger, Provider


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_provider(
    *,
    id_: str = "test_p",
    cost: float = 0.10,
    balance: float = 5.00,
    state: str = "healthy",
) -> None:
    async with get_session() as session:
        session.add(
            Provider(
                id=id_,
                label=id_.upper(),
                adapter_type="openai_v1",
                base_url="https://example.com",
                api_key_enc="v1:not-real-just-for-fk",
                cost_per_image_cny=cost,
                initial_balance_cny=balance,
                balance_cny=balance,
                circuit_state=state,
            )
        )


async def _balance(provider_id: str) -> float:
    async with get_session() as session:
        row = (
            await session.execute(select(Provider).where(Provider.id == provider_id))
        ).scalar_one()
    return float(row.balance_cny)


async def _circuit_state(provider_id: str) -> str:
    async with get_session() as session:
        row = (
            await session.execute(select(Provider).where(Provider.id == provider_id))
        ).scalar_one()
    return row.circuit_state


async def _ledger_rows(provider_id: str) -> list[BillingLedger]:
    async with get_session() as session:
        rows = (
            await session.execute(
                select(BillingLedger).where(
                    BillingLedger.provider_id == provider_id
                )
            )
        ).scalars().all()
    return list(rows)


# ---------------------------------------------------------------------------
# Deduct
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deduct_decrements_balance_and_writes_ledger(
    initialized_db: None,
) -> None:
    from app.domain.provider_ledger import get_provider_ledger

    await _make_provider(id_="p_one", cost=0.10, balance=5.0)
    ledger = get_provider_ledger()

    result = await ledger.deduct("p_one", "job_xyz", image_count=4)

    assert result.cost_cny == pytest.approx(0.40)
    assert result.balance_after == pytest.approx(4.60)
    assert await _balance("p_one") == pytest.approx(4.60)

    rows = await _ledger_rows("p_one")
    assert len(rows) == 1
    assert rows[0].job_id == "job_xyz"
    assert rows[0].image_count == 4
    assert rows[0].cost_cny == pytest.approx(0.40)


@pytest.mark.asyncio
async def test_deduct_zero_image_count_raises(initialized_db: None) -> None:
    from app.domain.provider_ledger import LedgerError, get_provider_ledger

    await _make_provider(id_="p_two")

    with pytest.raises(LedgerError):
        await get_provider_ledger().deduct("p_two", "j", image_count=0)


@pytest.mark.asyncio
async def test_deduct_unknown_provider_raises(initialized_db: None) -> None:
    from app.domain.provider_ledger import LedgerError, get_provider_ledger

    with pytest.raises(LedgerError):
        await get_provider_ledger().deduct("nope", "j", image_count=1)


@pytest.mark.asyncio
async def test_deduct_can_drive_balance_negative(initialized_db: None) -> None:
    """Deduct does NOT enforce a non-negative balance.

    The selector's hard-filter (PR-10) keeps providers with low balance
    out of the candidate pool; the ledger faithfully records what the
    executor decided to do. A negative balance after a race is exactly
    the kind of evidence admin needs to react.
    """
    from app.domain.provider_ledger import get_provider_ledger

    await _make_provider(id_="p_low", cost=2.0, balance=0.5)
    result = await get_provider_ledger().deduct("p_low", "j", image_count=1)
    assert result.balance_after == pytest.approx(-1.5)


@pytest.mark.asyncio
async def test_deduct_uses_caller_session_atomically(
    initialized_db: None,
) -> None:
    """When the caller passes a session, the deduct rides its transaction.

    We verify the rollback path: if the surrounding session aborts,
    neither the balance update nor the ledger row should be committed.
    """
    from app.db.engine import get_session_factory
    from app.domain.provider_ledger import get_provider_ledger

    await _make_provider(id_="p_atomic", cost=0.10, balance=2.0)
    factory = get_session_factory()

    session = factory()
    try:
        await get_provider_ledger().deduct(
            "p_atomic", "job_a", image_count=1, session=session
        )
        # Don't commit; explicitly roll back.
        await session.rollback()
    finally:
        await session.close()

    # Balance and ledger should be unchanged.
    assert await _balance("p_atomic") == pytest.approx(2.0)
    assert await _ledger_rows("p_atomic") == []


# ---------------------------------------------------------------------------
# Topup
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_topup_increments_balance(initialized_db: None) -> None:
    from app.domain.provider_ledger import get_provider_ledger

    await _make_provider(id_="p_top", balance=1.0)
    result = await get_provider_ledger().topup("p_top", 4.5)

    assert result.balance_after == pytest.approx(5.5)
    assert result.promoted_from_drained is False
    assert await _balance("p_top") == pytest.approx(5.5)


@pytest.mark.asyncio
async def test_topup_promotes_drained_to_healthy(initialized_db: None) -> None:
    from app.domain.provider_ledger import get_provider_ledger

    await _make_provider(id_="p_dr", balance=0.0, state="drained")
    result = await get_provider_ledger().topup("p_dr", 10.0)

    assert result.promoted_from_drained is True
    assert await _circuit_state("p_dr") == "healthy"


@pytest.mark.asyncio
async def test_topup_does_not_unsticky_open_circuit(
    initialized_db: None,
) -> None:
    """A topup must not paper over a fault-driven OPEN circuit."""
    from app.domain.provider_ledger import get_provider_ledger

    await _make_provider(id_="p_open", balance=1.0, state="open")
    result = await get_provider_ledger().topup("p_open", 5.0)

    assert result.promoted_from_drained is False
    assert await _circuit_state("p_open") == "open"


@pytest.mark.asyncio
async def test_topup_does_not_write_ledger_row(initialized_db: None) -> None:
    """Ledger only records charges (deductions), never credits."""
    from app.domain.provider_ledger import get_provider_ledger

    await _make_provider(id_="p_no_ledger", balance=0.0)
    await get_provider_ledger().topup("p_no_ledger", 5.0)
    assert await _ledger_rows("p_no_ledger") == []


@pytest.mark.asyncio
async def test_topup_zero_or_negative_raises(initialized_db: None) -> None:
    from app.domain.provider_ledger import LedgerError, get_provider_ledger

    await _make_provider(id_="p_z")

    with pytest.raises(LedgerError):
        await get_provider_ledger().topup("p_z", 0)
    with pytest.raises(LedgerError):
        await get_provider_ledger().topup("p_z", -1)


@pytest.mark.asyncio
async def test_topup_unknown_provider_raises(initialized_db: None) -> None:
    from app.domain.provider_ledger import LedgerError, get_provider_ledger

    with pytest.raises(LedgerError):
        await get_provider_ledger().topup("missing", 1.0)
