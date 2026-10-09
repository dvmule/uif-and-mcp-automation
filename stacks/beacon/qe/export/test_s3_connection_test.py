# test_s3_connection_test.py (UIFPE-440)
# The deliver Lambda's s3 connection test: write-then-delete a marker under
# the destination prefix, under the DELIVER role (the identity partners grant
# in their bucket policy). Failures are readable text; a write that never
# succeeded leaves nothing behind.

import unittest

from beacon_export.lambdas import deliver


class FakeS3:
    def __init__(self, put_error=None, delete_error=None):
        self.put_error, self.delete_error = put_error, delete_error
        self.puts, self.deletes = [], []

    def put_object(self, Bucket, Key, Body):
        if self.put_error:
            raise self.put_error
        self.puts.append((Bucket, Key))

    def delete_object(self, Bucket, Key):
        if self.delete_error:
            raise self.delete_error
        self.deletes.append((Bucket, Key))


def _test(prefix, s3):
    return deliver.handler(
        {"action": "connection_test", "channel": "s3",
         "destination_s3_prefix": prefix},
        s3_client=s3)


class TestS3ConnectionTest(unittest.TestCase):
    def test_success_writes_then_removes_the_marker(self):
        s3 = FakeS3()
        result = _test("s3://partner-bucket/inbound", s3)
        self.assertEqual(result, {"ok": True})
        marker = ("partner-bucket", "inbound/.beacon-connection-test")
        self.assertEqual(s3.puts, [marker])
        self.assertEqual(s3.deletes, [marker])

    def test_trailing_slash_never_doubles(self):
        s3 = FakeS3()
        _test("s3://partner-bucket/inbound/", s3)
        self.assertEqual(s3.puts[0][1], "inbound/.beacon-connection-test")

    def test_denied_write_reports_the_partner_gate_and_leaves_nothing(self):
        s3 = FakeS3(put_error=Exception(
            "An error occurred (AccessDenied) when calling PutObject"))
        result = _test("s3://partner-bucket/inbound", s3)
        self.assertFalse(result["ok"])
        self.assertIn("partner-bucket", result["error"])
        self.assertIn("partner's bucket policy", result["error"])
        self.assertEqual(s3.puts, [])      # nothing written
        self.assertEqual(s3.deletes, [])   # nothing to clean up

    def test_denied_delete_names_the_marker_and_the_missing_action(self):
        s3 = FakeS3(delete_error=Exception(
            "An error occurred (AccessDenied) when calling DeleteObject"))
        result = _test("s3://partner-bucket/inbound", s3)
        self.assertFalse(result["ok"])
        self.assertIn(".beacon-connection-test", result["error"])
        self.assertIn("s3:DeleteObject", result["error"])
        self.assertEqual(len(s3.puts), 1)  # honest: the marker WAS written

    def test_kms_failure_gets_the_unsupported_hint(self):
        s3 = FakeS3(put_error=Exception(
            "An error occurred (KMS.NotFoundException): key not found"))
        result = _test("s3://partner-bucket/inbound", s3)
        self.assertFalse(result["ok"])
        self.assertIn("KMS", result["error"])
        self.assertIn("not supported", result["error"])

    def test_sftp_connection_test_still_refused(self):
        # UIFPE-438: the in-Lambda sftp tester stays retired
        with self.assertRaises(NotImplementedError):
            deliver.handler({"action": "connection_test", "channel": "sftp"},
                            s3_client=FakeS3())
