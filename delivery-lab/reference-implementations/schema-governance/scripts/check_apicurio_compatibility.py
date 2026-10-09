#!/usr/bin/env python3
"""Step 3 — backward/forward compatibility check against the live Apicurio
Registry. Dry-runs each changed contract's candidate content against its
own declared compatibility rule and exits non-zero (blocking the PR) on an
incompatible change. No-ops (exit 0) if APICURIO_REGISTRY_URL is unset —
see README.md "Why every script no-ops instead of failing when unconfigured".
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

from apicurio_client import ApicurioClient, RegistryNotConfigured

CONTENT_TYPES = {".yaml": "application/yaml", ".yml": "application/yaml", ".json": "application/json"}


def load_coordinate(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    doc = yaml.safe_load(text) if path.suffix in (".yaml", ".yml") else __import__("json").loads(text)
    coordinate = doc.get("x-registry") or doc.get("registry")
    if not coordinate or "group" not in coordinate or "artifact" not in coordinate:
        raise SystemExit(
            f"{path}: missing an x-registry/registry block with group + artifact "
            "(stacks/apicurio/standards/guidelines.md §1) — cannot resolve where to check compatibility."
        )
    return coordinate


def check_one(client: ApicurioClient, path: Path) -> bool:
    coordinate = load_coordinate(path)
    content = path.read_text(encoding="utf-8")
    result = client.check_compatibility(
        group=coordinate["group"],
        artifact=coordinate["artifact"],
        content=content,
        content_type=CONTENT_TYPES.get(path.suffix, "application/octet-stream"),
    )
    if result.compatible:
        print(f"OK  {coordinate['group']}:{coordinate['artifact']} — compatible ({path})")
        return True
    print(
        f"FAIL {coordinate['group']}:{coordinate['artifact']} — incompatible change rejected by "
        f"the registry: {result.reason}\n  ({path})",
        file=sys.stderr,
    )
    return False


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: check_apicurio_compatibility.py <changed-contract-file> [...]", file=sys.stderr)
        return 2

    try:
        client = ApicurioClient()
    except RegistryNotConfigured as exc:
        print(f"SKIP: {exc}")
        return 0

    all_ok = True
    for raw_path in argv[1:]:
        path = Path(raw_path)
        if not path.exists():
            continue  # deleted in this diff — nothing to check
        all_ok &= check_one(client, path)
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
