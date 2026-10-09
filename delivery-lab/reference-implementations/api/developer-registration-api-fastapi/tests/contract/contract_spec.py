"""Loads the registered openapi.yaml and validates response bodies against its
component schemas. OpenAPI 3.1 is a strict superset of JSON Schema 2020-12, so we
validate against the very schemas the contract declares -- no second copy, no
drift. (Python analogue of the Express example's Ajv 2020 helper.)"""
from __future__ import annotations

import os

import yaml
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

_OPENAPI_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "openapi.yaml")

with open(_OPENAPI_PATH, "r", encoding="utf-8") as fh:
    _DOC = yaml.safe_load(fh)

# An OpenAPI doc is not itself a JSON Schema resource, so pin the dialect (3.1's
# schemas are Draft 2020-12) and register the whole document so internal
# `#/components/schemas/...` refs resolve.
_REGISTRY = Registry().with_resource(
    "urn:openapi", Resource(contents=_DOC, specification=DRAFT202012)
)


def schema_errors(schema_name: str, body: object) -> list[str]:
    """Returns [] when body matches the named component schema, else the errors."""
    validator = Draft202012Validator(
        {"$ref": f"urn:openapi#/components/schemas/{schema_name}"},
        registry=_REGISTRY,
        format_checker=Draft202012Validator.FORMAT_CHECKER,
    )
    return [f"{list(e.absolute_path)} {e.message}" for e in validator.iter_errors(body)]


def contract_has_path(template_path: str) -> bool:
    return template_path in _DOC.get("paths", {})
