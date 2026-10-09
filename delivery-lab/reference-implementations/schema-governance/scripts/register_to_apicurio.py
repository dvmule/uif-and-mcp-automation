#!/usr/bin/env python3
"""Step 4a — register/promote the approved contract into Apicurio Registry
under its artifact group, on merge to main only. No-ops (exit 0) if
APICURIO_REGISTRY_URL is unset.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

from apicurio_client import ApicurioClient, RegistryNotConfigured

CONTENT_TYPES = {".yaml": "application/yaml", ".yml": "application/yaml", ".json": "application/json"}


def load_coordinate(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    doc = yaml.safe_load(text) if path.suffix in (".yaml", ".yml") else json.loads(text)
    coordinate = doc.get("x-registry") or doc.get("registry")
    if not coordinate or "group" not in coordinate or "artifact" not in coordinate:
        raise SystemExit(f"{path}: missing an x-registry/registry block with group + artifact.")
    return coordinate


def register_one(client: ApicurioClient, path: Path) -> dict:
    coordinate = load_coordinate(path)
    content = path.read_text(encoding="utf-8")
    result = client.register_version(
        group=coordinate["group"],
        artifact=coordinate["artifact"],
        content=content,
        content_type=CONTENT_TYPES.get(path.suffix, "application/octet-stream"),
    )
    print(f"REGISTERED {coordinate['group']}:{coordinate['artifact']} -> version {result.get('version', '?')} ({path})")
    return {"group": coordinate["group"], "artifact": coordinate["artifact"], **result}


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: register_to_apicurio.py <contract-file> [...]", file=sys.stderr)
        return 2

    try:
        client = ApicurioClient()
    except RegistryNotConfigured as exc:
        print(f"SKIP: {exc}")
        return 0

    registered = [register_one(client, Path(p)) for p in argv[1:] if Path(p).exists()]
    # Emitted so a follow-up step (sync_to_glue_schema_registry.py) can be
    # driven from exactly what was just registered, rather than re-deriving it.
    print(json.dumps(registered))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
