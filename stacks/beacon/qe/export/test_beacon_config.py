# test_beacon_config.py
# Unit tests for beacon/export_config.py — the export config contract
# (validate() returns None | error string). Domain-specific config artifacts
# are linted by their own domain's suite (e.g. domains/product/qe/), not here.

import unittest

from beacon_export import export_config, renderers


def valid_body():
    return {
        "version": "1.0",
        "job_id": "product-export",
        "mapping_config_s3_path": "s3://bkt/configs/mapping.json",
        "canonical_s3_prefix": "s3://bkt/staging/{batch_id}/canonical/",
        "quality_gates": {"min_rows": 1, "max_rejection_pct": 2.0},
        "renderings": [
            {"format": "csv", "options": {"delimiter": ","}},
        ],
        "packaging": {
            "filename_pattern": "export_{yyyyMMdd}_{batch_id}.{ext}",
            "pickup_s3_prefix": "s3://bkt/pickup/{batch_id}/",
            "manifest": {"enabled": True, "checksum": "sha256"},
        },
        "delivery": {
            "channel": "sftp",
            "adapter": "paramiko",
            "secret_ref": "eds/dev/beacon/sftp",
            "remote_dir": "/inbound",
            "upload_pattern": "tmp-then-rename",
        },
    }


class TestValidate(unittest.TestCase):
    def test_valid_passes(self):
        self.assertIsNone(export_config.validate(valid_body()))

    def test_missing_top_field(self):
        for key in export_config.REQUIRED_TOP:
            body = valid_body()
            del body[key]
            self.assertIn(key, export_config.validate(body) or "",
                          f"missing {key} not reported")

    def test_canonical_prefix_needs_batch_token(self):
        body = valid_body()
        body["canonical_s3_prefix"] = "s3://bkt/staging/canonical/"
        self.assertIn("{batch_id}", export_config.validate(body))

    def test_pickup_prefix_needs_batch_token(self):
        body = valid_body()
        body["packaging"]["pickup_s3_prefix"] = "s3://bkt/pickup/"
        self.assertIn("{batch_id}", export_config.validate(body))

    def test_filename_pattern_needs_batch_id(self):
        body = valid_body()
        body["packaging"]["filename_pattern"] = "export_{yyyyMMdd}.{ext}"
        self.assertIn("batch_id", export_config.validate(body))

    def test_unknown_format_rejected(self):
        body = valid_body()
        body["renderings"] = [{"format": "yaml"}]
        self.assertIn("yaml", export_config.validate(body))

    def test_exactly_one_rendering_required(self):
        # single output format per export: same data in another format is
        # another job, with its own gates, destination and schedule
        body = valid_body()
        body["renderings"].append({"format": "json",
                                   "options": {"style": "jsonlines"}})
        self.assertIn("exactly one rendering", export_config.validate(body))
        body["renderings"] = []
        self.assertIn("non-empty", export_config.validate(body))

    def test_unknown_csv_option_rejected(self):
        body = valid_body()
        body["renderings"][0]["options"]["compression"] = "gzip"
        self.assertIn("compression", export_config.validate(body))

    def test_rejection_pct_bounds(self):
        body = valid_body()
        body["quality_gates"]["max_rejection_pct"] = 101
        self.assertIn("max_rejection_pct", export_config.validate(body))

    def test_paramiko_requires_secret_ref(self):
        body = valid_body()
        del body["delivery"]["secret_ref"]
        self.assertIn("secret_ref", export_config.validate(body))

    def test_transfer_family_requires_connector(self):
        body = valid_body()
        body["delivery"]["adapter"] = "transfer_family"
        del body["delivery"]["secret_ref"]
        self.assertIn("connector_id", export_config.validate(body))

    def test_relative_remote_dir_rejected(self):
        body = valid_body()
        body["delivery"]["remote_dir"] = "inbound"
        self.assertIn("absolute", export_config.validate(body))

    def test_load_rejects_bad_json(self):
        with self.assertRaises(ValueError):
            export_config.load("{not json")


class TestXmlTagNames(unittest.TestCase):
    # UIFPE-586: the author names the wrapper and record elements so a
    # partner's own document shape can be delivered. The renderer always
    # accepted the options; nothing validated the VALUES, and a bad name
    # would have reached the writer and produced a malformed document.

    def _xml(self, **options):
        return {"format": "xml", "options": options}

    def test_valid_names_pass_and_reach_the_writer(self):
        rendering = self._xml(root_tag="productCatalog", row_tag="product")
        self.assertIsNone(renderers.validate_rendering(rendering))
        written = renderers.spark_write_options(rendering)
        self.assertEqual(written["rootTag"], "productCatalog")
        self.assertEqual(written["rowTag"], "product")

    def test_absent_names_keep_the_defaults(self):
        # the compatibility guarantee: a version published before this
        # feature carries no tag options and must render exactly as before
        rendering = self._xml()
        self.assertIsNone(renderers.validate_rendering(rendering))
        written = renderers.spark_write_options(rendering)
        self.assertEqual((written["rootTag"], written["rowTag"]), ("rows", "row"))

    def test_invalid_names_are_refused_with_the_reason(self):
        for key in ("root_tag", "row_tag"):
            for bad in ("my product", "1product", "prod/uct", "", "a b"):
                error = renderers.validate_rendering(self._xml(**{key: bad}))
                self.assertIsNotNone(error, f"{key}={bad!r} should be rejected")
                self.assertIn(key, error)

    def test_a_colon_is_refused_because_namespaces_are_unsupported(self):
        error = renderers.validate_rendering(self._xml(root_tag="onix:Message"))
        self.assertIn("namespaces are not supported", error)

    def test_non_string_name_returns_an_error_not_a_crash(self):
        for bad in (["rows"], {"tag": "rows"}, 5, True, None):
            error = renderers.validate_rendering(self._xml(row_tag=bad))
            self.assertIsNotNone(error, f"{bad!r} should be rejected")
            self.assertIn("row_tag", error)

    def test_export_config_accepts_the_named_tags(self):
        body = valid_body()
        body["renderings"] = [{"format": "xml",
                               "options": {"root_tag": "productCatalog",
                                           "row_tag": "product"}}]
        self.assertIsNone(export_config.validate(body))
        body["renderings"][0]["options"]["row_tag"] = "bad name"
        self.assertIsNotNone(export_config.validate(body))


class TestDelimiterSelection(unittest.TestCase):
    # UIFPE-171: the delimiter is a constrained choice (comma/tab/pipe),
    # carried in configs as a symbolic name. Literal characters stay accepted
    # because published versions are immutable and pre-date the names.

    def _csv(self, **options):
        return {"format": "csv", "options": options}

    def test_symbolic_names_validate_and_map_to_separators(self):
        for name, sep in (("comma", ","), ("tab", "\t"), ("pipe", "|")):
            rendering = self._csv(delimiter=name)
            self.assertIsNone(renderers.validate_rendering(rendering))
            self.assertEqual(renderers.spark_write_options(rendering)["sep"],
                             sep, f"{name} should map to {sep!r}")

    def test_legacy_literal_characters_still_accepted(self):
        for sep in (",", "\t", "|"):
            rendering = self._csv(delimiter=sep)
            self.assertIsNone(renderers.validate_rendering(rendering))
            self.assertEqual(renderers.spark_write_options(rendering)["sep"], sep)

    def test_default_is_comma(self):
        rendering = self._csv()
        self.assertIsNone(renderers.validate_rendering(rendering))
        self.assertEqual(renderers.spark_write_options(rendering)["sep"], ",")

    def test_unsupported_delimiter_rejected(self):
        for bad in (";", "x", "semicolon", ""):
            error = renderers.validate_rendering(self._csv(delimiter=bad))
            self.assertIsNotNone(error, f"{bad!r} should be rejected")
            self.assertIn("delimiter", error)
            self.assertIn("comma", error)  # the message names the choices

    def test_non_string_delimiter_returns_error_not_crash(self):
        # config values are author-controlled JSON: the validator's contract
        # is an error string, never an exception (unhashables included)
        for bad in (["tab"], {"name": "tab"}, None, 5, True):
            error = renderers.validate_rendering(self._csv(delimiter=bad))
            self.assertIsNotNone(error, f"{bad!r} should be rejected")
            self.assertIn("delimiter", error)

    def test_export_config_accepts_symbolic_delimiter(self):
        body = valid_body()
        body["renderings"][0]["options"]["delimiter"] = "tab"
        self.assertIsNone(export_config.validate(body))

    def test_export_config_rejects_unsupported_delimiter(self):
        body = valid_body()
        body["renderings"][0]["options"]["delimiter"] = ";"
        self.assertIn("delimiter", export_config.validate(body))


class TestZipPackagingConfig(unittest.TestCase):
    # UIFPE-172: packaging.zip is optional; MISSING means uncompressed —
    # published configs are immutable, so pre-feature versions must keep
    # today's behavior on any engine version.

    def test_zip_enabled_and_disabled_validate(self):
        for enabled in (True, False):
            body = valid_body()
            body["packaging"]["zip"] = {"enabled": enabled}
            self.assertIsNone(export_config.validate(body))

    def test_missing_zip_key_validates(self):
        body = valid_body()
        self.assertNotIn("zip", body["packaging"])  # the back-compat shape
        self.assertIsNone(export_config.validate(body))

    def test_malformed_zip_rejected(self):
        for bad in ("yes", {"enabled": "yes"}, ["on"], 1):
            body = valid_body()
            body["packaging"]["zip"] = bad
            error = export_config.validate(body)
            self.assertIsNotNone(error, f"{bad!r} should be rejected")
            self.assertIn("packaging.zip", error)


class TestExtractionConfig(unittest.TestCase):
    # UIFPE-168: extraction is optional; MISSING means full load — published
    # pre-feature configs never change meaning (the UIFPE-172 convention).
    # The watermark VALUE never appears in the config: versions are immutable
    # (D22), so it rides the execution input instead.

    def test_missing_extraction_key_validates(self):
        body = valid_body()
        self.assertNotIn("extraction", body)  # the back-compat shape
        self.assertIsNone(export_config.validate(body))

    def test_explicit_full_validates(self):
        body = valid_body()
        body["extraction"] = {"mode": "full"}
        self.assertIsNone(export_config.validate(body))

    def test_incremental_requires_source_and_watermark_column(self):
        body = valid_body()
        body["extraction"] = {"mode": "incremental", "source": "product",
                              "watermark_column": "_gold_updated_at"}
        self.assertIsNone(export_config.validate(body))
        del body["extraction"]["source"]
        self.assertIn("extraction.source", export_config.validate(body))
        body["extraction"]["source"] = "product"
        del body["extraction"]["watermark_column"]
        self.assertIn("watermark_column", export_config.validate(body))

    def test_unknown_mode_rejected(self):
        body = valid_body()
        body["extraction"] = {"mode": "delta"}
        self.assertIn("extraction.mode", export_config.validate(body))

    def test_non_identifier_watermark_column_rejected(self):
        # the column is spliced into a source predicate — anything beyond a
        # plain identifier is an injection surface
        for bad in ("a b", "col; DROP", "1col", "", "col()"):
            body = valid_body()
            body["extraction"] = {"mode": "incremental", "source": "product",
                                  "watermark_column": bad}
            error = export_config.validate(body)
            self.assertIsNotNone(error, f"{bad!r} should be rejected")
            self.assertIn("watermark_column", error)

    def test_malformed_extraction_rejected(self):
        for bad in ("incremental", ["incremental"], 1, True):
            body = valid_body()
            body["extraction"] = bad
            self.assertIn("extraction", export_config.validate(body) or "",
                          f"{bad!r} should be rejected")


class TestS3Channel(unittest.TestCase):
    def _s3_body(self):
        body = valid_body()
        body["delivery"] = {"channel": "s3", "adapter": "s3_copy",
                            "destination_s3_prefix": "s3://partner-bucket/inbound/product"}
        return body

    def test_valid_s3_delivery_passes(self):
        self.assertIsNone(export_config.validate(self._s3_body()))

    def test_adapter_defaults_per_channel(self):
        body = self._s3_body()
        del body["delivery"]["adapter"]
        self.assertIsNone(export_config.validate(body))

    def test_destination_prefix_required(self):
        body = self._s3_body()
        del body["delivery"]["destination_s3_prefix"]
        self.assertIn("destination_s3_prefix", export_config.validate(body))

    def test_destination_must_be_s3_uri(self):
        body = self._s3_body()
        body["delivery"]["destination_s3_prefix"] = "/local/dir"
        self.assertIn("s3://", export_config.validate(body))

    def test_sftp_adapter_invalid_for_s3_channel(self):
        body = self._s3_body()
        body["delivery"]["adapter"] = "paramiko"
        self.assertIn("not valid for channel", export_config.validate(body))


class TestMultiTargetDelivery(unittest.TestCase):
    # UIFPE-170: `deliveries` (list) is the multi-target shape; `delivery`
    # (single object) stays valid forever — pre-feature published versions
    # never change meaning. Exactly one of the two.

    def _targets_body(self, *targets):
        body = valid_body()
        del body["delivery"]
        body["deliveries"] = list(targets)
        return body

    S3_A = {"channel": "s3", "destination_s3_prefix": "s3://a/in"}
    S3_B = {"channel": "s3", "destination_s3_prefix": "s3://b/in"}
    SFTP = {"channel": "sftp", "adapter": "paramiko",
            "secret_ref": "uif/beacon/dev/destinations/x",
            "remote_dir": "/inbound"}

    def test_multiple_targets_validate(self):
        self.assertIsNone(export_config.validate(
            self._targets_body(self.S3_A, self.S3_B, self.SFTP)))

    def test_single_delivery_object_still_validates(self):
        self.assertIsNone(export_config.validate(valid_body()))

    def test_missing_both_shapes_reported(self):
        body = valid_body()
        del body["delivery"]
        self.assertIn("delivery", export_config.validate(body))

    def test_both_shapes_at_once_rejected(self):
        body = valid_body()
        body["deliveries"] = [self.S3_A]
        self.assertIn("both", export_config.validate(body))

    def test_empty_or_malformed_list_rejected(self):
        for bad in ([], "s3", {"channel": "s3"}, 1):
            body = valid_body()
            del body["delivery"]
            body["deliveries"] = bad
            self.assertIsNotNone(export_config.validate(body),
                                 f"{bad!r} should be rejected")

    def test_each_target_is_validated_with_its_index(self):
        body = self._targets_body(self.S3_A, {"channel": "sftp",
                                              "adapter": "paramiko",
                                              "remote_dir": "/in"})
        error = export_config.validate(body)
        self.assertIn("deliveries[1]", error)
        self.assertIn("secret_ref", error)

    def test_duplicate_targets_rejected(self):
        error = export_config.validate(
            self._targets_body(self.S3_A, dict(self.S3_A)))
        self.assertIn("duplicates", error)
        # same server, same directory = duplicate even via different objects
        error = export_config.validate(
            self._targets_body(self.SFTP, dict(self.SFTP, host_key="k")))
        self.assertIn("duplicates", error)

    def test_same_server_different_directory_is_not_a_duplicate(self):
        self.assertIsNone(export_config.validate(self._targets_body(
            self.SFTP, dict(self.SFTP, remote_dir="/other"))))

    def test_delivery_targets_helper_normalizes_both_shapes(self):
        single = valid_body()
        self.assertEqual(export_config.delivery_targets(single),
                         [single["delivery"]])
        multi = self._targets_body(self.S3_A, self.S3_B)
        self.assertEqual(export_config.delivery_targets(multi),
                         [self.S3_A, self.S3_B])


class TestComplexTypedColumnGuard(unittest.TestCase):
    # UIFPE-237: Spark's csv writer cannot serialize array/struct/map columns
    # and fails AFTER the quality gate with an opaque AnalysisException — the
    # job refuses pre-render with an actionable message via this pure helper.

    DTYPES = [("ppid", "string"), ("copyrightYear", "int"),
              ("specs", "array<struct<priceListName:string,listPrice:double>>"),
              ("dims", "struct<w:double,h:double>"),
              ("tags", "map<string,string>")]

    def test_flags_complex_columns_for_csv(self):
        self.assertEqual(
            renderers.complex_typed_columns("csv", self.DTYPES),
            ["specs", "dims", "tags"])

    def test_scalar_only_csv_is_clean(self):
        self.assertEqual(
            renderers.complex_typed_columns(
                "csv", [("ppid", "string"), ("n", "bigint")]),
            [])

    def test_json_and_xml_serialize_nested_natively(self):
        for fmt in ("json", "xml"):
            self.assertEqual(renderers.complex_typed_columns(fmt, self.DTYPES),
                             [], fmt)
