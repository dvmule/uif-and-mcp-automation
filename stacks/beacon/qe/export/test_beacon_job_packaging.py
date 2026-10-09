# test_beacon_job_packaging.py
# Step-4 (package) behavior of beacon_export_job.run, driven end-to-end with
# fake spark/S3 clients — no Spark, no AWS. Pins the UIFPE-172 contract at the
# ENGINE level: zip.enabled wraps the rendered file and the manifest/delivery
# names describe the archive; a missing key is byte-for-byte today's
# uncompressed behavior (the D24 immutability promise).

import hashlib
import io
import json
import unittest
import zipfile
from types import SimpleNamespace
from unittest.mock import patch

import dynamic_mapping
from beacon_export import beacon_export_job

CSV_BYTES = b"ppid,isbn13\r\nA1,9781234567890\r\n"


class FakeS3:
    def __init__(self):
        self.objects = {}

    def get_object(self, Bucket, Key):
        return {"Body": io.BytesIO(self.objects[(Bucket, Key)])}

    def put_object(self, Bucket, Key, Body, **kw):
        self.objects[(Bucket, Key)] = Body if isinstance(Body, bytes) else Body.read()

    def copy_object(self, Bucket, Key, CopySource):
        self.objects[(Bucket, Key)] = self.objects[
            (CopySource["Bucket"], CopySource["Key"])]

    def list_objects_v2(self, Bucket, Prefix):
        return {"Contents": [{"Key": k, "Size": len(v)}
                             for (b, k), v in self.objects.items()
                             if b == Bucket and k.startswith(Prefix)]}


class FakeWriter:
    def __init__(self, s3):
        self.s3 = s3

    def mode(self, *_):
        return self

    def options(self, **_):
        return self

    def format(self, *_):
        return self

    def save(self, prefix):
        bucket, _, key = prefix.removeprefix("s3://").partition("/")
        self.s3.objects[(bucket, key.rstrip("/") + "/part-00000.csv")] = CSV_BYTES


class FakeDF:
    columns = ["ppid", "isbn13"]
    # the render stage inspects dtypes for the complex-type guard (UIFPE-237)
    dtypes = [("ppid", "string"), ("isbn13", "string")]

    def __init__(self, s3):
        self._s3 = s3

    def coalesce(self, *_):
        return self

    @property
    def write(self):
        return FakeWriter(self._s3)


class FakeSpark:
    def __init__(self, s3):
        self.read = SimpleNamespace(parquet=lambda prefix: FakeDF(s3))


def make_cfg(zip_block=None):
    packaging = {
        "filename_pattern": "job_{yyyyMMdd}_{batch_id}.{ext}",
        "pickup_s3_prefix": "s3://bkt/pickup/{batch_id}/",
        "manifest": {"enabled": True, "checksum": "sha256"},
    }
    if zip_block is not None:
        packaging["zip"] = zip_block
    return {
        "version": "1.0",
        "job_id": "job",
        "mapping_config_s3_path": "s3://bkt/configs/mapping.json",
        "canonical_s3_prefix": "s3://bkt/staging/{batch_id}/canonical/",
        "quality_gates": {"min_rows": 1, "max_rejection_pct": 2.0},
        # formula_escape off keeps the fake DF pyspark-free
        "renderings": [{"format": "csv",
                        "options": {"delimiter": ",", "formula_escape": False}}],
        "packaging": packaging,
        "delivery": {"channel": "s3", "adapter": "s3_copy",
                     "destination_s3_prefix": "s3://partner/in"},
    }


class TestJobPackaging(unittest.TestCase):
    def _run(self, cfg):
        s3 = FakeS3()
        s3.objects[("bkt", "configs/mapping.json")] = b"{}"
        report = SimpleNamespace(to_dict=lambda: {"output_rows": 1})
        gate = SimpleNamespace(passed=True, to_dict=lambda: {"passed": True})
        with patch.object(dynamic_mapping, "load_config", lambda path: {}), \
             patch.object(dynamic_mapping, "run", lambda *a, **k: report), \
             patch.object(beacon_export_job.quality_gate, "evaluate",
                          lambda *a, **k: gate):
            summary = beacon_export_job.run(
                FakeSpark(s3), s3, None, cfg, "b-1", deliver=False)
        pickup = {k: v for (b, k), v in s3.objects.items()
                  if b == "bkt" and k.startswith("pickup/b-1/")}
        return summary, pickup

    def test_zip_enabled_wraps_file_and_manifest_describes_the_zip(self):
        summary, pickup = self._run(make_cfg(zip_block={"enabled": True}))
        self.assertEqual(summary["status"], "succeeded")

        zips = {k: v for k, v in pickup.items() if k.endswith(".csv.zip")}
        self.assertEqual(len(zips), 1, f"expected one archive, got {sorted(pickup)}")
        (zip_key, zip_bytes), = zips.items()
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
            inner, = archive.namelist()
            # the inner file carries the name the partner would have received
            # uncompressed — the archive name is that plus .zip
            self.assertEqual(zip_key.rsplit("/", 1)[1], inner + ".zip")
            self.assertEqual(archive.read(inner), CSV_BYTES)

        data_entry, manifest_entry = summary["packaged_files"]
        self.assertTrue(data_entry["name"].endswith(".csv.zip"))
        self.assertEqual(data_entry["bytes"], len(zip_bytes))
        self.assertEqual(data_entry["sha256"],
                         hashlib.sha256(zip_bytes).hexdigest())
        self.assertTrue(manifest_entry["name"].endswith(".manifest.json"))
        manifest = json.loads(
            pickup["pickup/b-1/" + manifest_entry["name"]])
        self.assertEqual(manifest["files"][0]["name"], data_entry["name"])
        # no uncompressed data file leaks into pickup alongside the zip
        self.assertFalse([k for k in pickup if k.endswith(".csv")])

    def test_missing_zip_key_is_todays_uncompressed_behavior(self):
        summary, pickup = self._run(make_cfg(zip_block=None))
        self.assertEqual(summary["status"], "succeeded")
        data_entry = summary["packaged_files"][0]
        self.assertTrue(data_entry["name"].endswith(".csv"))
        self.assertEqual(data_entry["bytes"], len(CSV_BYTES))
        self.assertEqual(data_entry["sha256"],
                         hashlib.sha256(CSV_BYTES).hexdigest())
        self.assertEqual(pickup["pickup/b-1/" + data_entry["name"]], CSV_BYTES)
        self.assertFalse([k for k in pickup if k.endswith(".zip")])

    def test_zip_disabled_matches_missing_key(self):
        off, pickup_off = self._run(make_cfg(zip_block={"enabled": False}))
        missing, pickup_missing = self._run(make_cfg(zip_block=None))
        self.assertEqual(
            [f["name"] for f in off["packaged_files"]],
            [f["name"] for f in missing["packaged_files"]])
        self.assertEqual(sorted(pickup_off), sorted(pickup_missing))
