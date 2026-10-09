"""Seed Apicurio with non-Avro schema fixtures.

Adds 3 artifacts in formats the validator does not yet handle:
  - JSON Schema  : metadata in top-level x-pearson-metadata extension
  - Protobuf     : metadata in proto2 file-level options block
  - OpenAPI YAML : metadata in info.x-pearson-metadata extension

These exist to exercise the registry, list view, detail view, and the
validator's `schema_type_unsupported` rule path. None will pass validation —
the validator is Avro-only by design.

Each fixture's CMDB owner is one of the existing seeded CIs so the service
portfolio query continues to work.

Usage:
    cd stacks/apicurio/src
    python scripts/seed_mixed_formats.py              # create or update
    python scripts/seed_mixed_formats.py --dry-run    # print plan, no writes
    python scripts/seed_mixed_formats.py --clean      # delete the 3 fixtures, no add
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass

import httpx

APICURIO_BASE_URL = os.environ.get("APICURIO_BASE_URL", "http://localhost:8080")
APICURIO_API_PATH = os.environ.get("APICURIO_API_PATH", "/apis/registry/v2")
GROUP_ID = "default"
API_URL = f"{APICURIO_BASE_URL.rstrip('/')}{APICURIO_API_PATH}"


@dataclass(slots=True)
class MixedFixture:
    subject: str
    artifact_type: str          # JSON | PROTOBUF | OPENAPI
    content_type: str           # what to put in Content-Type header
    body: str                   # raw artifact body, format-native
    owner_service: str
    owner_team: str
    owner_email: str
    domain: str
    classification: str
    description: str


# --------------------------------------------------------------------------- #
# Fixture 1 — JSON Schema. Metadata in x-pearson-metadata top-level extension.
# Owner: catalog-svc (CI0093421, product domain).
# --------------------------------------------------------------------------- #
JSON_SCHEMA_BODY = json.dumps(
    {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://schema.pearson.com/json/product.product-shape",
        "title": "ProductShape",
        "description": "JSON Schema describing the shape of a product record served over REST.",
        "type": "object",
        "required": ["productId", "sku", "title", "priceUSD"],
        "properties": {
            "productId": {"type": "string", "format": "uuid"},
            "sku": {"type": "string", "minLength": 1},
            "title": {"type": "string", "minLength": 1, "maxLength": 200},
            "description": {"type": ["string", "null"]},
            "priceUSD": {"type": "number", "minimum": 0},
        },
        "additionalProperties": False,
        "x-pearson-metadata": {
            "owner": {
                "service": "CI0093421",
                "team": "TM-PRODUCT-CATALOG",
                "email": "product-catalog@pearson.com",
            },
            "domain": "product",
            "dataClassification": "internal",
            "lifecycle": "active",
            "piiFields": [],
        },
    },
    indent=2,
)


# --------------------------------------------------------------------------- #
# Fixture 2 — Protobuf. Metadata in proto2 file-level option block.
# Owner: enrollment-svc (CI0093422, learning domain).
# Hypothetical only — there is no real .proto consumer.
# --------------------------------------------------------------------------- #
PROTOBUF_BODY = '''syntax = "proto3";

package com.pearson.learning;

// Pearson schema metadata — pseudo-option, illustrative only.
// In a production setup this would resolve via a Pearson-defined
// custom option imported from a shared metadata.proto. For now
// it is a comment-block convention the validator can parse.
//
// @pearson:metadata {
//   "owner": {
//     "service": "CI0093422",
//     "team":    "TM-LEARNING-PLATFORM",
//     "email":   "learning-platform@pearson.com"
//   },
//   "domain":             "learning",
//   "dataClassification": "confidential",
//   "lifecycle":          "active",
//   "piiFields":          ["learner_email"]
// }

message LearnerSnapshot {
  string learner_id     = 1;
  string learner_email  = 2;
  string display_name   = 3;
  int64  created_at_ms  = 4;
}
'''


# --------------------------------------------------------------------------- #
# Fixture 3 — OpenAPI 3.1 YAML. Metadata in info.x-pearson-metadata extension.
# Owner: checkout-svc (CI0093423, commerce domain).
# --------------------------------------------------------------------------- #
OPENAPI_YAML_BODY = '''openapi: 3.1.0
info:
  title: Commerce Order API
  version: 1.0.0
  description: Read-side API for placed orders. Test fixture.
  x-pearson-metadata:
    owner:
      service: CI0093423
      team: TM-COMMERCE-PLATFORM
      email: commerce-platform@pearson.com
    domain: commerce
    dataClassification: confidential
    lifecycle: active
    piiFields:
      - "$.components.schemas.Order.properties.customerEmail"
      - "$.components.schemas.Order.properties.billingAddress"

paths:
  /orders/{orderId}:
    get:
      summary: Fetch a single order by ID
      parameters:
        - name: orderId
          in: path
          required: true
          schema:
            type: string
      responses:
        "200":
          description: The order
          content:
            application/json:
              schema:
                $ref: "#/components/schemas/Order"
        "404":
          description: Order not found

components:
  schemas:
    Order:
      type: object
      required: [orderId, customerEmail, totalUSD]
      properties:
        orderId:
          type: string
        customerEmail:
          type: string
          format: email
        billingAddress:
          type: string
        totalUSD:
          type: number
          minimum: 0
'''


FIXTURES: list[MixedFixture] = [
    MixedFixture(
        subject="product.product-shape",
        artifact_type="JSON",
        content_type="application/json",
        body=JSON_SCHEMA_BODY,
        owner_service="CI0093421",
        owner_team="TM-PRODUCT-CATALOG",
        owner_email="product-catalog@pearson.com",
        domain="product",
        classification="internal",
        description="JSON Schema for the REST representation of a product record.",
    ),
    MixedFixture(
        subject="learning.learner.snapshot",
        artifact_type="PROTOBUF",
        content_type="text/plain",
        body=PROTOBUF_BODY,
        owner_service="CI0093422",
        owner_team="TM-LEARNING-PLATFORM",
        owner_email="learning-platform@pearson.com",
        domain="learning",
        classification="confidential",
        description="Protobuf message for the learner snapshot. Hypothetical test fixture.",
    ),
    MixedFixture(
        subject="commerce.order.api",
        artifact_type="OPENAPI",
        content_type="application/x-yaml",
        body=OPENAPI_YAML_BODY,
        owner_service="CI0093423",
        owner_team="TM-COMMERCE-PLATFORM",
        owner_email="commerce-platform@pearson.com",
        domain="commerce",
        classification="confidential",
        description="OpenAPI 3.1 contract for the commerce order read-side API.",
    ),
]


# --------------------------------------------------------------------------- #
# Apicurio operations
# --------------------------------------------------------------------------- #
def to_properties(f: MixedFixture) -> dict[str, str]:
    """Mirror the metadata block into Apicurio properties for list-view parity.

    Per ADR-0001 the body is authoritative. Properties are a derived index. The
    body is the format-native location (x-pearson-metadata for JSON Schema and
    OpenAPI; option-block comment for Protobuf). The properties below let the
    list view show ownership without parsing each format's body.
    """
    return {
        "owner.service": f.owner_service,
        "owner.team": f.owner_team,
        "owner.email": f.owner_email,
        "domain": f.domain,
        "dataClassification": f.classification,
        "lifecycle": "active",
        "piiFields": json.dumps([]),  # populated case-by-case below
    }


def health_check(client: httpx.Client) -> None:
    try:
        r = client.get(f"{APICURIO_BASE_URL.rstrip('/')}/health", timeout=3.0)
        r.raise_for_status()
    except httpx.HTTPError as e:
        sys.exit(f"❌ Apicurio not reachable at {APICURIO_BASE_URL}: {e}")


def upsert(client: httpx.Client, f: MixedFixture, dry_run: bool) -> str:
    if dry_run:
        return "planned"

    headers = {
        "Content-Type": f"{f.content_type}; artifactType={f.artifact_type}",
        "X-Registry-ArtifactId": f.subject,
        "X-Registry-ArtifactType": f.artifact_type,
    }

    r = client.post(
        f"{API_URL}/groups/{GROUP_ID}/artifacts",
        params={"ifExists": "RETURN_OR_UPDATE"},
        content=f.body.encode("utf-8"),
        headers=headers,
    )
    r.raise_for_status()
    created_version = r.json().get("version")

    meta_payload = {
        "name": f.subject,
        "description": f.description,
        "labels": [f.domain, f.classification, f.artifact_type.lower()],
        "properties": to_properties(f),
    }
    client.put(
        f"{API_URL}/groups/{GROUP_ID}/artifacts/{f.subject}/meta",
        json=meta_payload,
    ).raise_for_status()

    return "created" if created_version == "1" else "updated"


def delete_fixtures(client: httpx.Client) -> int:
    count = 0
    for f in FIXTURES:
        r = client.delete(f"{API_URL}/groups/{GROUP_ID}/artifacts/{f.subject}")
        if r.status_code == 204:
            count += 1
        elif r.status_code != 404:
            r.raise_for_status()
    return count


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main() -> None:
    parser = argparse.ArgumentParser(description="Seed Apicurio with non-Avro fixtures.")
    parser.add_argument("--dry-run", action="store_true", help="Print plan, no writes.")
    parser.add_argument(
        "--clean", action="store_true", help="Delete the 3 fixtures, do not re-add."
    )
    args = parser.parse_args()

    print(f"▶ Apicurio: {API_URL}")
    print(f"▶ Group:    {GROUP_ID}")
    print(f"▶ Fixtures: {len(FIXTURES)}\n")

    with httpx.Client(timeout=10.0) as client:
        health_check(client)

        if args.clean:
            count = delete_fixtures(client)
            print(f"🧹 Deleted {count} of {len(FIXTURES)} fixtures.")
            return

        results: dict[str, int] = {"created": 0, "updated": 0, "planned": 0, "failed": 0}
        for f in FIXTURES:
            try:
                action = upsert(client, f, dry_run=args.dry_run)
                results[action] += 1
                marker = {"created": "✓", "updated": "↻", "planned": "→"}[action]
                print(
                    f"  {marker} {f.subject:<32} "
                    f"[{f.artifact_type}, owner={f.owner_service}, {f.classification}]"
                )
            except httpx.HTTPStatusError as e:
                results["failed"] += 1
                print(
                    f"  ✗ {f.subject:<32} {e.response.status_code} {e.response.text[:120]}"
                )

    print(
        f"\nDone. created={results['created']} updated={results['updated']} "
        f"planned={results['planned']} failed={results['failed']}"
    )

    if not args.dry_run and results["failed"] == 0:
        print("\nExpected validator behaviour for these fixtures:")
        print("  Each will fail with rule_id=schema_type_unsupported (Avro-only validator).")
        print("  This is correct — the rule exists to make non-Avro schemas fail loudly")
        print("  rather than pass silently. Extending the validator would change this.")

    if results["failed"]:
        sys.exit(1)


if __name__ == "__main__":
    main()