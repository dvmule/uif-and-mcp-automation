import { describe, it, expect } from 'vitest';
import {
  formatSubject,
  getMonacoLanguage,
  resolveCurrentVersion,
  schemaFile,
} from './utils';

describe('formatSubject', () => {
  it('strips -value suffix when showRaw is false', () => {
    expect(formatSubject('commerce.product.created-value', false)).toBe('commerce.product.created');
  });

  it('strips -key suffix when showRaw is false', () => {
    expect(formatSubject('identity.user-key', false)).toBe('identity.user');
  });

  it('returns original string when showRaw is true', () => {
    expect(formatSubject('commerce.product.created-value', true)).toBe('commerce.product.created-value');
  });

  it('does not modify subjects with no recognised suffix', () => {
    expect(formatSubject('analytics.pageview', false)).toBe('analytics.pageview');
  });

  it('does not strip -value in the middle of the string', () => {
    // Only trailing suffix should be stripped
    expect(formatSubject('commerce.value-schema-value', false)).toBe('commerce.value-schema');
  });

  it('handles empty string without throwing', () => {
    expect(formatSubject('', false)).toBe('');
    expect(formatSubject('', true)).toBe('');
  });

  it('handles schema with v2 suffix pattern', () => {
    expect(formatSubject('commerce.product.created.v2-value', false)).toBe('commerce.product.created.v2');
  });
});

describe('getMonacoLanguage', () => {
  it('maps the non-JSON registry types to their own languages', () => {
    expect(getMonacoLanguage('PROTOBUF', 'syntax = "proto3";')).toBe('plaintext');
    expect(getMonacoLanguage('XSD', '<xs:schema/>')).toBe('xml');
    expect(getMonacoLanguage('GRAPHQL', 'type Query { a: Int }')).toBe('graphql');
  });

  it('reads the body to tell a JSON OpenAPI document from a YAML one', () => {
    expect(getMonacoLanguage('OPENAPI', '{"openapi":"3.1.0"}')).toBe('json');
    expect(getMonacoLanguage('OPENAPI', 'openapi: 3.1.0')).toBe('yaml');
  });

  it('falls back to json for AVRO and for an unknown type', () => {
    expect(getMonacoLanguage('AVRO', '{}')).toBe('json');
    expect(getMonacoLanguage('', '{}')).toBe('json');
  });
});

describe('schemaFile — the download name and media type', () => {
  // The extension has to follow the schema type, not the transport. The graph
  // toolbar used to hardcode `.json` + application/json for every type, so a
  // Protobuf body downloaded as `orders.json` and a WSDL as `contract.json` —
  // files nothing downstream will open.
  it('gives Avro its own .avsc extension', () => {
    expect(schemaFile('AVRO', '{}', 'commerce.order.placed-value', '2')).toEqual({
      name: 'commerce.order.placed-value.v2.avsc',
      mimeType: 'application/json',
    });
  });

  it('names Protobuf, GraphQL, XSD and WSDL bodies for what they are', () => {
    expect(schemaFile('PROTOBUF', 'syntax = "proto3";', 'a-value', '1').name).toBe('a-value.v1.proto');
    expect(schemaFile('GRAPHQL', 'type Q { id: ID }', 'a-value', '1').name).toBe('a-value.v1.graphql');
    expect(schemaFile('XSD', '<xs:schema/>', 'a-value', '1').name).toBe('a-value.v1.xsd');
    expect(schemaFile('WSDL', '<definitions/>', 'a-value', '1').name).toBe('a-value.v1.wsdl');
  });

  it('sends XML types with an XML media type and text types with text/plain', () => {
    expect(schemaFile('XSD', '<x/>', 'a', '1').mimeType).toBe('application/xml');
    expect(schemaFile('WSDL', '<x/>', 'a', '1').mimeType).toBe('application/xml');
    expect(schemaFile('PROTOBUF', 'syntax', 'a', '1').mimeType).toBe('text/plain');
    expect(schemaFile('GRAPHQL', 'type Q', 'a', '1').mimeType).toBe('text/plain');
  });

  // OPENAPI and ASYNCAPI are carried in either JSON or YAML, so the body
  // decides — the same rule getMonacoLanguage uses to pick a highlighter.
  it('lets the body decide for OpenAPI and AsyncAPI', () => {
    expect(schemaFile('OPENAPI', '{"openapi":"3.1.0"}', 'a', '1')).toEqual({
      name: 'a.v1.json',
      mimeType: 'application/json',
    });
    expect(schemaFile('ASYNCAPI', 'asyncapi: 3.0.0\n', 'a', '1')).toEqual({
      name: 'a.v1.yaml',
      mimeType: 'application/yaml',
    });
  });

  it('defaults to JSON for an unknown type', () => {
    expect(schemaFile('SOMETHING-NEW', '{}', 'a', '1').name).toBe('a.v1.json');
  });

  // A directory of files that each came from a different version, all named
  // the same, is unusable — and nothing inside the file says which it is.
  it('carries the version in the name', () => {
    expect(schemaFile('AVRO', '{}', 'a-value', '7').name).toBe('a-value.v7.avsc');
  });

  it('omits the version part when there is no version to name', () => {
    expect(schemaFile('AVRO', '{}', 'a-value', null).name).toBe('a-value.avsc');
    expect(schemaFile('AVRO', '{}', 'a-value').name).toBe('a-value.avsc');
  });

  // An unconventional subject is warned about on the publish form and allowed,
  // so a slash can reach here. In a download name it would become a directory.
  it('does not let a subject become a path', () => {
    expect(schemaFile('AVRO', '{}', 'weird/sub\\ject', '1').name).toBe('weird_sub_ject.v1.avsc');
  });
});

/**
 * This had no coverage at all, and it decides which version the whole page
 * claims to be showing: the selector, the facts band, the timeline's
 * "Current" tag and the download filename.
 *
 * Apicurio returns versions oldest-first, so every case below is written in
 * that order — a fix that only works on a reversed list would be no fix.
 */
describe('resolveCurrentVersion', () => {
  const dated = [
    { version: '1', created_on: '2026-01-01T00:00:00Z' },
    { version: '2', created_on: '2026-01-02T00:00:00Z' },
    { version: '3', created_on: '2026-01-03T00:00:00Z' },
  ];

  it('passes through a version that is really in the list', () => {
    expect(resolveCurrentVersion(dated, '2')).toBe('2');
  });

  it('resolves the "latest" sentinel to the newest by timestamp', () => {
    // The BFF echoes `content_version: "latest"` when the detail is fetched
    // without an explicit version, which is how the subject page fetches it.
    expect(resolveCurrentVersion(dated, 'latest')).toBe('3');
  });

  it('does not trust array position', () => {
    const shuffled = [dated[2], dated[0], dated[1]];
    expect(resolveCurrentVersion(shuffled, 'latest')).toBe('3');
  });

  it('falls back to the version number when no timestamp is set', () => {
    // `Date.parse(null ?? '')` is NaN, and a comparator returning NaN sorts
    // nothing — so this used to return '1', the oldest, and the page named v1
    // while rendering v3's body.
    const undated = [{ version: '1' }, { version: '2' }, { version: '3' }].map((v) => ({
      ...v,
      created_on: null,
    }));
    expect(resolveCurrentVersion(undated, 'latest')).toBe('3');
  });

  it('falls back on a double-digit version without sorting it as text', () => {
    const undated = ['1', '2', '10'].map((version) => ({ version, created_on: null }));
    expect(resolveCurrentVersion(undated, 'latest')).toBe('10');
  });

  it('one missing timestamp does not lose the list', () => {
    // The case that reaches production first: a single version whose
    // createdOn was absent from the registry response.
    const mixed = [{ version: '1', created_on: null }, dated[1], dated[2]];
    expect(resolveCurrentVersion(mixed, 'latest')).toBe('3');
  });

  it('prefers the higher version number even when only the lower is dated', () => {
    // This asserted the opposite when it was written — that a timestamp beats
    // a name. It does not beat a SEQUENCE: Apicurio numbers versions in
    // order, so v9 is later than v2 and v2's timestamp says nothing about v9.
    // The rule changed when `compareVersionAge` was factored out for the
    // diff, which needs the same answer.
    const mixed = [dated[1], { version: '9', created_on: null }];
    expect(resolveCurrentVersion(mixed, 'latest')).toBe('9');
  });

  it('sorts an unparseable version last rather than letting it win', () => {
    const odd = [{ version: 'draft', created_on: null }, { version: '2', created_on: null }];
    expect(resolveCurrentVersion(odd, 'latest')).toBe('2');
  });

  it('returns the candidate unchanged when there are no versions', () => {
    expect(resolveCurrentVersion([], 'latest')).toBe('latest');
  });
});
