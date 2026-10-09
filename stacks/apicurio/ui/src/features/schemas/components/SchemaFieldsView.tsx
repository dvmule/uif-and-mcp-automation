import { useState } from 'react';
import { createPortal } from 'react-dom';
import { ChevronDown, ChevronRight } from 'lucide-react';
import { Badge } from '@/components/ui/Badge';
import { cn } from '@/lib/utils';
import { splitHit } from '../schema-body';
import type { ParsedSchemaBody, MetadataRow, SchemaField } from '../schema-body';
import { FullscreenModal } from './SchemaViewer';

/**
 * Renders the matched run of `text` on the amber highlight, and the rest
 * plainly. One occurrence, matching the design — the field and metadata
 * strings here are short enough that a second hit in the same cell would be
 * the same token twice.
 */
export function Highlight({ text, query }: { text: string; query: string }) {
  const { pre, match, post, hit } = splitHit(text, query);
  if (!hit) return <>{text}</>;
  return (
    <>
      {pre}
      <mark className="bg-amber-tint text-ink" style={{ borderRadius: 2 }}>
        {match}
      </mark>
      {post}
    </>
  );
}

const FIELD_COLS = 'minmax(0,1.6fr) 120px 80px minmax(0,1fr)';
const META_COLS = 'minmax(0,1.6fr) minmax(0,1.6fr)';

/**
 * The height of a section header, set rather than left to the content so the
 * table's column-header row can stick directly beneath it. A measured offset
 * would drift the moment the header's type size changed.
 */
const SECTION_HEADER_H = 42;

/**
 * The table's horizontal padding, shared by the section headers, the column
 * headers and every row — so the section name, `Field`, and the field names
 * all sit on one left edge rather than three.
 */
const ROW_PAD_X = 18;

/**
 * The pane scrolls past this. It is the Code view's pane height, not a new
 * number: the two views sit in the same slot on the same page, and a Fields
 * table that ran to 63 rows pushed the compatibility rail and the tabs off
 * the screen while Code beside it stayed put.
 */
const PANE_MAX_H = 570;

interface SchemaFieldsViewProps {
  body: ParsedSchemaBody;
  query: string;
  /** The page's Expand button. Fullscreen drops the pane's height cap. */
  isFullscreen?: boolean;
  onFullscreenChange?: (open: boolean) => void;
}

/**
 * 4a — the schema body read as a field table, plus the metadata block the
 * registry writes and compliance is read from.
 *
 * The metadata section is shown here, from the body, while the page's facts
 * band above reads the artifact's own properties. They can disagree — an owner
 * team spelled out here and `—` up there — and that disagreement is the
 * finding, not a bug to paper over: the band is what Apicurio's properties
 * carry, this is what the body carries.
 *
 * Both sections collapse, and a query FILTERS them rather than only tinting
 * the rows that match. Highlighting alone was enough for a five-field Avro
 * record and useless on `product.gold-schema`, where a match can be the
 * fortieth of 63 rows and off-screen: the count said "1 match" and the screen
 * showed no sign of it.
 */
export function SchemaFieldsView({
  body,
  query,
  isFullscreen = false,
  onFullscreenChange,
}: SchemaFieldsViewProps) {
  // Open by default: a collapsed section on arrival hides the body from
  // someone who came here to read it.
  const [openFields, setOpenFields] = useState(true);
  const [openMetadata, setOpenMetadata] = useState(true);

  if (!body.parsed) {
    return (
      <p className="p-5 text-sm text-ink-mute">
        This body is not JSON, so it has no fields to tabulate. Read it in Code view.
      </p>
    );
  }

  if (body.fields.length === 0 && body.metadata.length === 0) {
    return (
      <p className="p-5 text-sm text-ink-mute">
        This body parses, but declares no <code className="font-mono">fields</code> or{' '}
        <code className="font-mono">properties</code> and carries no{' '}
        <code className="font-mono">metadata</code> block. Read it in Code view.
      </p>
    );
  }

  // The same two predicates `countFieldViewMatches` counts, so the toolbar's
  // total and the rows on screen can never disagree.
  const fieldHit = (f: SchemaField) => splitHit(f.name, query).hit;
  const metaHit = (m: MetadataRow) => splitHit(m.key, query).hit || splitHit(m.value, query).hit;

  const fields = query ? body.fields.filter(fieldHit) : body.fields;
  const metadata = query ? body.metadata.filter(metaHit) : body.metadata;

  // One line for the whole pane rather than a "no match" line inside each
  // section, which said the same thing twice. Same sentence the code view's
  // match rail uses.
  if (query && fields.length === 0 && metadata.length === 0) {
    return <p className="p-5 text-sm text-ink-mute">Nothing in this body matches “{query}”.</p>;
  }

  const pane = (
    <div
      className="overflow-y-auto overflow-x-hidden"
      // `maxHeight`, so a short body is not padded out to 570px of empty card
      // and a long one scrolls. Fullscreen fills what it is given instead.
      style={isFullscreen ? { height: '100%' } : { maxHeight: PANE_MAX_H }}
    >
      {body.fields.length > 0 && (
        <section>
          <SectionHeader
            open={openFields}
            onToggle={() => setOpenFields((v) => !v)}
            name={body.record.name ?? '(unnamed)'}
            note={[body.record.kind, body.record.namespace].filter(Boolean).join(' · ')}
            noteMono
            shown={fields.length}
            total={body.fields.length}
            filtered={Boolean(query)}
          />

          {openFields && (
            <div role="table" aria-label="Schema fields">
              <div
                role="row"
                className="sticky z-10 grid items-center border-b border-paper-divider bg-paper-raised text-[11px] font-medium text-accent"
                // Directly beneath the section header, which is itself sticky
                // at 0 — so scrolling 63 rows never loses the column names.
                style={{
                  top: SECTION_HEADER_H,
                  gridTemplateColumns: FIELD_COLS,
                  gap: 14,
                  padding: `8px ${ROW_PAD_X}px`,
                }}
              >
                <span role="columnheader">Field</span>
                <span role="columnheader">Type</span>
                <span role="columnheader">Sensitivity</span>
                <span role="columnheader">Path</span>
              </div>

              {fields.map((f) => (
                <div
                  key={f.name || f.path}
                  role="row"
                  className="grid items-center border-b border-paper-divider"
                  style={{ gridTemplateColumns: FIELD_COLS, gap: 14, padding: `11px ${ROW_PAD_X}px` }}
                >
                  <span role="cell" className="font-mono text-xs text-ink truncate">
                    <Highlight text={f.name} query={query} />
                  </span>
                  {/* `title` because the column is 120px and a JSON Schema
                    * union is long — `array<object> | null` truncates, and a
                    * truncated type is a type you cannot act on. */}
                  <span
                    role="cell"
                    title={f.type}
                    className="font-mono text-xs text-ink-mute truncate"
                  >
                    {f.type}
                  </span>
                  <span role="cell">
                    {/* Sensitivity is read off the metadata block's piiFields,
                      * so a field is marked only because the block says so. */}
                    {f.pii ? <Badge tone="warn">PII</Badge> : <span className="text-ink-mute">—</span>}
                  </span>
                  <span role="cell" className="font-mono text-xs text-ink-mute truncate">
                    {f.path}
                  </span>
                </div>
              ))}
            </div>
          )}
        </section>
      )}

      {body.metadata.length > 0 && (
        <section>
          <SectionHeader
            open={openMetadata}
            onToggle={() => setOpenMetadata((v) => !v)}
            name="metadata"
            note="written by the registry · compliance is read from this block"
            raised
            shown={metadata.length}
            total={body.metadata.length}
            filtered={Boolean(query)}
          />

          {openMetadata && (
            <dl>
              {metadata.map((m) => (
                <div
                  key={m.key}
                  className="grid items-baseline border-b border-paper-divider"
                  style={{ gridTemplateColumns: META_COLS, gap: 14, padding: `11px ${ROW_PAD_X}px` }}
                >
                  <dt className="font-mono text-xs text-ink-mute truncate">
                    <Highlight text={m.key} query={query} />
                  </dt>
                  <dd className="font-mono text-xs text-ink break-all">
                    <Highlight text={m.value} query={query} />
                  </dd>
                </div>
              ))}
            </dl>
          )}
        </section>
      )}
    </div>
  );

  // The page's Expand button used to be wired to the code view alone, so in
  // Fields view it was a control that did nothing at all.
  return (
    <>
      {pane}
      {isFullscreen &&
        onFullscreenChange &&
        createPortal(
          <FullscreenModal label="Fields" onClose={() => onFullscreenChange(false)}>
            <div className="flex-1 overflow-hidden">
              <div className="h-full overflow-y-auto overflow-x-hidden">{pane}</div>
            </div>
          </FullscreenModal>,
          document.body,
        )}
    </>
  );
}

/**
 * A section's header, which is also its collapse control and its count.
 *
 * Sticky at the top of the scroller: with two sections and 63 rows, the block
 * you are reading scrolls out of sight long before its rows do.
 */
function SectionHeader({
  open,
  onToggle,
  name,
  note,
  noteMono = false,
  raised = false,
  shown,
  total,
  filtered,
}: {
  open: boolean;
  onToggle: () => void;
  name: string;
  note: string;
  noteMono?: boolean;
  raised?: boolean;
  shown: number;
  total: number;
  filtered: boolean;
}) {
  const Chevron = open ? ChevronDown : ChevronRight;
  return (
    <button
      type="button"
      onClick={onToggle}
      aria-expanded={open}
      className={cn(
        'sticky top-0 z-20 flex w-full items-center border-b border-paper-divider text-left',
        // The two sections carry different grounds in the design, and a sticky
        // header needs an opaque one or the rows show through it.
        raised ? 'bg-paper-raised' : 'bg-paper-card',
      )}
      // The same 18px the table's cells carry, so the section name sits on the
      // one left edge every row and column header below it shares.
      style={{ height: SECTION_HEADER_H, padding: `0 ${ROW_PAD_X}px`, gap: 14 }}
    >
      {/* `items-baseline` on this pair only. It is what sets the name against
        * its note; applied to the whole row it dropped the chevron and the
        * count below the text they sit beside. */}
      <span className="flex min-w-0 items-baseline gap-2">
        <span className="shrink-0 font-mono text-[13px] font-semibold text-ink">{name}</span>
        <span className={cn('truncate text-xs text-ink-mute', noteMono && 'font-mono')}>
          {note}
        </span>
      </span>

      {/* The disclosure sits with the count on the right rather than ahead of
        * the name: a 14px glyph and its gap indented the name 22px past the
        * `Field` column header and every field name under it. */}
      <span className="ml-auto flex shrink-0 items-center gap-2">
        <span className="font-mono text-[11px] text-ink-mute">
          {filtered ? `${shown} of ${total}` : total}
        </span>
        <Chevron size={14} className="text-ink-mute" aria-hidden />
      </span>
    </button>
  );
}
