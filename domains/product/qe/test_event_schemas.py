# test_event_schemas.py
# Guardrails for the product.created event contract (PR-review findings #2,
# #3 — CRITICAL). Two things must always hold:
#
#   1. The worked example event validates cleanly against the data schema.
#   2. The data schema's shape (property set, nullability, required) stays
#      an exact projection of master.json's event-tier (x-channels: "event")
#      attributes — computed here with the SAME rules
#      scripts/generate_tier_subsets.py uses, so drift between master.json
#      and the hand-authored event schema is caught immediately.
#
# Deliberately does NOT import the gold runtime or CLIs (stacks/echo/src/apps/)
# (owned by other workers on this migration) — the projection logic is
# inlined below instead.

import copy
import json
import unittest
from pathlib import Path

import jsonschema

QE_DIR = Path(__file__).resolve().parent
SCHEMAS = QE_DIR.parent / "src" / "schemas"
EVENTS_DIR = SCHEMAS / "events"

MASTER = json.loads(
    (SCHEMAS / "gold/product/v1/master.json").read_text(encoding="utf-8"))
DATA_SCHEMA = json.loads(
    (EVENTS_DIR / "product-created-data.json").read_text(encoding="utf-8"))
EXAMPLE = json.loads(
    (EVENTS_DIR / "product-created-example.json").read_text(encoding="utf-8"))

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


class TestExampleValidatesAgainstDataSchema(unittest.TestCase):
    def test_data_schema_is_valid_2020_12(self):
        jsonschema.Draft202012Validator.check_schema(DATA_SCHEMA)

    def test_example_data_validates_against_data_schema(self):
        validator = jsonschema.Draft202012Validator(DATA_SCHEMA)
        errors = list(validator.iter_errors(EXAMPLE["data"]))
        self.assertEqual(errors, [],
                         f"example data payload does not validate: "
                         f"{[e.message for e in errors]}")

    def test_example_envelope_validates_against_pearson_envelope(self):
        validator = jsonschema.Draft202012Validator(
            ENVELOPE_SCHEMA, format_checker=jsonschema.FormatChecker())
        errors = list(validator.iter_errors(EXAMPLE))
        self.assertEqual(errors, [],
                         f"example envelope does not validate: "
                         f"{[e.message for e in errors]}")


class TestDataSchemaMatchesMasterEventTier(unittest.TestCase):
    """Guards against the exact class of drift found in findings #2/#3: the
    hand-authored event data schema silently diverging from master.json's
    x-channels-tagged event tier (wrong nullability, phantom/missing
    properties, wrong array encodings)."""

    @classmethod
    def setUpClass(cls):
        cls.projection = event_tier_projection(MASTER)

    def test_property_set_matches_event_tier_exactly(self):
        expected = set(self.projection["properties"])
        actual = set(DATA_SCHEMA["properties"])
        missing = expected - actual
        extra = actual - expected
        self.assertEqual(
            (missing, extra), (set(), set()),
            f"data schema property set drifted from master's event tier — "
            f"missing (in master, not in data schema): {sorted(missing)}; "
            f"extra (in data schema, not tagged 'event' in master): {sorted(extra)}")

    def test_required_matches_event_tier_exactly(self):
        self.assertEqual(
            set(DATA_SCHEMA["required"]), set(self.projection["required"]),
            "required set drifted — required must be exactly the never-null "
            "event-tier fields per master.json's top-level `required`")

    def test_top_level_nullability_matches_master(self):
        offenders = []
        for name, expected_prop in self.projection["properties"].items():
            actual_prop = DATA_SCHEMA["properties"].get(name)
            if actual_prop is None:
                continue  # already reported by test_property_set_matches_event_tier_exactly
            expected_types = _type_set(expected_prop)
            actual_types = _type_set(actual_prop)
            if expected_types != actual_types:
                offenders.append(
                    f"{name}: master says type={sorted(expected_types)}, "
                    f"data schema says type={sorted(actual_types)}")
        self.assertEqual(offenders, [], "nullability/type drift:\n" + "\n".join(offenders))

    def test_nested_struct_and_array_shapes_match_master(self):
        # fulfillmentOrgDetails (object) and contributors (array of struct)
        # must carry exactly the event-tier field set — this is where the
        # marketingCopy-inclusion and affiliation-field-leak bugs lived.
        offenders = []
        for name, expected_prop in self.projection["properties"].items():
            actual_prop = DATA_SCHEMA["properties"].get(name)
            if actual_prop is None:
                continue
            expected_container = _nested_container(expected_prop)
            actual_container = _nested_container(actual_prop)
            if expected_container is None:
                continue
            if actual_container is None:
                offenders.append(f"{name}: master expects a nested object/array-of-object "
                                 f"shape but the data schema's property is flat")
                continue
            expected_fields = set(expected_container.get("properties", {}))
            actual_fields = set(actual_container.get("properties", {}))
            if expected_fields != actual_fields:
                offenders.append(
                    f"{name}: nested field set drifted — missing "
                    f"{sorted(expected_fields - actual_fields)}, extra "
                    f"{sorted(actual_fields - expected_fields)}")
        self.assertEqual(offenders, [], "nested shape drift:\n" + "\n".join(offenders))

    def test_web_consuming_system_is_a_struct_array_since_2026_09_07(self):
        # Owner item 10 (UIFPE-270): the plain string array became
        # {wcsName, endDate}. Both halves pinned: shape AND the old encoding
        # not creeping back.
        for schema in (DATA_SCHEMA, self.projection):
            items = schema["properties"]["webConsumingSystem"]["items"]
            self.assertEqual(set(items["properties"]), {"wcsName", "endDate"})

    def test_array_of_primitive_encodings_match_master(self):
        # programPpid: master encodes it as ARRAY<STRING>. The data schema
        # must match — not the mapping intake's [{"value": ...}] shape.
        for name in ("associatedPrograms",):
            master_items = self.projection["properties"][name]["items"]
            data_items = DATA_SCHEMA["properties"][name]["items"]
            self.assertEqual(master_items.get("type"), "string",
                             f"master {name}.items expected to be a plain string")
            self.assertEqual(data_items.get("type"), "string",
                             f"data schema {name}.items must be {{'type': 'string'}} "
                             f"(ARRAY<STRING>), not an object — got {data_items}")
            self.assertNotIn("properties", data_items,
                             f"data schema {name}.items must not be an object "
                             f"wrapper (e.g. {{'value': ...}})")

    def test_marketing_copy_promoted_to_event_2026_08_31(self):
        # Framework owner, 2026-08-31: marketingCopy joins the event tier
        # with ALL its fields (it was message/api-only before — this test
        # used to pin the exclusion; this is the deliberate reversal).
        for schema in (DATA_SCHEMA, self.projection):
            mc = schema["properties"].get("marketingCopy")
            self.assertIsNotNone(mc, "marketingCopy missing from the event tier")
            self.assertEqual(
                set(mc["items"]["properties"]),
                {"contentId", "marketingCopyText", "marketingCopyTypeCode", "audienceCode",
                 "geoVisibilityCode", "owningMarketCode",
                 # + the content-service pointer (UIFPE-253)
                 "contentUrl"})

    def test_cover_image_high_res_promoted_to_event_2026_09_07(self):
        # Owner decision 2026-09-07 (UIFPE-270): coverImageHighRes joins the
        # event tier (it was message/api-only; this test used to pin the
        # exclusion — this is the deliberate reversal).
        self.assertIn("coverImageHighResolutionUrl", DATA_SCHEMA["properties"])
        self.assertIn("coverImageHighResolutionUrl", self.projection["properties"])

    def test_2026_09_07_batch_is_on_the_event_tier(self):
        # UIFPE-270: every new column ships on all three channels, so each
        # must appear in the event data schema — with its nested shape.
        for name in ("courseDurationQuantity", "courseDurationUnitCode", "productUrl",
                     "enablingTechnology"):
            self.assertIn(name, DATA_SCHEMA["properties"], name)
        self.assertEqual(
            set(DATA_SCHEMA["properties"]["enablingTechnology"]["items"]["properties"]),
            {"enablingTechnologyLevel1", "enablingTechnologyLevel2"})
        fod = DATA_SCHEMA["properties"]["fulfillmentOrgDetails"]["properties"]
        self.assertEqual(
            set(fod["marketingInitiative"]["items"]["properties"]),
            {"marketingInitiativeName", "marketingInitiativeStartDate", "marketingInitiativeEndDate"})

    def test_categories_event_tier_2026_09_11(self):
        # Framework owner, 2026-09-11: disableDate joins the event tier;
        # categoryId leaves every channel (x-channels: [] — kept in the table
        # as the sort key). Both halves asserted so neither a leak nor an
        # over-trim can pass silently.
        for schema in (DATA_SCHEMA, self.projection):
            fields = set(
                schema["properties"]["categories"]["items"]["properties"])
            self.assertEqual(fields, {"categorySetName", "categoryCode",
                                      "categoryName", "categoryHierarchyPath",
                                      "ecatalogIncludeIndicator", "categoryDisabledDate"})
        self.assertEqual(
            MASTER["properties"]["categories"]["items"]["properties"]
                  ["categoryId"]["x-channels"], [])

    def test_2026_09_11_channel_batch(self):
        # Same decision: the four dimension columns join the event tier, the
        # access-duration trio is renamed, contributorId leaves every channel.
        for name in ("unitLengthQuantity", "unitWidthQuantity", "unitHeightQuantity", "dimensionUomCode",
                     "accessDurationType", "accessDurationQuantity", "accessDurationUnitCode"):
            self.assertIn(name, DATA_SCHEMA["properties"], name)
        for old in ("durationType", "durationValue", "durationUnit"):
            self.assertNotIn(old, DATA_SCHEMA["properties"], old)
            self.assertNotIn(old, MASTER["properties"], old)
        for schema in (DATA_SCHEMA, self.projection):
            self.assertNotIn(
                "contributorId",
                schema["properties"]["contributors"]["items"]["properties"])
        self.assertEqual(
            MASTER["properties"]["contributors"]["items"]["properties"]
                  ["contributorId"]["x-channels"], [])

    def test_contributor_affiliation_tiers_match_the_2026_08_17_decision(self):
        # Framework owner, 2026-08-17: affiliationType1/affiliation1 are
        # PROMOTED to the event tier; the 2/3 pairs stay message/api only.
        # Both halves asserted so neither a regression (slot 1 dropping back
        # out) nor a leak (slots 2/3 creeping in) can pass silently.
        data_fields = set(
            DATA_SCHEMA["properties"]["contributors"]["items"]["properties"])
        promoted = {"affiliationType1", "affiliation1"}
        missing = promoted - data_fields
        self.assertEqual(missing, set(),
                         f"event-tier affiliation fields missing from the "
                         f"event contributors shape: {sorted(missing)}")
        leaked = data_fields & {
            "affiliationType2", "affiliation2",
            "affiliationType3", "affiliation3",
        }
        self.assertEqual(leaked, set(),
                         f"message/api-only affiliation fields leaked into the "
                         f"event contributors shape: {sorted(leaked)}")


UPDATED_DATA = json.loads(
    (EVENTS_DIR / "product-updated-data.json").read_text(encoding="utf-8"))
UPDATED_EXAMPLE = json.loads(
    (EVENTS_DIR / "product-updated-example.json").read_text(encoding="utf-8"))


class TestUpdatedEventMirrorsCreated(unittest.TestCase):
    """product.updated (added 2026-08-17, completing the created/updated
    matrix across all four entities): the data schema is the SAME event-tier
    projection of master as product.created — only $id and title may differ.
    The identity assertion is what lets every projection gate above prove the
    whole family while reading one file."""

    def test_data_schema_identical_except_identity(self):
        a = {k: v for k, v in DATA_SCHEMA.items() if k not in ("$id", "title", "registry")}
        b = {k: v for k, v in UPDATED_DATA.items() if k not in ("$id", "title", "registry")}
        self.assertEqual(a, b)

    def test_updated_example_data_validates(self):
        validator = jsonschema.Draft202012Validator(UPDATED_DATA)
        errors = list(validator.iter_errors(UPDATED_EXAMPLE["data"]))
        self.assertEqual(errors, [], [e.message for e in errors])

    def test_updated_envelope_validates_and_is_action_correct(self):
        validator = jsonschema.Draft202012Validator(
            ENVELOPE_SCHEMA, format_checker=jsonschema.FormatChecker())
        errors = list(validator.iter_errors(UPDATED_EXAMPLE))
        self.assertEqual(errors, [], [e.message for e in errors])
        self.assertEqual(UPDATED_EXAMPLE["type"],
                         "com.pearson.product.product.updated.v1")
        self.assertEqual(UPDATED_EXAMPLE["dataschema"], UPDATED_DATA["$id"])


if __name__ == "__main__":
    unittest.main()
