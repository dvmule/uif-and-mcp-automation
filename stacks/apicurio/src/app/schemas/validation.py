"""Pydantic models for the metadata validation domain (request/response shapes only)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class ValidationFailure(BaseModel):
    """One rule that a schema failed."""

    rule: str
    message: str


class ValidationResult(BaseModel):
    """Validation outcome for a single artifact."""

    artifact_id: str
    group_id: str
    passed: bool
    failures: list[ValidationFailure]
    validated_at: datetime


class NonCompliantResponse(BaseModel):
    """Response envelope for the non-compliant endpoint."""

    items: list[ValidationResult]
    last_validated: datetime | None
    total_checked: int
