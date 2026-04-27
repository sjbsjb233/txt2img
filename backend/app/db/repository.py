"""Repository base class — placeholder for the ``JsonRepository[T]`` pattern
called out in the design doc §10 / §15.

The intention is for later PRs to build typed repositories on top of this
base so the API layer doesn't reach into ``AsyncSession`` directly. Right
now we only ship the abstract scaffolding so nobody invents a parallel
pattern; concrete subclasses arrive with PR-06 (providers) and PR-08
(jobs).
"""

from __future__ import annotations

from typing import Generic, TypeVar

from sqlalchemy.ext.asyncio import AsyncSession

T = TypeVar("T")


class JsonRepository(Generic[T]):
    """Base class for entity repositories.

    Subclasses bind a SQLAlchemy model and add their own CRUD methods. We
    deliberately do not provide generic ``get/create/update/delete`` here —
    those would require leaking session-management decisions into the base
    class, which the upcoming PRs need to control case-by-case (e.g.
    ``last_seq_no`` requires a BEGIN IMMEDIATE transaction).
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
