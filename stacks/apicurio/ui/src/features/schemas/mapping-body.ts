/**
 * Reads a registered gold attribute mapping into the shapes the Mapping tab
 * renders: a facts band, three flat sheets — one per column kind — and the
 * named definitions the rows cite.
 *
 * Deliberately shaped like `schema-body.ts`, whose docstring states the
 * governing idea: one parse, several presentations, so a source table named
 * in a row is the same one the Silver reference resolves and a search match
 * counts once.
 *
 * A mapping is a COMPLETELY different body from a schema. It has no `fields`,
 * no `properties` and no metadata block — so `parseSchemaBody` finds nothing
 * in it, the Fields view renders empty and the graph draws its columns as
 * anonymous objects. That is why this module exists rather than an extension
 * of that one.
 *
 * Everything here is pure: no React, no network. The detail endpoint already
 * returns the body (`SchemaDetailRead.content`), so the whole view is a
 * function of a string.
 *
 * The contract is `stacks/apicurio/standards/gold-attribute-mapping.schema.json`;
 * the worked standard beside it explains the parts JSON Schema cannot state.
 */

export interface EntityKeyPair {
  goldColumn: string;
  drivingColumn: string;
}

export interface MappingRegistry {
  group: string;
  artifact: string;
  /** Null when the artifact did not declare one — never defaulted here. */
  compatibility: string | null;
}

export interface MappingBatch {
  requestedBy: string;
  targetDate: string;
  backfillMode: string;
  newChannel: string | null;
}

export interface MappingFacts {
  domain: string | null;
  table: string | null;
  drivingTable: string | null;
  drivingSilverTable: string | null;
  entityKey: EntityKeyPair[];
  registry: MappingRegistry | null;
  batch: MappingBatch | null;
}

/** A plain object, which is the only thing any of this can read. */
function asRecord(value: unknown): Record<string, unknown> | null {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return null;
  return value as Record<string, unknown>;
}

function str(value: unknown): string | null {
  return typeof value === 'string' && value.length > 0 ? value : null;
}

/**
 * True when this body is a gold attribute mapping.
 *
 * By SHAPE, never by artifact name. Two naming conventions already exist for
 * the same artifact — `product.product.mapping` is the dev registry's
 * coordinate, `product-gold-attribute-mapping` is what the files' own
 * `registry.artifact` declares — so a rule keyed on either breaks on the
 * other. `_is_mapping_body` in `app/services/metadata_validator.py` is the
 * same predicate in Python; the design spec §3.1 is their shared definition.
 *
 * `columns` must be an ARRAY. A body carrying `columns: {}` is not a mapping,
 * and taking it for one would have the flatteners read a dict's keys as
 * column entries.
 */
export function isMappingBody(value: unknown): boolean {
  const body = asRecord(value);
  if (!body) return false;
  const schemaRef = body.$schema;
  if (typeof schemaRef === 'string' && schemaRef.includes('gold-attribute-mapping')) {
    return true;
  }
  return (
    typeof body.domain === 'string' &&
    typeof body.table === 'string' &&
    Array.isArray(body.columns)
  );
}

export function parseMappingFacts(body: Record<string, unknown>): MappingFacts {
  const registryRaw = asRecord(body.registry);
  const registry: MappingRegistry | null =
    registryRaw && str(registryRaw.group) && str(registryRaw.artifact)
      ? {
          group: String(registryRaw.group),
          artifact: String(registryRaw.artifact),
          // Not defaulted to BACKWARD. The contract says Apicurio defaults it,
          // but the view must not claim a rule the artifact did not declare —
          // the schema facts band already shows `not set` honestly.
          compatibility: str(registryRaw.compatibility),
        }
      : null;

  const batchRaw = asRecord(body.batch);
  const batch: MappingBatch | null = batchRaw
    ? {
        requestedBy: str(batchRaw.requestedBy) ?? '',
        targetDate: str(batchRaw.targetDate) ?? '',
        backfillMode: str(batchRaw.backfillMode) ?? '',
        newChannel: str(batchRaw.newChannel),
      }
    : null;

  const entityKey: EntityKeyPair[] = (Array.isArray(body.entityKey) ? body.entityKey : [])
    .map(asRecord)
    .filter((p): p is Record<string, unknown> => p !== null)
    .map((p) => ({
      goldColumn: str(p.goldColumn) ?? '',
      drivingColumn: str(p.drivingColumn) ?? '',
    }))
    .filter((p) => p.goldColumn !== '');

  return {
    domain: str(body.domain),
    table: str(body.table),
    drivingTable: str(body.drivingTable),
    drivingSilverTable: str(body.drivingSilverTable),
    entityKey,
    registry,
    batch,
  };
}

export type MappingKind = 'scalar' | 'conditional' | 'nested';

export interface Channels {
  event: boolean;
  message: boolean;
  api: boolean;
}

export interface RawSource {
  table: string | null;
  column: string | null;
  filter: string | null;
  /** The structured join, resolved by `resolveJoin`. */
  join: unknown;
}

export interface MappingRowBase {
  /** Stable React key, unique within its sheet. */
  key: string;
  /**
   * Identifies the gold column this row belongs to — one group per scalar
   * column, per conditional column (all its branches), per nested column (all
   * its fields). `filterRows` keeps a group whole, so searching a description
   * that only the first branch row carries cannot hide the other branches.
   */
  groupKey: string;
  goldColumnName: string;
  /**
   * Empty on a continuation row. The workbook carries the description once
   * per gold column, not once per branch or field — repeating it reads as
   * though the branches had different descriptions.
   */
  businessDescription: string;
  isFirstOfGroup: boolean;
  source: RawSource;
  channels: Channels;
  notes: string;
  /**
   * DEPRECATED (UIFPE-104), superseded by the per-source join. Carried so a
   * legacy file's value is still shown; null means the artifact declared
   * none, and the header says the column is superseded.
   */
  joinGrain: string | null;
  transform: unknown;
  derivation: unknown;
}

export interface ScalarRow extends MappingRowBase {
  kind: 'scalar';
  dataType: string;
  nullable: boolean;
}

export interface ConditionalRow extends MappingRowBase {
  kind: 'conditional';
  dataType: string;
  /** `Table.Column`, as the workbook's Discriminator cell shows it. */
  discriminator: string;
  condition: string;
  fallback: string;
  /** The branch scope, resolved by `resolveScope`. */
  scope: unknown;
}

export interface NestedRow extends MappingRowBase {
  kind: 'nested';
  fieldName: string;
  fieldType: string;
  joinKey: string;
  sortKey: boolean;
  /** The ranked pick, resolved by `resolvePick`. */
  pick: unknown;
}

export type MappingRow = ScalarRow | ConditionalRow | NestedRow;

const NO_SOURCE: RawSource = { table: null, column: null, filter: null, join: null };

function readSource(value: unknown, sharedFilter: string | null = null): RawSource {
  const src = asRecord(value);
  if (!src) return { ...NO_SOURCE, filter: sharedFilter };
  return {
    table: str(src.table),
    column: str(src.column),
    // A field-level filter wins; the nested column's shared `filter` is the
    // fallback, because the contract puts it on the column while the workbook
    // has one Source Filter cell per field row.
    filter: str(src.filter) ?? sharedFilter,
    join: src.join ?? null,
  };
}

function readChannels(value: unknown): Channels {
  const c = asRecord(value);
  return {
    event: c?.event === true,
    message: c?.message === true,
    api: c?.api === true,
  };
}

function columnsOfKind(columns: unknown[], kind: MappingKind): Record<string, unknown>[] {
  return columns
    .map(asRecord)
    .filter((c): c is Record<string, unknown> => c !== null && c.kind === kind);
}

export function flattenScalar(columns: unknown[]): ScalarRow[] {
  return columnsOfKind(columns, 'scalar').map((col, i) => ({
    kind: 'scalar' as const,
    key: `${str(col.goldColumnName) ?? 'col'}#${i}`,
    groupKey: `scalar#${i}`,
    goldColumnName: str(col.goldColumnName) ?? '',
    businessDescription: str(col.businessDescription) ?? '',
    isFirstOfGroup: true,
    dataType: str(col.dataType) ?? '',
    source: readSource(col.source),
    channels: readChannels(col.channels),
    nullable: col.nullable === true,
    notes: str(col.notes) ?? '',
    joinGrain: str(col.joinGrain),
    transform: col.transform ?? null,
    derivation: col.derivation ?? null,
  }));
}

export function flattenConditional(columns: unknown[]): ConditionalRow[] {
  const rows: ConditionalRow[] = [];
  columnsOfKind(columns, 'conditional').forEach((col, colIndex) => {
    const branches = Array.isArray(col.branches) ? col.branches : [];
    const disc = asRecord(col.discriminator);
    const discriminator =
      disc && str(disc.table) && str(disc.column)
        ? `${String(disc.table)}.${String(disc.column)}`
        : '';
    const goldColumnName = str(col.goldColumnName) ?? '';

    branches.map(asRecord).forEach((branch, branchIndex) => {
      if (!branch) return;
      rows.push({
        kind: 'conditional',
        // The gold column name alone is not unique — every branch of one
        // column shares it, and duplicate React keys silently drop rows.
        key: `${goldColumnName}#${colIndex}#${branchIndex}`,
        groupKey: `conditional#${colIndex}`,
        goldColumnName,
        businessDescription: branchIndex === 0 ? str(col.businessDescription) ?? '' : '',
        isFirstOfGroup: branchIndex === 0,
        dataType: branchIndex === 0 ? str(col.dataType) ?? '' : '',
        discriminator: branchIndex === 0 ? discriminator : '',
        condition: str(branch.condition) ?? '',
        source: readSource(branch.source),
        fallback: str(branch.fallback) ?? '',
        scope: branch.scope ?? null,
        channels: readChannels(col.channels),
        notes: str(branch.notes) ?? '',
        joinGrain: str(branch.joinGrain) ?? str(col.joinGrain),
        // A branch's own transform applies BEFORE the pick, to that branch's
        // expression; the column's applies after, to whichever branch won.
        // The row shows the branch's when it has one.
        transform: branch.transform ?? col.transform ?? null,
        derivation: col.derivation ?? null,
      });
    });
  });
  return rows;
}

export function flattenNested(columns: unknown[]): NestedRow[] {
  const rows: NestedRow[] = [];
  columnsOfKind(columns, 'nested').forEach((col, colIndex) => {
    const fields = Array.isArray(col.fields) ? col.fields : [];
    const sharedFilter = str(col.filter);
    const goldColumnName = str(col.goldColumnName) ?? '';
    const joinKey = str(col.joinKey) ?? '';

    fields.map(asRecord).forEach((field, fieldIndex) => {
      if (!field) return;
      rows.push({
        kind: 'nested',
        key: `${goldColumnName}#${colIndex}#${fieldIndex}`,
        groupKey: `nested#${colIndex}`,
        goldColumnName,
        businessDescription: fieldIndex === 0 ? str(col.businessDescription) ?? '' : '',
        isFirstOfGroup: fieldIndex === 0,
        fieldName: str(field.name) ?? '',
        fieldType: str(field.type) ?? '',
        joinKey: fieldIndex === 0 ? joinKey : '',
        sortKey: field.sortKey === true,
        source: readSource(field.source, sharedFilter),
        channels: readChannels(col.channels),
        notes: str(field.notes) ?? '',
        joinGrain: str(col.joinGrain),
        transform: field.transform ?? null,
        derivation: col.derivation ?? null,
        pick: col.pick ?? null,
      });
    });
  });
  return rows;
}

// ── Resolvers ────────────────────────────────────────────────────────────────
//
// Five things the registered JSON carries that the workbook has no column for
// (design spec §2.2). They are resolved here and rendered in the row detail,
// so the table keeps the workbook's shape and nothing is lost.

export interface ResolvedJoin {
  kind: 'driving' | 'join' | 'none';
  type: 'left' | 'inner';
  table: string | null;
  on: { left: string; right: string }[];
  filter: string | null;
}

/**
 * How a source attaches to the entity (UIFPE-104).
 *
 * `leftTable` is taken as declared and never rewritten to the driving table:
 * chains compose (A -> B -> C), so the ON clause must name the table the
 * contract names.
 */
export function resolveJoin(source: RawSource, drivingTable: string | null): ResolvedJoin {
  if (!source.table) {
    return { kind: 'none', type: 'left', table: null, on: [], filter: source.filter };
  }
  if (drivingTable && source.table === drivingTable) {
    return { kind: 'driving', type: 'left', table: source.table, on: [], filter: source.filter };
  }
  const join = asRecord(source.join);
  const pairs = Array.isArray(join?.on) ? join.on : [];
  return {
    kind: 'join',
    type: join?.type === 'inner' ? 'inner' : 'left',
    table: source.table,
    on: pairs
      .map(asRecord)
      .filter((p): p is Record<string, unknown> => p !== null)
      .map((p) => ({
        left: `${str(p.leftTable) ?? '?'}.${str(p.leftColumn) ?? '?'}`,
        right: `${source.table}.${str(p.rightColumn) ?? '?'}`,
      })),
    filter: source.filter,
  };
}

export interface ResolvedScope {
  kind: 'group' | 'inline' | 'else' | 'all' | 'unresolved';
  label: string;
  predicate: string | null;
}

function renderPredicate(spec: Record<string, unknown>): string {
  const table = str(spec.table);
  const column = str(spec.column) ?? '?';
  const qualified = table ? `${table}.${column}` : column;
  const op = str(spec.op) ?? '?';
  const values = (Array.isArray(spec.values) ? spec.values : []).map(String);
  if (values.length === 0) return `${qualified} ${op}`;

  // `startsWith` is rendered as the contract SAYS it renders, not as the op
  // name. gold-attribute-mapping.schema.json, $defs.branchScope.properties.op:
  // "startsWith renders LIKE 'value%'". Printing `startsWith '01'` would show
  // the reader something different from the SQL that runs, which is the one
  // thing this panel exists to prevent.
  if (op === 'startsWith') {
    // The column is repeated per disjunct. `C LIKE '01%' OR '02%'` is not a
    // SQL predicate, and this panel's whole job is to show the reader the
    // predicate that actually runs.
    return values.map((v) => `${qualified} LIKE '${v}%'`).join(' OR ');
  }
  if (op === 'in') {
    return `${qualified} IN (${values.map((v) => `'${v}'`).join(', ')})`;
  }
  if (op === 'equals') {
    return `${qualified} = '${values[0]}'`;
  }
  return `${qualified} ${op} ${values.map((v) => `'${v}'`).join(', ')}`;
}

/**
 * WHICH entity rows a conditional branch's value reaches.
 *
 * Distinct from the branch's `condition`, which is the discriminator value —
 * the contract is explicit that row-group qualifiers do not belong there.
 */
export function resolveScope(scope: unknown, body: Record<string, unknown>): ResolvedScope {
  const spec = asRecord(scope);
  if (!spec) {
    return { kind: 'all', label: 'every row — this branch declares no scope', predicate: null };
  }
  if (spec.op === 'else') {
    return {
      kind: 'else',
      label: "every row no sibling branch's scope claimed",
      predicate: null,
    };
  }
  const groupName = str(spec.group);
  if (groupName) {
    const groups = asRecord(body.rowGroups) ?? {};
    const group = asRecord(groups[groupName]);
    if (!group) {
      // The view reports what the artifact declares; rejecting a dangling
      // reference is CI's job (test_intake_hygiene.py).
      return { kind: 'unresolved', label: groupName, predicate: null };
    }
    const complementOf = str(group.complementOf);
    if (complementOf) {
      const base = asRecord(groups[complementOf]);
      const basePredicate = base ? renderPredicate(base) : complementOf;
      return {
        kind: 'group',
        label: groupName,
        predicate: `NOT (${basePredicate}) — the complement of ${complementOf}`,
      };
    }
    return { kind: 'group', label: groupName, predicate: renderPredicate(group) };
  }
  return {
    kind: 'inline',
    label: str(spec.label) ?? 'inline row group',
    predicate: renderPredicate(spec),
  };
}

export interface TransformStep { op: string; detail: string }
export interface TransformCase { when: string; then: string }

export interface ResolvedTransform {
  // No 'sql' member: a sqlExpr transform is still a `rule` or an `inline`
  // one, and `reviewFlagged` is what marks it. A union member nothing can
  // produce invites a consumer to branch on it and render nothing.
  kind: 'none' | 'rule' | 'inline' | 'unresolved';
  name: string;
  description: string | null;
  steps: TransformStep[];
  cases: TransformCase[];
  fallthrough: string | null;
  sqlExpr: string | null;
  reviewFlagged: boolean;
}

const NO_TRANSFORM: ResolvedTransform = {
  kind: 'none', name: '', description: null,
  steps: [], cases: [], fallthrough: null, sqlExpr: null, reviewFlagged: false,
};

function renderStep(step: Record<string, unknown>): TransformStep {
  const op = str(step.op) ?? '?';
  const detail =
    step.precision !== undefined ? `precision ${String(step.precision)}`
    : str(step.to) ?? str(step.value) ?? '';
  return { op, detail };
}

function renderWhenThen(spec: Record<string, unknown>): string {
  const op = str(spec.op) ?? '?';
  const values = Array.isArray(spec.values) ? spec.values : [];
  const text = str(spec.text) ?? str(spec.find);
  const replaceWith = str(spec.replaceWith);
  if (values.length > 0) return `${op} ${values.map((v) => `'${String(v)}'`).join(', ')}`;
  if (text && replaceWith) return `${op} '${text}' -> '${replaceWith}'`;
  if (text) return `${op} '${text}'`;
  return op;
}

function renderDefinition(
  def: Record<string, unknown>,
  kind: ResolvedTransform['kind'],
  name: string,
): ResolvedTransform {
  const sqlExpr = str(def.sqlExpr);
  const steps = (Array.isArray(def.steps) ? def.steps : [])
    .map(asRecord)
    .filter((s): s is Record<string, unknown> => s !== null)
    .map(renderStep);
  const cases = (Array.isArray(def.cases) ? def.cases : [])
    .map(asRecord)
    .filter((c): c is Record<string, unknown> => c !== null)
    .map((c) => ({
      when: renderWhenThen(asRecord(c.when) ?? {}),
      then: renderWhenThen(asRecord(c.then) ?? {}),
    }));
  return {
    kind,
    name,
    description: str(def.description),
    steps,
    cases,
    // Default `keep` per the contract, stated rather than left blank so a
    // reader is not left guessing what an unmatched row gets.
    fallthrough: cases.length > 0 ? str(def.else) ?? 'keep' : null,
    sqlExpr,
    // CI flags every sqlExpr for engineering review, so the row shows it.
    reviewFlagged: sqlExpr !== null,
  };
}

export function resolveTransform(
  transform: unknown,
  body: Record<string, unknown>,
): ResolvedTransform {
  if (transform === null || transform === undefined) return NO_TRANSFORM;

  // A prose string. DEPRECATED (UIFPE-105) but still accepted while old files
  // migrate — showing the text beats showing nothing.
  if (typeof transform === 'string') {
    return { ...NO_TRANSFORM, kind: 'unresolved', name: transform };
  }

  const spec = asRecord(transform);
  if (!spec) return NO_TRANSFORM;

  const ruleName = str(spec.rule);
  if (ruleName) {
    const named = asRecord(asRecord(body.transforms)?.[ruleName]);
    if (!named) return { ...NO_TRANSFORM, kind: 'unresolved', name: ruleName };
    return renderDefinition(named, 'rule', ruleName);
  }
  return renderDefinition(spec, 'inline', '');
}

/** The env-templated fully qualified Silver name for a bare source table. */
export function resolveSilverTable(
  table: string | null,
  body: Record<string, unknown>,
): string | null {
  if (!table) return null;
  if (table === str(body.drivingTable)) return str(body.drivingSilverTable);
  return str(asRecord(body.silverTables)?.[table]);
}

export interface ResolvedPick {
  partitionBy: string[];
  orderBy: { expr: string; direction: string }[];
}

export function resolvePick(pick: unknown): ResolvedPick | null {
  const spec = asRecord(pick);
  if (!spec) return null;
  return {
    partitionBy: (Array.isArray(spec.partitionBy) ? spec.partitionBy : []).map(String),
    orderBy: (Array.isArray(spec.orderBy) ? spec.orderBy : [])
      .map(asRecord)
      .filter((o): o is Record<string, unknown> => o !== null)
      .map((o) => ({ expr: str(o.expr) ?? '', direction: str(o.direction) ?? 'asc' })),
  };
}

// ── Reference catalogs and the entry point ──────────────────────────────────

export interface RowGroupEntry {
  name: string;
  predicate: string;
  description: string | null;
  complementOf: string | null;
}

export interface MappingReference {
  rowGroups: RowGroupEntry[];
  transforms: { name: string; resolved: ResolvedTransform; usedBy: number }[];
  silverTables: { name: string; qualified: string; driving: boolean }[];
  entityKey: EntityKeyPair[];
}

export interface ParsedMapping {
  isMapping: boolean;
  facts: MappingFacts;
  scalar: ScalarRow[];
  conditional: ConditionalRow[];
  nested: NestedRow[];
  reference: MappingReference;
  /** The parsed body, for the code view and for the resolvers. */
  value: unknown;
}

/**
 * A fresh object each time, deliberately not a shared const.
 *
 * Every non-mapping parse would otherwise hand back the SAME arrays, so one
 * consumer sorting a sheet in place would corrupt the value every later parse
 * returns. Nothing does that today; the cost of not relying on that is one
 * allocation on a path that renders no table.
 */
function notAMapping(value: unknown = null): ParsedMapping {
  return {
    isMapping: false,
    facts: {
      domain: null, table: null, drivingTable: null, drivingSilverTable: null,
      entityKey: [], registry: null, batch: null,
    },
    scalar: [], conditional: [], nested: [],
    reference: { rowGroups: [], transforms: [], silverTables: [], entityKey: [] },
    value,
  };
}

/**
 * Counts how many columns reference each named transform.
 *
 * This is the number that earns the transforms catalog its place: a rule used
 * by twelve columns is resolved inline twelve times and would otherwise never
 * be readable as one definition.
 */
function countTransformUses(columns: unknown[], name: string): number {
  let n = 0;
  for (const raw of columns) {
    const col = asRecord(raw);
    if (!col) continue;
    const refs: unknown[] = [col.transform];
    if (Array.isArray(col.branches)) {
      for (const b of col.branches) refs.push(asRecord(b)?.transform);
    }
    if (Array.isArray(col.fields)) {
      for (const f of col.fields) refs.push(asRecord(f)?.transform);
    }
    if (refs.some((r) => str(asRecord(r)?.rule) === name)) n += 1;
  }
  return n;
}

function buildReference(body: Record<string, unknown>, facts: MappingFacts): MappingReference {
  const columns = Array.isArray(body.columns) ? body.columns : [];

  const groupsRaw = asRecord(body.rowGroups) ?? {};
  const rowGroups: RowGroupEntry[] = Object.entries(groupsRaw).map(([name, raw]) => {
    const group = asRecord(raw) ?? {};
    const complementOf = str(group.complementOf);
    return {
      name,
      predicate: complementOf
        ? resolveScope({ group: name }, body).predicate ?? ''
        : renderPredicate(group),
      description: str(group.description),
      complementOf,
    };
  });

  const transformsRaw = asRecord(body.transforms) ?? {};
  const transforms = Object.keys(transformsRaw).map((name) => ({
    name,
    resolved: resolveTransform({ rule: name }, body),
    usedBy: countTransformUses(columns, name),
  }));

  const silverRaw = asRecord(body.silverTables) ?? {};
  const silverTables: { name: string; qualified: string; driving: boolean }[] = [];
  if (facts.drivingTable && facts.drivingSilverTable) {
    silverTables.push({
      name: facts.drivingTable,
      qualified: facts.drivingSilverTable,
      driving: true,
    });
  }
  for (const [name, qualified] of Object.entries(silverRaw)) {
    const value = str(qualified);
    if (value) silverTables.push({ name, qualified: value, driving: false });
  }

  return { rowGroups, transforms, silverTables, entityKey: facts.entityKey };
}

/**
 * The entry point. One parse, three sheets and a reference section — so a
 * source table named in a row is the same one the Silver catalog resolves.
 */
export function parseMappingBody(content: string): ParsedMapping {
  let parsed: unknown;
  try {
    parsed = JSON.parse(content);
  } catch {
    return notAMapping();
  }
  if (!isMappingBody(parsed)) return notAMapping(parsed);

  const body = parsed as Record<string, unknown>;
  const columns = Array.isArray(body.columns) ? body.columns : [];
  const facts = parseMappingFacts(body);

  return {
    isMapping: true,
    facts,
    scalar: flattenScalar(columns),
    conditional: flattenConditional(columns),
    nested: flattenNested(columns),
    reference: buildReference(body, facts),
    value: parsed,
  };
}

/**
 * The search predicate. FILTERS rows rather than only tinting them, following
 * the precedent SchemaFieldsView documents: highlighting alone "was enough
 * for a five-field Avro record and useless on product.gold-schema, where a
 * match can be the fortieth of 63 rows and off-screen". Product's mapping is
 * 74 columns and flattens to more rows than that.
 */
/**
 * Filters a sheet, keeping every row of a gold column whose group matched.
 *
 * A bare `rows.filter(rowMatchesQuery)` looks right and is wrong here. The
 * description lives on the FIRST row of a group only (the workbook's own
 * layout), so searching a conditional column's description matched its first
 * branch and hid the rest — answering "show me this column" by hiding most of
 * it. Searching is how you find a column; a column found in pieces is not
 * found.
 */
export function filterRows<T extends MappingRow>(rows: T[], query: string): T[] {
  if (!query) return rows;
  const hits = new Set<string>();
  for (const row of rows) {
    if (rowMatchesQuery(row, query)) hits.add(row.groupKey);
  }
  return rows.filter((r) => hits.has(r.groupKey));
}

export function rowMatchesQuery(row: MappingRow, query: string): boolean {
  if (!query) return true;
  const needle = query.toLowerCase();
  const haystack = [
    row.goldColumnName,
    row.businessDescription,
    row.source.table ?? '',
    row.source.column ?? '',
    row.notes,
    typeof row.transform === 'object' && row.transform !== null
      ? str((row.transform as Record<string, unknown>).rule) ?? ''
      : typeof row.transform === 'string'
        ? row.transform
        : '',
    row.kind === 'nested' ? row.fieldName : '',
    row.kind === 'conditional' ? row.condition : '',
  ];
  return haystack.some((h) => h.toLowerCase().includes(needle));
}
