# test_s3_delivery.py
# Unit tests for beacon/s3_delivery.py — copy planning (pure) and the
# adapter against a fake S3 client. Mirrors test_beacon_delivery.py's role
# for the SFTP channel.

import unittest

from beacon_export import s3_delivery

PICKUP = "s3://data-bucket/pickup/product-export/b-001/"
DEST = "s3://partner-bucket/inbound/product"

FILES = ["product_export_20260807_b-001.csv",
         "product_export_20260807_b-001.json",
         "product_export_20260807_b-001.manifest.json"]


class TestPlan(unittest.TestCase):
    def setUp(self):
        self.plan = s3_delivery.plan_delivery(
            FILES, pickup_s3_prefix=PICKUP, destination_s3_prefix=DEST)

    def test_manifest_last_even_if_listed_first(self):
        shuffled = [FILES[2], FILES[0], FILES[1]]
        plan = s3_delivery.plan_delivery(
            shuffled, pickup_s3_prefix=PICKUP, destination_s3_prefix=DEST)
        self.assertTrue(plan[-1].is_manifest)
        self.assertFalse(any(s.is_manifest for s in plan[:-1]))

    def test_keys_join_cleanly(self):
        step = self.plan[0]
        self.assertEqual(step.source_bucket, "data-bucket")
        self.assertEqual(step.source_key,
                         "pickup/product-export/b-001/" + FILES[0])
        self.assertEqual(step.dest_bucket, "partner-bucket")
        self.assertEqual(step.dest_key, "inbound/product/" + FILES[0])

    def test_plan_covers_every_file(self):
        self.assertEqual({s.file_name for s in self.plan}, set(FILES))

    def test_non_s3_uri_rejected(self):
        with self.assertRaises(ValueError):
            s3_delivery.plan_delivery(FILES, pickup_s3_prefix="/local/path",
                                      destination_s3_prefix=DEST)


class FakeS3:
    """Copy/head fake; objects: 'bucket/key' -> size."""

    def __init__(self, objects):
        self.objects = dict(objects)
        self.copies = []

    def copy_object(self, Bucket, Key, CopySource):
        src = f"{CopySource['Bucket']}/{CopySource['Key']}"
        self.objects[f"{Bucket}/{Key}"] = self.objects[src]
        self.copies.append((src, f"{Bucket}/{Key}"))

    def head_object(self, Bucket, Key):
        return {"ContentLength": self.objects[f"{Bucket}/{Key}"]}


class TestAdapter(unittest.TestCase):
    def setUp(self):
        self.s3 = FakeS3({
            f"data-bucket/pickup/product-export/b-001/{n}": 100 + i
            for i, n in enumerate(FILES)})
        self.plan = s3_delivery.plan_delivery(
            FILES, pickup_s3_prefix=PICKUP, destination_s3_prefix=DEST)

    def test_execute_copies_all_and_verifies(self):
        results = s3_delivery.S3Adapter(self.s3).execute(self.plan)
        self.assertEqual(len(results), 3)
        self.assertTrue(all(r["ok"] for r in results))
        # manifest copy happened last
        self.assertTrue(self.s3.copies[-1][0].endswith(".manifest.json"))
        # destination objects exist with source sizes
        self.assertEqual(self.s3.objects["partner-bucket/inbound/product/" + FILES[0]], 100)

    def test_redelivery_overwrites_idempotently(self):
        adapter = s3_delivery.S3Adapter(self.s3)
        adapter.execute(self.plan)
        adapter.execute(self.plan)  # retry: same keys, no duplicates
        dest_keys = [k for k in self.s3.objects if k.startswith("partner-bucket/")]
        self.assertEqual(len(dest_keys), 3)

    def test_size_mismatch_raises(self):
        class LyingS3(FakeS3):
            def head_object(self, Bucket, Key):
                size = self.objects[f"{Bucket}/{Key}"]
                return {"ContentLength": size if Bucket == "data-bucket" else size + 1}

        s3 = LyingS3(self.s3.objects)
        with self.assertRaises(IOError):
            s3_delivery.S3Adapter(s3).execute(self.plan)
