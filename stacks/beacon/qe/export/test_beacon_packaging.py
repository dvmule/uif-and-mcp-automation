# test_beacon_packaging.py
# Unit tests for beacon/packaging.py and beacon/renderers.py — filename
# patterns, checksums, manifest shape, and renderer option mapping.

import hashlib
import io
import json
import unittest
import zipfile
from datetime import datetime, timezone

from beacon_export import packaging, renderers

WHEN = datetime(2026, 8, 5, 14, 30, 0, tzinfo=timezone.utc)


class TestFilenames(unittest.TestCase):
    def test_pattern_expansion(self):
        name = packaging.render_filename(
            "product_export_{yyyyMMdd}_{batch_id}.{ext}",
            job_id="product-export", batch_id="b-001", ext="csv", when=WHEN)
        self.assertEqual(name, "product_export_20260805_b-001.csv")

    def test_all_tokens(self):
        name = packaging.render_filename(
            "{job_id}_{yyyyMMdd}_{HHmmss}_{batch_id}.{ext}",
            job_id="pe", batch_id="b1", ext="json", when=WHEN)
        self.assertEqual(name, "pe_20260805_143000_b1.json")

    def test_manifest_name_pairs_with_data_files(self):
        name = packaging.manifest_filename(
            "product_export_{yyyyMMdd}_{batch_id}.{ext}",
            job_id="product-export", batch_id="b-001", when=WHEN)
        self.assertEqual(name, "product_export_20260805_b-001.manifest.json")


class TestZipPayload(unittest.TestCase):
    # UIFPE-172: the delivered artifact is <name>.<ext>.zip containing the
    # file under its uncompressed name.

    def test_round_trip_single_entry(self):
        data = b"ppid,isbn13\nA1,9781234567890\n"
        payload = packaging.zip_payload("export.csv", data, when=WHEN)
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            self.assertEqual(archive.namelist(), ["export.csv"])
            self.assertEqual(archive.read("export.csv"), data)

    def test_deterministic_for_same_batch_time(self):
        # a package-stage retry within a batch must reproduce the same bytes,
        # or the manifest checksum would drift from the delivered file
        data = b"row" * 4096
        first = packaging.zip_payload("f.csv", data, when=WHEN)
        second = packaging.zip_payload("f.csv", data, when=WHEN)
        self.assertEqual(hashlib.sha256(first).hexdigest(),
                         hashlib.sha256(second).hexdigest())

    def test_compound_extension_names_the_archive(self):
        # {ext} is plain replacement, so ext="csv.zip" yields the delivered
        # name — same precedent as the manifest's ext="manifest.json"
        name = packaging.render_filename(
            "product_export_{yyyyMMdd}_{batch_id}.{ext}",
            job_id="product-export", batch_id="b-001", ext="csv.zip", when=WHEN)
        self.assertEqual(name, "product_export_20260805_b-001.csv.zip")


class TestChecksums(unittest.TestCase):
    def test_sha256_of_chunks_matches_whole(self):
        data = b"beacon" * 1000
        chunked = packaging.sha256_of_chunks(
            data[i:i + 64] for i in range(0, len(data), 64))
        self.assertEqual(chunked, hashlib.sha256(data).hexdigest())

    def test_empty_iterable(self):
        self.assertEqual(packaging.sha256_of_chunks([]),
                         hashlib.sha256(b"").hexdigest())


class TestManifest(unittest.TestCase):
    def test_manifest_shape(self):
        files = [{"name": "a.csv", "bytes": 10, "sha256": "x"}]
        m = packaging.build_manifest(job_id="pe", batch_id="b1", files=files,
                                     versions={"engine": "0.1.0"},
                                     generated_at="2026-08-05T14:30:00Z")
        self.assertEqual(m["file_count"], 1)
        self.assertEqual(m["files"], files)
        self.assertEqual(m["versions"]["engine"], "0.1.0")
        # bytes round-trip as valid JSON
        self.assertEqual(json.loads(packaging.manifest_bytes(m)), m)


class TestRenderers(unittest.TestCase):
    def test_csv_defaults_are_safe(self):
        opts = renderers.spark_write_options({"format": "csv", "options": {}})
        self.assertEqual(opts["quoteAll"], "true")
        self.assertEqual(opts["header"], "true")
        self.assertEqual(opts["encoding"], "UTF-8")

    def test_csv_delimiter_override(self):
        opts = renderers.spark_write_options(
            {"format": "csv", "options": {"delimiter": "|", "header": False}})
        self.assertEqual(opts["sep"], "|")
        self.assertEqual(opts["header"], "false")

    def test_formula_escape_default_on_for_csv_only(self):
        self.assertTrue(renderers.formula_escape_enabled(
            {"format": "csv", "options": {}}))
        self.assertFalse(renderers.formula_escape_enabled(
            {"format": "csv", "options": {"formula_escape": False}}))
        self.assertFalse(renderers.formula_escape_enabled(
            {"format": "json", "options": {}}))

    def test_json_takes_no_spark_options(self):
        self.assertEqual(renderers.spark_write_options(
            {"format": "json", "options": {"style": "jsonlines"}}), {})

    def test_json_array_style_rejected(self):
        err = renderers.validate_rendering(
            {"format": "json", "options": {"style": "array"}})
        self.assertIn("jsonlines", err)

    def test_extension(self):
        self.assertEqual(renderers.extension({"format": "csv"}), "csv")
        self.assertEqual(renderers.extension({"format": "json"}), "json")
        self.assertEqual(renderers.extension({"format": "xml"}), "xml")

    def test_xml_options_map_to_spark_xml(self):
        opts = renderers.spark_write_options(
            {"format": "xml", "options": {"root_tag": "products",
                                          "row_tag": "product"}})
        self.assertEqual(opts["rootTag"], "products")
        self.assertEqual(opts["rowTag"], "product")
        self.assertIn('version="1.0"', opts["declaration"])

    def test_xml_declaration_opt_out(self):
        opts = renderers.spark_write_options(
            {"format": "xml", "options": {"declaration": False}})
        self.assertEqual(opts["declaration"], "")

    def test_xml_unknown_option_rejected(self):
        err = renderers.validate_rendering(
            {"format": "xml", "options": {"xsd_path": "x.xsd"}})
        self.assertIn("xsd_path", err)

    def test_xml_accepted_by_registry(self):
        self.assertIsNone(renderers.validate_rendering(
            {"format": "xml", "options": {"root_tag": "products"}}))
