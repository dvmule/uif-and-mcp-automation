import { describe, it, expect } from 'vitest';
import { isMappingBody, parseMappingFacts } from './mapping-body';

const MAPPING_OBJ = {
  $schema: '../../gold-attribute-mapping.schema.json',
  domain: 'product',
  table: 'gold_product.product',
  registry: {
    group: 'product',
    artifact: 'product-gold-attribute-mapping',
    compatibility: 'BACKWARD',
  },
  drivingTable: 'MTL_SYSTEM_ITEMS_B',
  entityKey: [
    { goldColumn: 'inventoryItemId', drivingColumn: 'INVENTORY_ITEM_ID' },
    { goldColumn: 'organizationId', drivingColumn: 'ORGANIZATION_ID' },
  ],
  batch: {
    requestedBy: 'Reyas Ahamed H',
    targetDate: '2026-07-22',
    backfillMode: 'silent',
    newChannel: null,
  },
  columns: [
    {
      goldColumnName: 'inventoryItemId',
      businessDescription: 'Oracle internal surrogate.',
      kind: 'scalar',
      dataType: 'integer',
      source: { table: 'MTL_SYSTEM_ITEMS_B', column: 'INVENTORY_ITEM_ID', filter: null },
      transform: null,
      channels: { event: false, message: false, api: false },
      nullable: false,
    },
  ],
};

describe('isMappingBody', () => {
  it('matches a body with domain, table and a columns list', () => {
    expect(isMappingBody({ domain: 'p', table: 't', columns: [] })).toBe(true);
  });

  it('matches on a $schema naming the mapping contract alone', () => {
    // A mapping whose columns are not authored yet still declares what it is.
    expect(isMappingBody({ $schema: '../gold-attribute-mapping.schema.json' })).toBe(true);
  });

  it('rejects an Avro record', () => {
    expect(isMappingBody({ type: 'record', name: 'X', fields: [] })).toBe(false);
  });

  it('rejects a JSON Schema master', () => {
    expect(
      isMappingBody({
        $schema: 'https://json-schema.org/draft/2020-12/schema',
        type: 'object',
        properties: { id: { type: 'string' } },
      }),
    ).toBe(false);
  });

  it('rejects an OpenAPI document', () => {
    expect(isMappingBody({ openapi: '3.1.0', paths: {} })).toBe(false);
  });

  it('rejects columns that are not a list', () => {
    // Guards the same case the backend guards: `columns: {}` must not read as
    // a mapping, or the flatteners would iterate a dict.
    expect(isMappingBody({ domain: 'p', table: 't', columns: {} })).toBe(false);
  });

  it('rejects a non-object', () => {
    expect(isMappingBody(null)).toBe(false);
    expect(isMappingBody('a string')).toBe(false);
    expect(isMappingBody([1, 2, 3])).toBe(false);
  });
});

describe('parseMappingFacts', () => {
  it('reads the gold table, domain and driving table', () => {
    const facts = parseMappingFacts(MAPPING_OBJ);
    expect(facts.domain).toBe('product');
    expect(facts.table).toBe('gold_product.product');
    expect(facts.drivingTable).toBe('MTL_SYSTEM_ITEMS_B');
  });

  it('keeps the entity key ordered', () => {
    // It is the gold table's ORDERED primary key — reordering it would
    // misstate the grain.
    const facts = parseMappingFacts(MAPPING_OBJ);
    expect(facts.entityKey).toEqual([
      { goldColumn: 'inventoryItemId', drivingColumn: 'INVENTORY_ITEM_ID' },
      { goldColumn: 'organizationId', drivingColumn: 'ORGANIZATION_ID' },
    ]);
  });

  it('reads the registry coordinate and the batch', () => {
    const facts = parseMappingFacts(MAPPING_OBJ);
    expect(facts.registry).toEqual({
      group: 'product',
      artifact: 'product-gold-attribute-mapping',
      compatibility: 'BACKWARD',
    });
    expect(facts.batch?.requestedBy).toBe('Reyas Ahamed H');
    expect(facts.batch?.newChannel).toBeNull();
  });

  it('defaults compatibility to null rather than inventing BACKWARD', () => {
    // The contract says it defaults to BACKWARD when Apicurio enforces it,
    // but the VIEW must not claim a rule the artifact did not declare — the
    // facts band already shows `not set` honestly for schema compatibility.
    const facts = parseMappingFacts({
      ...MAPPING_OBJ,
      registry: { group: 'product', artifact: 'a' },
    });
    expect(facts.registry?.compatibility).toBeNull();
  });

  it('returns nulls and empties for a mapping declaring only the minimum', () => {
    const facts = parseMappingFacts({ domain: 'p', table: 't', columns: [] });
    expect(facts.drivingTable).toBeNull();
    expect(facts.drivingSilverTable).toBeNull();
    expect(facts.entityKey).toEqual([]);
    expect(facts.registry).toBeNull();
    expect(facts.batch).toBeNull();
  });

  it('reads the templated driving Silver table when one is declared', () => {
    const facts = parseMappingFacts({
      ...MAPPING_OBJ,
      drivingSilverTable: 'uif_echo_{env}_silver_tep.mtl_system_items_b',
    });
    expect(facts.drivingSilverTable).toBe('uif_echo_{env}_silver_tep.mtl_system_items_b');
  });
});

import {
  flattenScalar,
  flattenConditional,
  flattenNested,
} from './mapping-body';

const SCALAR_COL = {
  goldColumnName: 'itemStatus',
  businessDescription: 'Lifecycle status of the item.',
  kind: 'scalar',
  dataType: 'string',
  source: {
    table: 'MTL_SYSTEM_ITEMS_B',
    column: 'INVENTORY_ITEM_STATUS_CODE',
    filter: null,
  },
  transform: null,
  channels: { event: true, message: true, api: true },
  nullable: true,
  notes: 'A note.',
};

const CONDITIONAL_COL = {
  goldColumnName: 'isbn13',
  businessDescription: 'ISBN-13.',
  kind: 'conditional',
  dataType: 'string',
  discriminator: { table: 'EGO_MTL_SY_ITEMS_EXT_B', column: 'ITEM_CATALOG_GROUP_ID' },
  channels: { event: true, message: true, api: true },
  branches: [
    {
      condition: 'ELSE (<> 10002)',
      source: {
        table: 'EGO_MTL_SY_ITEMS_EXT_B',
        column: 'C_EXT_ATTR8',
        filter: 'ATTR_GROUP_ID = 741',
      },
      fallback: 'returnNull',
      notes: null,
    },
    {
      condition: '10002 (Digital Catalog)',
      source: {
        table: 'EGO_MTL_SY_ITEMS_EXT_B',
        column: 'C_EXT_ATTR4',
        filter: 'ATTR_GROUP_ID = 1401',
      },
      fallback: 'fallbackToElse',
      notes: 'branch 2',
    },
  ],
};

const NESTED_COL = {
  goldColumnName: 'bomComponents',
  businessDescription: 'Bill-of-material component lines.',
  kind: 'nested',
  joinKey: 'BILL_SEQUENCE_ID -> item',
  filter: 'EXPLOSION_TYPE = 1',
  channels: { event: false, message: false, api: true },
  fields: [
    {
      name: 'componentItemId',
      type: 'integer',
      sortKey: true,
      source: { table: 'BOM_COMPONENTS_B', column: 'COMPONENT_ITEM_ID' },
      transform: null,
    },
    {
      name: 'quantity',
      type: 'number',
      sortKey: false,
      source: { table: 'BOM_COMPONENTS_B', column: 'COMPONENT_QUANTITY' },
      transform: null,
    },
  ],
};

describe('flattenScalar', () => {
  it('produces one row per column, carrying the workbook cells', () => {
    const [row] = flattenScalar([SCALAR_COL]);
    expect(row.goldColumnName).toBe('itemStatus');
    expect(row.businessDescription).toBe('Lifecycle status of the item.');
    expect(row.dataType).toBe('string');
    expect(row.source.table).toBe('MTL_SYSTEM_ITEMS_B');
    expect(row.source.column).toBe('INVENTORY_ITEM_STATUS_CODE');
    expect(row.source.filter).toBeNull();
    expect(row.nullable).toBe(true);
    expect(row.channels).toEqual({ event: true, message: true, api: true });
    expect(row.notes).toBe('A note.');
    expect(row.isFirstOfGroup).toBe(true);
  });

  it('ignores columns of another kind', () => {
    expect(flattenScalar([CONDITIONAL_COL, NESTED_COL])).toEqual([]);
  });

  it('renders a legacy joinGrain and leaves it null otherwise', () => {
    // DEPRECATED per UIFPE-104. Shown when a legacy file still carries one so
    // the reader sees what the artifact says; null otherwise so the column
    // does not teach the deprecated thing.
    expect(flattenScalar([SCALAR_COL])[0].joinGrain).toBeNull();
    expect(flattenScalar([{ ...SCALAR_COL, joinGrain: 'Org Level' }])[0].joinGrain)
      .toBe('Org Level');
  });
});

describe('flattenConditional', () => {
  it('produces one row per branch', () => {
    const rows = flattenConditional([CONDITIONAL_COL]);
    expect(rows).toHaveLength(2);
    expect(rows.map((r) => r.condition)).toEqual([
      'ELSE (<> 10002)',
      '10002 (Digital Catalog)',
    ]);
  });

  it('carries the description on the first branch row only', () => {
    // Exactly what the workbook sheet does: the description is a property of
    // the gold column, not of each branch, and repeating it reads as though
    // the branches had different descriptions.
    const rows = flattenConditional([CONDITIONAL_COL]);
    expect(rows[0].businessDescription).toBe('ISBN-13.');
    expect(rows[0].isFirstOfGroup).toBe(true);
    expect(rows[1].businessDescription).toBe('');
    expect(rows[1].isFirstOfGroup).toBe(false);
  });

  it('renders the discriminator as Table.Column', () => {
    const rows = flattenConditional([CONDITIONAL_COL]);
    expect(rows[0].discriminator).toBe('EGO_MTL_SY_ITEMS_EXT_B.ITEM_CATALOG_GROUP_ID');
  });

  it('carries each branch its own source, filter and fallback', () => {
    const rows = flattenConditional([CONDITIONAL_COL]);
    expect(rows[0].source.column).toBe('C_EXT_ATTR8');
    expect(rows[0].source.filter).toBe('ATTR_GROUP_ID = 741');
    expect(rows[0].fallback).toBe('returnNull');
    expect(rows[1].source.column).toBe('C_EXT_ATTR4');
    expect(rows[1].fallback).toBe('fallbackToElse');
  });

  it('gives each branch row a distinct key', () => {
    // Two branches of one column share a gold column name, so the name alone
    // is not a React key — duplicate keys silently drop rows.
    const rows = flattenConditional([CONDITIONAL_COL]);
    expect(new Set(rows.map((r) => r.key)).size).toBe(2);
  });

  it('yields no rows for a column with an empty branches array', () => {
    // Review Focus 4. One row of undefineds is worse than none — it asserts
    // a branch exists.
    expect(flattenConditional([{ ...CONDITIONAL_COL, branches: [] }])).toEqual([]);
    expect(flattenConditional([{ ...CONDITIONAL_COL, branches: undefined }])).toEqual([]);
  });
});

describe('flattenNested', () => {
  it('produces one row per field', () => {
    const rows = flattenNested([NESTED_COL]);
    expect(rows).toHaveLength(2);
    expect(rows.map((r) => r.fieldName)).toEqual(['componentItemId', 'quantity']);
    expect(rows.map((r) => r.fieldType)).toEqual(['integer', 'number']);
  });

  it('carries the description on the first field row only', () => {
    const rows = flattenNested([NESTED_COL]);
    expect(rows[0].businessDescription).toBe('Bill-of-material component lines.');
    expect(rows[1].businessDescription).toBe('');
  });

  it('surfaces the sort key and the join key', () => {
    const rows = flattenNested([NESTED_COL]);
    expect(rows[0].sortKey).toBe(true);
    expect(rows[1].sortKey).toBe(false);
    expect(rows[0].joinKey).toBe('BILL_SEQUENCE_ID -> item');
  });

  it("falls back to the column's shared filter when a field declares none", () => {
    // The contract puts `filter` on the nested COLUMN, shared by all its
    // fields; the workbook has one Source Filter cell per field row. Showing
    // the shared filter is what makes those agree.
    const rows = flattenNested([NESTED_COL]);
    expect(rows[0].source.filter).toBe('EXPLOSION_TYPE = 1');
  });

  it('prefers a field-level filter over the shared one', () => {
    const col = {
      ...NESTED_COL,
      fields: [
        {
          ...NESTED_COL.fields[0],
          source: { table: 'T', column: 'C', filter: 'ATTR_GROUP_ID = 798' },
        },
      ],
    };
    expect(flattenNested([col])[0].source.filter).toBe('ATTR_GROUP_ID = 798');
  });

  it('handles a field declaring no source at all', () => {
    // The contract allows `source: null` — "declared with no Silver source
    // yet; the column is rendered NULL until a source registers."
    const col = { ...NESTED_COL, fields: [{ name: 'f', type: 'string', sortKey: true, source: null }] };
    const [row] = flattenNested([col]);
    expect(row.source.table).toBeNull();
    expect(row.source.column).toBeNull();
  });

  it('yields no rows for a column with an empty fields array', () => {
    // Review Focus 4, the nested half.
    expect(flattenNested([{ ...NESTED_COL, fields: [] }])).toEqual([]);
    expect(flattenNested([{ ...NESTED_COL, fields: undefined }])).toEqual([]);
  });
});

import {
  resolveJoin,
  resolveScope,
  resolveTransform,
  resolveSilverTable,
  resolvePick,
  parseMappingBody,
  rowMatchesQuery,
  filterRows,
} from './mapping-body';

const WITH_DEFS = {
  ...MAPPING_OBJ,
  drivingSilverTable: 'uif_echo_{env}_silver_tep.mtl_system_items_b',
  silverTables: {
    QP_LIST_HEADERS_B: 'uif_echo_{env}_silver_tep.qp_list_headers_b',
  },
  rowGroups: {
    ukOrgs: {
      table: 'MTL_PARAMETERS',
      column: 'ORGANIZATION_CODE',
      op: 'startsWith',
      values: ['01'],
      description: 'UK orgs (GBPE).',
    },
    nonUkOrgs: { complementOf: 'ukOrgs', description: 'Every org NOT in ukOrgs.' },
  },
  transforms: {
    roundTo2dp: { steps: [{ op: 'round', precision: 2 }] },
    pearsonImageUrl: {
      description: 'Normalize Pearson cover-image URLs.',
      cases: [
        { when: { op: 'startsWith', values: ['/A'] },
          then: { op: 'prepend', text: 'https://images.pearson.com/i/79475d28' } },
      ],
      else: 'keep',
    },
    custom: { sqlExpr: "regexp_replace({x}, 'a', 'b')" },
  },
};

describe('resolveJoin', () => {
  it('says driving when the source IS the driving table', () => {
    const j = resolveJoin(
      { table: 'MTL_SYSTEM_ITEMS_B', column: 'C', filter: null, join: null },
      'MTL_SYSTEM_ITEMS_B',
    );
    expect(j.kind).toBe('driving');
  });

  it('renders a single ON pair', () => {
    const j = resolveJoin(
      {
        table: 'EGO_MTL_SY_ITEMS_EXT_B',
        column: 'C_EXT_ATTR8',
        filter: 'ATTR_GROUP_ID = 741',
        join: {
          type: 'left',
          on: [{
            leftTable: 'MTL_SYSTEM_ITEMS_B',
            leftColumn: 'INVENTORY_ITEM_ID',
            rightColumn: 'INVENTORY_ITEM_ID',
          }],
        },
      },
      'MTL_SYSTEM_ITEMS_B',
    );
    expect(j.kind).toBe('join');
    expect(j.type).toBe('left');
    expect(j.table).toBe('EGO_MTL_SY_ITEMS_EXT_B');
    expect(j.on).toEqual([{
      left: 'MTL_SYSTEM_ITEMS_B.INVENTORY_ITEM_ID',
      right: 'EGO_MTL_SY_ITEMS_EXT_B.INVENTORY_ITEM_ID',
    }]);
    expect(j.filter).toBe('ATTR_GROUP_ID = 741');
  });

  it('renders multiple ON pairs in declared order', () => {
    const j = resolveJoin(
      {
        table: 'T', column: 'C', filter: null,
        join: { on: [
          { leftTable: 'D', leftColumn: 'A', rightColumn: 'A2' },
          { leftTable: 'D', leftColumn: 'B', rightColumn: 'B2' },
        ] },
      },
      'D',
    );
    expect(j.on).toEqual([
      { left: 'D.A', right: 'T.A2' },
      { left: 'D.B', right: 'T.B2' },
    ]);
  });

  it('defaults the join type to left, and honours inner', () => {
    const on = [{ leftTable: 'D', leftColumn: 'A', rightColumn: 'A' }];
    expect(resolveJoin({ table: 'T', column: 'C', filter: null, join: { on } }, 'D').type)
      .toBe('left');
    expect(resolveJoin({ table: 'T', column: 'C', filter: null, join: { type: 'inner', on } }, 'D').type)
      .toBe('inner');
  });

  it('keeps a chained leftTable that is another source, not the driving table', () => {
    // Chains compose: A -> B -> C. The ON clause must name the table the
    // contract names, not be rewritten to the driving table.
    const j = resolveJoin(
      { table: 'C_TBL', column: 'X', filter: null,
        join: { on: [{ leftTable: 'B_TBL', leftColumn: 'K', rightColumn: 'K' }] } },
      'A_TBL',
    );
    expect(j.on[0].left).toBe('B_TBL.K');
  });

  it('says none when there is no source table at all', () => {
    expect(resolveJoin({ table: null, column: null, filter: null, join: null }, 'D').kind)
      .toBe('none');
  });

  it('says join with no ON pairs when a non-driving source declares none', () => {
    // The contract REQUIRES a join for every non-driving source. A file
    // missing one is a CI finding; the view shows the gap rather than
    // inventing a key.
    const j = resolveJoin({ table: 'T', column: 'C', filter: null, join: null }, 'D');
    expect(j.kind).toBe('join');
    expect(j.on).toEqual([]);
  });
});

describe('resolveScope', () => {
  it('resolves a named row group to its predicate', () => {
    const s = resolveScope({ group: 'ukOrgs' }, WITH_DEFS);
    expect(s.kind).toBe('group');
    expect(s.label).toBe('ukOrgs');
    // The contract STATES this rendering: gold-attribute-mapping.schema.json,
    // $defs.branchScope.properties.op — "startsWith renders LIKE 'value%'".
    expect(s.predicate).toBe("MTL_PARAMETERS.ORGANIZATION_CODE LIKE '01%'");
  });

  it('resolves a complement group through its base', () => {
    const s = resolveScope({ group: 'nonUkOrgs' }, WITH_DEFS);
    expect(s.kind).toBe('group');
    expect(s.predicate).toContain('NOT');
    expect(s.predicate).toContain('ukOrgs');
  });

  it('marks a group reference that does not resolve', () => {
    // The view reports what the artifact declares. Rejecting this is CI's job.
    const s = resolveScope({ group: 'ghost' }, WITH_DEFS);
    expect(s.kind).toBe('unresolved');
    expect(s.label).toBe('ghost');
  });

  it('renders an inline predicate', () => {
    const s = resolveScope(
      { table: 'MTL_PARAMETERS', column: 'ORGANIZATION_CODE', op: 'in', values: ['01B', '01C'], label: 'two orgs' },
      WITH_DEFS,
    );
    expect(s.kind).toBe('inline');
    expect(s.label).toBe('two orgs');
    expect(s.predicate).toBe("MTL_PARAMETERS.ORGANIZATION_CODE IN ('01B', '01C')");
  });

  it('renders op else as the complement of its siblings', () => {
    const s = resolveScope({ op: 'else', column: 'x' }, WITH_DEFS);
    expect(s.kind).toBe('else');
    expect(s.label).toContain('no sibling');
  });

  it('says every row when no scope is declared', () => {
    const s = resolveScope(null, WITH_DEFS);
    expect(s.kind).toBe('all');
    expect(s.label).toContain('every row');
  });
});

describe('resolveTransform', () => {
  it('says none for a straight copy', () => {
    expect(resolveTransform(null, WITH_DEFS).kind).toBe('none');
  });

  it('resolves a rule reference to its steps', () => {
    const t = resolveTransform({ rule: 'roundTo2dp' }, WITH_DEFS);
    expect(t.kind).toBe('rule');
    expect(t.name).toBe('roundTo2dp');
    expect(t.steps).toEqual([{ op: 'round', detail: 'precision 2' }]);
  });

  it('resolves a rule reference to its cases and fallthrough', () => {
    const t = resolveTransform({ rule: 'pearsonImageUrl' }, WITH_DEFS);
    expect(t.description).toBe('Normalize Pearson cover-image URLs.');
    expect(t.cases).toEqual([{
      when: "startsWith '/A'",
      then: "prepend 'https://images.pearson.com/i/79475d28'",
    }]);
    expect(t.fallthrough).toBe('keep');
  });

  it('defaults the cases fallthrough to keep when unstated', () => {
    const t = resolveTransform(
      { cases: [{ when: { op: 'isNull' }, then: { op: 'literal', text: 'x' } }] },
      WITH_DEFS,
    );
    expect(t.fallthrough).toBe('keep');
  });

  it('marks a rule reference that does not resolve', () => {
    const t = resolveTransform({ rule: 'ghost' }, WITH_DEFS);
    expect(t.kind).toBe('unresolved');
    expect(t.name).toBe('ghost');
  });

  it('resolves an inline transform definition', () => {
    const t = resolveTransform({ steps: [{ op: 'cast', to: 'DECIMAL(38,2)' }] }, WITH_DEFS);
    expect(t.kind).toBe('inline');
    expect(t.steps).toEqual([{ op: 'cast', detail: 'DECIMAL(38,2)' }]);
  });

  it('flags a sqlExpr for review', () => {
    // CI flags every use for engineering review, so the row must show it.
    const t = resolveTransform({ rule: 'custom' }, WITH_DEFS);
    expect(t.sqlExpr).toBe("regexp_replace({x}, 'a', 'b')");
    expect(t.reviewFlagged).toBe(true);
  });

  it('treats a prose string as an unresolved rule name', () => {
    // Prose transforms are DEPRECATED (UIFPE-105) but still accepted while
    // old files migrate. Showing the text is better than showing nothing.
    const t = resolveTransform('round to 2dp', WITH_DEFS);
    expect(t.kind).toBe('unresolved');
    expect(t.name).toBe('round to 2dp');
  });
});

describe('resolveSilverTable', () => {
  it('resolves the driving table to drivingSilverTable', () => {
    expect(resolveSilverTable('MTL_SYSTEM_ITEMS_B', WITH_DEFS))
      .toBe('uif_echo_{env}_silver_tep.mtl_system_items_b');
  });

  it('resolves a lookup table from silverTables', () => {
    expect(resolveSilverTable('QP_LIST_HEADERS_B', WITH_DEFS))
      .toBe('uif_echo_{env}_silver_tep.qp_list_headers_b');
  });

  it('returns null for a table with no declared Silver name', () => {
    // Product's mapping declares neither, so this is the common case.
    expect(resolveSilverTable('EGO_MTL_SY_ITEMS_EXT_B', WITH_DEFS)).toBeNull();
    expect(resolveSilverTable('ANY', MAPPING_OBJ)).toBeNull();
    expect(resolveSilverTable(null, WITH_DEFS)).toBeNull();
  });
});

describe('resolvePick', () => {
  it('reads partitionBy and the ordered orderBy expressions', () => {
    const p = resolvePick({
      partitionBy: ['listHeaderId'],
      orderBy: [
        { expr: 'CASE WHEN END_DATE IS NULL THEN 0 ELSE 1 END' },
        { expr: 'START_DATE', direction: 'desc' },
      ],
    });
    expect(p?.partitionBy).toEqual(['listHeaderId']);
    expect(p?.orderBy).toEqual([
      { expr: 'CASE WHEN END_DATE IS NULL THEN 0 ELSE 1 END', direction: 'asc' },
      { expr: 'START_DATE', direction: 'desc' },
    ]);
  });

  it('returns null when no pick is declared', () => {
    expect(resolvePick(null)).toBeNull();
    expect(resolvePick(undefined)).toBeNull();
  });
});

describe('parseMappingBody', () => {
  it('says it is not a mapping for a schema body', () => {
    const parsed = parseMappingBody(JSON.stringify({ type: 'record', name: 'X', fields: [] }));
    expect(parsed.isMapping).toBe(false);
    expect(parsed.scalar).toEqual([]);
  });

  it('says it is not a mapping for unparseable text', () => {
    expect(parseMappingBody('syntax = "proto3";').isMapping).toBe(false);
    expect(parseMappingBody('').isMapping).toBe(false);
  });

  it('parses a mapping into facts, sheets and reference', () => {
    const parsed = parseMappingBody(JSON.stringify(WITH_DEFS));
    expect(parsed.isMapping).toBe(true);
    expect(parsed.facts.table).toBe('gold_product.product');
    expect(parsed.scalar).toHaveLength(1);
    expect(parsed.reference.rowGroups.map((g) => g.name)).toEqual(['ukOrgs', 'nonUkOrgs']);
    expect(parsed.reference.transforms.map((t) => t.name))
      .toEqual(['roundTo2dp', 'pearsonImageUrl', 'custom']);
    expect(parsed.reference.entityKey).toHaveLength(2);
  });

  it('lists the driving Silver table alongside the lookups, marked', () => {
    const parsed = parseMappingBody(JSON.stringify(WITH_DEFS));
    const driving = parsed.reference.silverTables.find((t) => t.driving);
    expect(driving?.name).toBe('MTL_SYSTEM_ITEMS_B');
    expect(parsed.reference.silverTables).toHaveLength(2);
  });

  it('leaves every reference collection empty for a mapping declaring none', () => {
    // Product's real mapping: no silverTables, no drivingSilverTable. Each
    // section hides when empty rather than rendering an empty accordion.
    const parsed = parseMappingBody(JSON.stringify(MAPPING_OBJ));
    expect(parsed.reference.silverTables).toEqual([]);
    expect(parsed.reference.rowGroups).toEqual([]);
    expect(parsed.reference.transforms).toEqual([]);
  });

  it('counts how many columns reference each named transform', () => {
    const body = {
      ...WITH_DEFS,
      columns: [
        { ...SCALAR_COL, goldColumnName: 'a', transform: { rule: 'roundTo2dp' } },
        { ...SCALAR_COL, goldColumnName: 'b', transform: { rule: 'roundTo2dp' } },
        { ...SCALAR_COL, goldColumnName: 'c', transform: null },
      ],
    };
    const parsed = parseMappingBody(JSON.stringify(body));
    const entry = parsed.reference.transforms.find((t) => t.name === 'roundTo2dp');
    expect(entry?.usedBy).toBe(2);
  });

  it('flattens a wide body across all three kinds', () => {
    // The case a hand-built two-column body cannot catch.
    const body = {
      ...WITH_DEFS,
      columns: [
        ...Array.from({ length: 31 }, (_, i) => ({ ...SCALAR_COL, goldColumnName: `s${i}` })),
        ...Array.from({ length: 33 }, (_, i) => ({ ...CONDITIONAL_COL, goldColumnName: `c${i}` })),
        ...Array.from({ length: 10 }, (_, i) => ({ ...NESTED_COL, goldColumnName: `n${i}` })),
      ],
    };
    const parsed = parseMappingBody(JSON.stringify(body));
    expect(parsed.scalar).toHaveLength(31);
    expect(parsed.conditional).toHaveLength(66); // 33 columns x 2 branches
    expect(parsed.nested).toHaveLength(20); // 10 columns x 2 fields
  });
});

describe('rowMatchesQuery', () => {
  const row = flattenScalar([SCALAR_COL])[0];

  it('matches the gold column, description, source table and column', () => {
    expect(rowMatchesQuery(row, 'itemStatus')).toBe(true);
    expect(rowMatchesQuery(row, 'lifecycle')).toBe(true); // description
    expect(rowMatchesQuery(row, 'MTL_SYSTEM')).toBe(true);
    expect(rowMatchesQuery(row, 'STATUS_CODE')).toBe(true);
  });

  it('is case-insensitive, like the rest of the page search', () => {
    expect(rowMatchesQuery(row, 'ITEMSTATUS')).toBe(true);
  });

  it('matches the notes', () => {
    expect(rowMatchesQuery(row, 'A note')).toBe(true);
  });

  it('does not match an unrelated query', () => {
    expect(rowMatchesQuery(row, 'zzzz')).toBe(false);
  });

  it('matches everything on an empty query', () => {
    expect(rowMatchesQuery(row, '')).toBe(true);
  });
});


// --- Corrections found reviewing the parser against the contract ------------

describe('renderPredicate against the contract', () => {
  it("renders startsWith as LIKE 'value%', which the contract states", () => {
    const s = resolveScope({ group: 'ukOrgs' }, WITH_DEFS);
    expect(s.predicate).toBe("MTL_PARAMETERS.ORGANIZATION_CODE LIKE '01%'");
    // The op NAME must not reach the panel: a reader comparing this to the
    // compiled SQL would see two different things.
    expect(s.predicate).not.toContain('startsWith');
  });

  it('renders equals as = and in as IN (...)', () => {
    expect(resolveScope({ column: 'C', op: 'equals', values: ['x'] }, WITH_DEFS).predicate)
      .toBe("C = 'x'");
    expect(resolveScope({ column: 'C', op: 'in', values: ['a', 'b'] }, WITH_DEFS).predicate)
      .toBe("C IN ('a', 'b')");
  });

  it('ORs a multi-value startsWith rather than concatenating the values', () => {
    expect(resolveScope({ column: 'C', op: 'startsWith', values: ['01', '02'] }, WITH_DEFS).predicate)
      .toBe("C LIKE '01%' OR C LIKE '02%'");
  });
});

describe('filterRows keeps a gold column whole', () => {
  const body = {
    ...WITH_DEFS,
    // The second column needs its OWN description and branch conditions, or
    // both groups match every query and the test proves nothing.
    columns: [
      CONDITIONAL_COL,
      {
        ...CONDITIONAL_COL,
        goldColumnName: 'other',
        businessDescription: 'Something else entirely.',
        branches: CONDITIONAL_COL.branches.map((b, i) => ({
          ...b,
          condition: `other-branch-${i}`,
        })),
      },
    ],
  };
  const parsed = parseMappingBody(JSON.stringify(body));

  it('returns every branch of a column matched only by its description', () => {
    // The description is on the first branch row only, so a bare
    // rows.filter(rowMatchesQuery) answered "show me this column" by
    // returning one branch of it and hiding the other.
    const rows = filterRows(parsed.conditional, 'ISBN-13.');
    expect(rows).toHaveLength(2);
    expect(rows.map((r) => r.condition)).toEqual([
      'ELSE (<> 10002)',
      '10002 (Digital Catalog)',
    ]);
  });

  it('still excludes a column that does not match at all', () => {
    expect(filterRows(parsed.conditional, 'zzzz')).toEqual([]);
  });

  it('returns every row on an empty query', () => {
    expect(filterRows(parsed.conditional, '')).toHaveLength(4);
  });

  it('excludes the other column entirely', () => {
    const rows = filterRows(parsed.conditional, 'ISBN-13.');
    expect(rows.every((r) => r.goldColumnName === 'isbn13')).toBe(true);
    expect(rows.some((r) => r.goldColumnName === 'other')).toBe(false);
  });

  it('matches a branch condition, and still returns the whole column', () => {
    // The query hits ONE branch row; the column's other branch has no text
    // matching it at all, and must come back anyway.
    const rows = filterRows(parsed.conditional, '10002 (Digital Catalog)');
    expect(rows).toHaveLength(2);
    expect(rows.every((r) => r.goldColumnName === 'isbn13')).toBe(true);
  });
});

describe('parseMappingBody does not share arrays between parses', () => {
  it('gives each non-mapping parse its own sheets', () => {
    const a = parseMappingBody('not json');
    const b = parseMappingBody('not json');
    expect(a.scalar).not.toBe(b.scalar);
    expect(a.reference.rowGroups).not.toBe(b.reference.rowGroups);
  });
});
