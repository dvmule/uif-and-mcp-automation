# test_register_gold_pipeline.py
# Unit tests for scripts/register_gold_pipeline.py covering finding #16:
#   - the "already registered?" existence check must happen BEFORE any S3
#     upload (uploading first risked overwriting live artifacts for a
#     gold_id that turns out to already be managed elsewhere).
#   - the DynamoDB put_item must be conditional
#     (attribute_not_exists(gold_id)) so two concurrent registrations can't
#     clobber each other's just-initialized watermark, and a
#     ConditionalCheckFailedException must surface as a clean [FAIL]
#     message rather than a raw boto3 traceback.
#
# boto3 itself is mocked throughout -- these tests make no real AWS calls.

import sys
from unittest import mock

import pytest
from botocore.exceptions import ClientError

import register_gold_pipeline as rgp


def _write_fixture_dirs(tmp_path):
    schema_dir = tmp_path / "schema"
    schema_dir.mkdir()
    (schema_dir / "master.json").write_text("{}")

    mapping_dir = tmp_path / "mapping"
    mapping_dir.mkdir()
    (mapping_dir / "manifest.yaml").write_text(
        "version: v1\n"
        "sql:\n"
        "  file: sql/test.sql\n"
        "sources:\n"
        "  - alias: items\n"
        "    silver_table: silver_test.items\n"
        "    is_source_of_truth: true\n"
        "target:\n"
        "  primary_key_cols: [id]\n"
    )
    return schema_dir, mapping_dir


def _argv(schema_dir, mapping_dir):
    return [
        "register_gold_pipeline.py",
        "--gold-id", "testgold",
        "--domain", "test",
        "--schema-dir", str(schema_dir),
        "--mapping-dir", str(mapping_dir),
        "--gold-database", "gold_test",
        "--gold-table", "test",
        "--owner", "me@example.com",
        "--env", "dev",
    ]


def _fake_boto3(dynamo_table, s3_client):
    fake = mock.MagicMock()
    fake.resource.return_value.Table.return_value = dynamo_table
    fake.client.return_value = s3_client
    return fake


def test_existence_check_happens_before_any_upload(tmp_path, monkeypatch):
    schema_dir, mapping_dir = _write_fixture_dirs(tmp_path)
    monkeypatch.setattr(sys, "argv", _argv(schema_dir, mapping_dir))

    calls = []
    dynamo_table = mock.MagicMock()
    dynamo_table.get_item.side_effect = (
        lambda **kw: calls.append("get_item") or {"Item": {"gold_id": "testgold"}})
    s3_client = mock.MagicMock()
    s3_client.upload_file.side_effect = lambda *a, **kw: calls.append("upload_file")

    monkeypatch.setattr(rgp, "boto3", _fake_boto3(dynamo_table, s3_client))

    with pytest.raises(SystemExit) as exc_info:
        rgp.main()

    assert "already exists" in str(exc_info.value)
    # No upload was ever attempted -- the existence check ran first and
    # short-circuited before any S3 write.
    assert calls == ["get_item"]
    s3_client.upload_file.assert_not_called()
    dynamo_table.put_item.assert_not_called()


def test_new_registration_uploads_then_conditional_put(tmp_path, monkeypatch):
    schema_dir, mapping_dir = _write_fixture_dirs(tmp_path)
    monkeypatch.setattr(sys, "argv", _argv(schema_dir, mapping_dir))

    calls = []
    dynamo_table = mock.MagicMock()
    dynamo_table.get_item.side_effect = lambda **kw: calls.append("get_item") or {}
    dynamo_table.put_item.side_effect = (
        lambda **kw: calls.append(("put_item", kw)))
    s3_client = mock.MagicMock()
    s3_client.upload_file.side_effect = lambda *a, **kw: calls.append("upload_file")

    monkeypatch.setattr(rgp, "boto3", _fake_boto3(dynamo_table, s3_client))

    rgp.main()

    # Order: existence check, then uploads, then the conditional write.
    assert calls[0] == "get_item"
    upload_positions = [i for i, c in enumerate(calls) if c == "upload_file"]
    put_positions = [i for i, c in enumerate(calls)
                      if isinstance(c, tuple) and c[0] == "put_item"]
    assert upload_positions and put_positions
    assert max(upload_positions) < min(put_positions)

    _, put_kwargs = next(c for c in calls if isinstance(c, tuple))
    assert put_kwargs["ConditionExpression"] == "attribute_not_exists(gold_id)"


def test_conditional_check_failure_gives_clean_message(tmp_path, monkeypatch):
    schema_dir, mapping_dir = _write_fixture_dirs(tmp_path)
    monkeypatch.setattr(sys, "argv", _argv(schema_dir, mapping_dir))

    dynamo_table = mock.MagicMock()
    dynamo_table.get_item.return_value = {}

    def _raise(**kw):
        raise ClientError(
            {"Error": {"Code": "ConditionalCheckFailedException", "Message": "x"}},
            "PutItem")
    dynamo_table.put_item.side_effect = _raise
    s3_client = mock.MagicMock()

    monkeypatch.setattr(rgp, "boto3", _fake_boto3(dynamo_table, s3_client))

    with pytest.raises(SystemExit) as exc_info:
        rgp.main()
    assert "concurrently" in str(exc_info.value)


def test_other_client_error_propagates_uncaught(tmp_path, monkeypatch):
    # Only ConditionalCheckFailedException gets the clean [FAIL] message --
    # any other DynamoDB error must NOT be swallowed as if it were a
    # duplicate registration.
    schema_dir, mapping_dir = _write_fixture_dirs(tmp_path)
    monkeypatch.setattr(sys, "argv", _argv(schema_dir, mapping_dir))

    dynamo_table = mock.MagicMock()
    dynamo_table.get_item.return_value = {}

    def _raise(**kw):
        raise ClientError(
            {"Error": {"Code": "ProvisionedThroughputExceededException", "Message": "x"}},
            "PutItem")
    dynamo_table.put_item.side_effect = _raise
    s3_client = mock.MagicMock()

    monkeypatch.setattr(rgp, "boto3", _fake_boto3(dynamo_table, s3_client))

    with pytest.raises(ClientError):
        rgp.main()


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
