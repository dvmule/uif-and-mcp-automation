# test_channel_eligibility.py
# UIFPE-540: beacon is the MESSAGE channel, so a gold column the entity's
# master declares on no channel (`x-channels: []`) must never be offered for
# authoring or accepted in a config. Before this, beacon validated a column
# for EXISTENCE only, and sources.yaml offered inventory_item_id and
# organization_id for gold_product.product — the Oracle surrogates rule N7
# keeps off every external surface.
#
# The guard is INERT until Terraform sets BEACON_GOLD_ARTIFACTS_BUCKET, which
# is what keeps this change safe to deploy. That makes an uncontrolled pass
# meaningless, so every test here either configures the bucket or asserts the
# inert case on purpose, and each enforcement test is paired with the
# known-bad control that shows the same input passing when the guard is off.
#
# Fake Glue and fake S3, no AWS.

import json
import os
import unittest

from control_panel import channel_eligibility, registries, validation
from gold_fixtures import CATALOG, GOLD_DBS, FakeGlue

BUCKET = "uif-echo-dev-gold-artifacts"

#: A master shaped like product's: two surrogates the channel generators strip
#: from every tier, one nested surrogate inside an eligible container, and two
#: ordinary message-eligible attributes.
MASTER = {
    "properties": {
        "inventoryItemId": {"type": "string", "x-channels": []},
        "organizationId": {"type": "string", "x-channels": []},
        # descriptions copied verbatim from domains/product/.../product/v1/master.json
        # (UIFPE-562 shows them in the wizard; beacon authors none of its own)
        "productId": {"type": "string", "x-channels": ["event", "message", "api"],
                      "description": "Item, also known as PPID (Pearson Product Identifier) — the unique identifier used within PDH to distinguish one item from another."},
        "isbn13": {"type": "string", "x-channels": ["event", "message", "api"],
                   "description": "International Standard Book Number auto-assigned to items in PDH; generally assigned to sellable items in the Higher Education, Schools and English Language Learning lines of business."},
        "contributors": {
            "type": "array", "x-channels": ["event", "message", "api"],
            "items": {"type": "object", "properties": {
                "contributorId": {"type": "string", "x-channels": []},
                "contributorFamilyName": {"type": "string"},
            }},
        },
    }
}

#: The catalogue spells them snake_case (N8); the master is camelCase.
PRODUCT_COLUMNS = ["inventory_item_id", "organization_id", "product_id",
                   "isbn13", "contributors"]


class FakeS3:
    """get_object over {(bucket, key): bytes}; a missing key raises, as the
    real service does. fail=True raises for every key (an outage)."""

    def __init__(self, objects=None, fail=False):
        self.objects = objects or {}
        self.fail = fail
        self.calls = 0

    def get_object(self, Bucket, Key, **kwargs):
        self.calls += 1
        if self.fail:
            raise RuntimeError("s3 down")
        try:
            body = self.objects[(Bucket, Key)]
        except KeyError:
            raise RuntimeError(f"NoSuchKey: {Bucket}/{Key}")
        return {"Body": _Body(body)}


class _Body:
    def __init__(self, data):
        self._data = data

    def read(self):
        return self._data


def _master_objects(entity="product", master=None):
    key = f"schemas/gold/{entity}/v1/master.json"
    return {(BUCKET, key): json.dumps(master or MASTER).encode("utf-8")}


class EligibilityBase(unittest.TestCase):
    """Gold catalog + a fake artifacts bucket. Subclasses opt in to the bucket
    so the inert case can be asserted separately."""

    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in
                       ("BEACON_ENV", "BEACON_GOLD_DATABASES",
                        channel_eligibility.BUCKET_ENV)}
        os.environ.pop("BEACON_ENV", None)
        os.environ["BEACON_GOLD_DATABASES"] = GOLD_DBS
        os.environ.pop(channel_eligibility.BUCKET_ENV, None)
        channel_eligibility.reset_cache()
        self.s3 = FakeS3(_master_objects())
        registries.set_clients_for_testing(FakeGlue(CATALOG), self.s3)

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        channel_eligibility.reset_cache()
        registries.set_clients_for_testing(None, None)

    def configure_bucket(self):
        os.environ[channel_eligibility.BUCKET_ENV] = BUCKET


# ── the excluded set itself ──────────────────────────────────────────────────

class TestExcludedColumns(EligibilityBase):
    def test_surrogates_are_excluded_when_the_bucket_is_configured(self):
        self.configure_bucket()
        denied, status = channel_eligibility.excluded_columns("product", PRODUCT_COLUMNS)
        self.assertEqual(status, channel_eligibility.OK)
        # the CATALOGUE spelling comes back, so callers can filter directly
        self.assertEqual(denied, {"inventory_item_id", "organization_id"})

    def test_KNOWN_BAD_CONTROL_nothing_is_excluded_with_no_bucket(self):
        # the same call, guard off: this is the state of every environment
        # until Terraform sets the variable, and it is why the submit gate
        # must not fail closed on DISABLED
        denied, status = channel_eligibility.excluded_columns("product", PRODUCT_COLUMNS)
        self.assertEqual(status, channel_eligibility.DISABLED)
        self.assertEqual(denied, frozenset())

    def test_unreadable_master_is_UNREADABLE_not_empty(self):
        # a configured bucket whose master cannot be read is a broken
        # deployment, and must be distinguishable from "nothing excluded"
        self.configure_bucket()
        registries.set_clients_for_testing(FakeGlue(CATALOG), FakeS3(fail=True))
        denied, status = channel_eligibility.excluded_columns("product", PRODUCT_COLUMNS)
        self.assertEqual(status, channel_eligibility.UNREADABLE)
        self.assertEqual(denied, frozenset())

    def test_a_non_gold_source_has_no_master_to_check(self):
        self.configure_bucket()
        self.assertEqual(channel_eligibility.entity_for_path("s3://bucket/prefix/"), "")
        denied, status = channel_eligibility.excluded_columns("", ["a", "b"])
        self.assertEqual(status, channel_eligibility.DISABLED)
        self.assertEqual(denied, frozenset())

    def test_the_master_is_read_once_per_entity(self):
        self.configure_bucket()
        channel_eligibility.excluded_columns("product", PRODUCT_COLUMNS)
        channel_eligibility.excluded_columns("product", PRODUCT_COLUMNS)
        self.assertEqual(self.s3.calls, 1)

    def test_nested_surrogate_does_not_exclude_its_eligible_parent(self):
        # contributors.contributorId is x-channels: [] but contributors is
        # message-eligible. Excluding the parent would be wrong. The nested
        # value still ships inside the struct — the deferred phase-2 gap,
        # pinned here so the decision is visible rather than forgotten.
        self.configure_bucket()
        denied, _ = channel_eligibility.excluded_columns("product", PRODUCT_COLUMNS)
        self.assertNotIn("contributors", denied)


class TestCanonicalComparison(unittest.TestCase):
    def test_camelCase_master_name_matches_snake_case_catalogue_name(self):
        self.assertEqual(channel_eligibility._canonical("inventoryItemId"),
                         channel_eligibility._canonical("inventory_item_id"))

    def test_it_does_not_collide_distinct_columns_in_a_real_master(self):
        # the comparison form is lossy, so it is only safe while it stays
        # injective over an actual master's property set. Product's is the
        # widest one in the repo.
        import pathlib
        root = pathlib.Path(__file__).resolve().parents[4]
        master = json.loads((root / "domains/product/src/schemas/gold/product/v1"
                             / "master.json").read_text(encoding="utf-8"))
        names = list(master["properties"])
        canon = [channel_eligibility._canonical(n) for n in names]
        self.assertEqual(len(set(canon)), len(names),
                         "two distinct top-level columns share a comparison form")


# ── the picker ───────────────────────────────────────────────────────────────

class TestColumnPicker(EligibilityBase):
    """The shared CATALOG fixture's product table carries no surrogates, so
    these install a catalog that mirrors the real one — which does."""

    def setUp(self):
        super().setUp()
        catalog = {"uif_echo_dev_gold_product": {"product": {
            "inventory_item_id": "bigint", "organization_id": "bigint",
            "product_id": "bigint", "isbn13": "string",
            "contributors": "array<struct<contributor_id:string,contributor_family_name:string>>",
            "_gold_updated_at": "timestamp",
        }}}
        registries.set_clients_for_testing(FakeGlue(catalog), self.s3)

    def _columns(self):
        return registries.source_columns("product.product")

    def test_the_picker_does_not_offer_the_surrogates(self):
        self.configure_bucket()
        result = self._columns()
        self.assertNotIn("inventory_item_id", result["columns"])
        self.assertNotIn("organization_id", result["columns"])
        self.assertEqual(result["excluded"], ["inventory_item_id", "organization_id"])

    def test_KNOWN_BAD_CONTROL_the_picker_offers_them_with_no_bucket(self):
        # the defect this ticket exists for, asserted as the current state:
        # without the guard the surrogates are offered for authoring
        result = self._columns()
        self.assertIn("inventory_item_id", result["columns"])
        self.assertNotIn("excluded", result)
        self.assertNotIn("eligibility", result)

    def test_eligible_columns_survive(self):
        self.configure_bucket()
        for column in ("product_id", "isbn13", "contributors"):
            self.assertIn(column, self._columns()["columns"], column)

    def test_types_drop_the_excluded_columns_too(self):
        # a stale type entry for a column no longer offered would let the
        # wizard's operator picker resurrect it
        self.configure_bucket()
        result = self._columns()
        self.assertNotIn("inventory_item_id", result.get("types", {}))

    def test_an_unreadable_master_marks_the_source_unverified(self):
        self.configure_bucket()
        registries.set_clients_for_testing(FakeGlue(CATALOG), FakeS3(fail=True))
        self.assertEqual(self._columns()["eligibility"], "unverified")


# ── attribute descriptions (UIFPE-562) ───────────────────────────────────────

class TestColumnDescriptions(TestColumnPicker):
    """The picker carries each column's `description` from the SAME master,
    keyed by the catalogue's snake_case spelling, and only for columns the
    master documents. Same catalog as TestColumnPicker."""

    def test_descriptions_are_the_masters_text_keyed_by_the_catalogue_name(self):
        self.configure_bucket()
        result = self._columns()
        self.assertEqual(
            result["descriptions"],
            {"product_id": MASTER["properties"]["productId"]["description"],
             "isbn13": MASTER["properties"]["isbn13"]["description"]})

    def test_a_property_without_a_description_is_simply_absent(self):
        # contributors is offered (eligible) but its master carries no text
        self.configure_bucket()
        result = self._columns()
        self.assertIn("contributors", result["columns"])
        self.assertNotIn("contributors", result["descriptions"])

    def test_excluded_columns_carry_no_description(self):
        self.configure_bucket()
        self.assertNotIn("inventory_item_id", self._columns()["descriptions"])

    def test_KNOWN_BAD_CONTROL_no_bucket_means_no_descriptions_key(self):
        # absent means unknown, the `types` convention — the wizard shows
        # nothing extra rather than an empty line
        self.assertNotIn("descriptions", self._columns())

    def test_an_unreadable_master_yields_no_descriptions_key(self):
        self.configure_bucket()
        registries.set_clients_for_testing(FakeGlue(CATALOG), FakeS3(fail=True))
        self.assertNotIn("descriptions", self._columns())

    def test_eligibility_and_descriptions_share_one_master_read(self):
        self.configure_bucket()
        self._columns()
        self._columns()
        self.assertEqual(self.s3.calls, 1)

    def test_the_real_product_master_documents_every_top_level_property(self):
        # the feature is only as good as the master: pin that product's has a
        # description on every property so a future gap is a visible failure
        import pathlib
        root = pathlib.Path(__file__).resolve().parents[4]
        master = json.loads((root / "domains/product/src/schemas/gold/product/v1"
                             / "master.json").read_text(encoding="utf-8"))
        missing = [n for n, p in master["properties"].items()
                   if not str(p.get("description", "")).strip()]
        self.assertEqual(missing, [])


# ── the submit gate ──────────────────────────────────────────────────────────

#: One source, one mapping, referencing a surrogate on purpose.
def _config(source_column):
    return {
        "version": "1.0",
        "sources": [{"name": "product",
                     "path": "glue_catalog.uif_echo_dev_gold_product.product",
                     "format": "iceberg"}],
        "joins": [],
        "mappings": [{"type": "one_to_one",
                      "source": f"product.{source_column}", "target": "someTarget"}],
        "validations": [],
        "rejected_path": "s3://b/rejected/${batch_id}/",
        "output": {"targets": [{"format": "parquet", "mode": "overwrite",
                                "path": "s3://b/staging/${batch_id}/canonical/"}]},
    }


class TestSubmitGate(EligibilityBase):
    def setUp(self):
        super().setUp()
        catalog = {"uif_echo_dev_gold_product": {"product": {
            "inventory_item_id": "bigint", "organization_id": "bigint",
            "product_id": "bigint", "isbn13": "string",
            "_gold_updated_at": "timestamp",
        }}}
        registries.set_clients_for_testing(FakeGlue(catalog), self.s3)

    def _validate(self, column):
        return validation.validate_mapping_config(_config(column))

    def test_a_surrogate_reference_is_refused_with_the_channel_reason(self):
        self.configure_bucket()
        result = self._validate("inventory_item_id")
        self.assertFalse(result["valid"])
        messages = " ".join(i["message"] for i in result["issues"])
        # the reason must be eligibility, NOT "not found" -- the column does
        # exist in gold, which is precisely why the old message was wrong
        self.assertIn("not exportable", messages)
        self.assertIn("x-channels: []", messages)
        self.assertNotIn("not found", messages)

    def test_KNOWN_BAD_CONTROL_the_same_config_is_accepted_with_no_bucket(self):
        # the defect, asserted: with the guard off this config validates, and
        # a partner receives an Oracle surrogate key
        result = self._validate("inventory_item_id")
        self.assertTrue(result["valid"], result["issues"])

    def test_an_eligible_column_still_validates(self):
        self.configure_bucket()
        self.assertTrue(self._validate("product_id")["valid"],
                        self._validate("product_id")["issues"])

    def test_a_genuinely_absent_column_still_says_not_found(self):
        # existence and eligibility stay two different answers
        self.configure_bucket()
        result = self._validate("no_such_column")
        self.assertFalse(result["valid"])
        messages = " ".join(i["message"] for i in result["issues"])
        self.assertIn("not found", messages)
        self.assertNotIn("not exportable", messages)

    def test_an_unreadable_master_fails_the_gate_closed(self):
        # deliberately the opposite posture to the reference check, which
        # skips rather than risk a false rejection: a missed reference costs a
        # failed run, a missed eligibility check costs a leak
        self.configure_bucket()
        registries.set_clients_for_testing(
            FakeGlue({"uif_echo_dev_gold_product": {"product": {
                "product_id": "bigint", "_gold_updated_at": "timestamp"}}}),
            FakeS3(fail=True))
        result = self._validate("product_id")
        self.assertFalse(result["valid"])
        self.assertIn("cannot verify channel eligibility",
                      " ".join(i["message"] for i in result["issues"]))
