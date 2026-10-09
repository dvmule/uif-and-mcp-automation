import { describe, it, expect } from 'vitest';
import { detectSchemaType, JSON_BASED } from './schema-type';

/**
 * These cases are the branches of `detect_schema_type` in
 * `stacks/apicurio/src/app/services/schema_type_detector.py`, one test per
 * branch. The publish form shows this result as "AVRO detected" before
 * submission, so a disagreement with the Python is a lie on screen — not just
 * a wrong label.
 */
describe('detectSchemaType mirrors the BFF detector', () => {
  it('reads a leading angle bracket as XSD', () => {
    expect(detectSchemaType('<?xml version="1.0"?><xs:schema/>')).toBe('XSD');
  });

  it('reads a proto3 syntax line as PROTOBUF, in either quote style', () => {
    expect(detectSchemaType('syntax = "proto3";\nmessage X {}')).toBe('PROTOBUF');
    expect(detectSchemaType("syntax = 'proto3';\nmessage X {}")).toBe('PROTOBUF');
  });

  it('reads an openapi or swagger key as OPENAPI', () => {
    expect(detectSchemaType('{"openapi":"3.1.0","paths":{}}')).toBe('OPENAPI');
    expect(detectSchemaType('{"swagger":"2.0","paths":{}}')).toBe('OPENAPI');
  });

  it('reads an asyncapi key as ASYNCAPI', () => {
    expect(detectSchemaType('{"asyncapi":"3.0.0","channels":{}}')).toBe('ASYNCAPI');
  });

  it('requires both type:record AND fields for AVRO', () => {
    expect(detectSchemaType('{"type":"record","name":"X","fields":[]}')).toBe('AVRO');
    // `type: record` alone is not Avro to the detector, so it must not be here.
    expect(detectSchemaType('{"type":"record","name":"X"}')).toBe('JSON');
  });

  it('reads a $schema key as JSON', () => {
    expect(detectSchemaType('{"$schema":"https://json-schema.org/draft/2020-12/schema"}')).toBe(
      'JSON',
    );
  });

  it('falls back to JSON for a JSON-shaped body it cannot parse', () => {
    // The detector does not reject it — the form's JSON gate does.
    expect(detectSchemaType('{not json')).toBe('JSON');
  });

  it('reads an array body as JSON', () => {
    expect(detectSchemaType('[{"a":1}]')).toBe('JSON');
  });

  it('reads GraphQL keywords at the start or on any line', () => {
    expect(detectSchemaType('type Query { id: ID }')).toBe('GRAPHQL');
    expect(detectSchemaType('# a comment\nschema {\n  query: Query\n}')).toBe('GRAPHQL');
    expect(detectSchemaType('scalar DateTime')).toBe('GRAPHQL');
  });

  it('defaults to JSON for anything else', () => {
    expect(detectSchemaType('just some prose')).toBe('JSON');
    expect(detectSchemaType('')).toBe('JSON');
  });

  // Order matters: a JSON-shaped body is JSON before it is anything else, and
  // GraphQL is only reached once the XSD, Protobuf and JSON shapes are ruled
  // out. A body containing "type " inside JSON must not read as GraphQL.
  it('does not mistake a JSON body containing a GraphQL keyword for GraphQL', () => {
    expect(detectSchemaType('{"description":"type Query { id: ID }"}')).toBe('JSON');
  });
});

describe('JSON_BASED', () => {
  it('is the set the registry writes a metadata block into', () => {
    // Mirrors `_JSON_BASED` in metadata_injector.py, minus KCONNECT which the
    // form does not offer. These four get a block; the other three do not, and
    // land non-compliant — which is what the form's override warning says.
    expect([...JSON_BASED].sort()).toEqual(['ASYNCAPI', 'AVRO', 'JSON', 'OPENAPI']);
    for (const t of ['PROTOBUF', 'GRAPHQL', 'XSD']) {
      expect(JSON_BASED.has(t)).toBe(false);
    }
  });
});
