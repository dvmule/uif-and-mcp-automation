"""Pydantic models for the Service domain (request/response shapes only)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from app.schemas.schema import SchemaSummary


class ServiceSummary(BaseModel):
    """Lightweight service summary for the list endpoint."""

    ci_id: str
    urn: str
    name: str
    domain: str
    produces_count: int
    consumes_count: int
    produces: list[str] = []
    consumes: list[str] = []
    app_id: str | None = None
    app_name: str | None = None


class ServiceDependency(BaseModel):
    """A single service linked to the current service via shared schemas."""

    ci_id: str
    urn: str
    name: str
    domain: str
    shared_schema_count: int


class ServiceDependencies(BaseModel):
    """Upstream and downstream service dependencies for a portfolio."""

    upstream: list[ServiceDependency]
    downstream: list[ServiceDependency]


class ServicePortfolioRead(BaseModel):
    """Portfolio of schemas produced and consumed by a single service."""

    ci_id: str
    urn: str
    name: str
    domain: str
    produces: list[SchemaSummary]
    consumes: list[SchemaSummary]
    dependencies: ServiceDependencies
    app_id: str | None = None
    app_name: str | None = None


class SchemaRegistrationInput(BaseModel):
    """Schema content + metadata fields supplied when pre-registering a produces topic."""

    content: str
    type_override: str | None = None
    data_classification: Literal["public", "internal", "confidential", "restricted"]
    pii_fields: list[str] = []
    lifecycle: Literal["active", "deprecated"] = "active"


class SchemaRegistrationResult(BaseModel):
    """Outcome of a single schema registration attempt."""

    topic: str
    registered: bool
    schema_type: str | None = None
    error: str | None = None


class DirectSchemaRegisterRequest(BaseModel):
    """Body for POST /schemas/{group_id}/{artifact_id} — standalone registration."""

    service_urn: str
    content: str
    type_override: str | None = None
    data_classification: Literal["public", "internal", "confidential", "restricted"]
    pii_fields: list[str] = []
    lifecycle: Literal["active", "deprecated"] = "active"


class OnboardManifest(BaseModel):
    """Base image manifest submitted when simulating a service deploy."""

    service_urn: str
    produces: list[str] = []
    consumes: list[str] = []
    schema_registrations: dict[str, SchemaRegistrationInput] = {}


class TopicReadiness(BaseModel):
    """Readiness status for a single declared topic."""

    topic: str
    registered: bool
    schema_type: str | None = None
    drift: str | None = None  # "D6" when service declares produces but schema is unregistered
    just_registered: bool = False


class OnboardResult(BaseModel):
    """Result returned after simulating a service deploy."""

    service_urn: str
    ci_id: str | None
    name: str | None
    produces: list[TopicReadiness]
    consumes: list[TopicReadiness]
    registrations: list[SchemaRegistrationResult] = []
