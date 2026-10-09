# test_event_schemas.py — the program.created + program.updated event contracts.
# Ported from product's gate (PR-review findings #2, #3 — CRITICAL there, and
# the same two things must hold for every entity's events):
#
#   1. The worked example event validates cleanly against the data schema AND
#      against the Pearson CloudEvents envelope.
#   2. The data schema's shape (property set, nullability, required) stays an
#      exact projection of master.json's event-tier (x-channels: "event")
#      attributes — computed with the SAME rules
#      scripts/generate_tier_subsets.py uses, so drift between master.json and
#      the hand-authored event schema is caught immediately.
#
# Self-contained by design: no imports from another domain's tree (UIFPE-122),
# projection logic inlined.

import copy
import json
import unittest
from pathlib import Path

import jsonschema

QE_DIR = Path(__file__).resolve().parent
SCHEMAS = QE_DIR.parent / "src" / "schemas"
EVENTS_DIR = SCHEMAS / "events"

MASTER = json.loads(
    (SCHEMAS / "gold/program/v1/master.json").read_text(encoding="utf-8"))

# (data schema, worked example) pairs under events/ — one per event type.
EVENT_PAIRS = [
    ("program-created-data.json", "program-created-example.json"),
    ("program-updated-data.json", "program-updated-example.json"),
]

# The shared uif/pearson-event-envelope v1.0.0 schema, embedded verbatim from
# .claude/skills/generate-event-schema/SKILL.md so the worked example can be
# checked against the envelope shape without a network/APICURIO call.
ENVELOPE_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://schemas.pearson.com/uif/pearson-event-envelope/v1",
    "title": "Pearson CloudEvents Envelope",
    "type": "object",
    "properties": {
        "specversion": {"const": "1.0"},
        "id": {"type": "string", "minLength": 1},
        "source": {"type": "string", "format": "uri-reference",
                   "pattern": r"^urn:pearson:(svc|domain):[a-z0-9-]+$"},
        "type": {"type": "string",
                 "pattern": r"^com\.pearson\.[a-z0-9]+\.[a-z0-9-]+\.[a-z0-9]+\.v[0-9]+$"},
        "time": {"type": "string", "format": "date-time"},
        "datacontenttype": {"const": "application/json"},
        "dataschema": {"type": "string", "format": "uri"},
        "subject": {"type": "string", "pattern": r"^[a-z-]+/[A-Za-z0-9._-]+$"},
        "correlationid": {"type": "string", "format": "uuid"},
        "partitionkey": {"type": "string", "minLength": 1, "maxLength": 256},
        # Delivery-channel discriminator (the x-channels vocabulary).
        # Set on every envelope by the Event Fabric Processor and matched
        # by subscription rules alongside the per-operation detail-type.
        "channel": {"enum": ["event", "message", "api"]},
        "data": {"type": "object"},
        "causationid": {"type": "string"},
        "tenantid": {"type": "string"},
        "producerapp": {"type": "string"},
        "dataclassification": {"enum": ["public", "internal", "confidential", "restricted"]},
        "piipresent": {"type": "boolean"},
        "traceparent": {"type": "string",
                        "pattern": r"^[0-9]{2}-[0-9a-f]{32}-[0-9a-f]{16}-[0-9a-f]{2}$"},
        "tracestate": {"type": "string"},
        "sequence": {"type": "string"},
    },
    "required": [
        "specversion", "id", "source", "type", "time",
        "datacontenttype", "dataschema", "subject",
        "correlationid", "partitionkey", "channel", "data",
    ],
}


# ---------------------------------------------------------------------------
# Event-tier projection — same rules as scripts/generate_tier_subsets.py
# (_nested_container / _filter_nested / _filter_properties), inlined so this
# test has no dependency on that script's ownership or CLI surface.
# ---------------------------------------------------------------------------

def _nested_container(prop: dict):
    t = prop.get("type")
    types = t if isinstance(t, list) else [t]
    if "array" in types and isinstance(prop.get("items"), dict) \
            and prop["items"].get("properties"):
        return prop["items"]
    if "object" in types and prop.get("properties"):
        return prop
    return None


def _filter_nested(prop: dict, tier: str) -> None:
    container = _nested_container(prop)
    if not container:
        return
    kept = {}
    for fname, fprop in container["properties"].items():
        tags = fprop.get("x-channels")
        if tags is None or tier in tags:
            fp = copy.deepcopy(fprop)
            fp.pop("x-channels", None)
            kept[fname] = fp
    container["properties"] = kept
    if "required" in container:
        container["required"] = [r for r in container["required"] if r in kept]


def event_tier_projection(master: dict) -> dict:
    """Return {property_name: schema} for every master property tagged for
    the 'event' tier, with nested array/object fields filtered the same way,
    plus the filtered top-level 'required' list."""
    props_in = master.get("properties", {})
    props_out = {}
    for name, prop in props_in.items():
        channels = prop.get("x-channels", [])
        if "event" in channels:
            p = copy.deepcopy(prop)
            _filter_nested(p, "event")
            p.pop("x-channels", None)
            props_out[name] = p
    required = [r for r in master.get("required", []) if r in props_out]
    return {"properties": props_out, "required": required}


def _type_set(schema_fragment: dict) -> set:
    t = schema_fragment.get("type")
    if t is None:
        return set()
    return set(t) if isinstance(t, list) else {t}


class TestEventContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.projection = event_tier_projection(MASTER)
        cls.pairs = []
        for data_name, example_name in EVENT_PAIRS:
            data = json.loads(
                (EVENTS_DIR / data_name).read_text(encoding="utf-8"))
            example = json.loads(
                (EVENTS_DIR / example_name).read_text(encoding="utf-8"))
            cls.pairs.append((data_name, data, example))

    def test_data_schemas_are_valid_2020_12(self):
        for name, data, _ in self.pairs:
            with self.subTest(name):
                jsonschema.Draft202012Validator.check_schema(data)

    def test_example_data_validates_against_its_data_schema(self):
        for name, data, example in self.pairs:
            with self.subTest(name):
                validator = jsonschema.Draft202012Validator(data)
                errors = list(validator.iter_errors(example["data"]))
                self.assertEqual(errors, [],
                                 f"example data payload does not validate: "
                                 f"{[e.message for e in errors]}")

    def test_example_envelope_validates_against_pearson_envelope(self):
        validator = jsonschema.Draft202012Validator(
            ENVELOPE_SCHEMA, format_checker=jsonschema.FormatChecker())
        for name, _, example in self.pairs:
            with self.subTest(name):
                errors = list(validator.iter_errors(example))
                self.assertEqual(errors, [],
                                 f"example envelope does not validate: "
                                 f"{[e.message for e in errors]}")

    def test_dataschema_uri_is_namespaced_by_this_domain(self):
        # Unlike price/stock (relocated from domains/product/), these files
        # were authored in the right domain — the gate keeps them that way.
        for name, data, example in self.pairs:
            with self.subTest(name):
                self.assertIn("/program/", data["$id"])
                self.assertEqual(example["dataschema"], data["$id"])
                self.assertTrue(
                    example["type"].startswith("com.pearson.program."),
                    example["type"])

    def test_property_set_matches_event_tier_exactly(self):
        expected = set(self.projection["properties"])
        for name, data, _ in self.pairs:
            with self.subTest(name):
                actual = set(data["properties"])
                missing = expected - actual
                extra = actual - expected
                self.assertEqual(
                    (missing, extra), (set(), set()),
                    f"data schema property set drifted from master's event tier — "
                    f"missing (in master, not in data schema): {sorted(missing)}; "
                    f"extra (in data schema, not tagged 'event' in master): {sorted(extra)}")

    def test_required_matches_event_tier_exactly(self):
        for name, data, _ in self.pairs:
            with self.subTest(name):
                self.assertEqual(
                    set(data["required"]), set(self.projection["required"]),
                    "required set drifted — required must be exactly the "
                    "never-null event-tier fields per master.json's top-level "
                    "`required`")

    def test_top_level_nullability_matches_master(self):
        offenders = []
        for name, data, _ in self.pairs:
            for prop_name, expected_prop in self.projection["properties"].items():
                actual_prop = data["properties"].get(prop_name)
                if actual_prop is None:
                    continue  # reported by the property-set test
                expected_types = _type_set(expected_prop)
                actual_types = _type_set(actual_prop)
                if expected_types != actual_types:
                    offenders.append(
                        f"{name}/{prop_name}: master says type="
                        f"{sorted(expected_types)}, data schema says "
                        f"type={sorted(actual_types)}")
        self.assertEqual(offenders, [],
                         "nullability/type drift:\n" + "\n".join(offenders))

    def test_nested_struct_and_array_shapes_match_master(self):
        offenders = []
        for name, data, _ in self.pairs:
            for prop_name, expected_prop in self.projection["properties"].items():
                actual_prop = data["properties"].get(prop_name)
                if actual_prop is None:
                    continue
                expected_container = _nested_container(expected_prop)
                actual_container = _nested_container(actual_prop)
                if expected_container is None:
                    continue
                if actual_container is None:
                    offenders.append(
                        f"{name}/{prop_name}: master expects a nested "
                        f"object/array-of-object shape but the data schema's "
                        f"property is flat")
                    continue
                expected_fields = list(expected_container.get("properties", {}))
                actual_fields = list(actual_container.get("properties", {}))
                if expected_fields != actual_fields:
                    offenders.append(
                        f"{name}/{prop_name}: nested field set drifted — "
                        f"master {expected_fields}, data schema {actual_fields}")
        self.assertEqual(offenders, [],
                         "nested shape drift:\n" + "\n".join(offenders))

    def test_action_pair_data_schemas_differ_only_in_identity(self):
        # created/updated carry the SAME payload contract — both are the
        # event-tier projection of master; only $id, title, and the Apicurio
        # registry coordinate's artifact id may differ.
        # This is what lets every projection test above prove the whole
        # family while reading each file once.
        self.assertGreaterEqual(len(self.pairs), 2,
                                "the entity must carry both actions")
        stripped = [(name, {k: v for k, v in data.items()
                            if k not in ("$id", "title", "registry")})
                    for name, data, _ in self.pairs]
        base_name, base = stripped[0]
        for name, other in stripped[1:]:
            self.assertEqual(base, other, f"{base_name} vs {name}")


if __name__ == "__main__":
    unittest.main()
