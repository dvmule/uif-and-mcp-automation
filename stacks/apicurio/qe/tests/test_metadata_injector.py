"""Tests for metadata_injector."""

import json

from app.clients.cmdb_client import CmdbCi
from app.schemas.service import SchemaRegistrationInput
from app.services.metadata_injector import build_apicurio_properties, inject_metadata

_CI = CmdbCi(
    ci_id="CI0093422",
    name="enrollment-svc",
    domain="learning",
    team_id="TM-LEARNING",
    team_email="learning@pearson.com",
    urn="urn:pearson:service:learning:enrollment-svc",
)

_REG = SchemaRegistrationInput(
    content="{}",
    data_classification="internal",
    pii_fields=["email"],
    lifecycle="active",
)


def test_inject_avro_adds_metadata_key():
    avro = json.dumps({"type": "record", "name": "Foo", "fields": []})
    result = inject_metadata(avro, "AVRO", _CI, _REG)
    parsed = json.loads(result)
    meta = parsed["metadata"]
    assert meta["owner"]["service"] == "CI0093422"
    assert meta["domain"] == "learning"
    assert meta["dataClassification"] == "internal"
    assert meta["piiFields"] == ["email"]
    assert meta["lifecycle"] == "active"


def test_inject_preserves_existing_schema_fields():
    avro = json.dumps(
        {"type": "record", "name": "Foo", "fields": [{"name": "id", "type": "string"}]}
    )
    result = inject_metadata(avro, "AVRO", _CI, _REG)
    parsed = json.loads(result)
    assert parsed["type"] == "record"
    assert parsed["fields"][0]["name"] == "id"


def test_inject_overwrites_existing_metadata():
    avro = json.dumps(
        {"type": "record", "name": "Foo", "fields": [], "metadata": {"domain": "old"}}
    )
    result = inject_metadata(avro, "AVRO", _CI, _REG)
    parsed = json.loads(result)
    assert parsed["metadata"]["domain"] == "learning"


def test_inject_non_json_type_returns_unchanged():
    proto = 'syntax = "proto3";\nmessage Foo {}'
    result = inject_metadata(proto, "PROTOBUF", _CI, _REG)
    assert result == proto


def test_inject_invalid_json_returns_unchanged():
    bad = "not json at all"
    result = inject_metadata(bad, "AVRO", _CI, _REG)
    assert result == bad


def test_build_apicurio_properties():
    props = build_apicurio_properties(_CI, _REG)
    assert props["owner.service"] == "CI0093422"
    assert props["domain"] == "learning"
    assert props["dataClassification"] == "internal"
    assert props["lifecycle"] == "active"
