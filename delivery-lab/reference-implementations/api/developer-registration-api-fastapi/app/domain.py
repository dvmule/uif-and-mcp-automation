"""Pure domain layer: Pydantic models that mirror the contract's schemas, plus a
small business helper. The unit tests target this module directly, no network."""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class DeveloperStatus(str, Enum):
    active = "active"
    suspended = "suspended"


class RegisterDeveloperRequest(BaseModel):
    # additionalProperties: false in the contract -> forbid unknown fields.
    model_config = ConfigDict(extra="forbid")

    email: EmailStr = Field(max_length=254)
    displayName: str = Field(min_length=1, max_length=120)


class Developer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    email: EmailStr
    displayName: str
    status: DeveloperStatus
    createdAt: str  # RFC 3339 UTC


class DeveloperPage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[Developer]
    nextCursor: Optional[str] = None


def normalize_email(email: str) -> str:
    return email.strip().lower()
