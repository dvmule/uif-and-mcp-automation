# Unit tests for Iceberg metadata pointer resolution in silver_bq_replicator.

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "lambda"))

for _mod in ("fastavro", "google", "google.cloud", "google.oauth2", "google.oauth2.service_account"):
    sys.modules.setdefault(_mod, MagicMock())

import boto3  # noqa: E402

_real_resource = boto3.resource
_real_client = boto3.client
boto3.resource = MagicMock(return_value=MagicMock())
boto3.client = MagicMock(return_value=MagicMock())

import silver_bq_replicator as sbr  # noqa: E402

boto3.resource = _real_resource
boto3.client = _real_client


class TestMetadataJsonVersionFromKey(unittest.TestCase):
    def test_glue_catalog_sequence_format(self):
        key = "product/mtl_system_items_b/metadata/00001-bc39b8f8-37fd-44c9-a630-bede6548071f.metadata.json"
        self.assertEqual(sbr._metadata_json_version_from_key(key), 1)

    def test_glue_catalog_initial_snapshot(self):
        key = "product/mtl_system_items_b/metadata/00000-e8417708-ba60-4bc9-872b-a6971350d43f.metadata.json"
        self.assertEqual(sbr._metadata_json_version_from_key(key), 0)

    def test_hadoop_catalog_v_format(self):
        key = "product/t/metadata/v10.metadata.json"
        self.assertEqual(sbr._metadata_json_version_from_key(key), 10)

    def test_v10_ranks_above_v9(self):
        keys = [
            "product/t/metadata/v9.metadata.json",
            "product/t/metadata/v10.metadata.json",
            "product/t/metadata/00001-uuid.metadata.json",
        ]
        self.assertEqual(max(keys, key=sbr._metadata_json_version_from_key), keys[1])


if __name__ == "__main__":
    unittest.main()
