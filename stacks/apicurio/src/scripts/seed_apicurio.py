"""Seed Apicurio with realistic schemas for local development.

Registers ~10 Avro schemas across product / learning / commerce / customer domains,
with a full metadata block on each (owner, classification, lifecycle, PII fields).
Idempotent: re-running updates existing artifacts and their metadata in place.

Usage:
    cd stacks/apicurio/src
    python scripts/seed_apicurio.py              # create or update
    python scripts/seed_apicurio.py --clean      # delete every artifact, then re-seed
    python scripts/seed_apicurio.py --dry-run    # print what would happen, no writes

Environment:
    APICURIO_BASE_URL    default http://localhost:8080
    APICURIO_API_PATH    default /apis/registry/v2
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from typing import Any

import httpx

APICURIO_BASE_URL = os.environ.get("APICURIO_BASE_URL", "http://localhost:8080")
APICURIO_API_PATH = os.environ.get("APICURIO_API_PATH", "/apis/registry/v2")
GROUP_ID = "default"
API_URL = f"{APICURIO_BASE_URL.rstrip('/')}{APICURIO_API_PATH}"


@dataclass(slots=True)
class SeedSchema:
    subject: str                   # e.g. "product.product.created-value"
    avro: dict[str, Any]
    owner_service: str             # CMDB CI ID
    owner_team: str
    owner_email: str
    domain: str
    classification: str            # public | internal | confidential | restricted
    lifecycle: str = "active"      # active | deprecated
    pii_fields: list[str] = field(default_factory=list)
    description: str = ""


# --------------------------------------------------------------------------- #
# Reusable Avro fragments
# --------------------------------------------------------------------------- #
TIMESTAMP_FIELD = {
    "name": "occurredAt",
    "type": {"type": "long", "logicalType": "timestamp-millis"},
}
EVENT_ID_FIELD = {"name": "eventId", "type": "string"}


# --------------------------------------------------------------------------- #
# The seed set
# --------------------------------------------------------------------------- #
SCHEMAS: list[SeedSchema] = [
    # ---------- product domain — owner CI0093421 ----------
    SeedSchema(
        subject="product.product-value",
        avro={
            "type": "record",
            "name": "Product",
            "namespace": "com.pearson.product",
            "fields": [
                {"name": "productId", "type": "string"},
                {"name": "sku", "type": "string"},
                {"name": "title", "type": "string"},
                {"name": "description", "type": ["null", "string"], "default": None},
                {"name": "priceUSD", "type": "double"},
                {"name": "createdAt", "type": {"type": "long", "logicalType": "timestamp-millis"}},
            ],
        },
        owner_service="CI0093421",
        owner_team="TM-PRODUCT-CATALOG",
        owner_email="product-catalog@pearson.com",
        domain="product",
        classification="internal",
        description="Canonical product entity snapshot. Referenced by product events.",
    ),
    SeedSchema(
        subject="product.product.created-value",
        avro={
            "type": "record",
            "name": "ProductCreated",
            "namespace": "com.pearson.product",
            "fields": [
                EVENT_ID_FIELD,
                {"name": "productId", "type": "string"},
                {"name": "sku", "type": "string"},
                {"name": "title", "type": "string"},
                TIMESTAMP_FIELD,
            ],
        },
        owner_service="CI0093421",
        owner_team="TM-PRODUCT-CATALOG",
        owner_email="product-catalog@pearson.com",
        domain="product",
        classification="internal",
        description="Emitted when a new product is published to the catalog.",
    ),
    SeedSchema(
        subject="product.product.updated-value",
        avro={
            "type": "record",
            "name": "ProductUpdated",
            "namespace": "com.pearson.product",
            "fields": [
                EVENT_ID_FIELD,
                {"name": "productId", "type": "string"},
                {"name": "changedFields", "type": {"type": "array", "items": "string"}},
                TIMESTAMP_FIELD,
            ],
        },
        owner_service="CI0093421",
        owner_team="TM-PRODUCT-CATALOG",
        owner_email="product-catalog@pearson.com",
        domain="product",
        classification="internal",
    ),

    # ---------- learning domain — owner CI0093422 ----------
    SeedSchema(
        subject="learning.learner-value",
        avro={
            "type": "record",
            "name": "Learner",
            "namespace": "com.pearson.learning",
            "fields": [
                {"name": "learnerId", "type": "string"},
                {"name": "email", "type": "string"},
                {"name": "displayName", "type": "string"},
                {"name": "locale", "type": "string", "default": "en-US"},
                {"name": "createdAt", "type": {"type": "long", "logicalType": "timestamp-millis"}},
            ],
        },
        owner_service="CI0093422",
        owner_team="TM-LEARNING-PLATFORM",
        owner_email="learning-platform@pearson.com",
        domain="learning",
        classification="confidential",
        pii_fields=["email", "displayName"],
        description="Canonical learner entity. Contains PII — confidential classification.",
    ),
    SeedSchema(
        subject="learning.learner.enrolled-value",
        avro={
            "type": "record",
            "name": "LearnerEnrolled",
            "namespace": "com.pearson.learning",
            "fields": [
                EVENT_ID_FIELD,
                {"name": "learnerId", "type": "string"},
                {"name": "learnerEmail", "type": "string"},
                {"name": "courseId", "type": "string"},
                {"name": "enrolledAt", "type": {"type": "long", "logicalType": "timestamp-millis"}},
            ],
        },
        owner_service="CI0093422",
        owner_team="TM-LEARNING-PLATFORM",
        owner_email="learning-platform@pearson.com",
        domain="learning",
        classification="confidential",
        pii_fields=["learnerEmail"],
    ),
    SeedSchema(
        subject="learning.progress.updated-value",
        avro={
            "type": "record",
            "name": "ProgressUpdated",
            "namespace": "com.pearson.learning",
            "fields": [
                EVENT_ID_FIELD,
                {"name": "learnerId", "type": "string"},
                {"name": "moduleId", "type": "string"},
                {"name": "percentComplete", "type": "float"},
                TIMESTAMP_FIELD,
            ],
        },
        owner_service="CI0093422",
        owner_team="TM-LEARNING-PLATFORM",
        owner_email="learning-platform@pearson.com",
        domain="learning",
        classification="internal",
    ),

    # ---------- commerce domain — owner CI0093423 ----------
    SeedSchema(
        subject="commerce.order.placed-value",
        avro={
            "type": "record",
            "name": "OrderPlaced",
            "namespace": "com.pearson.commerce",
            "fields": [
                EVENT_ID_FIELD,
                {"name": "orderId", "type": "string"},
                {"name": "customerId", "type": "string"},
                {"name": "customerEmail", "type": "string"},
                {"name": "totalUSD", "type": "double"},
                {"name": "billingAddress", "type": "string"},
                TIMESTAMP_FIELD,
            ],
        },
        owner_service="CI0093423",
        owner_team="TM-COMMERCE-PLATFORM",
        owner_email="commerce-platform@pearson.com",
        domain="commerce",
        classification="confidential",
        pii_fields=["customerEmail", "billingAddress"],
    ),
    SeedSchema(
        subject="commerce.payment.processed-value",
        avro={
            "type": "record",
            "name": "PaymentProcessed",
            "namespace": "com.pearson.commerce",
            "fields": [
                EVENT_ID_FIELD,
                {"name": "paymentId", "type": "string"},
                {"name": "orderId", "type": "string"},
                {"name": "amountUSD", "type": "double"},
                {"name": "paymentMethodToken", "type": "string"},
                {"name": "processorRef", "type": "string"},
                TIMESTAMP_FIELD,
            ],
        },
        owner_service="CI0093423",
        owner_team="TM-COMMERCE-PLATFORM",
        owner_email="commerce-platform@pearson.com",
        domain="commerce",
        classification="restricted",
        pii_fields=["paymentMethodToken"],
        description="Financial settlement event. Restricted — full compatibility enforced.",
    ),
    SeedSchema(
        subject="commerce.order.cancelled-value",
        avro={
            "type": "record",
            "name": "OrderCancelled",
            "namespace": "com.pearson.commerce",
            "fields": [
                EVENT_ID_FIELD,
                {"name": "orderId", "type": "string"},
                {"name": "reason", "type": "string"},
                TIMESTAMP_FIELD,
            ],
        },
        owner_service="CI0093423",
        owner_team="TM-COMMERCE-PLATFORM",
        owner_email="commerce-platform@pearson.com",
        domain="commerce",
        classification="internal",
        lifecycle="deprecated",
        description="DEPRECATED. Replaced by commerce.order.refunded-value (not yet seeded).",
    ),

    # ---------- customer domain — ORPHAN (CI does not exist in CMDB) ----------
    SeedSchema(
        subject="customer.customer.created-value",
        avro={
            "type": "record",
            "name": "CustomerCreated",
            "namespace": "com.pearson.customer",
            "fields": [
                EVENT_ID_FIELD,
                {"name": "customerId", "type": "string"},
                {"name": "email", "type": "string"},
                {"name": "displayName", "type": "string"},
                TIMESTAMP_FIELD,
            ],
        },
        owner_service="CI0099999",   # ← deliberately no such CI in CMDB (orphan-owner fixture).
        owner_team="TM-CUSTOMER-PLATFORM",
        owner_email="customer-platform@pearson.com",
        domain="customer",
        classification="confidential",
        pii_fields=["email", "displayName"],
        description="ORPHAN — owner CI does not resolve in CMDB. Drift D3 test fixture.",
    ),
]


# --------------------------------------------------------------------------- #
# Apicurio operations
# --------------------------------------------------------------------------- #
def to_properties(s: SeedSchema) -> dict[str, str]:
    """Apicurio v2 properties are flat string→string. Lists are JSON-encoded."""
    return {
        "owner.service": s.owner_service,
        "owner.team": s.owner_team,
        "owner.email": s.owner_email,
        "domain": s.domain,
        "dataClassification": s.classification,
        "lifecycle": s.lifecycle,
        "piiFields": json.dumps(s.pii_fields),
    }


def health_check(client: httpx.Client) -> None:
    try:
        r = client.get(f"{APICURIO_BASE_URL.rstrip('/')}/health", timeout=3.0)
        r.raise_for_status()
    except httpx.HTTPError as e:
        sys.exit(f"❌ Apicurio not reachable at {APICURIO_BASE_URL}: {e}")


def list_artifacts(client: httpx.Client) -> list[dict[str, Any]]:
    r = client.get(f"{API_URL}/search/artifacts", params={"limit": 500})
    r.raise_for_status()
    return r.json().get("artifacts", [])


def delete_all(client: httpx.Client) -> int:
    artifacts = list_artifacts(client)
    for a in artifacts:
        gid = a.get("groupId") or "default"
        aid = a["id"]
        client.delete(f"{API_URL}/groups/{gid}/artifacts/{aid}").raise_for_status()
    return len(artifacts)


def with_metadata(s: SeedSchema) -> dict[str, Any]:
    """Return the Avro dict with an embedded metadata block (ADR-0001: body is authoritative)."""
    return {
        **s.avro,
        "metadata": {
            "owner": {
                "service": s.owner_service,
                "team": s.owner_team,
                "email": s.owner_email,
            },
            "domain": s.domain,
            "dataClassification": s.classification,
            "lifecycle": s.lifecycle,
            "piiFields": s.pii_fields,
        },
    }


def upsert_schema(client: httpx.Client, s: SeedSchema, dry_run: bool) -> str:
    """Create or update an artifact, then PUT its metadata.

    Returns 'created' | 'updated' | 'planned'.
    """
    if dry_run:
        return "planned"

    body = json.dumps(with_metadata(s)).encode("utf-8")
    headers = {
        "Content-Type": "application/json; artifactType=AVRO",
        "X-Registry-ArtifactId": s.subject,
        "X-Registry-ArtifactType": "AVRO",
    }
    # ifExists=RETURN_OR_UPDATE → existing artifact: new version only if content differs
    r = client.post(
        f"{API_URL}/groups/{GROUP_ID}/artifacts",
        params={"ifExists": "RETURN_OR_UPDATE"},
        content=body,
        headers=headers,
    )
    r.raise_for_status()
    created_version = r.json().get("version")

    # PUT metadata (always — keeps properties in sync even on rerun)
    meta_payload = {
        "name": s.subject,
        "description": s.description or f"{s.domain} domain — {s.subject}",
        "labels": [s.domain, s.classification, s.lifecycle],
        "properties": to_properties(s),
    }
    client.put(
        f"{API_URL}/groups/{GROUP_ID}/artifacts/{s.subject}/meta",
        json=meta_payload,
    ).raise_for_status()

    return "created" if created_version == "1" else "updated"


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main() -> None:
    parser = argparse.ArgumentParser(description="Seed Apicurio with sample schemas.")
    parser.add_argument("--clean", action="store_true", help="Delete every artifact first.")
    parser.add_argument("--dry-run", action="store_true", help="Print plan, no writes.")
    args = parser.parse_args()

    print(f"▶ Apicurio:  {API_URL}")
    print(f"▶ Group:     {GROUP_ID}")
    print(f"▶ Schemas:   {len(SCHEMAS)}")
    print()

    with httpx.Client(timeout=10.0) as client:
        health_check(client)

        if args.clean and not args.dry_run:
            count = delete_all(client)
            print(f"🧹 Deleted {count} existing artifact(s)\n")

        results = {"created": 0, "updated": 0, "planned": 0, "failed": 0}
        for s in SCHEMAS:
            try:
                action = upsert_schema(client, s, dry_run=args.dry_run)
                results[action] += 1
                marker = {"created": "✓", "updated": "↻", "planned": "→"}[action]
                tags = [s.domain, s.classification]
                if s.lifecycle != "active":
                    tags.append(s.lifecycle.upper())
                if s.owner_service == "CI0099999":
                    tags.append("ORPHAN")
                print(f"  {marker} {s.subject:<42} [{', '.join(tags)}]")
            except httpx.HTTPStatusError as e:
                results["failed"] += 1
                print(f"  ✗ {s.subject:<42} {e.response.status_code} {e.response.text[:120]}")

    print()
    print(
        f"Done. created={results['created']} updated={results['updated']} "
        f"planned={results['planned']} failed={results['failed']}"
    )
    if results["failed"]:
        sys.exit(1)


if __name__ == "__main__":
    main()