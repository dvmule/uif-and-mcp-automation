"""Tests for schema_type_detector.detect_schema_type."""

import pytest

from app.services.schema_type_detector import detect_schema_type


@pytest.mark.parametrize(
    "content, expected",
    [
        # AVRO record
        ('{"type": "record", "name": "Foo", "fields": []}', "AVRO"),
        # OpenAPI
        ('{"openapi": "3.0.0", "info": {}}', "OPENAPI"),
        # Swagger 2
        ('{"swagger": "2.0"}', "OPENAPI"),
        # AsyncAPI
        ('{"asyncapi": "2.0.0"}', "ASYNCAPI"),
        # JSON Schema
        ('{"$schema": "http://json-schema.org/draft-07/schema#"}', "JSON"),
        # Plain JSON object
        ('{"foo": "bar"}', "JSON"),
        # JSON array
        ('[1, 2, 3]', "JSON"),
        # Protobuf
        ('syntax = "proto3";\nmessage Foo {}', "PROTOBUF"),
        ("syntax = 'proto2';\nmessage Bar {}", "PROTOBUF"),
        # GraphQL
        ("type Query { hello: String }", "GRAPHQL"),
        ("schema {\n  query: Query\n}", "GRAPHQL"),
        # XSD
        ("<xs:schema xmlns:xs='http://www.w3.org/2001/XMLSchema'/>", "XSD"),
        # Whitespace around valid JSON
        ('  {"type": "record", "fields": [], "name": "X"}  ', "AVRO"),
    ],
)
def test_detect(content: str, expected: str) -> None:
    assert detect_schema_type(content) == expected
