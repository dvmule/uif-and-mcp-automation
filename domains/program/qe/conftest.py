# conftest.py
# sys.path setup for the program domain's test suite, mirroring the product
# domain's approach: there is no pyproject.toml (domain-root packaging files
# are blocked on a framework decision), so this file is the only thing that
# puts domain code on sys.path.
#
# Program has no src/apps/ yet — its runtime is the shared, entity-agnostic
# Glue jobs driven by gold_id config (see CLAUDE.md "Boundaries"). The paths
# below are therefore added ONLY if they exist, so the suite works today
# (schema/contract gates that import nothing) and keeps working when
# src/apps/pipelines/ arrives with UIFPE-121.

import sys
from pathlib import Path

DOMAIN = Path(__file__).resolve().parents[1]          # domains/program
for candidate in (DOMAIN / "src" / "apps",
                  DOMAIN / "src" / "apps" / "scripts",
                  DOMAIN / "src"):
    if candidate.is_dir() and str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))
