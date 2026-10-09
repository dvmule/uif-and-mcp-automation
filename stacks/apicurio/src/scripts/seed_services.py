"""Seed the mock CMDB with additional services.

Adds 10 services across 8 domains by inserting CmdbCi entries directly into
backend/app/clients/cmdb_client.py. Each service's produces/consumes lists
reference subjects that exist in Apicurio (verified at run time), so the
service portfolio queries return real data.

Why mutate the file rather than maintain a fixture JSON?
  The CMDB mock is hard-coded Python by design (the file says so). This script
  keeps the one source of truth — the _FIXTURES dict — without introducing a
  parallel fixture format.

Idempotent: re-running detects services already present and skips them.

Usage:
    cd stacks/apicurio/src
    python scripts/seed_services.py --dry-run    # preview the entries
    python scripts/seed_services.py              # inject into cmdb_client.py
    python scripts/seed_services.py --validate   # check produces/consumes
                                                 # subjects exist in Apicurio
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import httpx

APICURIO_BASE_URL = os.environ.get("APICURIO_BASE_URL", "http://localhost:8080")
APICURIO_API_PATH = os.environ.get("APICURIO_API_PATH", "/apis/registry/v2")
API_URL = f"{APICURIO_BASE_URL.rstrip('/')}{APICURIO_API_PATH}"

CMDB_FILE = Path(__file__).resolve().parent.parent / "app" / "clients" / "cmdb_client.py"

# Sentinel comments in cmdb_client.py mark the injection point.
# If absent, the script adds them on first run, just before the closing brace
# of _FIXTURES. Subsequent runs replace the block between the markers.
INJECT_BEGIN = "    # >>> seeded by seed_services.py — do not edit by hand"
INJECT_END = "    # <<< end seed_services.py block"


@dataclass(slots=True)
class SeedService:
    ci_id: str
    name: str
    domain: str
    team_id: str
    team_email: str
    repo: str
    urn: str
    produces: list[str] = field(default_factory=list)
    consumes: list[str] = field(default_factory=list)


# 10 services. CI IDs continue the existing pattern (CI0093421+).
# All produces/consumes reference subjects from the seeded data
# (seed_apicurio.py + seed_catalog_svc.py + seed_mixed_formats.py).
SERVICES: list[SeedService] = [
    # ---- learning domain ------------------------------------------------- #
    SeedService(
        ci_id="CI0093440",
        name="course-svc",
        domain="learning",
        team_id="TM-LEARNING-PLATFORM",
        team_email="learning-platform@pearson.com",
        repo="pearson/course-svc",
        urn="urn:pearson:service:learning:course-svc",
        produces=[],  # passive consumer service for now
        consumes=[
            "product.product.created-value",
            "product.bundle.published-value",
            "content.asset.published-value",
        ],
    ),
    SeedService(
        ci_id="CI0093441",
        name="progress-svc",
        domain="learning",
        team_id="TM-LEARNING-PLATFORM",
        team_email="learning-platform@pearson.com",
        repo="pearson/progress-svc",
        urn="urn:pearson:service:learning:progress-svc",
        produces=["learning.progress.updated-value"],
        consumes=["learning.learner.enrolled-value"],
    ),

    # ---- assessment domain ----------------------------------------------- #
    SeedService(
        ci_id="CI0093442",
        name="assessment-svc",
        domain="assessment",
        team_id="TM-ASSESSMENT-PLATFORM",
        team_email="assessment-platform@pearson.com",
        repo="pearson/assessment-svc",
        urn="urn:pearson:service:assessment:assessment-svc",
        produces=[],
        consumes=[
            "learning.learner.enrolled-value",
            "learning.progress.updated-value",
        ],
    ),

    # ---- content domain -------------------------------------------------- #
    SeedService(
        ci_id="CI0093443",
        name="content-svc",
        domain="content",
        team_id="TM-CONTENT-PLATFORM",
        team_email="content-platform@pearson.com",
        repo="pearson/content-svc",
        urn="urn:pearson:service:content:content-svc",
        produces=["content.asset.published-value"],
        consumes=[],
    ),

    # ---- credentials domain ---------------------------------------------- #
    SeedService(
        ci_id="CI0093444",
        name="credentials-svc",
        domain="credentials",
        team_id="TM-CREDENTIALS-PLATFORM",
        team_email="credentials-platform@pearson.com",
        repo="pearson/credentials-svc",
        urn="urn:pearson:service:credentials:credentials-svc",
        produces=[],
        consumes=[
            "learning.progress.updated-value",
            "commerce.order.placed-value",
        ],
    ),

    # ---- identity domain ------------------------------------------------- #
    SeedService(
        ci_id="CI0093445",
        name="identity-svc",
        domain="identity",
        team_id="TM-IDENTITY-PLATFORM",
        team_email="identity-platform@pearson.com",
        repo="pearson/identity-svc",
        urn="urn:pearson:service:identity:identity-svc",
        produces=[],  # intentionally empty — no event seeded for this service
        consumes=["customer.customer.created-value"],
    ),

    # ---- commerce domain ------------------------------------------------- #
    SeedService(
        ci_id="CI0093446",
        name="subscription-svc",
        domain="commerce",
        team_id="TM-COMMERCE-PLATFORM",
        team_email="commerce-platform@pearson.com",
        repo="pearson/subscription-svc",
        urn="urn:pearson:service:commerce:subscription-svc",
        produces=[],
        consumes=[
            "commerce.order.placed-value",
            "commerce.payment.processed-value",
            "commerce.order.cancelled-value",
        ],
    ),

    # ---- finance domain -------------------------------------------------- #
    SeedService(
        ci_id="CI0093447",
        name="invoicing-svc",
        domain="finance",
        team_id="TM-FINANCE-PLATFORM",
        team_email="finance-platform@pearson.com",
        repo="pearson/invoicing-svc",
        urn="urn:pearson:service:finance:invoicing-svc",
        produces=[],
        consumes=[
            "commerce.order.placed-value",
            "commerce.payment.processed-value",
            "finance.pricing.changed-value",
        ],
    ),
    SeedService(
        ci_id="CI0093448",
        name="pricing-svc",
        domain="finance",
        team_id="TM-FINANCE-PRICING",
        team_email="finance-pricing@pearson.com",
        repo="pearson/pricing-svc",
        urn="urn:pearson:service:finance:pricing-svc",
        produces=["finance.pricing.changed-value"],
        consumes=["product.product.created-value"],
    ),

    # ---- analytics domain ------------------------------------------------ #
    SeedService(
        ci_id="CI0093449",
        name="analytics-svc",
        domain="analytics",
        team_id="TM-ANALYTICS-PLATFORM",
        team_email="analytics-platform@pearson.com",
        repo="pearson/analytics-svc",
        urn="urn:pearson:service:analytics:analytics-svc",
        produces=[],
        consumes=[
            "learning.learner.enrolled-value",
            "learning.progress.updated-value",
            "commerce.order.placed-value",
            "commerce.order.cancelled-value",
        ],
    ),
]


# --------------------------------------------------------------------------- #
# Apicurio validation
# --------------------------------------------------------------------------- #
def fetch_apicurio_subjects() -> set[str]:
    """Return the set of artifact IDs currently registered in Apicurio."""
    try:
        with httpx.Client(timeout=10.0) as client:
            r = client.get(f"{API_URL}/search/artifacts", params={"limit": 500})
            r.raise_for_status()
            return {a["id"] for a in r.json().get("artifacts", [])}
    except httpx.HTTPError as e:
        sys.exit(f"❌ Apicurio not reachable at {APICURIO_BASE_URL}: {e}")


def validate_links(services: list[SeedService], registered: set[str]) -> list[str]:
    """Check every produces/consumes subject is registered in Apicurio.

    Returns a list of error strings. Empty list means all good.
    """
    errors: list[str] = []
    for svc in services:
        for subject in svc.produces:
            if subject not in registered:
                errors.append(
                    f"{svc.ci_id} ({svc.name}): produces '{subject}' "
                    f"is not registered in Apicurio"
                )
        for subject in svc.consumes:
            if subject not in registered:
                errors.append(
                    f"{svc.ci_id} ({svc.name}): consumes '{subject}' "
                    f"is not registered in Apicurio"
                )
    return errors


# --------------------------------------------------------------------------- #
# Code generation
# --------------------------------------------------------------------------- #
def render_service(svc: SeedService) -> str:
    """Render one CmdbCi entry as a Python literal block."""
    produces_lit = format_string_list(svc.produces)
    consumes_lit = format_string_list(svc.consumes)
    return (
        f'    "{svc.ci_id}": CmdbCi(\n'
        f'        ci_id="{svc.ci_id}",\n'
        f'        name="{svc.name}",\n'
        f'        domain="{svc.domain}",\n'
        f'        team_id="{svc.team_id}",\n'
        f'        team_email="{svc.team_email}",\n'
        f'        repo="{svc.repo}",\n'
        f'        urn="{svc.urn}",\n'
        f"        produces={produces_lit},\n"
        f"        consumes={consumes_lit},\n"
        f"    ),\n"
    )


def format_string_list(items: list[str]) -> str:
    if not items:
        return "[]"
    if len(items) == 1:
        return f'["{items[0]}"]'
    inner = ",\n            ".join(f'"{x}"' for x in items)
    return "[\n            " + inner + ",\n        ]"


def render_block(services: list[SeedService]) -> str:
    body = "".join(render_service(s) for s in services)
    return f"{INJECT_BEGIN}\n{body}{INJECT_END}\n"


# --------------------------------------------------------------------------- #
# File mutation
# --------------------------------------------------------------------------- #
def inject_into_cmdb_file(block: str) -> tuple[bool, str]:
    """Inject the seeded block into cmdb_client.py.

    If the markers exist, replace the block between them.
    If not, insert the block just before the closing '}' of _FIXTURES.

    Returns (changed, message).
    """
    text = CMDB_FILE.read_text()

    if INJECT_BEGIN in text and INJECT_END in text:
        pattern = re.compile(
            rf"{re.escape(INJECT_BEGIN)}.*?{re.escape(INJECT_END)}\n",
            re.DOTALL,
        )
        new_text, n = pattern.subn(block, text)
        if n != 1:
            return False, f"expected one marker block, found {n}"
        if new_text == text:
            return False, "block already up to date"
        CMDB_FILE.write_text(new_text)
        return True, f"replaced existing block in {CMDB_FILE.name}"

    # No markers yet — insert before the closing brace of _FIXTURES.
    # Match: "}\n\n\n" preceded by a CmdbCi(...) entry. The simplest reliable
    # signal is the last "}," before the final "}" of the dict; we anchor on
    # the line containing only "}\n" that closes _FIXTURES.
    fixtures_close = re.search(
        r"^_FIXTURES: dict\[str, CmdbCi\] = \{.*?^\}\n",
        text,
        re.DOTALL | re.MULTILINE,
    )
    if not fixtures_close:
        return False, "could not locate _FIXTURES dict in cmdb_client.py"

    # Inject the block immediately before the closing }
    insert_at = fixtures_close.end() - len("}\n")
    new_text = text[:insert_at] + block + text[insert_at:]
    CMDB_FILE.write_text(new_text)
    return True, f"inserted new block into {CMDB_FILE.name}"


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main() -> None:
    parser = argparse.ArgumentParser(description="Seed mock CMDB with additional services.")
    parser.add_argument(
        "--dry-run", action="store_true", help="Print the rendered block, no writes."
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help="Check that every produces/consumes subject exists in Apicurio.",
    )
    parser.add_argument(
        "--skip-validate",
        action="store_true",
        help="Skip the Apicurio link validation step (default is to run it).",
    )
    args = parser.parse_args()

    print(f"▶ Services to seed: {len(SERVICES)}")
    print(f"▶ Target file:      {CMDB_FILE}\n")

    # Validate links unless explicitly skipped
    if not args.skip_validate:
        registered = fetch_apicurio_subjects()
        errors = validate_links(SERVICES, registered)
        if errors:
            print("❌ Schema link validation failed:\n")
            for e in errors:
                print(f"  - {e}")
            print(
                "\nFix: either register the missing subjects via the seed scripts "
                "or remove them from this script's SERVICES list. Use --skip-validate "
                "to bypass (not recommended)."
            )
            sys.exit(1)
        print(f"✓ All {sum(len(s.produces) + len(s.consumes) for s in SERVICES)} "
              f"schema links resolve in Apicurio.\n")

    if args.validate:
        # Validation-only mode: stop here.
        return

    block = render_block(SERVICES)

    if args.dry_run:
        print("─── Block that would be injected into cmdb_client.py ───\n")
        print(block)
        print("─── (dry run, no changes written) ───")
        return

    changed, msg = inject_into_cmdb_file(block)
    if not changed:
        print(f"○ {msg}")
    else:
        print(f"✓ {msg}")
        print(f"  Added {len(SERVICES)} service(s).")
        print("\nReview the diff with `git diff backend/app/clients/cmdb_client.py`")
        print("then restart the BFF (`make restart`) to pick up the new services.")


if __name__ == "__main__":
    main()