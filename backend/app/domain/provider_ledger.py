"""Provider balance accounting.

Two write paths land here, both atomic with respect to ``providers``:

- ``deduct(provider_id, job_id, image_count)`` — called by the executor
  on a *successful* upstream call. Decrements ``providers.balance_cny``
  by ``cost_per_image_cny * image_count`` and appends one row to
  ``billing_ledger`` for the ledger-of-truth.
- ``topup(provider_id, amount)`` — called by the admin "topup" endpoint.
  Increments ``providers.balance_cny`` and, if the provider was in
  ``DRAINED`` (i.e. starved of balance), promotes it back to ``healthy``.

Design notes (per design doc §4.5 / §13.4):

- Failed upstream calls do *not* land here. The executor decides what's
  a success; this module just records the consequence.
- Topups are deliberately *not* journalled into ``billing_ledger`` —
  that table is the deduction history. Crediting via the ledger would
  conflate "money moved out" with "balance corrected" and complicate
  per-provider COGS reports. The provider row's ``balance_cny`` plus the
  initial-balance baseline is enough.
- All writes go through a single ``AsyncSession`` per call so the
  balance update and the ledger insert (when applicable) commit
  atomically. A torn write here would mean a deduction shows in the
  ledger but not the balance (or vice-versa), which is exactly what
  this module exists to prevent.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.engine import get_session
from app.db.models import BillingLedger, Provider

logger = logging.getLogger("txt2img.ledger")


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DeductionResult:
    """Outcome of a single ``deduct`` call.

    ``balance_after`` is convenient for callers that want to log the new
    balance without re-querying. ``cost_cny`` is the *deducted* amount, not
    the cumulative cost — the ledger table holds the running history.
    """

    provider_id: str
    cost_cny: float
    image_count: int
    balance_after: float


@dataclass(frozen=True)
class TopupResult:
    provider_id: str
    amount_cny: float
    balance_after: float
    promoted_from_drained: bool


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class LedgerError(RuntimeError):
    """Raised when a ledger op cannot proceed (missing provider, bad input)."""


# ---------------------------------------------------------------------------
# Implementation
# ---------------------------------------------------------------------------


class ProviderLedger:
    """Stateless service object — no caches, no per-instance fields.

    Kept as a class (rather than module-level functions) so PR-11's
    executor can inject a mock for tests and so future enhancements
    (rate-limited topup, balance alerts) have a natural home.
    """

    async def deduct(
        self,
        provider_id: str,
        job_id: str,
        image_count: int,
        *,
        session: AsyncSession | None = None,
    ) -> DeductionResult:
        """Charge ``image_count`` images against ``provider_id``.

        The cost is computed from the provider's stored
        ``cost_per_image_cny`` × ``image_count``. Negative or zero counts
        raise ``LedgerError`` — the caller has a bug if they reach this
        with zero successful images.

        Caller-managed sessions: when the executor already holds a
        session for the surrounding job-finalisation transaction it can
        pass it in. Otherwise we open and commit our own.
        """
        if image_count <= 0:
            raise LedgerError(
                f"deduct() requires image_count > 0, got {image_count}"
            )

        if session is None:
            async with get_session() as new_session:
                return await self._deduct_in_session(
                    new_session, provider_id, job_id, image_count
                )
        return await self._deduct_in_session(
            session, provider_id, job_id, image_count
        )

    async def _deduct_in_session(
        self,
        session: AsyncSession,
        provider_id: str,
        job_id: str,
        image_count: int,
    ) -> DeductionResult:
        provider = (
            await session.execute(select(Provider).where(Provider.id == provider_id))
        ).scalar_one_or_none()
        if provider is None:
            raise LedgerError(f"provider {provider_id!r} not found")

        cost = float(provider.cost_per_image_cny) * float(image_count)
        provider.balance_cny = float(provider.balance_cny) - cost

        # Round to a sane precision so float drift does not produce
        # "1.0000000001" balances on the admin UI. SQLite stores these
        # as REAL; we keep a millicent of accuracy.
        provider.balance_cny = round(provider.balance_cny, 6)

        session.add(
            BillingLedger(
                job_id=job_id,
                provider_id=provider_id,
                cost_cny=cost,
                image_count=image_count,
            )
        )

        logger.info(
            "ledger.deduct provider=%s job=%s images=%d cost=%.4f balance=%.4f",
            provider_id,
            job_id,
            image_count,
            cost,
            provider.balance_cny,
        )
        return DeductionResult(
            provider_id=provider_id,
            cost_cny=cost,
            image_count=image_count,
            balance_after=provider.balance_cny,
        )

    async def topup(
        self,
        provider_id: str,
        amount_cny: float,
        *,
        session: AsyncSession | None = None,
    ) -> TopupResult:
        """Credit ``amount_cny`` to ``provider_id``.

        Side effects beyond the balance bump:

        * ``circuit_state == 'drained'`` flips back to ``'healthy'``
          and ``cooldown_until`` is cleared. The ``DRAINED`` state was
          our way of saying "stop trying, the wallet is empty"; once
          the wallet has money it's a healthy provider again. We never
          touch ``circuit_state == 'open'`` (that's a fault, not a
          balance problem) so a topup on a flapping provider does not
          mask the underlying outage.
        * No ``billing_ledger`` row: the ledger only records charges.
          Topup events are reconstructable from ``balance_cny`` /
          ``initial_balance_cny`` / sum-of-charges if needed.
        """
        if amount_cny <= 0:
            raise LedgerError(
                f"topup() requires amount_cny > 0, got {amount_cny}"
            )

        if session is None:
            async with get_session() as new_session:
                return await self._topup_in_session(
                    new_session, provider_id, amount_cny
                )
        return await self._topup_in_session(session, provider_id, amount_cny)

    async def _topup_in_session(
        self,
        session: AsyncSession,
        provider_id: str,
        amount_cny: float,
    ) -> TopupResult:
        provider = (
            await session.execute(select(Provider).where(Provider.id == provider_id))
        ).scalar_one_or_none()
        if provider is None:
            raise LedgerError(f"provider {provider_id!r} not found")

        promoted = False
        provider.balance_cny = round(
            float(provider.balance_cny) + float(amount_cny), 6
        )
        if provider.circuit_state == "drained":
            provider.circuit_state = "healthy"
            provider.cooldown_until = None
            promoted = True

        logger.info(
            "ledger.topup provider=%s amount=%.4f balance=%.4f promoted=%s",
            provider_id,
            amount_cny,
            provider.balance_cny,
            promoted,
        )
        return TopupResult(
            provider_id=provider_id,
            amount_cny=float(amount_cny),
            balance_after=provider.balance_cny,
            promoted_from_drained=promoted,
        )


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------


_instance: ProviderLedger | None = None


def get_provider_ledger() -> ProviderLedger:
    """Return the process-wide ledger singleton.

    Stateless, so the singleton is mostly a convenience for callers that
    want a stable handle to mock in tests via ``unittest.mock.patch``.
    """
    global _instance
    if _instance is None:
        _instance = ProviderLedger()
    return _instance
