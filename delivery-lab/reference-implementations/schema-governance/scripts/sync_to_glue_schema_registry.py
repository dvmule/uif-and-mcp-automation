#!/usr/bin/env python3
"""Step 4b — the Dual-Sync Bridge.

Given a just-registered Apicurio artifact, fetches its content and
compatibility rule and pushes the matching schema into AWS Glue Schema
Registry, so a Kinesis/EventBridge consumer using Glue's SerDe is always
validating against the identical schema Apicurio just admitted — never a
second, independently-authored copy.

See ../../../../stacks/apicurio/standards/apicurio-governance.md §4 for the why;
this is the how. No-ops (exit 0) if APICURIO_REGISTRY_URL is unset, and
separately if GLUE_REGISTRY_NAME is unset — both must be configured before
this does anything live.
"""

from __future__ import annotations

import argparse
import os
import sys

import boto3
from botocore.exceptions import ClientError

from apicurio_client import ApicurioClient, RegistryNotConfigured

GLUE_REGISTRY_NAME_ENV = "GLUE_REGISTRY_NAME"
AWS_REGION_ENV = "AWS_REGION"

# Apicurio's compatibility modes -> AWS Glue Schema Registry's equivalents.
# Glue: NONE | DISABLED | BACKWARD | BACKWARD_ALL | FORWARD | FORWARD_ALL | FULL | FULL_ALL
COMPATIBILITY_MODE_MAP = {
    "BACKWARD": "BACKWARD",
    "BACKWARD_TRANSITIVE": "BACKWARD_ALL",
    "FORWARD": "FORWARD",
    "FORWARD_TRANSITIVE": "FORWARD_ALL",
    "FULL": "FULL",
    "FULL_TRANSITIVE": "FULL_ALL",
    "NONE": "NONE",
}

# Apicurio artifact content-type -> Glue DataFormat. Avro/Protobuf content
# is passed straight through unmodified; OpenAPI/AsyncAPI documents are
# JSON-Schema-shaped once you take just the referenced `data`/component
# schema, which is what a caller of this script should already have
# resolved to (see contract-governance.yml — only the event *payload*
# schema, not the whole asyncapi.yaml, is synced to Glue).
CONTENT_TYPE_TO_GLUE_FORMAT = {
    "application/json": "JSON",
    "application/yaml": "JSON",  # JSON Schema authored as YAML still declares DataFormat=JSON to Glue
    "application/vnd.apache.avro+json": "AVRO",
    "application/x-protobuf": "PROTOBUF",
}


def map_compatibility(apicurio_mode: str) -> str:
    return COMPATIBILITY_MODE_MAP.get(apicurio_mode, "BACKWARD")


def sync_one(
    apicurio: ApicurioClient,
    glue,
    registry_name: str,
    group: str,
    artifact: str,
    content_type: str = "application/json",
) -> str:
    """Returns 'created', 'registered', or 'unchanged'."""
    content = apicurio.get_latest_content(group, artifact)
    compatibility = map_compatibility(apicurio.get_compatibility_rule(group, artifact))
    schema_name = f"{group}.{artifact}"
    data_format = CONTENT_TYPE_TO_GLUE_FORMAT.get(content_type, "JSON")

    try:
        existing = glue.get_schema_by_definition(
            SchemaId={"RegistryName": registry_name, "SchemaName": schema_name},
            SchemaDefinition=content,
        )
        return f"unchanged (already version {existing['SchemaVersionId']})"
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "EntityNotFoundException":
            raise

    try:
        glue.get_schema(SchemaId={"RegistryName": registry_name, "SchemaName": schema_name})
        glue.register_schema_version(
            SchemaId={"RegistryName": registry_name, "SchemaName": schema_name},
            SchemaDefinition=content,
        )
        return "registered new version"
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "EntityNotFoundException":
            raise

    glue.create_schema(
        RegistryId={"RegistryName": registry_name},
        SchemaName=schema_name,
        DataFormat=data_format,
        Compatibility=compatibility,
        SchemaDefinition=content,
    )
    return "created"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--group", required=True, help="Apicurio artifact group (the owning domain)")
    parser.add_argument("--artifact", required=True, help="Apicurio artifact id")
    parser.add_argument("--content-type", default="application/json")
    args = parser.parse_args()

    try:
        apicurio = ApicurioClient()
    except RegistryNotConfigured as exc:
        print(f"SKIP: {exc}")
        return 0

    registry_name = os.environ.get(GLUE_REGISTRY_NAME_ENV)
    if not registry_name:
        print(f"SKIP: {GLUE_REGISTRY_NAME_ENV} is not set — Dual-Sync Bridge is not wired up yet.")
        return 0

    glue = boto3.client("glue", region_name=os.environ.get(AWS_REGION_ENV, "us-east-1"))
    outcome = sync_one(apicurio, glue, registry_name, args.group, args.artifact, args.content_type)
    print(f"{outcome}: {args.group}.{args.artifact} -> Glue registry '{registry_name}'")
    return 0


if __name__ == "__main__":
    sys.exit(main())
