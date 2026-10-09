/**
 * Reads a registered schema body into the shapes the three detail views
 * render: a field table (4a), a token-coloured code listing (4b) and a node
 * graph (5a). One parse, three presentations — so a field that appears in the
 * table is the same field the graph draws, and a search match counts once.
 *
 * Everything here is derived from the body and nothing else. The facts band on
 * the schema page reads the artifact's own properties instead, which is why it
 * can legitimately show `—` for an owner team that the metadata block below it
 * spells out: the two are different sources and the design shows both
 * deliberately. Do not "fix" that by feeding the band from here.
 */

export interface SchemaField {
  name: string;
  /** Rendered Avro/JSON-Schema type, e.g. `string`, `long (timestamp-millis)`. */
  type: string;
  /** The container the field sits in, as the Path column shows it. */
  path: string;
  /** Listed in the metadata block's `piiFields`. */
  pii: boolean;
}

export interface MetadataRow {
  /** Dotted, so `owner.service` rather than a nested render. */
  key: string;
  value: string;
}

export interface SchemaRecord {
  name: string | null;
  /** `record`, `enum`, `object` … whatever the body declares. */
  kind: string | null;
  namespace: string | null;
}

export interface ParsedSchemaBody {
  /** False when the body is not JSON at all (PROTOBUF, GRAPHQL, XSD, WSDL). */
  parsed: boolean;
  record: SchemaRecord;
  fields: SchemaField[];
  metadata: MetadataRow[];
  /** The parsed value, for the graph builder. `null` when not JSON. */
  value: unknown;
}

const EMPTY: ParsedSchemaBody = {
  parsed: false,
  record: { name: null, kind: null, namespace: null },
  fields: [],
  metadata: [],
  value: null,
};

/**
 * Expands one member of a type — the element type of an array, the value type
 * of a map, the name of a nested record — using the sibling keys that describe
 * it. Split out because a JSON Schema union keeps those siblings on the
 * PARENT: `{"type":["array","null"],"items":{…}}` has one `items` shared by
 * the whole union, so each member has to be rendered against it.
 */
function renderTypeMember(base: string, spec: Record<string, unknown>): string {
  if (base === 'array' && spec.items !== undefined) return `array<${renderFieldType(spec.items)}>`;
  if (base === 'map' && spec.values !== undefined) return `map<${renderFieldType(spec.values)}>`;
  if (base === 'record' && typeof spec.name === 'string') return `record ${spec.name}`;
  if (base === 'enum' && typeof spec.name === 'string') return `enum ${spec.name}`;
  return base;
}

/** Renders an Avro or JSON Schema type union/complex type as one readable string. */
export function renderFieldType(type: unknown): string {
  if (typeof type === 'string') return type;

  if (Array.isArray(type)) {
    // A union. `["null","string"]` is the common nullable idiom.
    return type.map(renderFieldType).join(' | ');
  }

  if (type && typeof type === 'object') {
    const t = type as Record<string, unknown>;

    // The two dialects spell a union differently. Avro writes it as a bare
    // array, handled above. JSON Schema writes it as the value of `type` —
    // `{"type":["string","null"]}` — which read as a plain object rendered as
    // `object`, flattening 62 of the 63 properties of `product.gold-schema`
    // into one meaningless word.
    const rendered = Array.isArray(t.type)
      ? t.type
          .map((member) =>
            typeof member === 'string' ? renderTypeMember(member, t) : renderFieldType(member),
          )
          .join(' | ')
      : renderTypeMember(typeof t.type === 'string' ? t.type : 'object', t);

    // Avro's `logicalType` and JSON Schema's `format` both say what the value
    // MEANS — a long that is a timestamp is not interchangeable with a long
    // that is a count — so neither is collapsed into the primitive.
    const meaning =
      typeof t.logicalType === 'string'
        ? t.logicalType
        : typeof t.format === 'string'
          ? t.format
          : null;

    return meaning ? `${rendered} (${meaning})` : rendered;
  }

  return 'unknown';
}

/** Flattens nested objects to dotted keys; arrays of scalars join with `, `. */
function flatten(value: unknown, prefix: string, out: MetadataRow[]): void {
  if (value === null || value === undefined) {
    out.push({ key: prefix, value: 'null' });
    return;
  }
  if (Array.isArray(value)) {
    out.push({ key: prefix, value: value.map((v) => String(v)).join(', ') });
    return;
  }
  if (typeof value === 'object') {
    for (const [k, v] of Object.entries(value as Record<string, unknown>)) {
      flatten(v, prefix ? `${prefix}.${k}` : k, out);
    }
    return;
  }
  out.push({ key: prefix, value: String(value) });
}

export function parseSchemaBody(content: string): ParsedSchemaBody {
  let value: unknown;
  try {
    value = JSON.parse(content);
  } catch {
    // Not a failure worth surfacing here: PROTOBUF, GRAPHQL and XSD bodies are
    // legitimately not JSON. The caller falls back to the code view, which
    // renders any text. (Those three also get no metadata block and so land
    // non-compliant, which the compliance tab says in its own words.)
    return EMPTY;
  }

  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    return { ...EMPTY, parsed: true, value };
  }

  const root = value as Record<string, unknown>;

  const metadata: MetadataRow[] = [];
  if (root.metadata && typeof root.metadata === 'object') {
    flatten(root.metadata, '', metadata);
  }

  const piiFields = new Set<string>(
    Array.isArray((root.metadata as Record<string, unknown> | undefined)?.piiFields)
      ? ((root.metadata as Record<string, unknown>).piiFields as unknown[]).map((v) => String(v))
      : [],
  );

  const rawFields = Array.isArray(root.fields) ? root.fields : [];
  const avroFields: SchemaField[] = rawFields
    .filter((f): f is Record<string, unknown> => Boolean(f) && typeof f === 'object')
    .map((f) => {
      const name = typeof f.name === 'string' ? f.name : '';
      return {
        name,
        type: renderFieldType(f.type),
        // The container, not an index: every top-level record field lives in
        // the same `fields[]`, and an index would imply the position means
        // something to a consumer, which in Avro it does not.
        path: 'fields[]',
        pii: piiFields.has(name),
      };
    });

  // A JSON Schema names its fields in a `properties` MAP, not an Avro `fields`
  // ARRAY. The registry holds JSON artifacts alongside AVRO ones — reading
  // only `fields` left `product.gold-schema` and its 63 properties rendering
  // as its metadata block alone, which read as a schema that declares nothing.
  //
  // Avro wins where a body carries both: `fields` is the Avro contract, and a
  // `properties` key next to it is not a second field list to append.
  const jsonSchemaFields: SchemaField[] =
    avroFields.length > 0 || !root.properties || typeof root.properties !== 'object'
      ? []
      : Object.entries(root.properties as Record<string, unknown>).map(([name, spec]) => ({
          name,
          type: renderFieldType(spec),
          // `properties` with no `[]`, because it is a map: unlike Avro's
          // array there is no position here to mistake for meaning.
          path: 'properties',
          pii: piiFields.has(name),
        }));

  const fields = avroFields.length > 0 ? avroFields : jsonSchemaFields;

  return {
    parsed: true,
    record: {
      // Avro puts the record's name in `name`; a JSON Schema puts it in
      // `title`. `$id` is the closest thing it has to Avro's `namespace`, but
      // it is a URI rather than a name and is left to Code view.
      name:
        typeof root.name === 'string'
          ? root.name
          : typeof root.title === 'string'
            ? root.title
            : null,
      kind: typeof root.type === 'string' ? root.type : null,
      namespace: typeof root.namespace === 'string' ? root.namespace : null,
    },
    fields,
    metadata,
    value,
  };
}

// ---------------------------------------------------------------------------
// Graph (5a)
// ---------------------------------------------------------------------------

export interface GraphRow {
  key: string;
  value: string;
}

export interface GraphNodeSpec {
  id: string;
  /** `record`, `fields[]`, `metadata`, `owner`, a field name … */
  title: string;
  /** Scalar entries, rendered as mono key/value rows inside the node. */
  rows: GraphRow[];
}

export interface GraphEdgeSpec {
  id: string;
  source: string;
  target: string;
  /** Index of the source node's row the edge leaves from, or -1 for the title. */
  sourceRow: number;
}

/**
 * Walks the parsed body and emits one node per object (and per array of
 * objects), with the scalars of that object as its rows and an edge to each
 * nested one. For the customer example that is exactly seven nodes: the record
 * root, `fields[]`, its three field objects, `metadata`, `owner` and
 * `piiFields`.
 *
 * Edges carry the index of the row they leave, because the design anchors them
 * on the row rather than the node centre — an edge from the middle of a
 * six-row node tells you nothing about which key it belongs to.
 */
export function buildSchemaGraph(value: unknown): {
  nodes: GraphNodeSpec[];
  edges: GraphEdgeSpec[];
} {
  const nodes: GraphNodeSpec[] = [];
  const edges: GraphEdgeSpec[] = [];

  if (!value || typeof value !== 'object') return { nodes, edges };

  function isBranch(v: unknown): boolean {
    if (Array.isArray(v)) return v.some((e) => e && typeof e === 'object');
    return Boolean(v) && typeof v === 'object';
  }

  function visit(obj: Record<string, unknown>, id: string, title: string): void {
    const rows: GraphRow[] = [];
    const branches: { key: string; value: unknown }[] = [];

    for (const [k, v] of Object.entries(obj)) {
      if (isBranch(v)) {
        branches.push({ key: k, value: v });
        // The branch still gets a row, so the edge has something to leave
        // from and the reader can see the key without following the edge.
        rows.push({ key: k, value: Array.isArray(v) ? `[${v.length}]` : '{…}' });
      } else if (Array.isArray(v)) {
        rows.push({ key: k, value: `[${v.map((e) => String(e)).join(', ')}]` });
      } else {
        rows.push({ key: k, value: v === null ? 'null' : String(v) });
      }
    }

    nodes.push({ id, title, rows });

    for (const branch of branches) {
      const rowIndex = rows.findIndex((r) => r.key === branch.key);
      const childId = `${id}.${branch.key}`;

      if (Array.isArray(branch.value)) {
        // The array itself is a node, so `fields[]` is visible as a container
        // rather than the three field objects hanging off the root.
        const arrayTitle = `${branch.key}[]`;
        const objectEntries = branch.value.filter(
          (e): e is Record<string, unknown> => Boolean(e) && typeof e === 'object',
        );
        nodes.push({
          id: childId,
          title: arrayTitle,
          rows: objectEntries.map((e, i) => ({
            key: String(i),
            value: typeof e.name === 'string' ? e.name : '{…}',
          })),
        });
        edges.push({ id: `${id}->${childId}`, source: id, target: childId, sourceRow: rowIndex });

        objectEntries.forEach((entry, i) => {
          const leafId = `${childId}.${i}`;
          visit(entry, leafId, typeof entry.name === 'string' ? entry.name : `${arrayTitle}[${i}]`);
          edges.push({ id: `${childId}->${leafId}`, source: childId, target: leafId, sourceRow: i });
        });
        continue;
      }

      visit(branch.value as Record<string, unknown>, childId, branch.key);
      edges.push({ id: `${id}->${childId}`, source: id, target: childId, sourceRow: rowIndex });
    }
  }

  const root = value as Record<string, unknown>;
  visit(root, 'root', typeof root.name === 'string' ? root.name : 'root');

  return { nodes, edges };
}

// ---------------------------------------------------------------------------
// Search
// ---------------------------------------------------------------------------

export interface Hit {
  pre: string;
  match: string;
  post: string;
  hit: boolean;
}

/** Splits `text` around the first case-insensitive occurrence of `query`. */
export function splitHit(text: string, query: string): Hit {
  if (!query) return { pre: text, match: '', post: '', hit: false };
  const i = text.toLowerCase().indexOf(query.toLowerCase());
  if (i === -1) return { pre: text, match: '', post: '', hit: false };
  return {
    pre: text.slice(0, i),
    match: text.slice(i, i + query.length),
    post: text.slice(i + query.length),
    hit: true,
  };
}

/**
 * Field view's match total: field names, metadata keys and metadata values,
 * counted separately because a query can hit a key and its value in the same
 * row and both are highlighted.
 *
 * Derived, never hard-coded — the prototype's "6 matches" was a literal, and a
 * count that does not move when the query does is worse than no count.
 */
export function countFieldViewMatches(
  body: Pick<ParsedSchemaBody, 'fields' | 'metadata'>,
  query: string,
): number {
  if (!query) return 0;
  let n = 0;
  for (const f of body.fields) if (splitHit(f.name, query).hit) n += 1;
  for (const m of body.metadata) {
    if (splitHit(m.key, query).hit) n += 1;
    if (splitHit(m.value, query).hit) n += 1;
  }
  return n;
}

/** Whether a graph node's title or any of its rows contains `query`. */
export function graphNodeMatches(node: GraphNodeSpec, query: string): boolean {
  if (!query) return false;
  if (splitHit(node.title, query).hit) return true;
  return node.rows.some((r) => splitHit(r.key, query).hit || splitHit(r.value, query).hit);
}

/**
 * Graph view's match total, in nodes. Lives here rather than being reported up
 * from the graph component so the page can label its search field without the
 * graph having to be mounted — switching to Fields view must not blank the
 * count the graph would have shown.
 */
export function countGraphMatches(value: unknown, query: string): number {
  if (!query) return 0;
  const { nodes } = buildSchemaGraph(value);
  return nodes.filter((n) => graphNodeMatches(n, query)).length;
}
