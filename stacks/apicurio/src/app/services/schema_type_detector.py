"""Detect Apicurio schema type from raw content."""

from __future__ import annotations

import json


def detect_schema_type(content: str) -> str:
    """Sniff the Apicurio artifact type from raw schema content.

    Returns one of: AVRO, JSON, OPENAPI, ASYNCAPI, PROTOBUF, GRAPHQL, XSD — defaulting to JSON.
    """
    stripped = content.strip()

    if stripped.startswith("<"):
        return "XSD"

    if stripped.startswith('syntax = "proto') or stripped.startswith("syntax = 'proto"):
        return "PROTOBUF"

    if stripped.startswith("{") or stripped.startswith("["):
        try:
            parsed = json.loads(content)
        except (json.JSONDecodeError, ValueError):
            return "JSON"

        if isinstance(parsed, dict):
            if "openapi" in parsed or "swagger" in parsed:
                return "OPENAPI"
            if "asyncapi" in parsed:
                return "ASYNCAPI"
            if parsed.get("type") == "record" and "fields" in parsed:
                return "AVRO"
            if "$schema" in parsed:
                return "JSON"
        return "JSON"

    graphql_keywords = ("type ", "schema {", "interface ", "enum ", "union ", "scalar ")
    if any(stripped.startswith(kw) or f"\n{kw}" in stripped for kw in graphql_keywords):
        return "GRAPHQL"

    return "JSON"
