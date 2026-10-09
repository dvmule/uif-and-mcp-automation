"""Register the real Product artefacts in Apicurio.

Uploads four artefacts in dependency order:
  1. product.summary-value         (Avro, no references)
  2. product.product-value         (Avro, no references — replaces the synthetic v1)
  3. product.product.created-key   (Avro, no references)
  4. product.product.created-value (Avro, references product.summary-value)
  5. product.api                   (OpenAPI, no references)

Order matters: an artefact that uses Apicurio schema references can only be
registered after every artefact it references has been registered. Out-of-order
registration produces a 'reference not resolvable' error.

References on Apicurio v2:
  - Pass via body-shaped registration with Content-Type:
    application/create.extended+json
  - Header-shaped (X-Registry-References) is silently ignored on the
    /artifacts endpoint
  - References stored on Apicurio are visible via
    /ids/contentIds/{contentId}/references — NOT via the /meta endpoint
    (which silently omits the references field)

Run cleanup_apicurio.py first to wipe the synthetic product.* schemas before
this script lands the real ones.

Recommended sequence:
    cd stacks/apicurio/src

    python scripts/openapi_to_avro.py \\
        --input ../data/productsList.json \\
        --output-dir ../data/canonical/

    python scripts/cleanup_apicurio.py --match '^product\\.' --yes

    python scripts/register_real_product.py
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

APICURIO_BASE_URL = os.environ.get("APICURIO_BASE_URL", "http://localhost:8080")
APICURIO_API_PATH = os.environ.get("APICURIO_API_PATH", "/apis/registry/v2")
GROUP_ID = "default"
API_URL = f"{APICURIO_BASE_URL.rstrip('/')}{APICURIO_API_PATH}"

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_CANONICAL_DIR = SCRIPT_DIR.parent.parent / "data" / "canonical"
DEFAULT_EVENTS_DIR = SCRIPT_DIR.parent.parent / "data" / "events"
DEFAULT_OPENAPI_SPEC = SCRIPT_DIR.parent.parent / "data" / "productsList.json"


# --------------------------------------------------------------------------- #
# Registration plan
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class Artefact:
    artifact_id: str
    artifact_type: str
    content_type: str
    source_path: Path
    description: str
    references: list[dict[str, str]]
    label_keys: tuple[str, ...]


def build_plan(canonical_dir: Path, events_dir: Path, openapi_spec: Path) -> list[Artefact]:
    return [
        Artefact(
            artifact_id="product.summary-value",
            artifact_type="AVRO",
            content_type="application/json",
            source_path=canonical_dir / "product-summary.avsc",
            description="Slim Product representation carried in event payloads. "
                        "Subscribers needing more fields call GET /v2/{ppid}.",
            references=[],
            label_keys=("domain", "dataClassification"),
        ),
        Artefact(
            artifact_id="product.product-value",
            artifact_type="AVRO",
            content_type="application/json",
            source_path=canonical_dir / "product-canonical.avsc",
            description="Canonical Product entity. Mirrors GET /v2/{identifier} on Product API V2. "
                        "Source of truth for what a Product is across the enterprise.",
            references=[],
            label_keys=("domain", "dataClassification"),
        ),
        Artefact(
            artifact_id="product.product.created-key",
            artifact_type="AVRO",
            content_type="application/json",
            source_path=events_dir / "product.product.created-key.avsc",
            description="Kafka partition key for product.product.created. "
                        "ppid is the partitioning axis.",
            references=[],
            label_keys=("domain", "dataClassification"),
        ),
        Artefact(
            artifact_id="product.product.created-value",
            artifact_type="AVRO",
            content_type="application/json",
            source_path=events_dir / "product.product.created-value.avsc",
            description="Emitted when a product is published to the catalog. "
                        "Carries event metadata + a ProductSummary reference.",
            references=[
                {
                    "name": "com.pearson.product.canonical.ProductSummary",
                    "groupId": GROUP_ID,
                    "artifactId": "product.summary-value",
                    "version": "1",
                }
            ],
            label_keys=("domain", "dataClassification"),
        ),
        Artefact(
            artifact_id="product.api",
            artifact_type="OPENAPI",
            content_type="application/json",
            source_path=openapi_spec,
            description="OpenAPI 3.0.3 specification for Product API V2. "
                        "Source of truth for the canonical Product type.",
            references=[],
            label_keys=("domain", "dataClassification"),
        ),
    ]


# --------------------------------------------------------------------------- #
# Apicurio operations
# --------------------------------------------------------------------------- #
def health_check(client: httpx.Client) -> None:
    try:
        r = client.get(f"{APICURIO_BASE_URL.rstrip('/')}/health", timeout=3.0)
        r.raise_for_status()
    except httpx.HTTPError as e:
        sys.exit(f"❌ Apicurio not reachable at {APICURIO_BASE_URL}: {e}")


def load_content(path: Path) -> bytes:
    if not path.exists():
        sys.exit(f"❌ Source file not found: {path}")
    return path.read_bytes()


def parse_metadata_block(content: bytes, artifact_type: str) -> dict[str, Any]:
    try:
        doc = json.loads(content)
    except json.JSONDecodeError:
        return {}

    if artifact_type == "AVRO":
        return doc.get("metadata", {}) or {}
    if artifact_type == "OPENAPI":
        return doc.get("info", {}).get("x-pearson-metadata", {}) or {}
    return {}


def derive_properties(metadata: dict[str, Any]) -> dict[str, str]:
    if not metadata:
        return {}

    props: dict[str, str] = {}
    owner = metadata.get("owner", {}) or {}

    if "service" in owner:
        props["owner.service"] = str(owner["service"])
    if "team" in owner:
        props["owner.team"] = str(owner["team"])
    if "email" in owner:
        props["owner.email"] = str(owner["email"])

    for key in ("domain", "dataClassification", "lifecycle"):
        if key in metadata:
            props[key] = str(metadata[key])

    if "piiFields" in metadata:
        props["piiFields"] = json.dumps(metadata["piiFields"])

    return props


def derive_labels(metadata: dict[str, Any], label_keys: tuple[str, ...]) -> list[str]:
    return [str(metadata[k]) for k in label_keys if k in metadata]


def register_artefact(
    client: httpx.Client,
    artefact: Artefact,
    *,
    dry_run: bool,
) -> tuple[str, str | None, int | None, int]:
    """Register or update one artefact.

    Returns:
        (status, detail, content_id, references_returned_by_apicurio)
    """
    content = load_content(artefact.source_path)
    metadata = parse_metadata_block(content, artefact.artifact_type)

    if dry_run:
        size = len(content)
        ref_count = len(artefact.references)
        return (
            "planned",
            f"size={size}B refs-planned={ref_count} metadata-keys={len(metadata)}",
            None,
            0,
        )

    if artefact.references:
        # Body-shaped registration — required for references to land on Apicurio v2.
        body = {
            "type": artefact.artifact_type,
            "content": content.decode("utf-8"),
            "references": artefact.references,
        }
        r = client.post(
            f"{API_URL}/groups/{GROUP_ID}/artifacts",
            params={"ifExists": "RETURN_OR_UPDATE", "canonical": "false"},
            content=json.dumps(body),
            headers={
                "Content-Type": "application/create.extended+json",
                "X-Registry-ArtifactId": artefact.artifact_id,
                "X-Registry-ArtifactType": artefact.artifact_type,
            },
        )
    else:
        # Direct content registration — simpler, no body wrapping.
        r = client.post(
            f"{API_URL}/groups/{GROUP_ID}/artifacts",
            params={"ifExists": "RETURN_OR_UPDATE", "canonical": "false"},
            content=content,
            headers={
                "Content-Type": f"{artefact.content_type}; artifactType={artefact.artifact_type}",
                "X-Registry-ArtifactId": artefact.artifact_id,
                "X-Registry-ArtifactType": artefact.artifact_type,
            },
        )

    r.raise_for_status()
    response = r.json()
    version = str(response.get("version", "?"))
    content_id = response.get("contentId")
    refs_returned = len(response.get("references", []) or [])
    is_new = version == "1"

    # Push metadata (description, labels, properties) — separate API call.
    # ADR-0001: properties are a derived index, written alongside the body.
    # Note: do NOT include references here — Apicurio's /meta endpoint
    # silently strips references on update.
    meta_payload = {
        "name": artefact.artifact_id,
        "description": artefact.description,
        "labels": derive_labels(metadata, artefact.label_keys),
        "properties": derive_properties(metadata),
    }
    client.put(
        f"{API_URL}/groups/{GROUP_ID}/artifacts/{artefact.artifact_id}/meta",
        json=meta_payload,
    ).raise_for_status()

    return (
        "created" if is_new else "updated",
        version,
        content_id,
        refs_returned,
    )


# --------------------------------------------------------------------------- #
# Reference verification
# --------------------------------------------------------------------------- #
def verify_references(
    client: httpx.Client,
    artefacts: list[Artefact],
    content_ids: dict[str, int],
) -> list[str]:
    """Verify references resolved correctly via the contentId endpoint.

    Apicurio v2 exposes references at /ids/contentIds/{contentId}/references.
    The /meta endpoint silently omits them; the /versions/{v}/references
    endpoint does not exist on this Apicurio version.
    """
    errors: list[str] = []

    for artefact in artefacts:
        if not artefact.references:
            continue

        content_id = content_ids.get(artefact.artifact_id)
        if content_id is None:
            errors.append(
                f"{artefact.artifact_id}: no contentId captured during registration"
            )
            continue

        try:
            r = client.get(f"{API_URL}/ids/contentIds/{content_id}/references")
            r.raise_for_status()
            actual = r.json()
        except httpx.HTTPError as e:
            errors.append(f"{artefact.artifact_id}: failed to fetch references — {e}")
            continue

        expected = artefact.references
        if len(actual) != len(expected):
            errors.append(
                f"{artefact.artifact_id}: expected {len(expected)} reference(s), "
                f"got {len(actual)}"
            )
            continue

        for exp_ref in expected:
            match = next(
                (
                    act for act in actual
                    if act.get("name") == exp_ref["name"]
                    and act.get("artifactId") == exp_ref["artifactId"]
                ),
                None,
            )
            if match is None:
                errors.append(
                    f"{artefact.artifact_id}: reference to {exp_ref['name']} -> "
                    f"{exp_ref['artifactId']} not registered"
                )

    return errors


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main() -> None:
    parser = argparse.ArgumentParser(description="Register real Product artefacts in Apicurio.")
    parser.add_argument("--canonical-dir", type=Path, default=DEFAULT_CANONICAL_DIR,
                        help="Directory containing the canonical Avro files "
                             f"(default: {DEFAULT_CANONICAL_DIR}).")
    parser.add_argument("--events-dir", type=Path, default=DEFAULT_EVENTS_DIR,
                        help=f"Directory containing event schemas (default: {DEFAULT_EVENTS_DIR}).")
    parser.add_argument("--openapi-spec", type=Path, default=DEFAULT_OPENAPI_SPEC,
                        help=f"OpenAPI spec file (default: {DEFAULT_OPENAPI_SPEC}).")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show plan, no writes.")
    parser.add_argument("--skip-verify", action="store_true",
                        help="Skip the reference verification step after registration.")
    args = parser.parse_args()

    print(f"▶ Apicurio:        {API_URL}")
    print(f"▶ Group:           {GROUP_ID}")
    print(f"▶ Canonical dir:   {args.canonical_dir}")
    print(f"▶ Events dir:      {args.events_dir}")
    print(f"▶ OpenAPI spec:    {args.openapi_spec}\n")

    plan = build_plan(args.canonical_dir, args.events_dir, args.openapi_spec)

    missing = [a for a in plan if not a.source_path.exists()]
    if missing:
        print("❌ Missing source files:")
        for a in missing:
            print(f"   - {a.artifact_id}: {a.source_path}")
        sys.exit(1)

    print(f"▶ Will register {len(plan)} artefact(s) in dependency order:\n")
    for i, a in enumerate(plan, start=1):
        ref_note = f" → references {len(a.references)}" if a.references else ""
        print(f"  {i}. {a.artifact_id:<38} [{a.artifact_type}]{ref_note}")
    print()

    with httpx.Client(timeout=30.0) as client:
        health_check(client)

        results = {"created": 0, "updated": 0, "planned": 0, "failed": 0}
        content_ids: dict[str, int] = {}

        for a in plan:
            try:
                status, detail, content_id, refs_returned = register_artefact(
                    client, a, dry_run=args.dry_run
                )
                results[status] += 1
                if content_id is not None:
                    content_ids[a.artifact_id] = content_id

                marker = {"created": "✓", "updated": "↻", "planned": "→"}[status]

                # Annotate reference round-trip — Apicurio's response is the truth,
                # not what we sent. Mismatch surfaces silent-drop bugs immediately.
                if a.references and not args.dry_run:
                    refs_planned = len(a.references)
                    if refs_returned == refs_planned:
                        ref_note = f" (refs sent={refs_planned} returned={refs_returned} ✓)"
                    else:
                        ref_note = (
                            f" (refs sent={refs_planned} returned={refs_returned} ✗ MISMATCH)"
                        )
                elif a.references:
                    ref_note = f" (refs={len(a.references)})"
                else:
                    ref_note = ""

                print(f"  {marker} {a.artifact_id:<38} {detail or ''}{ref_note}")
            except httpx.HTTPStatusError as e:
                results["failed"] += 1
                print(f"  ✗ {a.artifact_id:<38} {e.response.status_code} {e.response.text[:200]}")
                print("\n❌ Halting registration. Subsequent artefacts may depend on this one.")
                sys.exit(1)

        print(
            f"\nRegistration: created={results['created']} updated={results['updated']} "
            f"planned={results['planned']} failed={results['failed']}"
        )

        if args.dry_run or args.skip_verify:
            return

        print("\n=== Reference verification ===")
        errors = verify_references(client, plan, content_ids)
        if errors:
            print("✗ Reference verification failed:")
            for e in errors:
                print(f"  - {e}")
            sys.exit(1)
        else:
            ref_count = sum(len(a.references) for a in plan)
            print(f"✓ All {ref_count} reference(s) resolved correctly.")


if __name__ == "__main__":
    main()
