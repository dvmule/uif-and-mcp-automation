#!/usr/bin/env python3
"""Validate every registered Gold master + mapping manifest for one or more
domains. Exit 0 only if everything checked out AND something was checked.

WHY THIS IS A SCRIPT (UIFPE-121). These checks used to live as heredoc Python
inside .github/workflows/product-gold-schemas.yml, which had three consequences:

  1. They were HARDCODED to `domains/product/**`, so the program domain's
     artifacts were validated by nothing at all. Cloning the workflow would have
     produced a FALSE GREEN — see (2).
  2. The master glob exited 0 when it matched nothing ("nothing to validate").
     A clone with a mistyped path, or a domain whose schemas move, therefore
     reports success while checking zero files. This module FAILS CLOSED instead:
     a domain named on the command line must yield at least one master, and every
     master must have a manifest.
  3. Nothing inline in a workflow can be unit-tested, so the gates themselves had
     no gates. qe/test_validate_gold_schemas.py mutation-tests them.

Entity-agnostic by design: it discovers domains and entities from the tree, so a
ninth domain needs no change here.

Usage:
    validate_gold_schemas.py --domain product [--domain program]
    validate_gold_schemas.py --all-domains
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

import yaml

try:                                        # optional: SQL parse check
    import sqlparse
except ImportError:                         # pragma: no cover
    sqlparse = None

# The runtime's pure-logic library (stdlib only) — scripts/ and glue_jobs/
# are siblings under src/apps/, and this lint must apply the SAME rules the
# transform applies (null_sentinels, UIFPE-268).
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))
import gold_cdc  # noqa: E402

try:
    from jsonschema import Draft7Validator
except ImportError:                         # pragma: no cover
    Draft7Validator = None

# Master metadata the BFF/registry contract requires. `audience` is asserted to
# be exactly "engineering" because a master is the ENGINEERING artifact —
# consumer-facing subsets are derived by generate_tier_subsets.py (ADR-14), and a
# master tagged for consumers means someone published the wrong file.
_REQUIRED_META = ("owner", "domain", "dataClassification", "audience", "lifecycle")
_REQUIRED_MANIFEST_KEYS = ("domain", "version", "source_system", "canonical_lineage",
                           "schema", "sql", "sources", "target", "output_columns")


def repo_root() -> Path:
    """stacks/echo/src/apps/cli/<this> -> repo root."""
    return Path(__file__).resolve().parents[5]


def discover_domains(root: Path) -> list[str]:
    return sorted(
        p.name for p in (root / "domains").iterdir()
        if p.is_dir() and (p / "src" / "schemas" / "gold").is_dir()
    )


def _masters(root: Path, domain: str) -> list[Path]:
    base = root / "domains" / domain / "src" / "schemas" / "gold"
    return sorted(base.glob("*/v*/master.json"))


def _manifests(root: Path, domain: str) -> list[Path]:
    base = root / "domains" / domain / "src" / "schemas" / "mapping"
    return sorted(base.glob("*/v*/manifest.yaml"))


def check_master(path: Path, fail) -> None:
    try:
        schema = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        return fail(f"{path}: unreadable JSON: {e}")
    if Draft7Validator is not None:
        try:
            Draft7Validator.check_schema(schema)
        except Exception as e:
            return fail(f"{path}: not valid JSON Schema Draft-07: {e}")
    meta = schema.get("metadata") or {}
    for required in _REQUIRED_META:
        if required not in meta:
            fail(f"{path}: missing metadata.{required}")
    if meta and meta.get("audience") != "engineering":
        fail(f"{path}: metadata.audience must be 'engineering' on a master "
             f"(got {meta.get('audience')!r}); consumer subsets are derived by "
             f"scripts/generate_tier_subsets.py — see ADR-14")
    if "service" not in (meta.get("owner") or {}):
        fail(f"{path}: missing metadata.owner.service")


def check_manifest(path: Path, fail) -> None:
    try:
        manifest = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as e:
        return fail(f"{path}: invalid YAML: {e}")
    if not isinstance(manifest, dict):
        return fail(f"{path}: manifest must be a mapping")

    for req in _REQUIRED_MANIFEST_KEYS:
        if req not in manifest:
            fail(f"{path}: missing '{req}'")

    if not (manifest.get("source_system") or {}).get("name"):
        fail(f"{path}: source_system.name is required")

    # ADR-0003 D1: every source declares key_mapping, and it may only map Gold
    # PK columns. A source without one makes the transform refuse to run; one
    # mapping a non-PK column would silently fan out to the wrong rows.
    #
    # `lookup_only: true` is the one documented exception (ADR-0003 D1
    # amendment, 2026-08-31): a taxonomy dimension carries NO Gold PK column,
    # so no expression over a changed row could name the rows it affects.
    # It is registered FULL for the mapping SQL and skipped by Phase 1. Keep
    # this in step with gold_cdc.validate_key_mappings — this lint runs in CI
    # before any Glue job does, so a divergence here fails the PR for a
    # manifest the transform would happily accept.
    pks_target = set((manifest.get("target") or {}).get("primary_key_cols") or [])
    for src in (manifest.get("sources") or []):
        alias = src.get("alias", "<missing>")
        km = src.get("key_mapping")
        if src.get("lookup_only"):
            if src.get("is_source_of_truth"):
                fail(f"{path}: sources[{alias}] is both lookup_only and "
                     f"is_source_of_truth — the driving source must map the "
                     f"full primary key")
            if km:
                fail(f"{path}: sources[{alias}] declares BOTH lookup_only and "
                     f"key_mapping — pick one (ADR-0003 D1 amendment)")
            continue
        if not km:
            fail(f"{path}: sources[{alias}] has no key_mapping (ADR-0003 D1) — "
                 f"a pure dimension carrying no Gold PK column declares "
                 f"lookup_only: true instead")
            continue
        unknown = set(km) - pks_target
        if unknown:
            fail(f"{path}: sources[{alias}].key_mapping maps non-PK columns "
                 f"{sorted(unknown)}")

    # UIFPE-268: declared placeholder values the transform turns into NULL at
    # the Silver view boundary. The RUNTIME validator is called, not mirrored,
    # so a declaration that passes CI cannot fail the first Glue run.
    if manifest.get("null_sentinels") is not None:
        try:
            gold_cdc.validate_null_sentinels(manifest)
        except ValueError as e:
            fail(f"{path}: {e}")
        # The intake compiler appends lineage entries immediately before the
        # `schema:` key (end of canonical_lineage) by TEXT position. A block
        # sitting between canonical_lineage and schema would swallow every
        # compiled entry, so it must come after schema.
        text = path.read_text(encoding="utf-8")
        ns_at = re.search(r"(?m)^null_sentinels:", text)
        schema_at = re.search(r"(?m)^schema:", text)
        if ns_at and schema_at and ns_at.start() < schema_at.start():
            fail(f"{path}: null_sentinels must be declared AFTER the schema: "
                 f"key — the intake compiler inserts lineage entries just "
                 f"before schema: and would write them inside the block")

    lineage = manifest.get("canonical_lineage") or {}
    schema_rel = (manifest.get("schema") or {}).get("master")
    if schema_rel:
        master_path = Path(os.path.normpath(path.parent / schema_rel))
        try:
            master = json.loads(master_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            fail(f"{path}: master.json not found at {master_path}")
        else:
            master_props = set((master.get("properties") or {}).keys())
            missing = master_props - set(lineage)
            if missing:
                fail(f"{path}: canonical_lineage missing entries for master.json "
                     f"properties: {sorted(missing)}")
            extras = set(lineage) - master_props
            if extras:
                fail(f"{path}: canonical_lineage has entries not present in "
                     f"master.json: {sorted(extras)}")
            # ADR-0003 D11: an ARRAY property without a sort key has unstable
            # hashes, so every consumer is pinged on every run.
            sort_keys = manifest.get("array_sort_keys") or {}
            for name, prop in (master.get("properties") or {}).items():
                t = prop.get("type")
                types = t if isinstance(t, list) else [t]
                if "array" in types and name not in sort_keys:
                    fail(f"{path}: array property '{name}' has no "
                         f"array_sort_keys entry (ADR-0003 D11)")

    pks_declared = {col for col, ent in lineage.items()
                    if isinstance(ent, dict) and ent.get("role") == "pk"}
    if pks_declared != pks_target:
        fail(f"{path}: canonical_lineage role:pk ({sorted(pks_declared)}) != "
             f"target.primary_key_cols ({sorted(pks_target)})")

    for col, ent in lineage.items():
        if not isinstance(ent, dict):
            fail(f"{path}: canonical_lineage.{col} must be a mapping")
            continue
        if "conditional" in ent:
            if not (ent.get("discriminator") or {}).get("pdh_column"):
                fail(f"{path}: canonical_lineage.{col} conditional entry needs "
                     f"discriminator.pdh_table/pdh_column")
            for i, br in enumerate(ent["conditional"] or []):
                for field in ("when", "pdh_table", "pdh_column"):
                    if not br.get(field):
                        fail(f"{path}: canonical_lineage.{col}.conditional[{i}]."
                             f"{field} is required")
            continue
        for field in ("pdh_table", "pdh_column"):
            if not ent.get(field):
                fail(f"{path}: canonical_lineage.{col}.{field} is required")

    sql_rel = (manifest.get("sql") or {}).get("file")
    if sql_rel:
        sql_path = path.parent / sql_rel
        try:
            text = sql_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            fail(f"{sql_path}: not found")
        else:
            # This is a smoke test — "is there real SQL here?" — not a
            # semantic parse. sqlparse carries a DoS guard
            # (MAX_GROUPING_TOKENS = 10000, MAX_GROUPING_DEPTH) and RAISES
            # SQLParseError on a large file, which propagated as an uncaught
            # crash instead of a clean pass/fail: product.sql crossed the
            # token limit at 712 lines (2026-08-31, the `categories` column),
            # and every mapping SQL is on a one-way growth path. A file too
            # big for sqlparse's guard is not evidence of invalid SQL, so fall
            # back to the structural check rather than failing or exploding —
            # and never disable the library's limit.
            parsed = None
            if sqlparse is not None:
                try:
                    parsed = sqlparse.parse(text)
                except sqlparse.exceptions.SQLParseError as e:
                    print(f"[INFO] {sql_path}: too large for sqlparse's "
                          f"safety limits ({e}) — falling back to the "
                          f"structural check")
            if parsed is not None and not parsed:
                fail(f"{sql_path}: empty/invalid SQL")
            elif not text.strip():
                fail(f"{sql_path}: empty SQL")
            elif parsed is None and "SELECT" not in text.upper():
                fail(f"{sql_path}: no SELECT — not a mapping query")


def validate_domain(root: Path, domain: str, fail) -> int:
    """Validate one domain. Returns the number of artifacts checked.

    FAIL-CLOSED (the false green this module exists to remove): a domain asked
    for by name must have at least one master, and every master must have a
    manifest beside it. Zero-artifact success is indistinguishable from a
    mistyped path, and a master with no manifest cannot run at all."""
    masters = _masters(root, domain)
    if not masters:
        fail(f"domains/{domain}: no Gold master schemas found under "
             f"src/schemas/gold/*/v*/master.json — refusing to report success "
             f"for a domain with nothing to validate (a passing run on zero "
             f"files is how a mistyped path looks)")
        return 0

    for m in masters:
        check_master(m, fail)

    manifests = _manifests(root, domain)
    by_entity_version = {(p.parent.parent.name, p.parent.name) for p in manifests}
    for m in masters:
        entity, version = m.parent.parent.name, m.parent.name
        try:
            lifecycle = (json.loads(m.read_text(encoding="utf-8"))
                         .get("metadata") or {}).get("lifecycle")
        except (OSError, json.JSONDecodeError):
            lifecycle = None
        if (entity, version) not in by_entity_version:
            # A DRAFT master is a contract-first entity (price/stock,
            # 2026-08-17): schemas authored, deliberately not yet
            # transformable, declared as such in metadata.lifecycle. Skipping
            # it here is lifecycle-GATED, not a hole — any other lifecycle
            # without a manifest still fails closed below.
            if lifecycle == "draft":
                print(f"[SKIP] domains/{domain}: master {entity}/{version} is "
                      f"lifecycle=draft with no manifest — contract-first, "
                      f"not yet transformable; the manifest requirement "
                      f"applies when it leaves draft")
                continue
            fail(f"domains/{domain}: master {entity}/{version} has no mapping "
                 f"manifest at src/schemas/mapping/{entity}/{version}/"
                 f"manifest.yaml — the entity cannot be transformed without one")
        elif lifecycle == "draft":
            # UIFPE-578. The converse of the skip above, and the hole it sat in:
            # a manifest means the entity IS transformable, so "draft" is no
            # longer true of it. Four live entities carried a stale draft for
            # weeks, which badged them as drafts in Transform, gave them
            # re-scaffold semantics on import, and made them unpublishable to a
            # registry whose vocabulary is {active, deprecated}. Nothing
            # promotes this field, so nothing but a gate will keep it honest.
            fail(f"domains/{domain}: master {entity}/{version} declares "
                 f"lifecycle=draft but has a mapping manifest — a manifest "
                 f"means it is transformable, so it has left draft. Set "
                 f"metadata.lifecycle to a value the registry accepts "
                 f"(active or deprecated), or remove the manifest")

    for mf in manifests:
        check_manifest(mf, fail)

    return len(masters) + len(manifests)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--domain", action="append", default=[],
                    help="domain to validate; repeatable")
    ap.add_argument("--all-domains", action="store_true",
                    help="validate every domain that registers a Gold schema")
    ap.add_argument("--repo-root", default=None)
    a = ap.parse_args(argv)

    root = Path(a.repo_root).resolve() if a.repo_root else repo_root()
    domains = list(dict.fromkeys(a.domain))
    if a.all_domains:
        domains = discover_domains(root)
    if not domains:
        print("[FAIL] no domain selected — pass --domain <name> or --all-domains")
        return 2

    failures: list[str] = []

    def fail(msg: str) -> None:
        failures.append(msg)
        print(f"[FAIL] {msg}")

    checked = 0
    for d in domains:
        n = validate_domain(root, d, fail)
        checked += n
        if n:
            print(f"[OK]   domains/{d}: {n} artifact(s) checked")

    if failures:
        print(f"\n{len(failures)} problem(s) across {len(domains)} domain(s)")
        return 1
    if not checked:
        print("[FAIL] nothing was validated")
        return 1
    print(f"\nall good — {checked} artifact(s) across {len(domains)} domain(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
