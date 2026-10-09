#!/usr/bin/env python3
"""Step 1 — detect affected domain contracts.

Diffs base..head, filters to domains/*/src/{api,events,schemas,mcp,agents}/**
(excluding READMEs), and resolves each changed file's registry coordinate
(x-registry for OpenAPI/AsyncAPI/MCP, or a plain top-level "registry" block
for a JSON Schema payload) so the CI workflow can matrix over the result.

Mirrors .github/workflows/domain-ci.yml's own detection style: a plain
`git diff`, no third-party action, so this never depends on the repo's
Actions allow-list.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

import yaml

CONTRACT_PATH_RE = re.compile(
    r"^domains/(?P<domain>[a-z0-9-]+)/src/(?P<capability>api|events|schemas|mcp|agents)/.*\.(yaml|yml|json)$"
)
CONTRACT_EXTENSIONS = {".yaml", ".yml", ".json"}


def changed_files(base: str, head: str, repo_root: Path) -> list[str]:
    out = subprocess.run(
        ["git", "diff", "--name-only", f"{base}..{head}"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=True,
    )
    return [line for line in out.stdout.splitlines() if line]


def is_contract_file(path: str) -> bool:
    if Path(path).name == "README.md":
        return False
    return bool(CONTRACT_PATH_RE.match(path))


def resolve_registry_coordinate(repo_root: Path, path: str) -> dict | None:
    """Reads x-registry (OpenAPI/AsyncAPI/MCP) or registry (plain JSON
    Schema payload) out of the file itself — stacks/apicurio/standards/guidelines.md
    §1: the coordinate is declared in the artifact, not only known to
    whoever runs the registration command."""
    full = repo_root / path
    if not full.exists():
        return None  # deleted in this diff
    try:
        text = full.read_text(encoding="utf-8")
        doc = yaml.safe_load(text) if full.suffix in (".yaml", ".yml") else json.loads(text)
    except (yaml.YAMLError, json.JSONDecodeError):
        return None
    if not isinstance(doc, dict):
        return None
    return doc.get("x-registry") or doc.get("registry")


def build_matrix(repo_root: Path, paths: list[str]) -> list[dict]:
    entries = []
    for path in paths:
        if not is_contract_file(path):
            continue
        match = CONTRACT_PATH_RE.match(path)
        assert match is not None
        coordinate = resolve_registry_coordinate(repo_root, path)
        entries.append(
            {
                "domain": match.group("domain"),
                "capability": match.group("capability"),
                "path": path,
                "artifact_group": (coordinate or {}).get("group"),
                "artifact_id": (coordinate or {}).get("artifact"),
                "compatibility": (coordinate or {}).get("compatibility"),
            }
        )
    return entries


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True, help="Base ref/SHA of the diff")
    parser.add_argument("--head", default="HEAD", help="Head ref/SHA of the diff")
    parser.add_argument("--repo-root", default=".", type=Path)
    args = parser.parse_args()

    paths = changed_files(args.base, args.head, args.repo_root)
    matrix = build_matrix(args.repo_root, paths)
    print(json.dumps(matrix))
    return 0


if __name__ == "__main__":
    sys.exit(main())
