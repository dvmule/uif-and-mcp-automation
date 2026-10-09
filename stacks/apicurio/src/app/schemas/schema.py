"""Pydantic models for the Schema domain (request/response shapes only)."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class DataClassification(StrEnum):
    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"


class Lifecycle(StrEnum):
    ACTIVE = "active"
    DEPRECATED = "deprecated"


class SchemaOwner(BaseModel):
    """Owner block parsed from the schema metadata."""

    service: str | None = Field(None, description="CMDB CI ID — e.g. CI0093421")
    team: str | None = Field(None, description="CMDB team ID")
    email: str | None = Field(None, description="Owning team distribution list")


class SchemaSummary(BaseModel):
    """Lightweight schema record for list views."""

    model_config = ConfigDict(from_attributes=True)

    artifact_id: str = Field(description="Apicurio artifact ID — usually the subject name")
    group_id: str = Field(default="default")
    name: str | None = Field(None, description="Human-friendly name from Apicurio metadata")
    type: str = Field(description="Schema type — AVRO, PROTOBUF, JSON")
    state: str = Field(description="Apicurio state — ENABLED, DISABLED, DEPRECATED")
    created_on: datetime | None = None
    modified_on: datetime | None = None
    latest_version: str | None = None

    # Parsed from metadata block (best effort — None if not present)
    owner: SchemaOwner = Field(default_factory=SchemaOwner)
    domain: str | None = None
    classification: DataClassification | None = None
    lifecycle: Lifecycle | None = None
    pii_fields: list[str] = Field(default_factory=list)


class SchemaListResponse(BaseModel):
    """Response envelope for paginated schema lists."""

    items: list[SchemaSummary]
    total: int
    limit: int
    offset: int


class SchemaVersionRead(BaseModel):
    """One entry in an artifact's version history."""

    version: str
    state: str
    created_on: datetime | None = None
    created_by: str | None = None


class SchemaDetailRead(BaseModel):
    """Full artifact detail — content, version history, and parsed metadata block."""

    model_config = ConfigDict(from_attributes=True)

    artifact_id: str
    group_id: str
    name: str | None = None
    type: str
    state: str
    created_on: datetime | None = None
    modified_on: datetime | None = None

    content: str
    content_version: str

    versions: list[SchemaVersionRead]

    owner: SchemaOwner = Field(default_factory=SchemaOwner)
    domain: str | None = None
    classification: DataClassification | None = None
    lifecycle: Lifecycle | None = None
    pii_fields: list[str] = Field(default_factory=list)
    compatibility: str | None = None


class SchemaServiceRef(BaseModel):
    """Lightweight service reference returned from the schema services endpoint."""

    ci_id: str
    urn: str
    name: str
    domain: str


class SchemaServicesRead(BaseModel):
    """Services that produce or consume a given schema."""

    artifact_id: str
    group_id: str
    producers: list[SchemaServiceRef] = Field(default_factory=list)
    consumers: list[SchemaServiceRef] = Field(default_factory=list)
