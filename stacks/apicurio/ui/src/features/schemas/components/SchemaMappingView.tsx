import { useState, useMemo, type ReactNode } from 'react';
import { ChevronDown, ChevronRight, Download, FileSpreadsheet } from 'lucide-react';
import { Badge } from '@/components/ui/Badge';
import { cn } from '@/lib/utils';
import { downloadBlob } from '../utils';
import { filterRows } from '../mapping-body';
import { SHEET_HEADERS, toCsv } from '../mapping-csv';
import type {
  ParsedMapping, MappingRow, ScalarRow, ConditionalRow, NestedRow,
} from '../mapping-body';
import {
  MAPPING_KIND_LABELS, MAPPING_KIND_ORDER,
} from '../hooks/use-schema-url-state';
import type { MappingKindView } from '../hooks/use-schema-url-state';
import { Highlight } from './SchemaFieldsView';
import { MappingRowDetail } from './MappingRowDetail';
import { MappingReferenceSections } from './MappingReferenceSections';

/**
 * The Mapping tab: a gold attribute mapping read as the workbook people
 * already read it.
 *
 * Three sheets, one per column kind, carrying the
 * *-gold-attribute-mapping-template.xlsx columns in the workbook's own order.
 * That is not incidental — the three kinds carry genuinely different
 * information, which is why the workbook splits them, and it is already the
 * shape in the readers' heads.
 *
 * What the registered JSON carries and the workbook has no column for —
 * structured joins, scopes, transforms, derivation SQL, picks — lives in the
 * per-row expansion, and the named shared definitions also get their own
 * catalogs below. Design spec §2.2 and §4.4.
 *
 * Search FILTERS rather than only tinting, following the precedent
 * SchemaFieldsView documents: highlighting alone was useless at 63 rows, and
 * product's mapping flattens to more than that.
 */

/*
 */

/**
 * Grid templates, one per sheet. First track is the chevron.
 *
 * FIXED widths, and deliberately no `fr` anywhere. An `fr` track inside the
 * `min-width: max-content` scroller below resolves to its own max-content
 * size, not to a share of the container — a business description of 200
 * characters became a 2,474px column, and the scalar sheet measured 33,645px
 * wide in a 1,399px viewport. Every column after the first sat thousands of
 * pixels off-screen. jsdom has no layout, so the whole component suite passed
 * over it; it took loading the real 74-column artifact in a browser to see.
 *
 * The widths sum to a little over a laptop viewport on purpose: the tail
 * scrolls, which is the design, while the columns that identify a row stay put
 * (see GOLD_COL_W and the sticky cells below).
 */
const CHEVRON_W = 42;
/**
 * Where the pinned gold column sits: past the chevron track and the row's own
 * left padding, so it lands exactly on the column it labels.
 */
const GOLD_COL_W = 180;

const SHEET_COLS: Record<MappingKindView, string> = {
  scalar:
    `${CHEVRON_W}px ${GOLD_COL_W}px 260px 90px 180px 190px 160px 130px ` +
    '60px 70px 55px 75px 200px 100px',
  conditional:
    `${CHEVRON_W}px ${GOLD_COL_W}px 240px 90px 210px 190px 180px 190px 150px 130px ` +
    '130px 60px 70px 55px 180px 100px',
  nested:
    `${CHEVRON_W}px ${GOLD_COL_W}px 240px 160px 90px 180px 190px 180px 150px ` +
    '130px 80px 60px 70px 55px 180px 100px',
};

/** The Code and Fields views' pane height. All three sit in the same slot. */
const PANE_MAX_H = 570;
const ROW_PAD_X = 14;

/**
 * Left offset of the pinned gold column: past the chevron track and the grid
 * gap after it. The chevron track ABSORBS the row's left padding (which is why
 * the row has none on that side), so both pinned cells together cover the
 * gutter completely — with the padding on the row instead, the columns
 * scrolling past showed a sliver of themselves to the left of the pinned gold
 * column, and `integer` reading as `eger` looks like a rendering fault.
 */
const STICKY_LEFT = CHEVRON_W + 10;

const JOIN_GRAIN_NOTE =
  'DEPRECATED (UIFPE-104) — superseded by each source’s own structured join. ' +
  'Shown only where a legacy file still declares one; expand a row for the real join.';

/** `Y` / `–`, as the workbook writes it. */
function Flag({ on }: { on: boolean }) {
  return on ? <span className="text-ink">Y</span> : <span className="text-ink-mute">–</span>;
}

function TransformCell({ row, query }: { row: MappingRow; query: string }) {
  const t = row.transform;
  if (t === null || t === undefined) return <span className="text-ink-mute">—</span>;
  if (typeof t === 'string') {
    // Prose. DEPRECATED (UIFPE-105) but still accepted while files migrate.
    return <Badge tone="warn"><Highlight text={t} query={query} /></Badge>;
  }
  const spec = t as Record<string, unknown>;
  // Tinted like every other cell: the search matches a rule NAME, so a hit
  // the reader cannot see is the failure this column was added to fix.
  if (typeof spec.rule === 'string') {
    return <Badge tone="accent"><Highlight text={spec.rule} query={query} /></Badge>;
  }
  // CI flags every sqlExpr for engineering review, so the row says so without
  // the reader having to expand it.
  if (typeof spec.sqlExpr === 'string') return <Badge tone="warn">SQL</Badge>;
  return <Badge tone="neutral">inline</Badge>;
}

/** The cells of one row, in the sheet's header order, minus the chevron. */
function rowCells(row: MappingRow, query: string): ReactNode[] {
  const gold = <Highlight text={row.goldColumnName} query={query} />;
  const desc = <Highlight text={row.businessDescription} query={query} />;
  const table = <Highlight text={row.source.table ?? ''} query={query} />;
  const column = <Highlight text={row.source.column ?? ''} query={query} />;
  const filter = row.source.filter ?? '';
  const notes = <Highlight text={row.notes} query={query} />;
  const grain = row.joinGrain ?? '';
  const chan = [
    <Flag key="e" on={row.channels.event} />,
    <Flag key="m" on={row.channels.message} />,
    <Flag key="a" on={row.channels.api} />,
  ];
  const xform = <TransformCell row={row} query={query} />;

  if (row.kind === 'scalar') {
    const r = row as ScalarRow;
    return [gold, desc, r.dataType, table, column, filter, xform,
      ...chan, <Flag key="n" on={r.nullable} />, notes, grain];
  }
  if (row.kind === 'conditional') {
    const r = row as ConditionalRow;
    return [gold, desc, r.dataType, r.discriminator, r.condition,
      table, column, filter, r.fallback, xform, ...chan, notes, grain];
  }
  const r = row as NestedRow;
  return [gold, desc, <Highlight key="f" text={r.fieldName} query={query} />, r.fieldType,
    table, column, r.joinKey, filter, xform,
    <Flag key="s" on={r.sortKey} />, ...chan, notes, grain];
}



// ── The view ────────────────────────────────────────────────────────────────

function Fact({ label, children, testId }: {
  label: string; children: ReactNode; testId?: string;
}) {
  const empty = children === null || children === undefined || children === '';
  return (
    <div className="min-w-0" data-testid={testId}>
      <dt className="text-xs text-ink-mute">{label}</dt>
      <dd className="mt-0.5 font-mono text-[13px] text-ink break-all">
        {/* An em-dash, not a blank — the same rule the schema facts band
          * follows. A blank reads as "nothing to say here". */}
        {empty ? <span className="text-ink-mute">—</span> : children}
      </dd>
    </div>
  );
}

export interface SchemaMappingViewProps {
  mapping: ParsedMapping;
  kind: MappingKindView;
  onKindChange: (kind: MappingKindView) => void;
  subject: string;
  version: string | null;
}

export function SchemaMappingView({
  mapping, kind, onKindChange, subject, version,
}: SchemaMappingViewProps) {
  const [query, setQuery] = useState('');
  // One row at a time. Two 14-column rows expanded at once pushes the second
  // off-screen; the panel is a reading aid, not a comparison view.
  const [openRow, setOpenRow] = useState<string | null>(null);

  const { facts } = mapping;
  const sheets: Record<MappingKindView, MappingRow[]> = useMemo(
    () => ({ scalar: mapping.scalar, conditional: mapping.conditional, nested: mapping.nested }),
    [mapping],
  );
  // Distinct gold columns per sheet. `groupKey` is one per gold column by
  // construction, which is what makes this the column count rather than the
  // row count the toggle shows.
  const columnCounts = useMemo(
    () => ({
      scalar: new Set(mapping.scalar.map((r) => r.groupKey)).size,
      conditional: new Set(mapping.conditional.map((r) => r.groupKey)).size,
      nested: new Set(mapping.nested.map((r) => r.groupKey)).size,
    }),
    [mapping],
  );

  const allRows = sheets[kind];
  // `filterRows`, not a bare `.filter(rowMatchesQuery)`. The description
  // sits on the first row of a group only, so filtering rows independently
  // answers "show me this column" by returning one branch of it.
  const rows = useMemo(() => filterRows(allRows, query), [allRows, query]);

  const headers = SHEET_HEADERS[kind];
  const cols = SHEET_COLS[kind];

  const handleCsv = () => {
    const name = `${subject}${version ? `-v${version}` : ''}-${kind}.csv`;
    downloadBlob(new Blob([toCsv(headers, rows)], { type: 'text/csv;charset=utf-8' }), name);
  };

  return (
    <div className="flex flex-col" style={{ gap: 12 }}>
      {/* ---------------------------------------------------- facts band */}
      <dl
        data-testid="mapping-facts"
        className="grid bg-paper-card border border-paper-line"
        style={{
          gridTemplateColumns: 'repeat(4, minmax(0,1fr))',
          gap: '16px 24px', borderRadius: 16, padding: '16px 20px',
        }}
      >
        <Fact label="Gold table">{facts.table}</Fact>
        <Fact label="Domain">{facts.domain}</Fact>
        <Fact label="Driving table">{facts.drivingTable}</Fact>
        <Fact label="Registry">
          {facts.registry ? (
            <span className="flex flex-col gap-1">
              <span>{facts.registry.group} / {facts.registry.artifact}</span>
              {facts.registry.compatibility
                ? <Badge tone="neutral">{facts.registry.compatibility}</Badge>
                : <Badge tone="warn">not set</Badge>}
            </span>
          ) : null}
        </Fact>
        <Fact label="Entity key" testId="mapping-entity-key">
          {facts.entityKey.length > 0 ? (
            <span className="flex flex-col">
              {/* Declared order, which IS the grain — never sorted. */}
              {facts.entityKey.map((p) => (
                <span key={p.goldColumn}>{p.goldColumn} ← {p.drivingColumn}</span>
              ))}
            </span>
          ) : null}
        </Fact>
        <Fact label="Driving Silver table">{facts.drivingSilverTable}</Fact>
        <Fact label="Batch">
          {facts.batch ? (
            <span className="flex flex-col">
              <span>{facts.batch.requestedBy} · {facts.batch.targetDate}</span>
              <span className="text-ink-mute">
                backfill {facts.batch.backfillMode}
                {facts.batch.newChannel ? ` · new channel ${facts.batch.newChannel}` : ' · no new channel'}
              </span>
            </span>
          ) : null}
        </Fact>
        <Fact label="Columns">
          {/* COLUMNS, counted by distinct gold column — not rows. A
            * conditional column contributes one row per branch and a nested
            * one row per field, so product's 33 conditional columns flatten
            * to 68 rows. A band labelled "Columns" reporting 68 is simply
            * wrong, and it was. */}
          {`${columnCounts.scalar} scalar · ${columnCounts.conditional} conditional · ${columnCounts.nested} nested`}
        </Fact>
      </dl>

      {/* ------------------------------------- sheet toggle · search · CSV */}
      <div className="flex items-center flex-wrap" style={{ gap: 10 }}>
        <div
          role="tablist"
          aria-label="Which kind of mapping column"
          className="flex shrink-0 bg-paper"
          style={{ borderRadius: 999, padding: 3 }}
        >
          {MAPPING_KIND_ORDER.map((k) => {
            const isActive = k === kind;
            return (
              <button
                key={k}
                type="button"
                role="tab"
                aria-selected={isActive}
                onClick={() => onKindChange(k)}
                className={cn(
                  'flex items-center gap-1.5 text-[13px] whitespace-nowrap',
                  isActive ? 'bg-accent text-white font-semibold'
                           : 'bg-transparent text-ink-soft font-medium',
                )}
                style={{ borderRadius: 999, padding: '7px 15px' }}
              >
                {MAPPING_KIND_LABELS[k]}
                {/* The count on the label, so the size of a sheet is known
                  * before opening it. */}
                <span
                  className={isActive ? 'text-white/80' : 'text-ink-mute'}
                  title={`${sheets[k].length} rows across ${columnCounts[k]} ${k} column${columnCounts[k] === 1 ? '' : 's'}`}
                >
                  {sheets[k].length}
                </span>
              </button>
            );
          })}
        </div>

        <label className="flex items-center border border-light bg-paper-card"
               style={{ height: 34, borderRadius: 4, padding: '0 10px', gap: 6 }}>
          <span className="sr-only">Search this mapping</span>
          <input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search columns, sources, transforms"
            className="bg-transparent text-[13px] text-ink outline-none"
            style={{ width: 240 }}
          />
        </label>

        {query && (
          <span data-testid="mapping-match-count" className="text-[13px] text-ink-mute">
            {rows.length} match{rows.length === 1 ? '' : 'es'}
          </span>
        )}

        <button
          type="button"
          onClick={handleCsv}
          aria-label={`Export the ${MAPPING_KIND_LABELS[kind]} sheet as CSV`}
          title={`Export the ${MAPPING_KIND_LABELS[kind]} sheet as CSV`}
          className="flex items-center gap-1.5 shrink-0 border border-light text-accent text-[13px]"
          style={{ height: 34, borderRadius: 4, padding: '0 10px' }}
        >
          <FileSpreadsheet size={15} />
          CSV
          <Download size={13} />
        </button>
      </div>

      {/* ----------------------------------------------------- the sheet */}
      <div className="bg-paper-card border border-paper-line overflow-hidden"
           style={{ borderRadius: 16 }}>
        {allRows.length === 0 ? (
          <p className="p-5 text-sm text-ink-mute">
            No {MAPPING_KIND_LABELS[kind].toLowerCase()} columns in this mapping.
          </p>
        ) : rows.length === 0 ? (
          // One line for the whole pane, the same sentence the Fields view
          // uses when a query matches nothing.
          <p className="p-5 text-sm text-ink-mute">
            Nothing in this mapping matches “{query}”.
          </p>
        ) : (
          <div className="overflow-auto" style={{ maxHeight: PANE_MAX_H }}>
            <div role="table" aria-label={`${MAPPING_KIND_LABELS[kind]} columns`}
                 style={{ minWidth: 'max-content' }}>
              <div
                role="row"
                className="sticky top-0 z-10 grid items-center border-b border-paper-divider bg-paper-raised text-[11px] font-medium text-accent"
                style={{
                  gridTemplateColumns: cols,
                  gap: 10,
                  padding: `8px ${ROW_PAD_X}px 8px 0`,
                }}
              >
                <span aria-hidden className="sticky z-20 bg-paper-raised" style={{ left: 0 }} />
                {headers.map((h, i) => (
                  <span
                    key={h}
                    role="columnheader"
                    title={h === 'Join Grain' ? JOIN_GRAIN_NOTE : undefined}
                    className={cn(
                      h === 'Join Grain' && 'text-ink-mute',
                      // The gold column pins so the tail can scroll without
                      // taking the row's identity with it. z above the row
                      // cells, below nothing — this cell is the corner where
                      // the sticky row and the sticky column cross.
                      i === 0 && 'sticky z-20 bg-paper-raised',
                    )}
                    style={i === 0 ? { left: STICKY_LEFT } : undefined}
                  >
                    {h}
                  </span>
                ))}
              </div>

              {rows.map((row) => {
                const isOpen = openRow === row.key;
                const Chevron = isOpen ? ChevronDown : ChevronRight;
                return (
                  <div key={row.key}>
                    <div
                      role="row"
                      data-testid={`mapping-row-${row.key}`}
                      className="grid items-start border-b border-paper-divider"
                      style={{
                        gridTemplateColumns: cols,
                        gap: 10,
                        padding: `10px ${ROW_PAD_X}px 10px 0`,
                      }}
                    >
                      <button
                        type="button"
                        aria-expanded={isOpen}
                        aria-label={`${isOpen ? 'Hide' : 'Show'} detail for ${row.goldColumnName}`}
                        onClick={() => setOpenRow(isOpen ? null : row.key)}
                        // Pinned with the gold column: expanding a row you can
                        // see should not require scrolling back to find its
                        // chevron.
                        className="sticky z-10 flex items-center justify-center bg-paper-card text-ink-mute"
                        style={{ left: 0, height: 20, paddingLeft: ROW_PAD_X }}
                      >
                        <Chevron size={14} />
                      </button>
                      {rowCells(row, query).map((cell, i) => (
                        <span
                          key={headers[i]}
                          role="cell"
                          className={cn(
                            'font-mono text-xs break-words',
                            i === 1 ? 'text-ink-soft font-sans' : 'text-ink',
                            // Opaque, not transparent: a sticky cell with no
                            // background lets the scrolling columns slide
                            // visibly underneath it.
                            i === 0 && 'sticky z-10 bg-paper-card',
                          )}
                          style={i === 0 ? { left: STICKY_LEFT } : undefined}
                        >
                          {cell}
                        </span>
                      ))}
                    </div>
                    {isOpen && (
                      <div
                        role="region"
                        aria-label={`Detail for ${row.goldColumnName}`}
                        data-testid={`mapping-detail-${row.key}`}
                        className="border-b border-paper-divider bg-accent-soft"
                        style={{ padding: `12px ${ROW_PAD_X + 20}px` }}
                      >
                        <MappingRowDetail
                          row={row}
                          body={(mapping.value ?? {}) as Record<string, unknown>}
                        />
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        )}
      </div>

      <MappingReferenceSections reference={mapping.reference} />
    </div>
  );
}
