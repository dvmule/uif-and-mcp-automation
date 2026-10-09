# test_gold_mapping_intake_parity.py
# Intake <-> compiled-artifact parity (PR-review finding #19's root cause):
# product.mapping.json (the human-authored intake) and master.json (the
# compiled runtime truth) drifted apart twice — 'organizationid' vs
# 'organizationId' casing, and 'dimentionUomCode' vs 'dimensionUomCode' — and
# nothing caught either one automatically. This test makes that drift a
# hard failure in both directions:
#
#   forward: every mapping.json column resolves to a real master.json
#             property (dotted names -> struct fields; nested columns ->
#             array item fields, with a documented allowance for
#             single-field value-array encodings like webConsumingSystem
#             where the mapping declares [{"value": ...}] but master
#             encodes ARRAY<STRING>).
#   reverse: every master.json property has mapping.json coverage (struct
#             sub-fields need a dotted mapping entry; the struct's own
#             bare name does not).
#   channels: the mapping's per-column channels equal master's x-channels --
#             the two are read by different consumers (the projection
#             generator reads x-channels, the Event Fabric Processor reads
#             the mapping's flags), so a one-sided edit is publishable drift.
#
# Deliberately self-contained: no imports from src/apps/scripts/ or
# stacks/echo/src/apps/ (owned by other workers on this migration).

import copy
import json
import unittest
from pathlib import Path

QE_DIR = Path(__file__).resolve().parent
SCHEMAS = QE_DIR.parent / "src" / "schemas"

MASTER = json.loads(
    (SCHEMAS / "gold/product/v1/master.json").read_text(encoding="utf-8"))
MAPPING = json.loads(
    (SCHEMAS / "mapping/product/v1/product.mapping.json").read_text(encoding="utf-8"))


def _type_set(schema_fragment: dict) -> set:
    t = schema_fragment.get("type")
    if t is None:
        return set()
    return set(t) if isinstance(t, list) else {t}


def find_mapping_master_offenders(columns: list, master_properties: dict):
    """Return (forward_offenders, reverse_offenders) — human-readable
    strings describing every mismatch between a mapping intake's `columns`
    and a master schema's top-level `properties`. Empty/empty means the two
    artifacts are fully reconciled."""
    mapping_by_name = {c["goldColumnName"]: c for c in columns}

    forward = []
    for name, col in mapping_by_name.items():
        if "." in name:
            parent, sub = name.split(".", 1)
            parent_prop = master_properties.get(parent)
            if (parent_prop is None
                    or "object" not in _type_set(parent_prop)
                    or sub not in (parent_prop.get("properties") or {})):
                forward.append(
                    f"{name}: no matching struct field master.{parent}.{sub}")
            continue

        if col["kind"] == "nested":
            master_prop = master_properties.get(name)
            if master_prop is None or "array" not in _type_set(master_prop):
                forward.append(f"{name}: no matching array property in master.json")
                continue
            items = master_prop.get("items", {}) or {}
            field_names = {f["name"] for f in col["fields"]}
            if items.get("properties"):
                master_item_fields = set(items["properties"])
                if field_names != master_item_fields:
                    forward.append(
                        f"{name}: nested fields {sorted(field_names)} != "
                        f"master item fields {sorted(master_item_fields)}")
            elif items.get("type") == "string":
                # Documented allowance: mapping may declare a single-field
                # value-array encoding ([{"value": ...}]) for a master
                # property encoded as ARRAY<STRING>.
                if len(col["fields"]) != 1:
                    forward.append(
                        f"{name}: master encodes ARRAY<STRING> but mapping "
                        f"declares {len(col['fields'])} fields (expected "
                        f"exactly 1 under the value-array allowance)")
            else:
                forward.append(f"{name}: master.json items shape not understood: {items}")
            continue

        # scalar / conditional — must resolve to a plain top-level property
        if name not in master_properties:
            forward.append(f"{name}: no matching top-level property in master.json")

    reverse = []
    for name, prop in master_properties.items():
        types = _type_set(prop)
        if "object" in types and prop.get("properties"):
            for sub in prop["properties"]:
                dotted = f"{name}.{sub}"
                if dotted not in mapping_by_name:
                    reverse.append(f"master.{dotted}: no mapping.json column")
        elif "array" in types and isinstance(prop.get("items"), dict) \
                and prop["items"].get("properties"):
            col = mapping_by_name.get(name)
            if col is None or col["kind"] != "nested":
                reverse.append(f"master.{name}: no nested mapping.json column")
        elif "array" in types and isinstance(prop.get("items"), dict) \
                and prop["items"].get("type") == "string":
            col = mapping_by_name.get(name)
            if col is None or col["kind"] != "nested":
                reverse.append(
                    f"master.{name}: no nested mapping.json column (ARRAY<STRING>)")
        else:
            if name not in mapping_by_name:
                reverse.append(f"master.{name}: no mapping.json column")

    return forward, reverse


class TestIntakeCompiledParity(unittest.TestCase):
    def test_current_mapping_matches_master_exactly(self):
        forward, reverse = find_mapping_master_offenders(
            MAPPING["columns"], MASTER["properties"])
        self.assertEqual(forward, [],
                         "mapping.json columns with no matching master.json "
                         "property/field:\n" + "\n".join(forward))
        self.assertEqual(reverse, [],
                         "master.json properties/fields with no matching "
                         "mapping.json column:\n" + "\n".join(reverse))

    def test_check_would_have_caught_the_organizationid_casing_regression(self):
        # Historical bug: mapping.json had 'organizationid' (lowercase),
        # master.json/the PK column is camelCase 'organizationId'.
        columns = copy.deepcopy(MAPPING["columns"])
        for col in columns:
            if col["goldColumnName"] == "organizationId":
                col["goldColumnName"] = "organizationid"
                break
        else:
            self.fail("fixture assumption broken: 'organizationId' not found in mapping.json")

        forward, _ = find_mapping_master_offenders(columns, MASTER["properties"])
        self.assertTrue(
            any("organizationid" in o for o in forward),
            f"check failed to flag the organizationid casing regression; forward={forward}")

    def test_check_would_have_caught_the_dimension_uom_code_typo_regression(self):
        # Historical bug: mapping.json had 'dimentionUomCode' (typo),
        # master.json has the correct 'dimensionUomCode'.
        columns = copy.deepcopy(MAPPING["columns"])
        for col in columns:
            if col["goldColumnName"] == "dimensionUomCode":
                col["goldColumnName"] = "dimentionUomCode"
                break
        else:
            self.fail("fixture assumption broken: 'dimensionUomCode' not found in mapping.json")

        forward, reverse = find_mapping_master_offenders(columns, MASTER["properties"])
        self.assertTrue(
            any("dimentionUomCode" in o for o in forward),
            f"check failed to flag the dimentionUomCode typo regression; forward={forward}")
        # And with the typo'd name gone from the mapping, master's real
        # dimensionUomCode property loses its mapping coverage too.
        self.assertTrue(
            any("dimensionUomCode" in o for o in reverse),
            f"check failed to flag master.dimensionUomCode losing mapping "
            f"coverage; reverse={reverse}")


if __name__ == "__main__":
    unittest.main()


def test_master_descriptions_are_the_business_description():
    # Framework-owner convention (2026-08-17, tightened same day): master.json
    # is THE master schema and its description field carries the ACTUAL
    # business description — the intake's businessDescription VERBATIM, no
    # technical lineage appended (that lives in the mapping/manifest). Dotted
    # columns resolve to their struct field.
    bad = []
    for col in MAPPING["columns"]:
        name = col["goldColumnName"]
        bd = (col.get("businessDescription") or "").strip()
        if not bd:
            continue
        if "." in name:
            parent, field = name.split(".", 1)
            desc = MASTER["properties"][parent]["properties"][field].get("description") or ""
        else:
            desc = MASTER["properties"][name].get("description") or ""
        if desc != bd:
            bad.append(f"{name}: master description must equal the business "
                       f"description {bd[:50]!r}, got {desc[:50]!r}")
    assert not bad, bad


def test_channels_agree_between_the_artifacts():
    # The two artifacts each carry the channel gating independently, and the
    # two readers disagree about which one is authoritative: the projection
    # generator (stacks/event/scripts/generate_channel_schemas.py) reads
    # master.json's x-channels, while the Event Fabric Processor reads
    # mapping.json's channels flags from S3 ("channel gating lives HERE" —
    # stacks/event/src/glue/event_fabric_processor.py:14). A one-sided edit
    # therefore makes the PUBLISHED payload disagree with the REGISTERED
    # projection, silently. price/program/stock already gate this; product —
    # the domain that actually publishes — did not.
    TIERS = ("event", "message", "api")
    bad = []
    for col in MAPPING["columns"]:
        name = col["goldColumnName"]
        if "." in name:
            parent, field = name.split(".", 1)
            frag = (MASTER["properties"].get(parent) or {}).get(
                "properties", {}).get(field)
        else:
            frag = MASTER["properties"].get(name)
        if frag is None:
            # Resolution failures are the coverage test's job; don't mask them
            # as a channel mismatch.
            continue
        want = [t for t in TIERS if col["channels"][t]]
        got = frag.get("x-channels")
        if want != got:
            bad.append(f"{name}: mapping channels {want} != master "
                       f"x-channels {got}")
    assert not bad, bad


def test_nested_fields_carry_explicit_channels_and_business_descriptions():
    # Framework-owner convention (2026-08-17): master is self-describing —
    # every nested/struct field carries EXPLICIT x-channels (a subset of the
    # parent) and a business description; nothing relies on the untagged-
    # inherits-parent rule.
    bad = []
    for pname, prop in MASTER["properties"].items():
        t = prop.get("type")
        types = t if isinstance(t, list) else [t]
        if "array" in types and isinstance(prop.get("items"), dict)                 and prop["items"].get("properties"):
            container = prop["items"]["properties"]
        elif "object" in types and prop.get("properties"):
            container = prop["properties"]
        else:
            continue
        parent = set(prop.get("x-channels") or [])
        for fname, fprop in container.items():
            tags = fprop.get("x-channels")
            if tags is None:
                bad.append(f"{pname}.{fname}: no explicit x-channels")
            elif not set(tags) <= parent:
                bad.append(f"{pname}.{fname}: x-channels {tags} exceed the "
                           f"parent's {sorted(parent)}")
            if not (fprop.get("description") or "").strip():
                bad.append(f"{pname}.{fname}: no business description")
    assert not bad, bad
