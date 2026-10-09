"""Pydantic shapes for the health endpoint."""

from enum import StrEnum

from pydantic import BaseModel


class HealthState(StrEnum):
    UP = "up"
    DOWN = "down"
    DEGRADED = "degraded"


class DependencyHealth(BaseModel):
    name: str
    state: HealthState
    detail: str | None = None


class HealthResponse(BaseModel):
    state: HealthState
    version: str
    dependencies: list[DependencyHealth]
