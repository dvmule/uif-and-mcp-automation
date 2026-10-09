/**
 * Client-side mirror of `detect_schema_type` in
 * `stacks/apicurio/src/app/services/schema_type_detector.py`.
 *
 * The publish form shows the detected type before you submit — "AVRO
 * detected" — and that label is only useful if it is the type the registry
 * will actually pick. So this is a deliberate port, branch for branch, not an
 * independent guess. If the Python changes, change this with it; the order of
 * the checks is load-bearing (a `{` body is JSON-shaped before it is anything
 * else, and GraphQL is only reached once the JSON and Protobuf shapes are
 * ruled out).
 *
 * Keeping it client-side rather than asking the BFF is what makes the badge
 * live: it has to update as the body is typed or a file is dropped in, and a
 * request per keystroke to learn a string we can derive is not worth it.
 */

export const SCHEMA_TYPES = [
  'AVRO',
  'JSON',
  'OPENAPI',
  'ASYNCAPI',
  'PROTOBUF',
  'GRAPHQL',
  'XSD',
] as const;

export type SchemaType = (typeof SCHEMA_TYPES)[number];

/** The types the registry writes a metadata block into. Mirrors `_JSON_BASED`
  * in `metadata_injector.py` — minus KCONNECT, which the form does not offer. */
export const JSON_BASED = new Set<string>(['AVRO', 'JSON', 'OPENAPI', 'ASYNCAPI']);

const GRAPHQL_KEYWORDS = ['type ', 'schema {', 'interface ', 'enum ', 'union ', 'scalar '];

export function detectSchemaType(content: string): SchemaType {
  const stripped = content.trim();

  if (stripped.startsWith('<')) return 'XSD';

  if (stripped.startsWith('syntax = "proto') || stripped.startsWith("syntax = 'proto")) {
    return 'PROTOBUF';
  }

  if (stripped.startsWith('{') || stripped.startsWith('[')) {
    let parsed: unknown;
    try {
      parsed = JSON.parse(content);
    } catch {
      // Unparseable but JSON-shaped is still JSON as far as the registry is
      // concerned — it is the JSON gate, not the detector, that rejects it.
      return 'JSON';
    }

    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
      const obj = parsed as Record<string, unknown>;
      if ('openapi' in obj || 'swagger' in obj) return 'OPENAPI';
      if ('asyncapi' in obj) return 'ASYNCAPI';
      if (obj.type === 'record' && 'fields' in obj) return 'AVRO';
      if ('$schema' in obj) return 'JSON';
    }
    return 'JSON';
  }

  if (GRAPHQL_KEYWORDS.some((kw) => stripped.startsWith(kw) || stripped.includes(`\n${kw}`))) {
    return 'GRAPHQL';
  }

  return 'JSON';
}
