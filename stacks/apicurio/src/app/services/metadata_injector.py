"""Inject the Pearson metadata block into a schema body before registration."""

from __future__ import annotations

import json

from app.clients.cmdb_client import CmdbCi
from app.schemas.service import SchemaRegistrationInput

_JSON_BASED = {"AVRO", "JSON", "OPENAPI", "ASYNCAPI", "KCONNECT"}


def inject_metadata(
    content: str,
    schema_type: str,
    ci: CmdbCi,
    reg_input: SchemaRegistrationInput,
) -> str:
    """Merge the Pearson metadata block into the schema body.

    For JSON-based types (AVRO, JSON, OPENAPI, ASYNCAPI) the metadata key is set
    at the top level of the parsed object. Non-JSON types are returned as-is
    Format-specific injectors for non-JSON types are not implemented.
    """
    if schema_type not in _JSON_BASED:
        return content

    try:
        body = json.loads(content)
    except (json.JSONDecodeError, ValueError):
        return content

    if not isinstance(body, dict):
        return content

    body["metadata"] = {
        "owner": {
            "service": ci.ci_id,
            "team": ci.team_id,
            "email": ci.team_email,
        },
        "domain": ci.domain,
        "dataClassification": reg_input.data_classification,
        "piiFields": reg_input.pii_fields,
        "lifecycle": reg_input.lifecycle,
    }
    return json.dumps(body, indent=2)


def build_apicurio_properties(ci: CmdbCi, reg_input: SchemaRegistrationInput) -> dict[str, str]:
    """Build the Apicurio properties dict that mirrors the injected metadata block (ADR-0001)."""
    return {
        "owner.service": ci.ci_id,
        "domain": ci.domain,
        "dataClassification": reg_input.data_classification,
        "lifecycle": reg_input.lifecycle,
    }
