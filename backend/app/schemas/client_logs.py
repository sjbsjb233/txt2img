"""Pydantic schemas for the /api/client-logs endpoint.

The frontend ships one batch per flush; each batch is a list of items.
Keeping the schema permissive (``extra="ignore"``, plenty of optionals)
matters because the wire format is older browsers' best guess at our
contract — we don't want a CRC on a stack-trace field to drop the
whole batch.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

ClientLogLevel = Literal["debug", "info", "warn", "error"]


class ClientLogItem(BaseModel):
    """One log record, as the browser saw it.

    All fields are optional except ``level`` and ``msg`` so a hand-typed
    curl test still goes through. ``extra`` holds anything the caller
    wants to attach (stack trace shrapnel, route params, …).
    """

    model_config = ConfigDict(extra="ignore")

    ts: str | None = None
    level: ClientLogLevel = "info"
    msg: str = Field(..., max_length=2000)
    route: str | None = Field(default=None, max_length=512)
    build_version: str | None = Field(default=None, max_length=64)
    session_id: str | None = Field(default=None, max_length=128)
    user_agent: str | None = Field(default=None, max_length=512)
    stack: str | None = Field(default=None, max_length=8000)
    extra: dict[str, Any] | None = None


class ClientLogsRequest(BaseModel):
    """Wrapper around a batch of ``ClientLogItem`` records."""

    model_config = ConfigDict(extra="ignore")

    items: list[ClientLogItem] = Field(..., min_length=1)


class ClientLogsResponse(BaseModel):
    accepted: int
    dropped: int
