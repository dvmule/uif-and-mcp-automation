/**
 * The workbook's own column headers, and the CSV serialiser behind the
 * Mapping tab's export.
 *
 * They lived in `SchemaMappingView.tsx` beside the component, which
 * `react-refresh/only-export-components` reports: Fast Refresh cannot
 * hot-swap a module exporting both a component and other values, so it
 * remounts the module and loses the open row and the query while you edit.
 * `code-view.ts` answered the same report the same way, and its reasoning
 * holds here — none of this needs React.
 *
 * The headers are the workbook's, verbatim and in its order, because the CSV
 * has to open in Excel next to the file it replaces and read as the same
 * document. That is also why the channel flags are written `Y`/`N` here while
 * the on-screen table shows `Y`/`–`: the dash is legible in a table and
 * meaningless in a spreadsheet cell.
 */
import type { MappingRow, ScalarRow, ConditionalRow, NestedRow } from './mapping-body';
import type { MappingKindView } from './hooks/use-schema-url-state';

export const SHEET_HEADERS: Record<MappingKindView, string[]> = {
  scalar: [
    'Gold Column Name', 'Business Description', 'Data Type',
    'Source Table', 'Source Column', 'Source Filter', 'Transform Logic',
    'Event?', 'Message?', 'API?', 'Nullable?', 'Notes', 'Join Grain',
  ],
  conditional: [
    'Gold Column Name', 'Business Description', 'Data Type',
    'Discriminator', 'Branch Condition',
    'Source Table', 'Source Column', 'Source Filter', 'Fallback',
    // NOT in the workbook's conditional sheet, and added deliberately. Four of
    // product's 33 conditional columns carry a transform, and the search
    // matches a transform's rule name — so without this column a query for
    // `pearsonImageUrl` reported "6 matches" over six rows with nothing on
    // screen explaining any of them. That is the exact failure the Fields view
    // redesign set out to kill.
    'Transform Logic',
    'Event?', 'Message?', 'API?', 'Notes', 'Join Grain',
  ],
  nested: [
    'Gold Column Name', 'Business Description', 'Field Name', 'Field Type',
    'Source Table', 'Source Column', 'Join Key to Item', 'Source Filter',
    // Same reasoning as the conditional sheet: three of product's nested
    // FIELDS carry a transform.
    'Transform Logic',
    'Sort Key?', 'Event?', 'Message?', 'API?', 'Notes', 'Join Grain',
  ],
};

/** Grid templates, one per sheet. First column is the chevron. */


function csvCell(value: string): string {
  return `"${value.replace(/"/g, '""')}"`;
}

function flagText(on: boolean): string {
  return on ? 'Y' : 'N';
}

function transformText(row: MappingRow): string {
  const t = row.transform;
  if (t === null || t === undefined) return '';
  if (typeof t === 'string') return t;
  const spec = t as Record<string, unknown>;
  if (typeof spec.rule === 'string') return String(spec.rule);
  if (typeof spec.sqlExpr === 'string') return String(spec.sqlExpr);
  return 'inline';
}

export function rowToCsvCells(row: MappingRow): string[] {
  const common = [
    row.goldColumnName, row.businessDescription,
  ];
  const src = [row.source.table ?? '', row.source.column ?? '', row.source.filter ?? ''];
  const chan = [flagText(row.channels.event), flagText(row.channels.message), flagText(row.channels.api)];

  if (row.kind === 'scalar') {
    const r = row as ScalarRow;
    return [...common, r.dataType, src[0], src[1], src[2], transformText(row),
      ...chan, flagText(r.nullable), row.notes, row.joinGrain ?? ''];
  }
  if (row.kind === 'conditional') {
    const r = row as ConditionalRow;
    return [...common, r.dataType, r.discriminator, r.condition,
      src[0], src[1], src[2], r.fallback, transformText(row),
      ...chan, row.notes, row.joinGrain ?? ''];
  }
  const r = row as NestedRow;
  return [...common, r.fieldName, r.fieldType, src[0], src[1], r.joinKey, src[2],
    transformText(row), flagText(r.sortKey), ...chan, row.notes, row.joinGrain ?? ''];
}

export function toCsv(headers: string[], rows: MappingRow[]): string {
  return [
    headers.map(csvCell).join(','),
    ...rows.map((r) => rowToCsvCells(r).map(csvCell).join(',')),
  ].join('\n');
}

