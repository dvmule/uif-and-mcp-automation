# test_silver_bq_native.py
# Unit tests for silver-transform-bq-native.py — pure-Python helpers.
# No Spark, no live AWS/GCP. The BQ client and DynamoDB calls are mocked at
# import time so the module loads without network or credentials.

import os, sys, unittest
from unittest.mock import MagicMock, patch

# Provide module-level args that the script's argparse expects at import. Doing
# this BEFORE importing the module avoids a SystemExit on missing args.
sys.argv = [
    "silver-transform-bq-native.py",
    "--pipeline_id",   "test-pipeline",
    "--CONFIG_TABLE",  "test_config",
    "--METRICS_TABLE", "test_metrics",
    "--JOB_NAME",      "test-job",
    "--JOB_RUN_ID",    "jr_test_abc123",
    "--env",           "dev",
]


# Stub boto3 + google libs before importing the job. The real clients try to
# read AWS credentials at construction time.
mock_boto3            = MagicMock()
mock_dynamo_resource  = MagicMock()
mock_secrets_client   = MagicMock()
mock_boto3.resource.return_value = mock_dynamo_resource
mock_boto3.client.return_value   = mock_secrets_client
sys.modules["boto3"] = mock_boto3

mock_bq               = MagicMock()
mock_bq_exceptions    = MagicMock()
mock_oauth2_sa        = MagicMock()
sys.modules["google"]                           = MagicMock()
sys.modules["google.cloud"]                     = MagicMock()
sys.modules["google.cloud.bigquery"]            = mock_bq
sys.modules["google.cloud.exceptions"]          = mock_bq_exceptions
sys.modules["google.api_core"]                  = MagicMock()
sys.modules["google.api_core.exceptions"]       = MagicMock()
sys.modules["google.oauth2"]                    = MagicMock()
sys.modules["google.oauth2.service_account"]    = mock_oauth2_sa


# Now safe to import.
from job_loader import load_pipeline_module

job = load_pipeline_module("silver-transform-bq-native.py")


class TestResolvePhase(unittest.TestCase):
    def test_missing_key_is_initial(self):
        self.assertEqual(job.resolve_phase({}), "initial")

    def test_none_is_initial(self):
        self.assertEqual(job.resolve_phase({"last_processed_date": None}), "initial")

    def test_empty_string_is_initial(self):
        self.assertEqual(job.resolve_phase({"last_processed_date": ""}), "initial")

    def test_epoch_sentinel_is_initial(self):
        self.assertEqual(job.resolve_phase({"last_processed_date": "1970-01-01"}), "initial")

    def test_full_iso_sentinel_is_initial(self):
        self.assertEqual(
            job.resolve_phase({"last_processed_date": "1970-01-01T00:00:00"}),
            "initial",
        )

    def test_real_timestamp_is_incremental(self):
        self.assertEqual(
            job.resolve_phase({"last_processed_date": "2026-06-15"}),
            "incremental",
        )

    def test_iso_timestamp_is_incremental(self):
        self.assertEqual(
            job.resolve_phase({"last_processed_date": "2026-06-15T19:08:09"}),
            "incremental",
        )


class TestMergeSourceSelectExpr(unittest.TestCase):
    def _field(self, name, ftype):
        m = MagicMock()
        m.name = name
        m.field_type = ftype
        return m

    def test_casts_bignumeric_to_int64_when_target_is_int64(self):
        target = {"RSS_LINE_ID": self._field("RSS_LINE_ID", "INT64")}
        source = {"RSS_LINE_ID": self._field("RSS_LINE_ID", "BIGNUMERIC")}
        sql = job._merge_source_select_expr("RSS_LINE_ID", target, source)
        self.assertEqual(sql, "CAST(`RSS_LINE_ID` AS INT64) AS `RSS_LINE_ID`")

    def test_casts_when_target_field_type_is_integer(self):
        """BQ Python client reports INT64 columns as field_type INTEGER."""
        target = {"RSS_LINE_ID": self._field("RSS_LINE_ID", "INTEGER")}
        source = {"RSS_LINE_ID": self._field("RSS_LINE_ID", "BIGNUMERIC")}
        sql = job._merge_source_select_expr("RSS_LINE_ID", target, source)
        self.assertEqual(sql, "CAST(`RSS_LINE_ID` AS INT64) AS `RSS_LINE_ID`")

    def test_no_cast_when_types_match(self):
        target = {"RSS_LINE_ID": self._field("RSS_LINE_ID", "BIGNUMERIC")}
        source = {"RSS_LINE_ID": self._field("RSS_LINE_ID", "BIGNUMERIC")}
        sql = job._merge_source_select_expr("RSS_LINE_ID", target, source)
        self.assertEqual(sql, "`RSS_LINE_ID`")

    def test_sanitizes_hash_in_oracle_column_names(self):
        target = {"SHEET#": self._field("SHEET#", "STRING")}
        source = {"SHEET_": self._field("SHEET_", "STRING")}
        sql = job._merge_source_select_expr("SHEET#", target, source)
        self.assertEqual(sql, "`SHEET_`")


class TestBqColumnName(unittest.TestCase):
    def test_hash_becomes_underscore(self):
        self.assertEqual(job.to_bq_column_name("SHEET#"), "SHEET_")
        self.assertEqual(job.to_bq_column_name("ROW#"), "ROW_")

    def test_plain_name_unchanged(self):
        self.assertEqual(job.to_bq_column_name("GUID"), "GUID")

    def test_dedupes_hash_and_sanitized_duplicate(self):
        cols = job.canonical_bq_columns(["SHEET#", "SHEET_", "GUID"])
        self.assertEqual(cols, ["SHEET_", "GUID"])


class TestBuildPkConditions(unittest.TestCase):
    """Mirror test_silver_transform.TestBuildPkConditions but for IS NOT DISTINCT FROM."""

    def test_single_pk(self):
        sql = job.build_pk_conditions(["CUSTOMER_TRX_ID"])
        self.assertEqual(sql, "t.`CUSTOMER_TRX_ID` IS NOT DISTINCT FROM s.`CUSTOMER_TRX_ID`")

    def test_compound_pk(self):
        sql = job.build_pk_conditions(["ORDER_ID", "LINE_NUM"])
        self.assertEqual(
            sql,
            "t.`ORDER_ID` IS NOT DISTINCT FROM s.`ORDER_ID` AND "
            "t.`LINE_NUM` IS NOT DISTINCT FROM s.`LINE_NUM`",
        )

    def test_custom_aliases(self):
        sql = job.build_pk_conditions(["X"], alias_left="target", alias_right="src")
        self.assertEqual(sql, "target.`X` IS NOT DISTINCT FROM src.`X`")

    def test_null_free_slice_uses_plain_equality(self):
        # Plain = keeps the broadcast join path; IS NOT DISTINCT FROM forces a
        # full target shuffle that exceeds small-reservation shuffle quota.
        sql = job.build_pk_conditions(["SALES_ID"], null_safe=False)
        self.assertEqual(sql, "t.`SALES_ID` = s.`SALES_ID`")


class TestBuildIncrementalFilter(unittest.TestCase):
    def test_ts_watermark_uses_exact_filter_with_partition_prune(self):
        sql = job.build_incremental_filter({
            "last_processed_date": "2026-07-20",
            "last_processed_ts":   "2026-07-20 21:17:03.123456+00",
        })
        self.assertEqual(
            sql,
            "_extract_date >= DATE('2026-07-20') "
            "AND _extract_timestamp > TIMESTAMP('2026-07-20 21:17:03.123456+00')",
        )

    def test_date_derived_from_ts_not_stale_date_field(self):
        # ts is authoritative — a manually-edited ts moves the partition prune too.
        sql = job.build_incremental_filter({
            "last_processed_date": "2026-01-01",
            "last_processed_ts":   "2026-07-20 21:17:03+00",
        })
        self.assertIn("DATE('2026-07-20')", sql)

    def test_legacy_date_only_keeps_strict_greater(self):
        sql = job.build_incremental_filter({"last_processed_date": "2026-07-20"})
        self.assertEqual(sql, "_extract_date > DATE('2026-07-20')")

    def test_empty_ts_falls_back_to_date(self):
        sql = job.build_incremental_filter({
            "last_processed_date": "2026-07-20",
            "last_processed_ts":   None,
        })
        self.assertEqual(sql, "_extract_date > DATE('2026-07-20')")


class TestBuildPkRangePredicate(unittest.TestCase):
    def _field(self, ftype):
        f = MagicMock()
        f.field_type = ftype
        return f

    def test_int64_target_plain_literals(self):
        from decimal import Decimal
        pred = job.build_pk_range_predicate(
            "SALES_ID", self._field("INTEGER"), Decimal("538"), Decimal("212925295"))
        self.assertEqual(pred, "t.`SALES_ID` BETWEEN 538 AND 212925295")

    def test_bignumeric_target_typed_literals(self):
        pred = job.build_pk_range_predicate("SALES_ID", self._field("BIGNUMERIC"), 1, 9)
        self.assertEqual(pred, "t.`SALES_ID` BETWEEN BIGNUMERIC '1' AND BIGNUMERIC '9'")

    def test_numeric_target_typed_literals(self):
        pred = job.build_pk_range_predicate("SALES_ID", self._field("NUMERIC"), 1, 9)
        self.assertEqual(pred, "t.`SALES_ID` BETWEEN NUMERIC '1' AND NUMERIC '9'")

    def test_string_target_skips_pruning(self):
        self.assertIsNone(
            job.build_pk_range_predicate("CODE", self._field("STRING"), "A", "Z"))

    def test_missing_target_field_skips_pruning(self):
        self.assertIsNone(job.build_pk_range_predicate("SALES_ID", None, 1, 9))

    def test_null_bounds_skip_pruning(self):
        self.assertIsNone(
            job.build_pk_range_predicate("SALES_ID", self._field("INT64"), None, 9))


class TestFqtnHelpers(unittest.TestCase):
    SILVER = {
        "bq_project": "pearson-rr",
        "bq_dataset": "ENTERPRISE_IPM_SILVER",
        "table":      "XXPSO_POS_ORASALE_STG",
    }

    def test_target_fqtn(self):
        self.assertEqual(
            job._target_fqtn(self.SILVER),
            "pearson-rr.ENTERPRISE_IPM_SILVER.XXPSO_POS_ORASALE_STG",
        )

    def test_external_fqtn_uses_target_bq_dataset(self):
        cfg = {"target": {"bq_dataset": "ENTERPRISE_IPM_BRONZE",
                          "gcs_path": "gs://enterprise-ipm-bronze-dev/apps/x/"}}
        self.assertEqual(
            job._external_fqtn(cfg, self.SILVER),
            "pearson-rr.ENTERPRISE_IPM_BRONZE.XXPSO_POS_ORASALE_STG_EXT",
        )

    def test_external_fqtn_requires_target_bq_dataset(self):
        cfg = {"target": {"gcs_path": "gs://enterprise-ipm-bronze-dev/apps/x/"}}
        with self.assertRaisesRegex(ValueError, "target.bq_dataset is required"):
            job._external_fqtn(cfg, self.SILVER)

    def test_staging_fqtn_includes_run_suffix(self):
        fqtn = job._staging_fqtn(self.SILVER, "init")
        # Format: {project}.{dataset}._stg_{table}_{suffix}_{RUN_ID_SHORT}
        self.assertTrue(fqtn.startswith("pearson-rr.ENTERPRISE_IPM_SILVER._stg_XXPSO_POS_ORASALE_STG_init_"))
        self.assertIn(job.RUN_ID_SHORT, fqtn)


class TestBronzePrefix(unittest.TestCase):
    def test_trailing_slash_kept(self):
        cfg = {"target": {"gcs_path": "gs://bucket/path/"}}
        self.assertEqual(job._bronze_prefix(cfg), "gs://bucket/path/")

    def test_trailing_slash_added(self):
        cfg = {"target": {"gcs_path": "gs://bucket/path"}}
        self.assertEqual(job._bronze_prefix(cfg), "gs://bucket/path/")


class TestDecimalTargetTypes(unittest.TestCase):
    """Phase 1 spike learning encoded in code — guard against accidental change."""

    def test_decimal_fallback_order(self):
        self.assertEqual(
            job.DECIMAL_TARGET_TYPES,
            ["NUMERIC", "BIGNUMERIC", "STRING"],
        )

    def test_decimal_fallback_includes_bignumeric(self):
        # Oracle NUMBER → Parquet DECIMAL(38,10) overflows NUMERIC(38,9).
        # BIGNUMERIC must be in the list or initial loads fail on real EBS data.
        self.assertIn("BIGNUMERIC", job.DECIMAL_TARGET_TYPES)


class TestSchemaEvolve(unittest.TestCase):
    """ADD COLUMN IF NOT EXISTS + safe NUMERIC->BIGNUMERIC widening.
    No drops, no narrowings, no other type changes."""

    def _mock_field(self, name, ftype="STRING"):
        m = MagicMock()
        m.name = name
        m.field_type = ftype
        return m

    def _mock_table(self, fields):
        tbl = MagicMock()
        tbl.schema = fields
        return tbl

    def test_no_changes_when_source_subset_same_types(self):
        bq = MagicMock()
        bq.get_table.return_value = self._mock_table([
            self._mock_field("A"), self._mock_field("B"), self._mock_field("C"),
        ])
        evolve = job._schema_evolve(
            bq=bq,
            target_fqtn="p.d.t",
            source_schema=[self._mock_field("A"), self._mock_field("B")],
            location="EU",
        )
        self.assertEqual(evolve, {"added": [], "widened": []})
        bq.query.assert_not_called()

    def test_adds_only_new_columns(self):
        bq = MagicMock()
        bq.get_table.return_value = self._mock_table([
            self._mock_field("EXISTING"),
        ])
        evolve = job._schema_evolve(
            bq=bq,
            target_fqtn="p.d.t",
            source_schema=[
                self._mock_field("EXISTING"),
                self._mock_field("NEW_COL_1", "INT64"),
                self._mock_field("NEW_COL_2", "STRING"),
            ],
            location="EU",
        )
        self.assertEqual(evolve["added"], ["NEW_COL_1", "NEW_COL_2"])
        self.assertEqual(evolve["widened"], [])
        self.assertEqual(bq.query.call_count, 2)
        first_sql = bq.query.call_args_list[0][0][0]
        self.assertIn("ADD COLUMN IF NOT EXISTS `NEW_COL_1` INT64", first_sql)
        self.assertIn("ALTER TABLE `p.d.t`", first_sql)

    def test_widens_numeric_to_bignumeric(self):
        """Source field is BIGNUMERIC, target field is NUMERIC — widen the
        target. Fixes the Spark DECIMAL(38,10) -> BQ BIGNUMERIC routine drift."""
        bq = MagicMock()
        bq.get_table.return_value = self._mock_table([
            self._mock_field("YEAR", "NUMERIC"),
            self._mock_field("RATE", "NUMERIC"),
        ])
        evolve = job._schema_evolve(
            bq=bq,
            target_fqtn="p.d.t",
            source_schema=[
                self._mock_field("YEAR", "BIGNUMERIC"),
                self._mock_field("RATE", "NUMERIC"),   # unchanged, no widen
            ],
            location="EU",
        )
        self.assertEqual(evolve["added"], [])
        self.assertEqual(evolve["widened"], ["YEAR"])
        self.assertEqual(bq.query.call_count, 1)
        sql = bq.query.call_args_list[0][0][0]
        self.assertIn("ALTER COLUMN `YEAR` SET DATA TYPE BIGNUMERIC", sql)

    def test_does_not_narrow_or_change_unrelated_types(self):
        """BIGNUMERIC in target, NUMERIC in source = NO narrowing (lose data).
        STRING -> INT64 etc. also untouched — manual migration only."""
        bq = MagicMock()
        bq.get_table.return_value = self._mock_table([
            self._mock_field("BIG_COL", "BIGNUMERIC"),
            self._mock_field("STR_COL", "STRING"),
        ])
        evolve = job._schema_evolve(
            bq=bq,
            target_fqtn="p.d.t",
            source_schema=[
                self._mock_field("BIG_COL", "NUMERIC"),   # narrowing — skip
                self._mock_field("STR_COL", "INT64"),     # unrelated — skip
            ],
            location="EU",
        )
        self.assertEqual(evolve, {"added": [], "widened": []})
        bq.query.assert_not_called()

    def test_combines_adds_and_widens_in_one_pass(self):
        bq = MagicMock()
        bq.get_table.return_value = self._mock_table([
            self._mock_field("YEAR", "NUMERIC"),
        ])
        evolve = job._schema_evolve(
            bq=bq,
            target_fqtn="p.d.t",
            source_schema=[
                self._mock_field("YEAR", "BIGNUMERIC"),       # widen
                self._mock_field("NEW_COL", "STRING"),        # add
            ],
            location="EU",
        )
        self.assertEqual(evolve["added"], ["NEW_COL"])
        self.assertEqual(evolve["widened"], ["YEAR"])
        self.assertEqual(bq.query.call_count, 2)

    def test_target_not_found_returns_empty(self):
        """When target doesn't exist (true initial path), schema evolution is a no-op."""
        bq = MagicMock()
        with patch.object(job, "NotFound", Exception):
            bq.get_table.side_effect = Exception("not found")
            evolve = job._schema_evolve(
                bq=bq, target_fqtn="p.d.t",
                source_schema=[self._mock_field("X")], location="EU",
            )
        self.assertEqual(evolve, {"added": [], "widened": []})
        bq.query.assert_not_called()


class TestGuardrails(unittest.TestCase):
    """Pre-flight validation in run() should reject misconfigured pipelines."""

    def _base_cfg(self):
        return {
            "enabled": True,
            "destination": {
                "cloud": "gcp",
                "gcp_credentials_secret_arn": "arn:aws:secretsmanager:eu-west-1:111:secret:gcp-x",
            },
            "target":  {"gcs_path": "gs://bucket/x/"},
            "silver":  {
                "enabled":          True,
                "write_mode":       "bq_native",
                "table":            "T",
                "bq_project":       "p",
                "bq_dataset":       "d",
                "bq_location":      "EU",
                "primary_key_cols": ["ID"],
            },
        }

    def _run_with_cfg(self, cfg):
        """Invoke job.run() with cfg patched in. Returns the metrics put_item
        call args so tests can assert on status/error."""
        metrics_calls = []
        config_table  = MagicMock()
        config_table.get_item.return_value = {"Item": cfg}
        metrics_table = MagicMock()
        metrics_table.put_item = lambda Item: metrics_calls.append(Item)

        def _table_factory(name):
            return config_table if name == "test_config" else metrics_table

        with patch.object(job.dynamo, "Table", side_effect=_table_factory):
            with patch.object(job, "gcp_client") as gcp_mock:
                # gcp_client is reached only after guardrails pass; if it does
                # get called the test config is too valid for this assertion.
                gcp_mock.side_effect = AssertionError(
                    "gcp_client should not be reached — guardrail let bad cfg through"
                )
                with self.assertRaises(RuntimeError):
                    job.run()
        return metrics_calls

    def test_rejects_write_mode_iceberg(self):
        cfg = self._base_cfg()
        cfg["silver"]["write_mode"] = "iceberg_biglake"
        metrics = self._run_with_cfg(cfg)
        self.assertEqual(metrics[-1]["status"], "FAILED")
        self.assertIn("write_mode", metrics[-1]["error_message"])

    def test_rejects_cloud_aws(self):
        cfg = self._base_cfg()
        cfg["destination"]["cloud"] = "aws"
        metrics = self._run_with_cfg(cfg)
        self.assertEqual(metrics[-1]["status"], "FAILED")
        self.assertIn("destination.cloud", metrics[-1]["error_message"])

    def test_rejects_empty_pk_cols(self):
        cfg = self._base_cfg()
        cfg["silver"]["primary_key_cols"] = []
        metrics = self._run_with_cfg(cfg)
        self.assertEqual(metrics[-1]["status"], "FAILED")
        self.assertIn("primary_key_cols", metrics[-1]["error_message"])

    def test_rejects_missing_gcs_path(self):
        cfg = self._base_cfg()
        cfg["target"] = {}
        metrics = self._run_with_cfg(cfg)
        self.assertEqual(metrics[-1]["status"], "FAILED")
        self.assertIn("gcs_path", metrics[-1]["error_message"])


if __name__ == "__main__":
    unittest.main()
