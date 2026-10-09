"""Clean up Apicurio artifacts.

Unlike `seed_apicurio.py --clean`, this script does NOT read from a seed list.
It enumerates whatever Apicurio actually contains and deletes it. That means
artifacts added by `seed_catalog_svc.py`, `seed_mixed_formats.py`, manual
registrations through the Apicurio UI, or any other source are all caught.

The seed scripts are the ones that drift; the cleanup script asks Apicurio
itself what's there.

Usage
-----
    cd stacks/apicurio/src

    # Preview what would be deleted (always run first):
    python scripts/cleanup_apicurio.py --dry-run

    # Delete every artifact in Apicurio:
    python scripts/cleanup_apicurio.py

    # Delete only artifacts in a specific group:
    python scripts/cleanup_apicurio.py --group default

    # Delete only artifacts whose ID matches a regex:
    python scripts/cleanup_apicurio.py --match '^product\\.'

    # Delete only artifacts of a given Apicurio type:
    python scripts/cleanup_apicurio.py --type JSON

    # Wipe everything: Apicurio + BFF cache:
    python scripts/cleanup_apicurio.py --all

    # Just the graphs, leave Apicurio alone:
    python scripts/cleanup_apicurio.py --graph-only

Filters compose with AND. Default scope is Apicurio only — graphs untouched
unless you ask. Confirmation prompt before any destructive action unless --yes.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass

import httpx

APICURIO_BASE_URL = os.environ.get("APICURIO_BASE_URL", "http://localhost:8080")
APICURIO_API_PATH = os.environ.get("APICURIO_API_PATH", "/apis/registry/v2")
API_URL = f"{APICURIO_BASE_URL.rstrip('/')}{APICURIO_API_PATH}"


BFF_BASE_URL = os.environ.get("BFF_BASE_URL", "http://localhost:8000")


@dataclass(slots=True)
class Artifact:
    group_id: str
    artifact_id: str
    type: str

    def display(self) -> str:
        return f"{self.group_id}/{self.artifact_id} [{self.type}]"


# --------------------------------------------------------------------------- #
# Apicurio
# --------------------------------------------------------------------------- #
def health_check(client: httpx.Client) -> None:
    try:
        r = client.get(f"{APICURIO_BASE_URL.rstrip('/')}/health", timeout=3.0)
        r.raise_for_status()
    except httpx.HTTPError as e:
        sys.exit(f"❌ Apicurio not reachable at {APICURIO_BASE_URL}: {e}")


def list_all_artifacts(client: httpx.Client) -> list[Artifact]:
    """Page through Apicurio's full artifact list."""
    artifacts: list[Artifact] = []
    offset = 0
    page_size = 200

    while True:
        r = client.get(
            f"{API_URL}/search/artifacts",
            params={"limit": page_size, "offset": offset},
        )
        r.raise_for_status()
        body = r.json()
        page = body.get("artifacts", [])
        for a in page:
            artifacts.append(
                Artifact(
                    group_id=a.get("groupId") or "default",
                    artifact_id=a["id"],
                    type=a.get("type", "UNKNOWN"),
                )
            )
        if len(page) < page_size:
            break
        offset += page_size

    return artifacts


def filter_artifacts(
    artifacts: list[Artifact],
    *,
    group: str | None,
    match: str | None,
    artifact_type: str | None,
) -> list[Artifact]:
    pattern = re.compile(match) if match else None
    out: list[Artifact] = []
    for a in artifacts:
        if group is not None and a.group_id != group:
            continue
        if artifact_type is not None and a.type.upper() != artifact_type.upper():
            continue
        if pattern is not None and not pattern.search(a.artifact_id):
            continue
        out.append(a)
    return out


def delete_artifact(client: httpx.Client, a: Artifact) -> tuple[bool, str]:
    r = client.delete(f"{API_URL}/groups/{a.group_id}/artifacts/{a.artifact_id}")
    if r.status_code == 204:
        return True, "deleted"
    if r.status_code == 404:
        return True, "already gone"
    return False, f"{r.status_code} {r.text[:140]}"



# --------------------------------------------------------------------------- #
# BFF cache
# --------------------------------------------------------------------------- #
def clear_bff_validator_cache() -> tuple[bool, str]:
    """Hit the validator's refresh endpoint so it stops serving stale results."""
    try:
        with httpx.Client(timeout=10.0) as client:
            r = client.post(f"{BFF_BASE_URL}/api/v1/schemas/non-compliant/refresh")
            if r.status_code in (200, 204):
                return True, "refreshed"
            return False, f"{r.status_code} {r.text[:140]}"
    except httpx.HTTPError as e:
        # BFF might not be running — that's fine for cleanup
        return False, f"BFF not reachable: {e}"


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def confirm(prompt: str, auto_yes: bool) -> bool:
    if auto_yes:
        return True
    answer = input(f"{prompt} [y/N]: ").strip().lower()
    return answer in ("y", "yes")


def main() -> None:
    parser = argparse.ArgumentParser(description="Clean up Apicurio artifacts.")

    # Filters
    parser.add_argument("--group", help="Only delete artifacts in this group.")
    parser.add_argument("--match", help="Regex on artifact ID — delete only matching.")
    parser.add_argument(
        "--type",
        dest="artifact_type",
        help="Only delete artifacts of this type (AVRO, JSON, PROTOBUF, OPENAPI).",
    )

    # Scope
    parser.add_argument("--all", action="store_true", help="Wipe Apicurio AND the BFF cache.")

    # Behaviour
    parser.add_argument("--dry-run", action="store_true", help="Preview deletions, no writes.")
    parser.add_argument("--yes", action="store_true", help="Skip confirmation prompt.")

    args = parser.parse_args()

    # ------------------------------------------------------------------ #
    # Apicurio cleanup  (--graph-only removed with the RDF graph, ADR-0007)
    # ------------------------------------------------------------------ #
    print(f"▶ Apicurio: {API_URL}")
    with httpx.Client(timeout=15.0) as client:
        health_check(client)

        all_artifacts = list_all_artifacts(client)
        targets = filter_artifacts(
            all_artifacts,
            group=args.group,
            match=args.match,
            artifact_type=args.artifact_type,
        )

        if not targets:
            print("○ No artifacts match the filters. Nothing to delete.")
        else:
            print(f"▶ {len(targets)} artifact(s) match (out of {len(all_artifacts)} total):\n")
            for a in targets:
                print(f"  - {a.display()}")

            if args.dry_run:
                print("\n(dry run, no deletions)")
            elif confirm(f"\nDelete {len(targets)} artifact(s)?", args.yes):
                print()
                deleted = failed = 0
                for a in targets:
                    ok, msg = delete_artifact(client, a)
                    marker = "✓" if ok else "✗"
                    print(f"  {marker} {a.display():<60} {msg}")
                    if ok:
                        deleted += 1
                    else:
                        failed += 1
                print(f"\nApicurio: deleted={deleted} failed={failed}")
            else:
                print("Aborted.")
                sys.exit(0)

    # Oxigraph graph cleanup removed with the RDF graph (ADR-0007).

    # ------------------------------------------------------------------ #
    # BFF validator cache
    # ------------------------------------------------------------------ #
    if args.all:
        print(f"\n▶ BFF validator cache: {BFF_BASE_URL}")
        if args.dry_run:
            print("  → would POST /api/v1/schemas/non-compliant/refresh")
        else:
            ok, msg = clear_bff_validator_cache()
            marker = "✓" if ok else "○"  # circle, not cross — BFF being down is fine
            print(f"  {marker} {msg}")

    print("\nDone.")


if __name__ == "__main__":
    main()