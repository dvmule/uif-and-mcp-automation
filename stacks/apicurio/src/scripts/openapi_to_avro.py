"""Translate the Pearson Product API V2 OpenAPI spec to Avro.

Generates two Avro schema files:
  - product-canonical.avsc — full Product entity, ~1000 fields after nested
    types are inlined. Mirrors the OpenAPI spec faithfully apart from the
    leakage cleanup. Source of truth for what a Product is.
  - product-summary.avsc — slim 25-field slice. Carried in event payloads.
    Lives separately so events can reference it via Apicurio schema reference.

Applies these locked design decisions (agreed; do not change casually):
  - Drop source-system leakage: OracleEBSProductAttributes, LegacyCode,
    Correction, nirNo, opaNo, pdhItemStatus
  - Required on canonical: ppid, name
  - Required on summary: ppid
  - Date normalisation: Avro stricter than OpenAPI
    *Date    -> logicalType: date     (e.g. productionLaunchDate)
    *DateTime / format=date-time  -> logicalType: timestamp-millis
  - Inline all nested types into the canonical (no separate Apicurio artefacts
    for Courseware, MarketingCopy, Taxonomy, etc.)
  - Embed the metadata block as a top-level Avro field-level annotation so
    ADR-0001 read path resolves it from the body.

Usage:
    cd stacks/apicurio/src
    python scripts/openapi_to_avro.py \\
        --input ../data/productsList.json \\
        --output-dir ../data/canonical/

    # Dry run — print summaries, no files written
    python scripts/openapi_to_avro.py \\
        --input ../data/productsList.json --dry-run
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# --------------------------------------------------------------------------- #
# Locked design decisions. Changing any of these requires reapproval before
# running this script. NOTE: the worked-example doc that recorded the original
# rationale lives in the source repo and was not migrated.
# --------------------------------------------------------------------------- #

# Source-system leakage we agreed to drop.
LEAKAGE_REFS: set[str] = {
    "OracleEBSProductAttributes",
    "LegacyCode",
    "Correction",
}
LEAKAGE_FIELDS: set[str] = {
    "nirNo",
    "opaNo",
    "pdhItemStatus",
    # Note: "correction" and "legacyCode" properties are dropped because
    # their target types are in LEAKAGE_REFS — handled there, not here.
}

# Required fields on each schema.
CANONICAL_REQUIRED: set[str] = {"ppid", "name"}
SUMMARY_REQUIRED: set[str] = {"ppid"}

# The summary slice — locked at 25 fields.
SUMMARY_FIELDS: tuple[str, ...] = (
    # Identity
    "ppid", "isbn13", "gtin14", "pearId",
    # Lifecycle
    "productLifecycleStage", "lifecyclePhase", "eciStatus",
    "productionLaunchDate", "publicationDate",
    # Display + classification
    "name", "brand", "subBrand",
    "productTypeLevel1", "productFormatLevel1",
    "audience", "medium", "disciplineLevel1", "productFamily",
    # Commercial
    "businessModel", "division", "lineOfBusiness",
    "owningSubsidiary", "deliveryMethod",
    # Other scalar
    "copyrightYear", "editionNumber",
)
assert len(SUMMARY_FIELDS) == 25, "Summary slice locked at 25 fields per worked example"

# Metadata blocks. Both schemas get the same ownership.
OWNERSHIP_BLOCK: dict[str, Any] = {
    "owner": {
        "service": "CI0093421",
        "team": "TM-PRODUCT-CATALOG",
        "email": "product-catalog@pearson.com",
    },
    "domain": "product",
    "dataClassification": "internal",
    "lifecycle": "active",
    "piiFields": [],
}

CANONICAL_NAMESPACE = "com.pearson.product.canonical"


# --------------------------------------------------------------------------- #
# Data classes
# --------------------------------------------------------------------------- #
@dataclass
class TranslationStats:
    canonical_fields: int = 0
    summary_fields: int = 0
    nested_types_inlined: int = 0
    leakage_fields_dropped: int = 0
    leakage_refs_dropped: int = 0
    date_fields_promoted: int = 0
    timestamp_fields_promoted: int = 0


# --------------------------------------------------------------------------- #
# Date / type heuristics
# --------------------------------------------------------------------------- #
def normalise_avro_type(field_name: str, openapi_def: dict[str, Any]) -> Any:
    """Convert an OpenAPI property definition to an Avro type union.

    Returns the Avro 'type' value for use in a schema field. All non-required
    fields are wrapped in ['null', T] unions; required fields are added by
    the caller.

    Date heuristics (locked per Q-B):
      - format=date-time            -> timestamp-millis (long)
      - field name ends in DateTime -> timestamp-millis
      - field name ends in Date     -> logicalType: date (string)
    """
    openapi_type = openapi_def.get("type")
    openapi_format = openapi_def.get("format")

    # Explicit date-time format
    if openapi_format == "date-time":
        return {"type": "long", "logicalType": "timestamp-millis"}

    # Heuristics on field-name suffix when OpenAPI type is plain string
    if openapi_type == "string":
        if field_name.endswith("DateTime"):
            return {"type": "long", "logicalType": "timestamp-millis"}
        if field_name.endswith("Date"):
            return {"type": "string", "logicalType": "date"}
        return "string"

    if openapi_type == "integer":
        # OpenAPI int32 / int64 — Avro int / long
        if openapi_format == "int64":
            return "long"
        return "int"

    if openapi_type == "number":
        if openapi_format == "float":
            return "float"
        return "double"

    if openapi_type == "boolean":
        return "boolean"

    if openapi_type == "array":
        # Caller handles array shape; we shouldn't be called with arrays
        raise ValueError(f"normalise_avro_type called with array for {field_name}")

    # Fallback for unknown types — string is the safe default
    return "string"


def is_date_promoted(field_name: str, openapi_def: dict[str, Any]) -> str | None:
    """Return 'date', 'timestamp', or None to track promotion stats."""
    if openapi_def.get("format") == "date-time":
        return "timestamp"
    if openapi_def.get("type") == "string":
        if field_name.endswith("DateTime"):
            return "timestamp"
        if field_name.endswith("Date"):
            return "date"
    return None


# --------------------------------------------------------------------------- #
# Schema translation
# --------------------------------------------------------------------------- #
def resolve_ref(ref: str, schemas: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Resolve '#/components/schemas/Foo' to ('Foo', schemas['Foo'])."""
    name = ref.rsplit("/", 1)[-1]
    if name not in schemas:
        raise ValueError(f"Unresolved $ref: {ref}")
    return name, schemas[name]


def translate_property(
    field_name: str,
    openapi_def: dict[str, Any],
    schemas: dict[str, Any],
    required: set[str],
    seen_records: dict[str, dict[str, Any]],
    stats: TranslationStats,
) -> dict[str, Any] | None:
    """Translate one OpenAPI property to an Avro field. Returns None to drop the field."""

    # Drop leakage fields
    if field_name in LEAKAGE_FIELDS:
        stats.leakage_fields_dropped += 1
        return None

    is_required = field_name in required

    # Reference to a component schema
    if "$ref" in openapi_def:
        ref_name, ref_def = resolve_ref(openapi_def["$ref"], schemas)
        if ref_name in LEAKAGE_REFS:
            stats.leakage_refs_dropped += 1
            return None
        avro_record = inline_record(ref_name, ref_def, schemas, seen_records, stats)
        avro_type = avro_record if is_required else ["null", avro_record]
        return {
            "name": field_name,
            "type": avro_type,
            **({"default": None} if not is_required else {}),
        }

    # Array of $ref or array of primitive
    if openapi_def.get("type") == "array":
        items = openapi_def.get("items", {})
        if "$ref" in items:
            ref_name, ref_def = resolve_ref(items["$ref"], schemas)
            if ref_name in LEAKAGE_REFS:
                stats.leakage_refs_dropped += 1
                return None
            avro_record = inline_record(ref_name, ref_def, schemas, seen_records, stats)
            return {
                "name": field_name,
                "type": {"type": "array", "items": avro_record},
                "default": [],
            }
        # Array of primitive
        item_type = normalise_avro_type(field_name, items)
        return {
            "name": field_name,
            "type": {"type": "array", "items": item_type},
            "default": [],
        }

    # Scalar
    avro_type = normalise_avro_type(field_name, openapi_def)
    promoted = is_date_promoted(field_name, openapi_def)
    if promoted == "date":
        stats.date_fields_promoted += 1
    elif promoted == "timestamp":
        stats.timestamp_fields_promoted += 1

    if is_required:
        return {"name": field_name, "type": avro_type}
    return {"name": field_name, "type": ["null", avro_type], "default": None}


def inline_record(
    name: str,
    openapi_def: dict[str, Any],
    schemas: dict[str, Any],
    seen_records: dict[str, dict[str, Any]],
    stats: TranslationStats,
) -> Any:
    """Inline a referenced component schema as an Avro record.

    Avro requires that record names appear as definitions only once within
    a single schema document. Subsequent references use the name as a string.
    seen_records tracks which records have been emitted at full definition.
    """
    if name in seen_records:
        # Already emitted full definition — reference by name
        return f"{CANONICAL_NAMESPACE}.{name}"

    fields: list[dict[str, Any]] = []
    properties = openapi_def.get("properties", {})
    nested_required: set[str] = set(openapi_def.get("required", []))

    for prop_name, prop_def in properties.items():
        translated = translate_property(
            prop_name, prop_def, schemas, nested_required, seen_records, stats
        )
        if translated is not None:
            fields.append(translated)

    record: dict[str, Any] = {
        "type": "record",
        "name": name,
        "namespace": CANONICAL_NAMESPACE,
        "fields": fields,
    }
    seen_records[name] = record
    stats.nested_types_inlined += 1
    return record


# --------------------------------------------------------------------------- #
# Main schema builders
# --------------------------------------------------------------------------- #
def build_canonical(spec: dict[str, Any]) -> tuple[dict[str, Any], TranslationStats]:
    """Build the full canonical Product Avro schema."""
    stats = TranslationStats()
    schemas = spec["components"]["schemas"]
    product_def = schemas["Product"]

    seen_records: dict[str, dict[str, Any]] = {}
    fields: list[dict[str, Any]] = []

    properties = product_def.get("properties", {})
    for prop_name, prop_def in properties.items():
        translated = translate_property(
            prop_name, prop_def, schemas, CANONICAL_REQUIRED, seen_records, stats
        )
        if translated is not None:
            fields.append(translated)

    stats.canonical_fields = len(fields)

    canonical = {
        "type": "record",
        "name": "Product",
        "namespace": CANONICAL_NAMESPACE,
        "doc": (
            "Pearson canonical Product entity. Mirrors GET /v2/{identifier} "
            "from Product API V2. Source of truth for what a Product is across "
            "the enterprise. Event payloads carry ProductSummary (a slice of "
            "this) to avoid broadcasting the full entity."
        ),
        "metadata": {
            **OWNERSHIP_BLOCK,
            "x-pearson-canonical": True,
            "x-pearson-source-of-truth": "openapi://product-api-v2",
            "x-pearson-application": "SVC04030",
        },
        "fields": fields,
    }
    return canonical, stats


def build_summary(spec: dict[str, Any]) -> tuple[dict[str, Any], TranslationStats]:
    """Build the 25-field summary schema by extracting fields from the canonical."""
    stats = TranslationStats()
    schemas = spec["components"]["schemas"]
    product_def = schemas["Product"]
    properties = product_def.get("properties", {})

    fields: list[dict[str, Any]] = []
    missing_from_spec: list[str] = []

    for field_name in SUMMARY_FIELDS:
        if field_name not in properties:
            missing_from_spec.append(field_name)
            continue
        # Summary fields are always scalar — the slice excludes nested types
        translated = translate_property(
            field_name,
            properties[field_name],
            schemas,
            SUMMARY_REQUIRED,
            {},  # summary doesn't reference nested records
            stats,
        )
        if translated is not None:
            fields.append(translated)

    if missing_from_spec:
        sys.exit(
            f"❌ Summary fields missing from OpenAPI spec: {missing_from_spec}\n"
            f"   Either the spec changed or SUMMARY_FIELDS is wrong. Reconcile "
            f"the worked example before re-running."
        )

    stats.summary_fields = len(fields)

    summary = {
        "type": "record",
        "name": "ProductSummary",
        "namespace": CANONICAL_NAMESPACE,
        "doc": (
            "Slim representation of a Product. Carried in event payloads to "
            "avoid broadcasting the full canonical entity. Subscribers needing "
            "more fields call GET /v2/{ppid}."
        ),
        "metadata": {
            **OWNERSHIP_BLOCK,
            "x-pearson-derives-from": "product.product-value",
        },
        "fields": fields,
    }
    return summary, stats


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def write_schema(schema: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(schema, f, indent=2, ensure_ascii=False)
        f.write("\n")


def summarise(label: str, stats: TranslationStats) -> str:
    return (
        f"  {label}\n"
        f"    fields:                 {stats.canonical_fields or stats.summary_fields}\n"
        f"    nested types inlined:   {stats.nested_types_inlined}\n"
        f"    leakage fields dropped: {stats.leakage_fields_dropped}\n"
        f"    leakage refs dropped:   {stats.leakage_refs_dropped}\n"
        f"    date promotions:        {stats.date_fields_promoted}\n"
        f"    timestamp promotions:   {stats.timestamp_fields_promoted}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Translate Product OpenAPI spec to Avro canonical + summary."
    )
    parser.add_argument(
        "--input", required=True, type=Path, help="Path to productsList.json (OpenAPI spec)."
    )
    parser.add_argument(
        "--output-dir", type=Path, default=Path("data/canonical"), help="Output directory."
    )
    parser.add_argument("--dry-run", action="store_true", help="Print summary, do not write files.")
    args = parser.parse_args()

    if not args.input.exists():
        sys.exit(f"❌ Input not found: {args.input}")

    print(f"▶ Input:      {args.input}")
    print(f"▶ Output dir: {args.output_dir}\n")

    with args.input.open() as f:
        spec = json.load(f)

    canonical, can_stats = build_canonical(spec)
    summary, sum_stats = build_summary(spec)

    print("=== Canonical (product.product-value v1) ===")
    print(summarise("Build complete.", can_stats))
    print()
    print("=== Summary (product.summary-value v1) ===")
    print(summarise("Build complete.", sum_stats))

    if args.dry_run:
        print("\n(dry run, no files written)")
        return

    canonical_path = args.output_dir / "product-canonical.avsc"
    summary_path = args.output_dir / "product-summary.avsc"
    write_schema(canonical, canonical_path)
    write_schema(summary, summary_path)

    print(f"\n✓ Wrote {canonical_path} ({canonical_path.stat().st_size:,} bytes)")
    print(f"✓ Wrote {summary_path}    ({summary_path.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
