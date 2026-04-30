"""Pydantic shapes for ``/api/admin/cleanup/*``.

Mirrors design doc §13.7. The admin UI stitches these together with
the suggestions endpoint: render the suggestion buckets, let the
admin tweak them into a custom ``CleanupRequest``, run dry-run, then
execute.

Two body shapes:

- ``CleanupRequest`` is the unified body for both dry-run and execute.
  ``dry_run=True`` means "tell me what would happen, change nothing";
  ``dry_run=False`` enqueues a real cleanup task whose progress is
  pollable via ``GET /api/admin/cleanup/<task_id>``.

- ``CleanupRule`` is one rule entry inside the request. We keep its
  validation intentionally simple (two ``kind``s, an optional days
  field, an optional statuses array) so the admin UI can surface each
  decision point as a dedicated control rather than a free-form DSL.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class CleanupRule(BaseModel):
    """One rule entry inside ``CleanupRequest.rules``.

    ``kind`` values:

    - ``older_than_days`` requires ``days``; ``statuses`` is optional
      and limits the match to those statuses.
    - ``status_only`` requires ``statuses`` and ignores ``days``.

    Domain-side validation (``coerce_cleanup_rules``) does the cross-
    field check; we keep this schema permissive so a confused admin's
    request still parses and the 422 message is precise.
    """

    model_config = ConfigDict(extra="forbid")

    kind: str = Field(..., min_length=1, max_length=64)
    days: int | None = Field(default=None, ge=0, le=365 * 10)
    statuses: list[str] = Field(default_factory=list)


class CleanupRequest(BaseModel):
    """Body of ``POST /api/admin/cleanup``."""

    model_config = ConfigDict(extra="forbid")

    rules: list[CleanupRule]
    dry_run: bool = False
    exempt_starred: bool = True


class CleanupSuggestionView(BaseModel):
    period_label: str
    rule: CleanupRule
    cutoff: str | None
    job_count: int
    image_count: int
    disk_bytes: int
    disk_human: str


class CleanupSuggestionListResponse(BaseModel):
    suggestions: list[CleanupSuggestionView]
    disk_usage: dict[str, Any] = Field(default_factory=dict)
    refreshed_at: str | None = None


class CleanupDryRunResponse(BaseModel):
    """Outcome of a dry-run cleanup request — pure projection, no DB write."""

    job_count: int
    image_count: int
    disk_bytes: int
    disk_human: str


class CleanupTaskView(BaseModel):
    task_id: str
    status: str
    dry_run: bool
    started_at: str
    finished_at: str | None
    total_jobs: int
    processed_jobs: int
    deleted_bytes: int
    affected_user_count: int
    error: str | None
