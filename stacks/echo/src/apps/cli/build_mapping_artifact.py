#!/usr/bin/env python3
# build_mapping_artifact.py
# Serialize a Gold mapping manifest.yaml into a JSON artifact for the
# Pearson Schema Registry under the `engineering` group. Inherits owner +
# domain from the master schema so the registry view is internally consistent.
# See ADR-14.

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml


def build(manifest_path: Path, entity: str, version: str) -> dict:
    manifest = yaml.safe_load(manifest_path.read_text())

    schema_rel = (manifest.get("schema") or {}).get("master")
    master_meta: dict = {}
    if schema_rel:
        master_path = (manifest_path.parent / schema_rel).resolve()
        if master_path.exists():
            master_meta = json.loads(master_path.read_text()).get("metadata") or {}

    return {
        "$id":     f"https://schemas.pearson.com/gold/{entity}/{version}/mapping.json",
        "title":   f"{manifest.get('domain', entity).capitalize()} Gold mapping manifest",
        "version": version,
        "metadata": {
            "owner":              master_meta.get("owner", {}),
            "domain":             master_meta.get("domain", manifest.get("domain")),
            "dataClassification": "internal",
            "audience":           "engineering",
            "lifecycle":          "active",
        },
        "source_system":     manifest.get("source_system"),
        "canonical_lineage": manifest.get("canonical_lineage"),
        "sources":           manifest.get("sources"),
        "target":            manifest.get("target"),
        "output_columns":    manifest.get("output_columns"),
        "sql_relative_path": (manifest.get("sql") or {}).get("file"),
        # UIFPE-268: consumers reading the registry artifact should know which
        # source placeholders the table renders as NULL.
        "null_sentinels":    manifest.get("null_sentinels"),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--entity",   required=True)
    ap.add_argument("--version",  default="v1")
    ap.add_argument("--out",      required=True)
    args = ap.parse_args()

    artifact = build(Path(args.manifest), args.entity, args.version)

    missing = [k for k in ("source_system", "canonical_lineage", "sources",
                            "target", "output_columns") if not artifact.get(k)]
    if missing:
        print(f"[FAIL] manifest is missing fields: {missing}", file=sys.stderr)
        sys.exit(1)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(artifact, indent=2) + "\n")
    print(f"  wrote {out_path} ({len(artifact['canonical_lineage'])} lineage entries)")


if __name__ == "__main__":
    main()
