# test_pipeline_controller.py
# Unit tests for the validation logic in lambda/pipeline_controller.py.
# Tests run without AWS credentials — boto3 clients are stubbed at import time.

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


# Stub heavy/optional deps before importing the module
sys.modules.setdefault("oracledb", MagicMock())

# Make sure the controller module is importable
_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "lambda"))

# Avoid real boto3 client construction during import
import boto3  # noqa: E402

_real_resource = boto3.resource
_real_client = boto3.client
boto3.resource = MagicMock(return_value=MagicMock())
boto3.client = MagicMock(return_value=MagicMock())

import pipeline_controller as pc  # noqa: E402

# Restore (good hygiene; other tests in the same process may want real boto3)
boto3.resource = _real_resource
boto3.client = _real_client


class TestValidateExtractConfig(unittest.TestCase):
    def test_incremental_without_watermark_fails(self):
        err = pc._validate_extract_config({
            "extract": {"mode": "incremental", "table": "FIPM.FPI_PAR_BATCH"},
        })
        self.assertIsNotNone(err)
        self.assertIn("watermark_col", err)

    def test_fallback_requires_incremental(self):
        err = pc._validate_extract_config({
            "extract": {
                "mode": "full",
                "watermark_col": "LAST_UPDATE_DATE",
                "watermark_fallback_col": "CREATION_DATE",
            },
        })
        self.assertIsNotNone(err)
        self.assertIn("incremental", err)

    def test_fallback_must_differ_from_primary(self):
        err = pc._validate_extract_config({
            "extract": {
                "mode": "incremental",
                "watermark_col": "LAST_UPDATE_DATE",
                "watermark_fallback_col": "last_update_date",
            },
        })
        self.assertIsNotNone(err)
        self.assertIn("differ", err)

    def test_valid_fallback_passes(self):
        self.assertIsNone(pc._validate_extract_config({
            "extract": {
                "mode": "incremental",
                "table": "FIPM.FPI_PAR_BATCH",
                "watermark_col": "LAST_UPDATE_DATE",
                "watermark_fallback_col": "CREATION_DATE",
            },
        }))



class TestValidateExtractCustomSql(unittest.TestCase):
    """UIFPE-665: custom_sql gets the watermark through the {{WATERMARK}}
    token. A config that would silently full-scan, or fail with ORA-00911, is
    rejected at the API boundary on create AND update."""

    SQL = ("SELECT a.* FROM IPMREP.ASSETS_V a WHERE NOT EXISTS (SELECT 1 FROM "
           "IPMREP.HEADER_V h WHERE h.project_id = a.project_id)")

    def _err(self, **ext):
        return pc._validate_extract_config({"extract": ext})

    def test_incremental_custom_sql_without_watermark_col_fails(self):
        # Allowed before UIFPE-665: the old check had an `and not custom` escape.
        err = self._err(mode="incremental", custom_sql=f"{self.SQL} AND {{{{WATERMARK}}}}")
        self.assertIsNotNone(err)
        self.assertIn("watermark_col", err)
        self.assertNotIn("unless custom_sql", err)

    def test_incremental_custom_sql_without_token_fails(self):
        err = self._err(mode="incremental", watermark_col="LAST_UPDATE_DATE",
                        custom_sql=self.SQL)
        self.assertIsNotNone(err)
        self.assertIn("{{WATERMARK}}", err)

    def test_missing_mode_is_incremental_so_token_required(self):
        # _validate_extract_config treats an absent mode as incremental.
        err = self._err(watermark_col="LAST_UPDATE_DATE", custom_sql=self.SQL)
        self.assertIsNotNone(err)
        self.assertIn("{{WATERMARK}}", err)

    def test_semicolon_rejected_in_any_mode(self):
        for mode in ("full", "incremental"):
            with self.subTest(mode=mode):
                err = self._err(mode=mode, watermark_col="LAST_UPDATE_DATE",
                                custom_sql=f"{self.SQL} AND {{{{WATERMARK}}}};")
                self.assertIsNotNone(err)
                self.assertIn("ORA-00911", err)

    def test_near_miss_tokens_rejected_in_any_mode(self):
        for token in ("{{ WATERMARK }}", "{{watermark}}", "{{Watermark}}",
                      "{WATERMARK}", "{{{WATERMARK}}}"):
            for mode in ("full", "incremental"):
                with self.subTest(token=token, mode=mode):
                    err = self._err(mode=mode, watermark_col="LAST_UPDATE_DATE",
                                    custom_sql=f"{self.SQL} AND {token}")
                    self.assertIsNotNone(err)
                    self.assertIn("exactly {{WATERMARK}}", err)

    def test_full_custom_sql_without_token_passes(self):
        self.assertIsNone(self._err(mode="full", custom_sql=self.SQL))

    def test_full_custom_sql_with_token_and_no_watermark_col_passes(self):
        # The token renders 1=1 on full, so one config can flip modes.
        self.assertIsNone(self._err(mode="full", custom_sql=f"{self.SQL} AND {{{{WATERMARK}}}}"))

    def test_incremental_custom_sql_with_token_and_watermark_col_passes(self):
        self.assertIsNone(self._err(
            mode="incremental", watermark_col="LAST_UPDATE_DATE",
            watermark_fallback_col="CREATION_DATE",
            custom_sql=f"{self.SQL} AND {{{{WATERMARK}}}}",
        ))

    def test_token_matches_the_one_bronze_renders(self):
        # The controller cannot import the Glue module at runtime (different
        # artifact), so it keeps a copy. This is the drift guard.
        import bronze_extract_sql
        self.assertEqual(pc._WATERMARK_TOKEN, bronze_extract_sql.WATERMARK_TOKEN)


class TestCustomSqlValidatedOnCreateAndUpdate(unittest.TestCase):
    """_validate_extract_config is called from both create_pipeline and
    update_pipeline, so a config registered as full + token cannot be flipped
    to incremental without a token past the check."""

    BODY_EXTRACT = {
        "mode":          "incremental",
        "table":         "IPMREP.ASSETS_V",
        "watermark_col": "LAST_UPDATE_DATE",
        "custom_sql":    "SELECT * FROM IPMREP.ASSETS_V",  # no token
    }

    def _body(self):
        return {
            "pipeline_id": "tep-ipm-ipmrep-assets-v",
            "extract": dict(self.BODY_EXTRACT),
            "silver": {
                "enabled":          True,
                "database":         "echo_dev_silver_tep",
                "table":            "assets_v",
                "s3_path":          "s3://echo-dev-silver/tep/assets_v/",
                "primary_key_cols": ["ASSET_ID"],
            },
        }

    def _assert_rejected(self, resp, mock_table):
        self.assertEqual(resp["statusCode"], 400)
        self.assertIn("{{WATERMARK}}", resp["body"])
        mock_table.return_value.put_item.assert_not_called()

    def test_create_rejects_missing_token(self):
        with patch.object(pc.dynamo, "Table") as mock_table:
            resp = pc.create_pipeline(self._body())
            self._assert_rejected(resp, mock_table)

    def test_update_rejects_missing_token(self):
        with patch.object(pc.dynamo, "Table") as mock_table:
            resp = pc.update_pipeline("tep-ipm-ipmrep-assets-v", self._body())
            self._assert_rejected(resp, mock_table)


class TestValidateSilverConfig(unittest.TestCase):
    """Catches the EGO_MTL_SY_ITEMS_EXT_B 2026-05-28 regression where a partial
    PUT wiped silver.primary_key_cols and silver MERGE failed with empty ON."""

    def test_silver_disabled_passes(self):
        self.assertIsNone(pc._validate_silver_config({"silver": {"enabled": False}}))

    def test_no_silver_block_passes(self):
        self.assertIsNone(pc._validate_silver_config({}))

    def test_silver_enabled_without_pk_fails(self):
        err = pc._validate_silver_config({
            "silver": {
                "enabled": True,
                "database": "echo_dev_silver_product",
                "table": "ego_mtl_sy_items_ext_b",
                "s3_path": "s3://echo-dev-silver/product/ego_mtl_sy_items_ext_b/",
            }
        })
        self.assertIsNotNone(err)
        self.assertIn("primary_key_cols", err)

    def test_silver_enabled_with_empty_pk_fails(self):
        err = pc._validate_silver_config({
            "silver": {
                "enabled": True,
                "primary_key_cols": [],
                "database": "echo_dev_silver_product",
                "table": "ego_mtl_sy_items_ext_b",
                "s3_path": "s3://echo-dev-silver/product/ego_mtl_sy_items_ext_b/",
            }
        })
        self.assertIsNotNone(err)
        self.assertIn("primary_key_cols", err)

    def test_silver_enabled_with_non_list_pk_fails(self):
        err = pc._validate_silver_config({
            "silver": {
                "enabled": True,
                "primary_key_cols": "EXTENSION_ID",
                "database": "echo_dev_silver_product",
                "table": "ego_mtl_sy_items_ext_b",
                "s3_path": "s3://echo-dev-silver/product/ego_mtl_sy_items_ext_b/",
            }
        })
        self.assertIsNotNone(err)

    def test_silver_enabled_missing_database_fails(self):
        err = pc._validate_silver_config({
            "silver": {
                "enabled": True,
                "primary_key_cols": ["EXTENSION_ID"],
                "table": "ego_mtl_sy_items_ext_b",
                "s3_path": "s3://echo-dev-silver/product/ego_mtl_sy_items_ext_b/",
            }
        })
        self.assertIsNotNone(err)
        self.assertIn("database", err)

    def test_silver_enabled_missing_table_fails(self):
        err = pc._validate_silver_config({
            "silver": {
                "enabled": True,
                "primary_key_cols": ["EXTENSION_ID"],
                "database": "echo_dev_silver_product",
                "s3_path": "s3://echo-dev-silver/product/ego_mtl_sy_items_ext_b/",
            }
        })
        self.assertIsNotNone(err)
        self.assertIn("table", err)

    def test_silver_enabled_missing_s3_path_fails(self):
        err = pc._validate_silver_config({
            "silver": {
                "enabled": True,
                "primary_key_cols": ["EXTENSION_ID"],
                "database": "echo_dev_silver_product",
                "table": "ego_mtl_sy_items_ext_b",
            }
        })
        self.assertIsNotNone(err)
        self.assertIn("s3_path", err)

    def test_silver_enabled_fully_specified_passes(self):
        self.assertIsNone(pc._validate_silver_config({
            "silver": {
                "enabled": True,
                "primary_key_cols": ["EXTENSION_ID"],
                "database": "echo_dev_silver_product",
                "table": "ego_mtl_sy_items_ext_b",
                "s3_path": "s3://echo-dev-silver/product/ego_mtl_sy_items_ext_b/",
            }
        }))


_VALID_BQ_SECRET_ARN = ("arn:aws:secretsmanager:eu-west-1:123456789012:"
                        "secret:gcp-silver-bq-dev-AbCdEf")


def _valid_silver_bq_body(**overrides) -> dict:
    """Build a body that passes both silver and silver_bq validation."""
    body = {
        "silver": {
            "enabled": True,
            "primary_key_cols": ["EXTENSION_ID"],
            "database": "echo_dev_silver_product",
            "table": "ego_mtl_sy_items_ext_b",
            "s3_path": "s3://echo-dev-silver/product/ego_mtl_sy_items_ext_b/",
        },
        "replication": {
            "silver_bq": {
                "enabled": True,
                "bucket": "pearson-silver-bq-dev",
                "prefix": "silver/product/ego_mtl_sy_items_ext_b/",
                "credentials_secret_arn": _VALID_BQ_SECRET_ARN,
                "bq_project": "pearson-analytics-dev",
                "bq_dataset": "echo_dev_silver_product",
                "bq_location": "EU",
            }
        },
    }
    for k, v in overrides.items():
        body["replication"]["silver_bq"][k] = v
    return body


class TestValidateSilverBqConfig(unittest.TestCase):
    """Validation for Silver→BigQuery replication config block."""

    def test_no_replication_block_passes(self):
        self.assertIsNone(pc._validate_silver_bq_config({}))

    def test_silver_bq_disabled_passes(self):
        body = _valid_silver_bq_body()
        body["replication"]["silver_bq"]["enabled"] = False
        self.assertIsNone(pc._validate_silver_bq_config(body))

    def test_silver_bq_enabled_but_silver_disabled_fails(self):
        body = _valid_silver_bq_body()
        body["silver"]["enabled"] = False
        err = pc._validate_silver_bq_config(body)
        self.assertIsNotNone(err)
        self.assertIn("silver.enabled", err)

    def test_missing_bucket_fails(self):
        body = _valid_silver_bq_body(bucket="")
        err = pc._validate_silver_bq_config(body)
        self.assertIsNotNone(err)
        self.assertIn("bucket", err)

    def test_prefix_must_end_with_slash(self):
        body = _valid_silver_bq_body(prefix="silver/product/ego")
        err = pc._validate_silver_bq_config(body)
        self.assertIsNotNone(err)
        self.assertIn("prefix", err)

    def test_invalid_secret_arn_fails(self):
        body = _valid_silver_bq_body(credentials_secret_arn="not-an-arn")
        err = pc._validate_silver_bq_config(body)
        self.assertIsNotNone(err)
        self.assertIn("credentials_secret_arn", err)

    def test_missing_bq_project_fails(self):
        body = _valid_silver_bq_body(bq_project="")
        err = pc._validate_silver_bq_config(body)
        self.assertIsNotNone(err)
        self.assertIn("bq_project", err)

    def test_missing_bq_dataset_fails(self):
        body = _valid_silver_bq_body(bq_dataset="")
        err = pc._validate_silver_bq_config(body)
        self.assertIsNotNone(err)
        self.assertIn("bq_dataset", err)

    def test_unknown_location_fails(self):
        body = _valid_silver_bq_body(bq_location="moon-base-1")
        err = pc._validate_silver_bq_config(body)
        self.assertIsNotNone(err)
        self.assertIn("bq_location", err)

    def test_empty_location_passes(self):
        body = _valid_silver_bq_body(bq_location="")
        self.assertIsNone(pc._validate_silver_bq_config(body))

    def test_fully_specified_passes(self):
        self.assertIsNone(pc._validate_silver_bq_config(_valid_silver_bq_body()))


# ── destination.cloud=gcp validation (Option 4 — GCP-native ingestion) ──

_VALID_GCP_SECRET_ARN = ("arn:aws:secretsmanager:eu-west-1:123456789012:"
                         "secret:gcp-bronze-sa-dev-XyZ123")


def _valid_gcp_body(**target_overrides) -> dict:
    """Body that passes _validate_destination_config for cloud='gcp', silver disabled."""
    body = {
        "destination": {
            "cloud": "gcp",
            "gcp_credentials_secret_arn": _VALID_GCP_SECRET_ARN,
        },
        "target": {
            "gcs_path": "gs://pearson-bronze-dev/product/orders/",
        },
    }
    body["target"].update(target_overrides)
    return body


def _valid_gcp_with_silver_body(**silver_overrides) -> dict:
    """Body that passes for cloud='gcp' with silver enabled."""
    body = _valid_gcp_body()
    body["silver"] = {
        "enabled": True,
        "table": "ORDERS",
        "primary_key_cols": ["ORDER_ID"],
        "gcs_path": "gs://pearson-silver-dev/product/orders/",
        "bq_project": "pearson-analytics-dev",
        "bq_dataset": "echo_dev_silver_product",
        "bq_location": "EU",
    }
    body["silver"].update(silver_overrides)
    return body


class TestValidateDestinationConfig(unittest.TestCase):
    """Validation for the `destination` block — gates AWS vs GCP-native flow."""

    def test_no_destination_block_defaults_to_aws_passes(self):
        self.assertIsNone(pc._validate_destination_config({}))

    def test_destination_cloud_aws_passes(self):
        self.assertIsNone(pc._validate_destination_config({"destination": {"cloud": "aws"}}))

    def test_invalid_cloud_fails(self):
        err = pc._validate_destination_config({"destination": {"cloud": "azure"}})
        self.assertIsNotNone(err)
        self.assertIn("cloud", err)

    def test_gcp_missing_secret_arn_fails(self):
        body = _valid_gcp_body()
        del body["destination"]["gcp_credentials_secret_arn"]
        err = pc._validate_destination_config(body)
        self.assertIsNotNone(err)
        self.assertIn("gcp_credentials_secret_arn", err)

    def test_gcp_invalid_secret_arn_fails(self):
        body = _valid_gcp_body()
        body["destination"]["gcp_credentials_secret_arn"] = "not-an-arn"
        err = pc._validate_destination_config(body)
        self.assertIsNotNone(err)
        self.assertIn("gcp_credentials_secret_arn", err)

    def test_gcp_missing_gcs_path_fails(self):
        body = _valid_gcp_body()
        del body["target"]["gcs_path"]
        err = pc._validate_destination_config(body)
        self.assertIsNotNone(err)
        self.assertIn("target.gcs_path", err)

    def test_gcp_gcs_path_wrong_scheme_fails(self):
        body = _valid_gcp_body(gcs_path="s3://wrong-scheme/x/")
        err = pc._validate_destination_config(body)
        self.assertIsNotNone(err)
        self.assertIn("gs://", err)

    def test_gcp_target_s3_path_present_fails(self):
        body = _valid_gcp_body()
        body["target"]["s3_path"] = "s3://leaking/"
        err = pc._validate_destination_config(body)
        self.assertIsNotNone(err)
        self.assertIn("target.s3_path", err)

    def test_gcp_silver_disabled_passes(self):
        self.assertIsNone(pc._validate_destination_config(_valid_gcp_body()))

    def test_gcp_silver_enabled_fully_specified_passes(self):
        self.assertIsNone(pc._validate_destination_config(_valid_gcp_with_silver_body()))

    def test_gcp_silver_missing_gcs_path_fails(self):
        body = _valid_gcp_with_silver_body()
        del body["silver"]["gcs_path"]
        err = pc._validate_destination_config(body)
        self.assertIsNotNone(err)
        self.assertIn("silver.gcs_path", err)

    def test_gcp_silver_missing_bq_project_fails(self):
        body = _valid_gcp_with_silver_body()
        body["silver"]["bq_project"] = ""
        err = pc._validate_destination_config(body)
        self.assertIsNotNone(err)
        self.assertIn("bq_project", err)

    def test_gcp_silver_missing_bq_dataset_fails(self):
        body = _valid_gcp_with_silver_body()
        del body["silver"]["bq_dataset"]
        err = pc._validate_destination_config(body)
        self.assertIsNotNone(err)
        self.assertIn("bq_dataset", err)

    def test_gcp_silver_invalid_location_fails(self):
        body = _valid_gcp_with_silver_body(bq_location="moon-base-1")
        err = pc._validate_destination_config(body)
        self.assertIsNotNone(err)
        self.assertIn("bq_location", err)

    def test_gcp_silver_missing_primary_keys_fails(self):
        body = _valid_gcp_with_silver_body()
        body["silver"]["primary_key_cols"] = []
        err = pc._validate_destination_config(body)
        self.assertIsNotNone(err)
        self.assertIn("primary_key_cols", err)

    def test_gcp_silver_s3_path_leak_fails(self):
        body = _valid_gcp_with_silver_body()
        body["silver"]["s3_path"] = "s3://leaking-silver/x/"
        err = pc._validate_destination_config(body)
        self.assertIsNotNone(err)
        self.assertIn("silver.s3_path", err)

    def test_gcp_replication_gcs_enabled_rejected(self):
        body = _valid_gcp_body()
        body["replication"] = {"gcs": {"enabled": True, "bucket": "x", "prefix": "x/",
                                        "credentials_secret_arn": _VALID_GCP_SECRET_ARN}}
        err = pc._validate_destination_config(body)
        self.assertIsNotNone(err)
        self.assertIn("self-replication", err)

    def test_gcp_replication_silver_bq_enabled_rejected(self):
        body = _valid_gcp_with_silver_body()
        body["replication"] = {"silver_bq": {"enabled": True}}
        err = pc._validate_destination_config(body)
        self.assertIsNotNone(err)
        self.assertIn("self-replication", err)


def _valid_gcp_bq_native_body() -> dict:
    """GCP body with silver.write_mode='bq_native' — no Silver gcs_path.
    Includes target.bq_dataset (Bronze BQ dataset for the BigLake external
    table), which is required for bq_native and must differ from
    silver.bq_dataset."""
    body = _valid_gcp_with_silver_body()
    body["silver"]["write_mode"] = "bq_native"
    body["silver"].pop("gcs_path", None)
    body["target"]["bq_dataset"] = "bronze_product"
    return body


class TestSilverWriteModeValidation(unittest.TestCase):
    """silver.write_mode gates Iceberg+BigLake (default) vs BQ-native MERGE.
    Validation must reject contradictory combos (gcs_path + bq_native, etc.)."""

    def test_write_mode_unset_defaults_to_iceberg_biglake(self):
        """No write_mode field → existing Iceberg validation applies."""
        self.assertIsNone(pc._validate_destination_config(_valid_gcp_with_silver_body()))

    def test_write_mode_iceberg_biglake_explicit_passes(self):
        body = _valid_gcp_with_silver_body(write_mode="iceberg_biglake")
        self.assertIsNone(pc._validate_destination_config(body))

    def test_write_mode_bq_native_without_gcs_path_passes(self):
        self.assertIsNone(pc._validate_destination_config(_valid_gcp_bq_native_body()))

    def test_write_mode_bq_native_with_gcs_path_fails(self):
        body = _valid_gcp_bq_native_body()
        body["silver"]["gcs_path"] = "gs://should-not-be-here/x/"
        err = pc._validate_destination_config(body)
        self.assertIsNotNone(err)
        self.assertIn("silver.gcs_path", err)

    def test_write_mode_iceberg_biglake_without_gcs_path_fails(self):
        body = _valid_gcp_with_silver_body()
        del body["silver"]["gcs_path"]
        err = pc._validate_destination_config(body)
        self.assertIsNotNone(err)
        self.assertIn("silver.gcs_path", err)

    def test_write_mode_invalid_value_fails(self):
        body = _valid_gcp_with_silver_body(write_mode="bigquery_streaming")
        err = pc._validate_destination_config(body)
        self.assertIsNotNone(err)
        self.assertIn("write_mode", err)

    def test_write_mode_bq_native_normalises_case(self):
        body = _valid_gcp_bq_native_body()
        body["silver"]["write_mode"] = "BQ_NATIVE"
        self.assertIsNone(pc._validate_destination_config(body))

    def test_bq_partition_field_default_passes(self):
        # Default of "_silver_updated_at" when field absent.
        self.assertIsNone(pc._validate_destination_config(_valid_gcp_bq_native_body()))

    def test_bq_partition_field_explicit_null_passes(self):
        body = _valid_gcp_bq_native_body()
        body["silver"]["bq_partition_field"] = None
        self.assertIsNone(pc._validate_destination_config(body))

    def test_bq_partition_field_empty_string_fails(self):
        body = _valid_gcp_bq_native_body()
        body["silver"]["bq_partition_field"] = ""
        err = pc._validate_destination_config(body)
        self.assertIsNotNone(err)
        self.assertIn("bq_partition_field", err)

    def test_bq_partition_integer_range_valid(self):
        body = _valid_gcp_bq_native_body()
        body["silver"]["int64_cols"] = ["SALES_ID"]
        body["silver"]["bq_partition"] = {
            "type": "integer_range",
            "field": "SALES_ID",
            "range": {"start": 0, "end": 1000, "interval": 100},
        }
        self.assertIsNone(pc._validate_destination_config(body))

    def test_bq_partition_integer_range_missing_int64_cols_fails(self):
        body = _valid_gcp_bq_native_body()
        body["silver"]["bq_partition"] = {
            "type": "integer_range",
            "field": "SALES_ID",
            "range": {"start": 0, "end": 1000, "interval": 100},
        }
        err = pc._validate_destination_config(body)
        self.assertIsNotNone(err)
        self.assertIn("int64_cols", err)

    def test_bq_partition_conflicts_with_legacy_field(self):
        body = _valid_gcp_bq_native_body()
        body["silver"]["bq_partition_field"] = "_extract_date"
        body["silver"]["bq_partition"] = {
            "type": "date_day",
            "field": "_silver_updated_at",
        }
        err = pc._validate_destination_config(body)
        self.assertIsNotNone(err)
        self.assertIn("conflicts", err)

    def test_bq_native_missing_target_bq_dataset_fails(self):
        body = _valid_gcp_bq_native_body()
        del body["target"]["bq_dataset"]
        err = pc._validate_destination_config(body)
        self.assertIsNotNone(err)
        self.assertIn("target.bq_dataset", err)

    def test_bq_native_empty_target_bq_dataset_fails(self):
        body = _valid_gcp_bq_native_body()
        body["target"]["bq_dataset"] = "   "
        err = pc._validate_destination_config(body)
        self.assertIsNotNone(err)
        self.assertIn("target.bq_dataset", err)

    def test_bq_native_target_bq_dataset_matches_silver_fails(self):
        body = _valid_gcp_bq_native_body()
        body["target"]["bq_dataset"] = body["silver"]["bq_dataset"]
        err = pc._validate_destination_config(body)
        self.assertIsNotNone(err)
        self.assertIn("must differ from silver.bq_dataset", err)

    def test_iceberg_biglake_does_not_require_target_bq_dataset(self):
        # Iceberg+BigLake reuses silver.bq_dataset for the external table.
        body = _valid_gcp_with_silver_body()  # iceberg_biglake default
        self.assertNotIn("bq_dataset", body.get("target", {}))
        self.assertIsNone(pc._validate_destination_config(body))


class TestSilverValidationShortCircuitsForGcp(unittest.TestCase):
    """When destination.cloud=gcp, _validate_silver_config defers to
    _validate_destination_config (which checks gcs_path/bq_* instead of
    database/s3_path). It should not reject GCP silver configs."""

    def test_gcp_silver_with_no_s3_path_passes_silver_validator(self):
        # In AWS mode this would fail (missing database, s3_path); in GCP mode
        # the silver validator must short-circuit.
        body = _valid_gcp_with_silver_body()
        self.assertIsNone(pc._validate_silver_config(body))


class TestDestCloudHelper(unittest.TestCase):
    def test_defaults_to_aws(self):
        self.assertEqual(pc._dest_cloud({}), "aws")
        self.assertEqual(pc._dest_cloud({"destination": {}}), "aws")

    def test_normalises_case(self):
        self.assertEqual(pc._dest_cloud({"destination": {"cloud": "GCP"}}), "gcp")
        self.assertEqual(pc._dest_cloud({"destination": {"cloud": "AWS"}}), "aws")


class TestEnsureBqDatasetCalledForGcp(unittest.TestCase):
    """Phase 3 wiring: create_pipeline() routes the auto-DB-create call by
    destination.cloud — AWS pipelines hit ensure_silver_database, GCP pipelines
    hit ensure_bq_dataset. Verify the wiring only — don't exercise the real
    google-cloud-bigquery client (that's covered by the helper's own
    deployment validation)."""

    def _gcp_create_body(self):
        return {
            # UIFPE-355: extract is required - PUT/POST are full configs, and an
            # absent extract.mode defaults to "incremental"
            # (pipeline_controller.py:398), which then demands a watermark_col.
            "extract": {
                "mode":          "incremental",
                "table":         "APPS.ORDERS",
                "watermark_col": "LAST_UPDATE_DATE",
            },
            "pipeline_id": "test-gcp-pipeline",
            "destination": {
                "cloud": "gcp",
                "gcp_credentials_secret_arn": _VALID_GCP_SECRET_ARN,
            },
            "target": {"gcs_path": "gs://pearson-bronze-dev/product/orders/"},
            "silver": {
                "enabled":           True,
                "table":             "ORDERS",
                "primary_key_cols":  ["ORDER_ID"],
                "gcs_path":          "gs://pearson-silver-dev/product/orders/",
                "bq_project":        "pearson-analytics-dev",
                "bq_dataset":        "echo_dev_silver_product",
                "bq_location":       "EU",
            },
        }

    def test_create_pipeline_gcp_calls_ensure_bq_dataset(self):
        body = self._gcp_create_body()
        with patch.object(pc, "ensure_bq_dataset") as mock_bq, \
             patch.object(pc, "ensure_silver_database") as mock_silver, \
             patch.object(pc.dynamo, "Table") as mock_table:
            mock_table.return_value.put_item = MagicMock()
            resp = pc.create_pipeline(body)
            self.assertEqual(resp["statusCode"], 201)
            mock_bq.assert_called_once_with(
                "pearson-analytics-dev",   # bq_project
                "echo_dev_silver_product",          # bq_dataset
                "EU",                      # bq_location
                _VALID_GCP_SECRET_ARN,     # secret_arn
            )
            mock_silver.assert_not_called()

    def test_create_pipeline_gcp_defaults_bq_location_to_eu(self):
        body = self._gcp_create_body()
        del body["silver"]["bq_location"]
        with patch.object(pc, "ensure_bq_dataset") as mock_bq, \
             patch.object(pc.dynamo, "Table") as mock_table:
            mock_table.return_value.put_item = MagicMock()
            pc.create_pipeline(body)
            mock_bq.assert_called_once()
            # 3rd positional arg is bq_location
            self.assertEqual(mock_bq.call_args.args[2], "EU")

    def test_create_pipeline_gcp_silver_disabled_skips_ensure_bq(self):
        body = self._gcp_create_body()
        body["silver"]["enabled"] = False
        # When silver is disabled the body no longer needs the full silver block
        # to pass destination validation — strip extras for clarity.
        body["silver"] = {"enabled": False}
        with patch.object(pc, "ensure_bq_dataset") as mock_bq, \
             patch.object(pc, "ensure_silver_database") as mock_silver, \
             patch.object(pc.dynamo, "Table") as mock_table:
            mock_table.return_value.put_item = MagicMock()
            pc.create_pipeline(body)
            mock_bq.assert_not_called()
            mock_silver.assert_not_called()

    def test_create_pipeline_aws_does_not_call_ensure_bq(self):
        # Sanity check: the AWS branch still goes through ensure_silver_database
        # and not through ensure_bq_dataset.
        body = {
            "pipeline_id": "test-aws-pipeline",
            # UIFPE-355: an absent extract.mode defaults to "incremental", which
            # then requires a watermark_col, so the create 400s before it ever
            # reaches the ensure_* branch this test is about.
            "extract": {
                "mode":          "incremental",
                "table":         "APPS.ORDERS",
                "watermark_col": "LAST_UPDATE_DATE",
            },
            "silver": {
                "enabled":           True,
                "database":          "echo_dev_silver_product",
                "table":             "orders",
                "s3_path":           "s3://echo-dev-silver/product/orders/",
                "primary_key_cols":  ["ORDER_ID"],
            },
        }
        with patch.object(pc, "ensure_bq_dataset") as mock_bq, \
             patch.object(pc, "ensure_silver_database") as mock_silver, \
             patch.object(pc.dynamo, "Table") as mock_table:
            mock_table.return_value.put_item = MagicMock()
            pc.create_pipeline(body)
            mock_bq.assert_not_called()
            mock_silver.assert_called_once_with("echo_dev_silver_product")


class TestEnsureBqDatasetCalledForGcpOnUpdate(unittest.TestCase):
    """Regression: the 2026-06-13 GCP smoke registered via PUT (not POST), and
    the BQ dataset was silently never created because update_pipeline didn't
    mirror create_pipeline's ensure_* hook. Both ensure helpers are idempotent
    by design, so calling them on every PUT is safe and removes the gap.
    Mirrors the create_pipeline tests above."""

    _PIPELINE_ID = "test-gcp-pipeline-put"

    def _gcp_update_body(self):
        return {
            # UIFPE-355: extract is required - PUT/POST are full configs, and an
            # absent extract.mode defaults to "incremental"
            # (pipeline_controller.py:398), which then demands a watermark_col.
            "extract": {
                "mode":          "incremental",
                "table":         "APPS.ORDERS",
                "watermark_col": "LAST_UPDATE_DATE",
            },
            "destination": {
                "cloud": "gcp",
                "gcp_credentials_secret_arn": _VALID_GCP_SECRET_ARN,
            },
            "target": {"gcs_path": "gs://pearson-bronze-dev/product/orders/"},
            "silver": {
                "enabled":           True,
                "table":             "ORDERS",
                "primary_key_cols":  ["ORDER_ID"],
                "gcs_path":          "gs://pearson-silver-dev/product/orders/",
                "bq_project":        "pearson-analytics-dev",
                "bq_dataset":        "echo_dev_silver_product",
                "bq_location":       "EU",
            },
        }

    def test_update_pipeline_gcp_calls_ensure_bq_dataset(self):
        body = self._gcp_update_body()
        with patch.object(pc, "ensure_bq_dataset") as mock_bq, \
             patch.object(pc, "ensure_silver_database") as mock_silver, \
             patch.object(pc.dynamo, "Table") as mock_table:
            mock_table.return_value.put_item = MagicMock()
            resp = pc.update_pipeline(self._PIPELINE_ID, body)
            self.assertEqual(resp["statusCode"], 200)
            mock_bq.assert_called_once_with(
                "pearson-analytics-dev",
                "echo_dev_silver_product",
                "EU",
                _VALID_GCP_SECRET_ARN,
            )
            mock_silver.assert_not_called()

    def test_update_pipeline_aws_calls_ensure_silver_database(self):
        body = {
            # UIFPE-355: see the note on the create-side sibling above.
            "extract": {
                "mode":          "incremental",
                "table":         "APPS.ORDERS",
                "watermark_col": "LAST_UPDATE_DATE",
            },
            "silver": {
                "enabled":           True,
                "database":          "echo_dev_silver_product",
                "table":             "orders",
                "s3_path":           "s3://echo-dev-silver/product/orders/",
                "primary_key_cols":  ["ORDER_ID"],
            },
        }
        with patch.object(pc, "ensure_bq_dataset") as mock_bq, \
             patch.object(pc, "ensure_silver_database") as mock_silver, \
             patch.object(pc.dynamo, "Table") as mock_table:
            mock_table.return_value.put_item = MagicMock()
            pc.update_pipeline("test-aws-pipeline-put", body)
            mock_bq.assert_not_called()
            mock_silver.assert_called_once_with("echo_dev_silver_product")

    def test_update_pipeline_gcp_silver_disabled_skips_ensure_bq(self):
        body = self._gcp_update_body()
        body["silver"] = {"enabled": False}
        with patch.object(pc, "ensure_bq_dataset") as mock_bq, \
             patch.object(pc, "ensure_silver_database") as mock_silver, \
             patch.object(pc.dynamo, "Table") as mock_table:
            mock_table.return_value.put_item = MagicMock()
            pc.update_pipeline(self._PIPELINE_ID, body)
            mock_bq.assert_not_called()
            mock_silver.assert_not_called()


class TestUpdatePipelineMetadataPreservation(unittest.TestCase):
    """PUT /pipelines/{id} must preserve audit fields (registered_at,
    last_run_id, last_run_at, last_toggled_at) from the existing DynamoDB
    record. User-editable fields (owner, tags, domain, source_system) on the
    incoming body should override the existing values.

    Regression context: prior to this fix, update_pipeline did a naked put_item
    that wiped the metadata block whenever the client constructed a fresh
    body — silently resetting registered_at on every UI save and dropping
    the Glue-written last_run_id/last_run_at.
    """

    _PIPELINE_ID = "test-metadata-preservation"
    _ORIGINAL_REGISTERED_AT = "2026-06-15T22:58:42.416456+00:00"
    _ORIGINAL_LAST_RUN_ID   = "jr_original_run_xyz"
    _ORIGINAL_LAST_RUN_AT   = "2026-06-16T08:38:46.061105+00:00"
    _ORIGINAL_LAST_TOGGLED  = "2026-06-15T23:00:00.000000+00:00"

    def _minimal_aws_body(self):
        # "Minimal" means minimal metadata, not a partial config. PUT
        # /pipelines/{id} is a FULL REPLACE and the documented flow is GET first
        # (docs/echo-backend-reference.md), so a real caller always sends the
        # whole config back - extract included.
        #
        # This body originally omitted `extract` entirely. That predates the
        # watermark validation: pipeline_controller defaults an absent
        # extract.mode to "incremental" (pipeline_controller.py:398), and an
        # incremental extract with no watermark_col is rejected - correctly,
        # because it would full-scan the source on every run. The same file's
        # TestValidateExtract asserts exactly that rejection and passes.
        return {
            "extract": {
                "mode":          "incremental",
                "table":         "APPS.ORDERS",
                "watermark_col": "LAST_UPDATE_DATE",
            },
            "silver": {
                "enabled":          True,
                "database":         "echo_dev_silver_product",
                "table":            "orders",
                "s3_path":          "s3://echo-dev-silver/product/orders/",
                "primary_key_cols": ["ORDER_ID"],
            },
        }

    def _existing_record(self, **meta_overrides):
        meta = {
            "registered_at":   self._ORIGINAL_REGISTERED_AT,
            "last_run_id":     self._ORIGINAL_LAST_RUN_ID,
            "last_run_at":     self._ORIGINAL_LAST_RUN_AT,
            "last_toggled_at": self._ORIGINAL_LAST_TOGGLED,
            "owner":           "original-owner@pearson.com",
            "domain":          "product",
            "source_system":   "Oracle EBS",
            "tags":            ["sales", "europe"],
        }
        meta.update(meta_overrides)
        return {
            "pipeline_id": self._PIPELINE_ID,
            "version":     "latest",
            "metadata":    meta,
        }

    def _run_update_and_get_persisted(self, body, existing=None):
        """Invoke update_pipeline and return the dict that was passed to
        put_item. Mocks both ensure_* helpers and DynamoDB."""
        put_calls = []
        mock_tbl = MagicMock()
        mock_tbl.put_item = lambda Item: put_calls.append(Item)
        mock_tbl.get_item = MagicMock(return_value={"Item": existing} if existing else {})
        with patch.object(pc, "ensure_bq_dataset"), \
             patch.object(pc, "ensure_silver_database"), \
             patch.object(pc.dynamo, "Table", return_value=mock_tbl):
            resp = pc.update_pipeline(self._PIPELINE_ID, body)
        self.assertEqual(resp["statusCode"], 200, msg=f"update_pipeline returned {resp}")
        self.assertEqual(len(put_calls), 1, msg="expected exactly one put_item")
        return put_calls[0]

    # ── Audit fields preserved ───────────────────────────────────

    def test_put_without_metadata_block_preserves_registered_at(self):
        body = self._minimal_aws_body()  # no metadata at all
        persisted = self._run_update_and_get_persisted(body, existing=self._existing_record())
        self.assertEqual(
            persisted["metadata"]["registered_at"],
            self._ORIGINAL_REGISTERED_AT,
            "registered_at must be preserved when body omits metadata",
        )

    def test_put_without_metadata_block_preserves_last_run_id_and_at(self):
        body = self._minimal_aws_body()
        persisted = self._run_update_and_get_persisted(body, existing=self._existing_record())
        self.assertEqual(persisted["metadata"]["last_run_id"], self._ORIGINAL_LAST_RUN_ID)
        self.assertEqual(persisted["metadata"]["last_run_at"], self._ORIGINAL_LAST_RUN_AT)

    def test_put_without_metadata_block_preserves_last_toggled_at(self):
        body = self._minimal_aws_body()
        persisted = self._run_update_and_get_persisted(body, existing=self._existing_record())
        self.assertEqual(persisted["metadata"]["last_toggled_at"], self._ORIGINAL_LAST_TOGGLED)

    def test_put_cannot_overwrite_registered_at_via_body(self):
        """Even if the client TRIES to send a different registered_at,
        the existing value wins (it's an audit-only field)."""
        body = self._minimal_aws_body()
        body["metadata"] = {"registered_at": "2099-12-31T23:59:59+00:00"}
        persisted = self._run_update_and_get_persisted(body, existing=self._existing_record())
        self.assertEqual(
            persisted["metadata"]["registered_at"],
            self._ORIGINAL_REGISTERED_AT,
            "client-supplied registered_at must be ignored",
        )

    def test_put_cannot_overwrite_last_run_id_via_body(self):
        body = self._minimal_aws_body()
        body["metadata"] = {"last_run_id": "jr_FORGED"}
        persisted = self._run_update_and_get_persisted(body, existing=self._existing_record())
        self.assertEqual(persisted["metadata"]["last_run_id"], self._ORIGINAL_LAST_RUN_ID)

    # ── User-editable fields: incoming wins ──────────────────────

    def test_put_metadata_owner_overrides_existing(self):
        body = self._minimal_aws_body()
        body["metadata"] = {"owner": "new-owner@pearson.com"}
        persisted = self._run_update_and_get_persisted(body, existing=self._existing_record())
        self.assertEqual(persisted["metadata"]["owner"], "new-owner@pearson.com")

    def test_put_metadata_tags_overrides_existing(self):
        body = self._minimal_aws_body()
        body["metadata"] = {"tags": ["asia"]}
        persisted = self._run_update_and_get_persisted(body, existing=self._existing_record())
        self.assertEqual(persisted["metadata"]["tags"], ["asia"])

    def test_put_metadata_domain_overrides_existing(self):
        body = self._minimal_aws_body()
        body["metadata"] = {"domain": "finance"}
        persisted = self._run_update_and_get_persisted(body, existing=self._existing_record())
        self.assertEqual(persisted["metadata"]["domain"], "finance")

    # ── First-time PUT (no existing record) ──────────────────────

    def test_put_with_no_existing_record_initializes_metadata(self):
        body = self._minimal_aws_body()
        # UIFPE-355: override the table only. This used to REPLACE the whole
        # extract dict with a table-only one, which reintroduced the missing
        # watermark_col and 400'd. The test is about metadata initialisation,
        # not about extract, so mutating the one field keeps its intent.
        body["extract"]["table"] = "APPS.MTL_SYSTEM_ITEMS_B"
        persisted = self._run_update_and_get_persisted(body, existing=None)
        # registered_at gets defaulted to now() (we don't assert exact value)
        self.assertIn("registered_at", persisted["metadata"])
        self.assertEqual(persisted["metadata"]["source_system"], "Oracle EBS")
        self.assertEqual(persisted["metadata"]["tags"], [])
        # No audit fields to preserve since there was no existing record
        self.assertNotIn("last_run_id", persisted["metadata"])
        self.assertNotIn("last_run_at", persisted["metadata"])

    # ── Audit fields absent in existing record → no resurrection ──

    def test_put_does_not_create_missing_audit_fields_from_existing(self):
        """If an existing record lacks last_run_id (e.g. never ran), the PUT
        result should also lack it — not introduce it as null."""
        existing = self._existing_record()
        del existing["metadata"]["last_run_id"]
        del existing["metadata"]["last_run_at"]
        body = self._minimal_aws_body()
        persisted = self._run_update_and_get_persisted(body, existing=existing)
        self.assertNotIn("last_run_id", persisted["metadata"])
        self.assertNotIn("last_run_at", persisted["metadata"])
        # registered_at + last_toggled_at still preserved
        self.assertEqual(persisted["metadata"]["registered_at"], self._ORIGINAL_REGISTERED_AT)
        self.assertEqual(persisted["metadata"]["last_toggled_at"], self._ORIGINAL_LAST_TOGGLED)


class TestEnsureBqDatasetRest(unittest.TestCase):
    """Direct tests for ensure_bq_dataset's REST-API body.

    pipeline_controller no longer depends on google-cloud-bigquery (too heavy
    to coexist with oracle_instant_client under Lambda's 250 MiB layer limit).
    Instead it mints a token via google-auth + urllib3 and calls the BigQuery
    REST API directly. These tests pin that contract so the layer can never
    silently regress to "import succeeded but body broken".
    """

    _SECRET_ARN = "arn:aws:secretsmanager:eu-west-1:283556840805:secret:gcp-bronze-replicator-dev-gQgSLh"

    def _secret_payload(self):
        # Shape of the GCP service-account JSON we store in Secrets Manager.
        return {
            "project_id": "informatica-etl-nonprd",
            "client_email": "data-connect@informatica-etl-nonprd.iam.gserviceaccount.com",
            "private_key_id": "k1",
            "private_key": "PEM-NOT-VALIDATED-IN-TESTS",
        }

    def _patches(self, get_status=404, post_status=200):
        """Common patches: secrets fetch, google-auth credentials, urllib3 HTTP."""
        # Pretend the slim layer is present and the urllib3 module is importable.
        # Tests construct their own MagicMock pool below.
        mock_http_resp_get  = MagicMock(status=get_status,  data=b'{"error":"not found"}')
        mock_http_resp_post = MagicMock(status=post_status, data=b'{"id":"ok"}')
        mock_pool = MagicMock()
        mock_pool.request.side_effect = [mock_http_resp_get, mock_http_resp_post]

        mock_urllib3 = MagicMock()
        mock_urllib3.PoolManager.return_value = mock_pool

        mock_creds = MagicMock()
        mock_creds.token = "ya29.fake-token"

        mock_sa = MagicMock()
        mock_sa.Credentials.from_service_account_info.return_value = mock_creds

        return mock_pool, mock_urllib3, mock_sa, mock_creds

    def test_skips_when_layer_missing(self):
        with patch.object(pc, "_bigquery_available", False), \
             patch.object(pc, "_urllib3", None), \
             patch.object(pc, "secrets") as mock_secrets:
            pc.ensure_bq_dataset("p", "d", "EU", self._SECRET_ARN)
            mock_secrets.get_secret_value.assert_not_called()

    def test_skips_when_secret_arn_empty(self):
        with patch.object(pc, "_bigquery_available", True), \
             patch.object(pc, "_urllib3", MagicMock()), \
             patch.object(pc, "secrets") as mock_secrets:
            pc.ensure_bq_dataset("p", "d", "EU", "")
            mock_secrets.get_secret_value.assert_not_called()

    def test_creates_dataset_when_404(self):
        pool, urllib3_mod, sa_mod, _creds = self._patches(
            get_status=404, post_status=200
        )
        with patch.object(pc, "_bigquery_available", True), \
             patch.object(pc, "_urllib3", urllib3_mod), \
             patch.object(pc, "_gcp_service_account", sa_mod), \
             patch.object(pc, "secrets") as mock_secrets:
            mock_secrets.get_secret_value.return_value = {
                "SecretString": '{"project_id":"informatica-etl-nonprd",'
                                 '"client_email":"x@y","private_key_id":"k",'
                                 '"private_key":"PEM"}'
            }
            pc.ensure_bq_dataset("informatica-etl-nonprd", "smoke_silver",
                                  "EU", self._SECRET_ARN)

        # Two calls: GET (probe) then POST (create).
        self.assertEqual(pool.request.call_count, 2)
        get_call, post_call = pool.request.call_args_list
        self.assertEqual(get_call.args[0], "GET")
        self.assertIn("/datasets/smoke_silver", get_call.args[1])
        self.assertEqual(post_call.args[0], "POST")
        self.assertIn("/datasets", post_call.args[1])
        # The POST body must carry the EU location + dataset reference.
        body = post_call.kwargs.get("body") or post_call.args[3]
        import json as _json
        parsed = _json.loads(body)
        self.assertEqual(parsed["location"], "EU")
        self.assertEqual(parsed["datasetReference"]["datasetId"], "smoke_silver")
        self.assertEqual(parsed["datasetReference"]["projectId"],
                         "informatica-etl-nonprd")

    def test_short_circuits_when_dataset_exists(self):
        pool, urllib3_mod, sa_mod, _creds = self._patches(
            get_status=200, post_status=200
        )
        with patch.object(pc, "_bigquery_available", True), \
             patch.object(pc, "_urllib3", urllib3_mod), \
             patch.object(pc, "_gcp_service_account", sa_mod), \
             patch.object(pc, "secrets") as mock_secrets:
            mock_secrets.get_secret_value.return_value = {
                "SecretString": '{"project_id":"p","client_email":"x@y",'
                                 '"private_key_id":"k","private_key":"PEM"}'
            }
            pc.ensure_bq_dataset("p", "d", "EU", self._SECRET_ARN)
        # Only the GET probe — no POST.
        self.assertEqual(pool.request.call_count, 1)
        self.assertEqual(pool.request.call_args.args[0], "GET")

    def test_swallows_409_as_already_exists(self):
        pool, urllib3_mod, sa_mod, _creds = self._patches(
            get_status=404, post_status=409
        )
        with patch.object(pc, "_bigquery_available", True), \
             patch.object(pc, "_urllib3", urllib3_mod), \
             patch.object(pc, "_gcp_service_account", sa_mod), \
             patch.object(pc, "secrets") as mock_secrets:
            mock_secrets.get_secret_value.return_value = {
                "SecretString": '{"project_id":"p","client_email":"x@y",'
                                 '"private_key_id":"k","private_key":"PEM"}'
            }
            # Should not raise — 409 = idempotent race with concurrent create.
            pc.ensure_bq_dataset("p", "d", "EU", self._SECRET_ARN)

    def test_defaults_location_to_eu(self):
        pool, urllib3_mod, sa_mod, _creds = self._patches()
        with patch.object(pc, "_bigquery_available", True), \
             patch.object(pc, "_urllib3", urllib3_mod), \
             patch.object(pc, "_gcp_service_account", sa_mod), \
             patch.object(pc, "secrets") as mock_secrets:
            mock_secrets.get_secret_value.return_value = {
                "SecretString": '{"project_id":"p","client_email":"x@y",'
                                 '"private_key_id":"k","private_key":"PEM"}'
            }
            pc.ensure_bq_dataset("p", "d", "", self._SECRET_ARN)
        post_call = pool.request.call_args_list[1]
        body = post_call.kwargs.get("body") or post_call.args[3]
        import json as _json
        self.assertEqual(_json.loads(body)["location"], "EU")


class TestTriggerRunLayerRouting(unittest.TestCase):
    """`POST /pipelines/{id}/run?layer=reconcile` must route to the correct
    cloud-specific reconcile Glue job. Default (no layer) stays on bronze."""

    def _stub_dynamo(self, cfg):
        mock_table = MagicMock()
        mock_table.get_item.return_value = {"Item": cfg}
        return mock_table

    def _aws_cfg(self):
        return {
            "pipeline_id": "p1", "version": "latest",
            "extract": {"mode": "incremental"},
            "glue":    {"worker_type": "G.1X", "num_workers": 2, "timeout_minutes": 60},
        }

    def _gcp_cfg(self):
        cfg = self._aws_cfg()
        cfg["destination"] = {"cloud": "gcp"}
        return cfg

    def test_default_layer_uses_bronze_job(self):
        with patch.object(pc, "dynamo") as mock_dynamo, \
             patch.object(pc, "glue")   as mock_glue:
            mock_dynamo.Table.return_value = self._stub_dynamo(self._aws_cfg())
            mock_glue.start_job_run.return_value = {"JobRunId": "jr-1"}
            resp = pc.trigger_run("p1", {})
        self.assertEqual(resp["statusCode"], 200)
        self.assertEqual(mock_glue.start_job_run.call_args.kwargs["JobName"], pc.GLUE_JOB_NAME)

    def test_reconcile_layer_aws_routes_to_silver_reconcile(self):
        with patch.object(pc, "dynamo") as mock_dynamo, \
             patch.object(pc, "glue")   as mock_glue:
            mock_dynamo.Table.return_value = self._stub_dynamo(self._aws_cfg())
            mock_glue.start_job_run.return_value = {"JobRunId": "jr-2"}
            resp = pc.trigger_run("p1", {}, layer="reconcile")
        self.assertEqual(resp["statusCode"], 200)
        self.assertEqual(
            mock_glue.start_job_run.call_args.kwargs["JobName"],
            pc.RECONCILE_JOB_NAME,
        )

    def test_reconcile_layer_gcp_routes_to_gcp_reconcile(self):
        with patch.object(pc, "dynamo") as mock_dynamo, \
             patch.object(pc, "glue")   as mock_glue:
            mock_dynamo.Table.return_value = self._stub_dynamo(self._gcp_cfg())
            mock_glue.start_job_run.return_value = {"JobRunId": "jr-3"}
            resp = pc.trigger_run("p1", {}, layer="reconcile")
        self.assertEqual(resp["statusCode"], 200)
        self.assertEqual(
            mock_glue.start_job_run.call_args.kwargs["JobName"],
            pc.RECONCILE_JOB_NAME_GCP,
        )

    def test_silver_layer_aws_routes_to_silver_transform(self):
        cfg = self._aws_cfg()
        cfg["silver"] = {"enabled": True, "database": "db", "table": "t", "s3_path": "s3://x/", "primary_key_cols": ["ID"]}
        with patch.object(pc, "dynamo") as mock_dynamo, \
             patch.object(pc, "glue")   as mock_glue:
            mock_dynamo.Table.return_value = self._stub_dynamo(cfg)
            mock_glue.start_job_run.return_value = {"JobRunId": "jr-silver"}
            resp = pc.trigger_run("p1", {}, layer="silver")
        self.assertEqual(resp["statusCode"], 200)
        self.assertEqual(
            mock_glue.start_job_run.call_args.kwargs["JobName"],
            pc.SILVER_JOB_NAME,
        )

    def test_invalid_layer_returns_400(self):
        with patch.object(pc, "dynamo") as mock_dynamo, \
             patch.object(pc, "glue")   as mock_glue:
            mock_dynamo.Table.return_value = self._stub_dynamo(self._aws_cfg())
            resp = pc.trigger_run("p1", {}, layer="gold")
        self.assertEqual(resp["statusCode"], 400)
        mock_glue.start_job_run.assert_not_called()


class TestInferDomain(unittest.TestCase):
    """Domain inference drives the UI filter dropdown; mirrors
    ui/src/lib/domain.ts. Both sides must produce identical labels for the
    same table, otherwise filter selections silently drop matching rows."""

    def test_mtl_prefix_is_inventory(self):
        self.assertEqual(pc._infer_domain("MTL_SYSTEM_ITEMS_B"), "Inventory")

    def test_ego_prefix_is_catalog(self):
        self.assertEqual(pc._infer_domain("EGO_MTL_SY_ITEMS_EXT_B"), "Catalog")

    def test_gl_prefix_is_general_ledger(self):
        self.assertEqual(pc._infer_domain("GL_JE_LINES"), "General Ledger")

    def test_schema_qualified_table_stripped(self):
        # "APPS.MTL_…" or "INV.MTL_…" still resolves on the table portion.
        self.assertEqual(pc._infer_domain("APPS.MTL_SYSTEM_ITEMS_B"), "Inventory")

    def test_unknown_prefix_defaults_to_other(self):
        self.assertEqual(pc._infer_domain("XLA_AE_LINES"), "Other")

    def test_empty_or_none_returns_other(self):
        self.assertEqual(pc._infer_domain(""), "Other")
        self.assertEqual(pc._infer_domain(None), "Other")


class TestApplyMetadataDefaults(unittest.TestCase):
    """Auto-init keeps the API/UI contract loose: never returns an item with
    a missing metadata block, so filter dropdowns and downstream nested
    DynamoDB writes don't crash."""

    def _base(self):
        return {"extract": {"table": "MTL_SYSTEM_ITEMS_B"}}

    def test_seeds_all_default_keys(self):
        out = pc._apply_metadata_defaults(self._base())
        meta = out["metadata"]
        self.assertIn("registered_at", meta)
        self.assertEqual(meta["source_system"], "Oracle EBS")
        self.assertEqual(meta["domain"], "Inventory")
        self.assertEqual(meta["tags"], [])

    def test_preserves_explicit_values(self):
        item = {
            **self._base(),
            "metadata": {
                "registered_at": "2026-01-01T00:00:00",
                "source_system": "Salesforce",
                "domain": "CRM",
                "tags": ["PII", "production"],
            },
        }
        out = pc._apply_metadata_defaults(item)
        meta = out["metadata"]
        self.assertEqual(meta["registered_at"], "2026-01-01T00:00:00")
        self.assertEqual(meta["source_system"], "Salesforce")
        self.assertEqual(meta["domain"], "CRM")
        self.assertEqual(meta["tags"], ["PII", "production"])

    def test_partial_metadata_fills_missing_keys_only(self):
        item = {**self._base(), "metadata": {"tags": ["PII"]}}
        out = pc._apply_metadata_defaults(item)
        meta = out["metadata"]
        self.assertEqual(meta["tags"], ["PII"])
        self.assertEqual(meta["source_system"], "Oracle EBS")
        self.assertEqual(meta["domain"], "Inventory")

    def test_no_extract_block_yields_other_domain(self):
        out = pc._apply_metadata_defaults({})
        self.assertEqual(out["metadata"]["domain"], "Other")


class TestHomeMetricsHelpers(unittest.TestCase):
    def test_normalize_metrics_status_from_replication(self):
        self.assertEqual(
            pc._normalize_metrics_status({"replication_status": "replicated"}),
            "SUCCEEDED",
        )
        self.assertEqual(
            pc._normalize_metrics_status({"replication_status": "failed"}),
            "FAILED",
        )
        self.assertEqual(
            pc._normalize_metrics_status({"status": "SUCCEEDED"}),
            "SUCCEEDED",
        )

    def test_metrics_started_at_prefers_started_at(self):
        row = {"started_at": "2026-08-06T18:33:42Z", "start_time": "2026-08-06T18:00:00Z"}
        self.assertEqual(pc._metrics_started_at(row), "2026-08-06T18:33:42Z")

    def test_is_ingest_job_excludes_replicators(self):
        self.assertFalse(pc._is_ingest_job("echo-dev-gcs-replicator"))
        self.assertFalse(pc._is_ingest_job("echo-dev-silver-bq-replicator"))
        self.assertTrue(pc._is_ingest_job("echo-dev-bronze-ingest"))
        self.assertTrue(pc._is_ingest_job("echo-dev-silver-transform"))

    def test_metrics_in_window(self):
        floor = "2026-08-06T12:00:00+00:00"
        self.assertTrue(
            pc._metrics_in_window({"started_at": "2026-08-06T18:33:42+00:00"}, floor)
        )
        self.assertFalse(
            pc._metrics_in_window({"started_at": "2026-08-05T18:33:42+00:00"}, floor)
        )


if __name__ == "__main__":
    unittest.main()
