# test_validate_gold_schemas.py
# UIFPE-121. These checks used to be heredoc Python inside
# .github/workflows/product-gold-schemas.yml, where nothing could test them — the
# gates had no gates. Extracting them to a script is what makes this file
# possible, and the reason it is worth writing is that a schema gate which passes
# on zero files is indistinguishable from one that works.
#
# The headline behaviour under test is FAIL-CLOSED. The workflow's master glob
# used to `sys.exit(0)` with "nothing to validate", so:
#   * cloning the workflow for a new domain and mistyping one path -> green;
#   * moving a domain's schemas -> green;
#   * program's artifacts, which the product-scoped globs never matched at all
#     -> green, while being validated by nothing whatsoever.
# Every one of those is now a failure.

import json
import shutil
import unittest
from pathlib import Path

import validate_gold_schemas as v

REPO = Path(__file__).resolve().parents[4]


class RealRepoTest(unittest.TestCase):
    """Against the artifacts actually committed, so the gate cannot pass only on
    synthetic fixtures — the blind spot that let a real mapping defect through in
    UIFPE-120."""

    def test_product_validates(self):
        self.assertEqual(v.main(["--domain", "product",
                                 "--repo-root", str(REPO)]), 0)

    def test_program_validates(self):
        """Program is the whole point: before this script, NO workflow validated
        it, because product-gold-schemas.yml is hard-scoped to domains/product/**."""
        self.assertEqual(v.main(["--domain", "program",
                                 "--repo-root", str(REPO)]), 0)

    def test_all_domains_discovers_every_registering_domain(self):
        # Four domains register Gold masters since 2026-08-17. All four are
        # transformable now and all declare lifecycle=active: price and stock
        # began contract-first (masters with no manifest, which the validator
        # SKIPs by lifecycle), but gained manifests and went live, and
        # UIFPE-578 corrected the lifecycle they had been left declaring.
        self.assertEqual(
            set(v.discover_domains(REPO))
            & {"product", "program", "price", "stock"},
            {"product", "program", "price", "stock"})
        self.assertEqual(v.main(["--all-domains", "--repo-root", str(REPO)]), 0)

    def test_discovery_skips_domains_with_no_gold_schemas(self):
        """A stamped domain that registers no Gold schema must not be swept in by
        --all-domains, or every green run would need a schema in eight domains."""
        self.assertNotIn("legal", v.discover_domains(REPO))


class FailClosedTest(unittest.TestCase):
    """The false green, asserted from every direction."""

    def test_a_domain_with_no_masters_FAILS(self):
        self.assertEqual(v.main(["--domain", "legal",
                                 "--repo-root", str(REPO)]), 1)

    def test_an_unknown_domain_FAILS(self):
        """A mistyped --domain is the exact mistake the old glob rewarded."""
        self.assertEqual(v.main(["--domain", "prodcut",
                                 "--repo-root", str(REPO)]), 1)

    def test_no_domain_selected_FAILS(self):
        self.assertEqual(v.main(["--repo-root", str(REPO)]), 2)


class BrokenArtifactTest(unittest.TestCase):
    """Deliberately break a copy of the real tree and assert each break is
    caught. A gate is only worth its runtime if it fails when it should."""

    def setUp(self):
        import tempfile
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        dst = self.tmp / "domains" / "program"
        (self.tmp / "domains").mkdir(parents=True)
        shutil.copytree(REPO / "domains" / "program", dst)
        self.master = dst / "src/schemas/gold/program/v1/master.json"
        self.manifest = dst / "src/schemas/mapping/program/v1/manifest.yaml"

    def _run(self):
        return v.main(["--domain", "program", "--repo-root", str(self.tmp)])

    def test_the_copy_is_green_before_breaking_anything(self):
        """Guards against a self-defeating fixture: if the copy did not pass,
        every assertion below would 'catch' a break it never caused."""
        self.assertEqual(self._run(), 0)

    def test_missing_manifest_is_caught(self):
        """The ticket's explicit ask. A master with no manifest cannot be
        transformed at all, and the old empty-glob would not have noticed.
        The fixture is program's master, which is genuinely lifecycle=draft —
        flip it to active first, or the 2026-08-17 draft skip (deliberate,
        see the next test) would absorb the deletion."""
        master = json.loads(self.master.read_text(encoding="utf-8"))
        master["metadata"]["lifecycle"] = "active"
        self.master.write_text(json.dumps(master), encoding="utf-8")
        self.manifest.unlink()
        self.assertEqual(self._run(), 1)

    def test_the_draft_skip_is_lifecycle_gated_not_a_hole(self):
        """The draft skip (price/stock, 2026-08-17) must apply ONLY to
        lifecycle=draft: the same manifest-less master with lifecycle back on
        'active' still fails closed."""
        self.manifest.unlink()
        master = json.loads(self.master.read_text(encoding="utf-8"))
        master["metadata"]["lifecycle"] = "draft"
        self.master.write_text(json.dumps(master), encoding="utf-8")
        self.assertEqual(self._run(), 0)
        master["metadata"]["lifecycle"] = "active"
        self.master.write_text(json.dumps(master), encoding="utf-8")
        self.assertEqual(self._run(), 1)

    def test_a_manifest_with_a_draft_master_fails_closed(self):
        """UIFPE-578, the converse of the skip above and the hole it sat in.
        The skip only fires when a manifest is ABSENT, so nothing used to
        notice a master that had a manifest and still declared draft — which
        is exactly how four live entities kept a stale label for weeks. A
        manifest means the entity is transformable, so draft is false of it.

        Asserted in both directions so the test is pinned to the rule and not
        to incidental fixture state: reintroducing the mutation fails, and
        reverting it passes again."""
        master = json.loads(self.master.read_text(encoding="utf-8"))
        self.assertTrue(self.manifest.exists(),
                        "fixture must keep its manifest — the rule under test "
                        "only applies when one is present")

        master["metadata"]["lifecycle"] = "draft"
        self.master.write_text(json.dumps(master), encoding="utf-8")
        self.assertEqual(self._run(), 1)

        master["metadata"]["lifecycle"] = "active"
        self.master.write_text(json.dumps(master), encoding="utf-8")
        self.assertEqual(self._run(), 0)

    def test_a_consumer_audience_on_a_master_is_caught(self):
        doc = json.loads(self.master.read_text(encoding="utf-8"))
        doc["metadata"]["audience"] = "consumer"
        self.master.write_text(json.dumps(doc, indent=2), encoding="utf-8")
        self.assertEqual(self._run(), 1)

    def test_missing_metadata_is_caught(self):
        doc = json.loads(self.master.read_text(encoding="utf-8"))
        del doc["metadata"]["lifecycle"]
        self.master.write_text(json.dumps(doc, indent=2), encoding="utf-8")
        self.assertEqual(self._run(), 1)

    def test_a_new_master_property_with_no_lineage_entry_is_caught(self):
        """The commonest real drift: an attribute added to the schema and not to
        the manifest. The transform would emit a column it cannot populate."""
        doc = json.loads(self.master.read_text(encoding="utf-8"))
        doc["properties"]["someNewThing"] = {"type": ["string", "null"]}
        self.master.write_text(json.dumps(doc, indent=2), encoding="utf-8")
        self.assertEqual(self._run(), 1)

    def test_an_array_without_a_sort_key_is_caught(self):
        """ADR-0003 D11. Unstable ordering moves channel hashes every run, so
        every consumer is pinged forever — expensive to find late."""
        text = self.manifest.read_text(encoding="utf-8")
        self.manifest.write_text(
            text.replace("  associatedProducts:", "  associatedProductsXX:"),
            encoding="utf-8")
        self.assertEqual(self._run(), 1)

    def test_a_bad_null_sentinels_block_is_caught_and_a_good_one_passes(self):
        """UIFPE-268: the declaration becomes SQL literals on every Silver
        view, so a typo'd key or a quoted value must fail the PR, not the
        first Glue run. The lint mirrors gold_cdc.validate_null_sentinels."""
        text = self.manifest.read_text(encoding="utf-8")
        good = text + "\nnull_sentinels:\n  string: [TBD, N/A]\n  numeric: [99998, 99999]\n"
        self.manifest.write_text(good, encoding="utf-8")
        self.assertEqual(self._run(), 0)
        # placed ABOVE schema: the compiler would write lineage entries into it
        above = text.replace("\nschema:", "\nnull_sentinels:\n  string: [TBD]\n\nschema:", 1)
        assert above != text
        self.manifest.write_text(above, encoding="utf-8")
        self.assertEqual(self._run(), 1)
        for bad in ("null_sentinels:\n  strings: [TBD]\n",          # typo'd key
                    "null_sentinels:\n  string: TBD\n",             # not a list
                    "null_sentinels:\n  string: []\n  numeric: []\n",
                    "null_sentinels:\n  numeric: ['99999']\n",      # number as text
                    "null_sentinels:\n  string: [\"it's\"]\n"):     # breaks the literal
            with self.subTest(bad=bad):
                self.manifest.write_text(text + "\n" + bad, encoding="utf-8")
                self.assertEqual(self._run(), 1)

    def test_a_source_losing_its_key_mapping_is_caught(self):
        text = self.manifest.read_text(encoding="utf-8")
        self.manifest.write_text(text.replace("    key_mapping:", "    key_mappingX:"),
                                 encoding="utf-8")
        self.assertEqual(self._run(), 1)

    def test_a_missing_sql_file_is_caught(self):
        (self.tmp / "domains/program/src/schemas/mapping/program/v1/sql/program.sql").unlink()
        self.assertEqual(self._run(), 1)

    def test_malformed_yaml_is_caught_rather_than_crashing(self):
        self.manifest.write_text("domain: program\n  bad: [indent\n", encoding="utf-8")
        self.assertEqual(self._run(), 1)


if __name__ == "__main__":
    unittest.main()
