import { describe, it, expect } from 'vitest';
import {
  parseSchemaBody,
  renderFieldType,
  buildSchemaGraph,
  splitHit,
  countFieldViewMatches,
  countGraphMatches,
  graphNodeMatches,
} from './schema-body';

const AVRO = JSON.stringify({
  type: 'record',
  name: 'PreferencesChanged',
  namespace: 'com.pearson.customer',
  fields: [
    { name: 'customer_id', type: 'string' },
    { name: 'email', type: 'string' },
    { name: 'locale', type: ['null', 'string'] },
  ],
  metadata: {
    owner: { service: 'CI0093432', team: 'TM-CUSTOMER-PLATFORM' },
    domain: 'customer',
    dataClassification: 'confidential',
    piiFields: ['customer_id', 'email'],
    lifecycle: 'active',
  },
});

/**
 * A JSON Schema body, which spells its fields as a `properties` MAP rather
 * than Avro's `fields` ARRAY, and its optionality as a type union rather than
 * a default. `product.gold-schema` in the live registry is this shape and
 * carries 63 of them, none of which the Fields view showed.
 *
 * Trimmed to the shapes that body actually uses: a plain type, three unions, a
 * `format`, an array with an object element, and a nested object.
 */
const JSON_SCHEMA = JSON.stringify({
  $schema: 'http://json-schema.org/draft-07/schema#',
  $id: 'https://schemas.pearson.com/gold/product/v1/master.json',
  title: 'Product',
  type: 'object',
  required: ['productId'],
  properties: {
    productId: { type: 'string', description: 'PPID' },
    isbn13: { type: ['string', 'null'] },
    listPrice: { type: ['number', 'null'] },
    digitalEffectiveStartDate: { type: ['string', 'null'], format: 'date' },
    contributors: { type: ['array', 'null'], items: { type: 'object' } },
    fulfillmentOrgDetails: { type: ['object', 'null'] },
  },
  metadata: {
    owner: { service: 'CI0093421', team: 'TM-PRODUCT-CATALOG' },
    domain: 'product',
    dataClassification: 'internal',
    piiFields: ['isbn13'],
    lifecycle: 'active',
  },
});

describe('parseSchemaBody', () => {
  it('reads the record header', () => {
    const body = parseSchemaBody(AVRO);
    expect(body.parsed).toBe(true);
    expect(body.record).toEqual({
      name: 'PreferencesChanged',
      kind: 'record',
      namespace: 'com.pearson.customer',
    });
  });

  it('reads the fields with their rendered types', () => {
    const { fields } = parseSchemaBody(AVRO);
    expect(fields.map((f) => f.name)).toEqual(['customer_id', 'email', 'locale']);
    expect(fields.map((f) => f.type)).toEqual(['string', 'string', 'null | string']);
  });

  it('marks a field PII only because the metadata block lists it', () => {
    const { fields } = parseSchemaBody(AVRO);
    expect(fields.filter((f) => f.pii).map((f) => f.name)).toEqual(['customer_id', 'email']);
  });

  it('gives every top-level field the container path, not an index', () => {
    // An index would imply the position means something to a consumer. In
    // Avro it does not — the field name is the contract.
    const { fields } = parseSchemaBody(AVRO);
    expect(new Set(fields.map((f) => f.path))).toEqual(new Set(['fields[]']));
  });

  it('flattens the metadata block to dotted keys', () => {
    const { metadata } = parseSchemaBody(AVRO);
    expect(metadata).toEqual([
      { key: 'owner.service', value: 'CI0093432' },
      { key: 'owner.team', value: 'TM-CUSTOMER-PLATFORM' },
      { key: 'domain', value: 'customer' },
      { key: 'dataClassification', value: 'confidential' },
      { key: 'piiFields', value: 'customer_id, email' },
      { key: 'lifecycle', value: 'active' },
    ]);
  });

  it('renders an unset metadata value as null rather than dropping the row', () => {
    // A missing row reads as "this schema has no such field". `null` reads as
    // "it has one and nothing is in it", which is what makes it non-compliant.
    const { metadata } = parseSchemaBody(
      JSON.stringify({ type: 'record', name: 'X', fields: [], metadata: { dataClassification: null } }),
    );
    expect(metadata).toEqual([{ key: 'dataClassification', value: 'null' }]);
  });

  it('reports a non-JSON body as unparsed rather than throwing', () => {
    // PROTOBUF, GRAPHQL and XSD bodies are legitimately not JSON.
    const body = parseSchemaBody('syntax = "proto3";\nmessage Order { string id = 1; }');
    expect(body.parsed).toBe(false);
    expect(body.fields).toEqual([]);
    expect(body.metadata).toEqual([]);
  });

  it('handles a body with no fields and no metadata block', () => {
    const body = parseSchemaBody('{"type":"enum","name":"Status","symbols":["A","B"]}');
    expect(body.parsed).toBe(true);
    expect(body.fields).toEqual([]);
    expect(body.metadata).toEqual([]);
  });
});

describe('parseSchemaBody, on a JSON Schema body', () => {
  // The registry carries JSON and OPENAPI artifacts as well as AVRO, and a
  // JSON Schema names its fields in `properties`. Reading only Avro's
  // `fields` left a 63-property body rendering as its metadata block alone.
  it('reads the properties as fields', () => {
    const { fields } = parseSchemaBody(JSON_SCHEMA);
    expect(fields.map((f) => f.name)).toEqual([
      'productId',
      'isbn13',
      'listPrice',
      'digitalEffectiveStartDate',
      'contributors',
      'fulfillmentOrgDetails',
    ]);
  });

  it('renders a JSON Schema type union, and does not flatten it to object', () => {
    const { fields } = parseSchemaBody(JSON_SCHEMA);
    expect(Object.fromEntries(fields.map((f) => [f.name, f.type]))).toEqual({
      productId: 'string',
      isbn13: 'string | null',
      listPrice: 'number | null',
      // `format` says what the value means, exactly as Avro's `logicalType`.
      digitalEffectiveStartDate: 'string | null (date)',
      contributors: 'array<object> | null',
      fulfillmentOrgDetails: 'object | null',
    });
  });

  it('names the container in the Path column as properties, not fields[]', () => {
    const { fields } = parseSchemaBody(JSON_SCHEMA);
    expect(new Set(fields.map((f) => f.path))).toEqual(new Set(['properties']));
  });

  it('marks PII from the same metadata block', () => {
    const { fields } = parseSchemaBody(JSON_SCHEMA);
    expect(fields.filter((f) => f.pii).map((f) => f.name)).toEqual(['isbn13']);
  });

  it('takes the header name from title, which is where a JSON Schema puts it', () => {
    const { record } = parseSchemaBody(JSON_SCHEMA);
    expect(record).toEqual({ name: 'Product', kind: 'object', namespace: null });
  });

  it('counts a search across JSON Schema fields like any other', () => {
    // `listPrice` appears in the field lane only. `isbn` would count twice —
    // once as the field and once inside the metadata block's `piiFields` —
    // which is correct behaviour and a poor assertion about this lane.
    expect(countFieldViewMatches(parseSchemaBody(JSON_SCHEMA), 'listPrice')).toBe(1);
    expect(countFieldViewMatches(parseSchemaBody(JSON_SCHEMA), 'isbn')).toBe(2);
  });

  it('prefers Avro fields when a body somehow carries both', () => {
    // `fields` is the Avro contract; a `properties` key alongside it is not a
    // second field list, so it must not double the table.
    const { fields } = parseSchemaBody(
      JSON.stringify({
        type: 'record',
        name: 'X',
        fields: [{ name: 'a', type: 'string' }],
        properties: { b: { type: 'string' } },
      }),
    );
    expect(fields.map((f) => f.name)).toEqual(['a']);
  });

  it('leaves a body with neither field list empty', () => {
    const { fields } = parseSchemaBody('{"openapi":"3.1.0","info":{"title":"Orders"}}');
    expect(fields).toEqual([]);
  });
});

describe('renderFieldType', () => {
  it('keeps a logical type visible', () => {
    // A long that is a timestamp is not interchangeable with a long that is a
    // count, so the logical type is not collapsed into the primitive.
    expect(renderFieldType({ type: 'long', logicalType: 'timestamp-millis' })).toBe(
      'long (timestamp-millis)',
    );
  });

  it('renders a union', () => {
    expect(renderFieldType(['null', 'string'])).toBe('null | string');
  });

  it('renders arrays and maps with their element type', () => {
    expect(renderFieldType({ type: 'array', items: 'string' })).toBe('array<string>');
    expect(renderFieldType({ type: 'map', values: 'long' })).toBe('map<long>');
  });

  it('renders a JSON Schema union, which spells itself inside the type key', () => {
    // Avro writes a union as a bare array; JSON Schema writes it as the value
    // of `type`. Read as an object, the second used to render as `object`.
    expect(renderFieldType({ type: ['string', 'null'] })).toBe('string | null');
    expect(renderFieldType({ type: ['array', 'null'], items: 'string' })).toBe(
      'array<string> | null',
    );
  });

  it("treats a format the way it treats Avro's logicalType", () => {
    expect(renderFieldType({ type: 'string', format: 'date-time' })).toBe('string (date-time)');
  });

  it('names a nested record and enum', () => {
    expect(renderFieldType({ type: 'record', name: 'Address' })).toBe('record Address');
    expect(renderFieldType({ type: 'enum', name: 'Status' })).toBe('enum Status');
  });
});

describe('buildSchemaGraph', () => {
  const { nodes, edges } = buildSchemaGraph(JSON.parse(AVRO));

  it('emits one node per object, plus the array container', () => {
    // The seven the design names: the record root, `fields[]`, its three field
    // objects, `metadata` and `owner`. `piiFields` is an array of strings, so
    // it is a row on `metadata` rather than a node of its own.
    expect(nodes.map((n) => n.id)).toEqual([
      'root',
      'root.fields',
      'root.fields.0',
      'root.fields.1',
      'root.fields.2',
      'root.metadata',
      'root.metadata.owner',
    ]);
  });

  it('titles the array container with brackets and the leaves with field names', () => {
    expect(nodes.find((n) => n.id === 'root.fields')!.title).toBe('fields[]');
    expect(nodes.find((n) => n.id === 'root.fields.1')!.title).toBe('email');
  });

  it('anchors each edge on the row it leaves', () => {
    const rootRows = nodes.find((n) => n.id === 'root')!.rows.map((r) => r.key);
    const toFields = edges.find((e) => e.target === 'root.fields')!;
    // Not the node centre: the edge leaves the `fields` row specifically.
    expect(rootRows[toFields.sourceRow]).toBe('fields');

    const toMetadata = edges.find((e) => e.target === 'root.metadata')!;
    expect(rootRows[toMetadata.sourceRow]).toBe('metadata');
  });

  it('gives a branch row a summary value so the key is readable without following the edge', () => {
    const rootRows = nodes.find((n) => n.id === 'root')!.rows;
    expect(rootRows.find((r) => r.key === 'fields')!.value).toBe('[3]');
    expect(rootRows.find((r) => r.key === 'metadata')!.value).toBe('{…}');
  });

  it('keeps a scalar array as a row rather than promoting it to a node', () => {
    const metaRows = nodes.find((n) => n.id === 'root.metadata')!.rows;
    expect(metaRows.find((r) => r.key === 'piiFields')!.value).toBe('[customer_id, email]');
  });

  it('returns nothing for a body that is not an object', () => {
    expect(buildSchemaGraph('a string')).toEqual({ nodes: [], edges: [] });
    expect(buildSchemaGraph(null)).toEqual({ nodes: [], edges: [] });
  });
});

describe('splitHit', () => {
  it('splits around the first occurrence, case-insensitively', () => {
    expect(splitHit('buyer_email', 'EMAIL')).toEqual({
      pre: 'buyer_',
      match: 'email',
      post: '',
      hit: true,
    });
  });

  it('preserves the original casing of the matched run', () => {
    expect(splitHit('TM-COMMERCE', 'commerce').match).toBe('COMMERCE');
  });

  it('is not a hit for an empty query', () => {
    expect(splitHit('anything', '')).toEqual({ pre: 'anything', match: '', post: '', hit: false });
  });

  it('is not a hit when absent', () => {
    expect(splitHit('locale', 'zzz').hit).toBe(false);
  });
});

describe('match counts are derived, never literal', () => {
  const body = parseSchemaBody(AVRO);

  it('counts field names, metadata keys and metadata values separately', () => {
    // `email`: the field name, plus the `piiFields` value that lists it, plus
    // `owner.team`? no. So two — and a query hitting both a key and its value
    // in one row counts twice, because both are highlighted.
    expect(countFieldViewMatches(body, 'email')).toBe(2);
    expect(countFieldViewMatches(body, 'customer')).toBe(
      // `customer_id` field name, `owner.team` value TM-CUSTOMER-PLATFORM,
      // `domain` value customer, `piiFields` value listing customer_id.
      4,
    );
  });

  it('is 0 for an empty query and for a miss', () => {
    expect(countFieldViewMatches(body, '')).toBe(0);
    expect(countFieldViewMatches(body, 'zzzznope')).toBe(0);
  });

  it('moves when the query moves', () => {
    expect(countFieldViewMatches(body, 'locale')).toBe(1);
    expect(countFieldViewMatches(body, 'confidential')).toBe(1);
  });

  it('counts graph matches in nodes', () => {
    // `email` rings the `fields[]` container (its row lists the field), the
    // `email` field node itself, and `metadata` (its piiFields row).
    expect(countGraphMatches(body.value, 'email')).toBe(3);
    expect(countGraphMatches(body.value, '')).toBe(0);
    expect(countGraphMatches(body.value, 'zzzznope')).toBe(0);
  });

  it('matches a node on its title as well as its rows', () => {
    const { nodes } = buildSchemaGraph(body.value);
    const emailNode = nodes.find((n) => n.id === 'root.fields.1')!;
    expect(graphNodeMatches(emailNode, 'email')).toBe(true);
    expect(graphNodeMatches(emailNode, 'string')).toBe(true);
    expect(graphNodeMatches(emailNode, 'locale')).toBe(false);
  });
});
