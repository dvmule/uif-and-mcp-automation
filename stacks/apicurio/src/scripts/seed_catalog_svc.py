"""Mock-up data focused on catalog-svc (CI0093421). Run after seed_apicurio.py.

Adds:
  - 4 more schemas owned by catalog-svc (extends Produces tab to 7 total)
  - 3 schemas owned by other services that catalog-svc consumes (Consumes tab → 4)
  - Updates CMDB mock fixtures via a generated Python snippet you paste in
"""

import json
import os
import sys

import httpx

BASE = os.environ.get("APICURIO_BASE_URL", "http://localhost:8080")
API = f"{BASE.rstrip('/')}/apis/registry/v2"
GROUP = "default"

# --- catalog-svc produces these (in addition to the three from seed_apicurio.py) ---
CATALOG_PRODUCES = [
    {
        "subject": "product.product.deprecated-value",
        "schema": {
            "type": "record", "name": "ProductDeprecated", "namespace": "com.pearson.product",
            "fields": [
                {"name": "eventId", "type": "string"},
                {"name": "productId", "type": "string"},
                {"name": "reason", "type": "string"},
                {"name": "replacementProductId", "type": ["null", "string"], "default": None},
                {"name": "occurredAt", "type": {"type": "long", "logicalType": "timestamp-millis"}},
            ],
        },
        "owner": "CI0093421", "team": "TM-PRODUCT-CATALOG", "domain": "product",
        "classification": "internal",
    },
    {
        "subject": "product.sku.added-value",
        "schema": {
            "type": "record", "name": "SkuAdded", "namespace": "com.pearson.product",
            "fields": [
                {"name": "eventId", "type": "string"},
                {"name": "productId", "type": "string"},
                {"name": "sku", "type": "string"},
                {"name": "variantAttributes", "type": {"type": "map", "values": "string"}},
                {"name": "occurredAt", "type": {"type": "long", "logicalType": "timestamp-millis"}},
            ],
        },
        "owner": "CI0093421", "team": "TM-PRODUCT-CATALOG", "domain": "product",
        "classification": "internal",
    },
    {
        "subject": "product.bundle.published-value",
        "schema": {
            "type": "record", "name": "BundlePublished", "namespace": "com.pearson.product",
            "fields": [
                {"name": "eventId", "type": "string"},
                {"name": "bundleId", "type": "string"},
                {"name": "componentProductIds", "type": {"type": "array", "items": "string"}},
                {"name": "priceUSD", "type": "double"},
                {"name": "occurredAt", "type": {"type": "long", "logicalType": "timestamp-millis"}},
            ],
        },
        "owner": "CI0093421", "team": "TM-PRODUCT-CATALOG", "domain": "product",
        "classification": "internal",
    },
    {
        "subject": "product.catalog-value",
        "schema": {
            "type": "record", "name": "Catalog", "namespace": "com.pearson.product",
            "fields": [
                {"name": "catalogId", "type": "string"},
                {"name": "name", "type": "string"},
                {"name": "region", "type": "string"},
                {"name": "productCount", "type": "int"},
            ],
        },
        "owner": "CI0093421", "team": "TM-PRODUCT-CATALOG", "domain": "product",
        "classification": "public",
    },
]

# --- catalog-svc consumes these (owned by other services) ---
CATALOG_CONSUMES = [
    {
        "subject": "content.asset.published-value",
        "schema": {
            "type": "record", "name": "AssetPublished", "namespace": "com.pearson.content",
            "fields": [
                {"name": "eventId", "type": "string"},
                {"name": "assetId", "type": "string"},
                {"name": "contentType", "type": "string"},
                {"name": "url", "type": "string"},
                {"name": "occurredAt", "type": {"type": "long", "logicalType": "timestamp-millis"}},
            ],
        },
        "owner": "CI0093430", "team": "TM-CONTENT-PLATFORM", "domain": "content",
        "classification": "internal",
    },
    {
        "subject": "finance.pricing.changed-value",
        "schema": {
            "type": "record", "name": "PricingChanged", "namespace": "com.pearson.finance",
            "fields": [
                {"name": "eventId", "type": "string"},
                {"name": "productId", "type": "string"},
                {"name": "oldPriceUSD", "type": "double"},
                {"name": "newPriceUSD", "type": "double"},
                {
                    "name": "effectiveAt",
                    "type": {"type": "long", "logicalType": "timestamp-millis"},
                },
            ],
        },
        "owner": "CI0093431", "team": "TM-FINANCE-PRICING", "domain": "finance",
        "classification": "confidential",
    },
    {
        "subject": "customer.preference.updated-value",
        "schema": {
            "type": "record", "name": "PreferenceUpdated", "namespace": "com.pearson.customer",
            "fields": [
                {"name": "eventId", "type": "string"},
                {"name": "customerId", "type": "string"},
                {"name": "preferences", "type": {"type": "map", "values": "string"}},
                {"name": "occurredAt", "type": {"type": "long", "logicalType": "timestamp-millis"}},
            ],
        },
        "owner": "CI0093432", "team": "TM-CUSTOMER-PLATFORM", "domain": "customer",
        "classification": "confidential",
        "pii_fields": ["preferences"],
    },
]


def with_metadata(item: dict) -> dict:
    """Return the Avro dict with an embedded metadata block (ADR-0001: body is authoritative)."""
    return {
        **item["schema"],
        "metadata": {
            "owner": {
                "service": item["owner"],
                "team": item["team"],
                "email": f"{item['team'].lower().replace('tm-', '')}@pearson.com",
            },
            "domain": item["domain"],
            "dataClassification": item["classification"],
            "lifecycle": item.get("lifecycle", "active"),
            "piiFields": item.get("pii_fields", []),
        },
    }


def upsert(client: httpx.Client, item: dict) -> None:
    body = json.dumps(with_metadata(item)).encode("utf-8")
    headers = {
        "Content-Type": "application/json; artifactType=AVRO",
        "X-Registry-ArtifactId": item["subject"],
        "X-Registry-ArtifactType": "AVRO",
    }
    client.post(
        f"{API}/groups/{GROUP}/artifacts",
        params={"ifExists": "RETURN_OR_UPDATE"},
        content=body, headers=headers,
    ).raise_for_status()

    meta = {
        "name": item["subject"],
        "description": f"{item['domain']} domain",
        "labels": [item["domain"], item["classification"]],
        "properties": {
            "owner.service": item["owner"],
            "owner.team": item["team"],
            "owner.email": f"{item['team'].lower().replace('tm-','')}@pearson.com",
            "domain": item["domain"],
            "dataClassification": item["classification"],
            "lifecycle": "active",
            "piiFields": json.dumps(item.get("pii_fields", [])),
        },
    }
    client.put(
        f"{API}/groups/{GROUP}/artifacts/{item['subject']}/meta", json=meta
    ).raise_for_status()


def main() -> None:
    print(f"▶ Apicurio: {API}\n")
    with httpx.Client(timeout=10.0) as client:
        for item in CATALOG_PRODUCES + CATALOG_CONSUMES:
            try:
                upsert(client, item)
                print(f"  ✓ {item['subject']:<42} owner={item['owner']}")
            except httpx.HTTPStatusError as e:
                print(f"  ✗ {item['subject']:<42} {e.response.status_code} {e.response.text[:100]}")
                sys.exit(1)

    print(
        "\nDone. Update backend/app/clients/cmdb_client.py to teach "
        "catalog-svc about its new produces/consumes:\n"
    )
    print(
        "CI0093421.produces should include: "
        + ", ".join(f'"{x["subject"]}"' for x in CATALOG_PRODUCES)
    )
    print(
        "CI0093421.consumes should include: "
        + ", ".join(f'"{x["subject"]}"' for x in CATALOG_CONSUMES)
    )


if __name__ == "__main__":
    main()