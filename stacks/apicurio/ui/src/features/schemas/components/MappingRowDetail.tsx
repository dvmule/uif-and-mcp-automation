import type { ReactNode } from 'react';
import {
  resolveJoin, resolveScope, resolveTransform, resolveSilverTable, resolvePick,
} from '../mapping-body';
import type { MappingRow, ConditionalRow, NestedRow, ResolvedTransform } from '../mapping-body';
import { Badge } from '@/components/ui/Badge';

/**
 * One row's expansion: the five things the registered mapping carries that
 * the *-gold-attribute-mapping-template.xlsx workbook has no column for.
 *
 * The table keeps the workbook's 13–14 columns; these live here instead, so
 * nothing is lost and nothing is crammed in. Design spec §4.3.
 *
 * Everything is rendered as DECLARED. An unresolved row group or transform
 * rule shows an unresolved marker rather than an error: the registry reports
 * what the artifact says, and rejecting a dangling reference is CI's job
 * (domains/product/qe/test_intake_hygiene.py).
 */

function Row({ label, testId, children }: {
  label: string; testId: string; children: ReactNode;
}) {
  return (
    <div className="grid" style={{ gridTemplateColumns: '92px minmax(0,1fr)', gap: 12 }}
         data-testid={testId}>
      <span className="text-[11px] font-semibold text-accent" style={{ paddingTop: 2 }}>
        {label}
      </span>
      <div className="min-w-0 font-mono text-xs text-ink">{children}</div>
    </div>
  );
}

function TransformBody({ t }: { t: ResolvedTransform }) {
  return (
    <div className="flex flex-col" style={{ gap: 4 }}>
      <span className="flex items-center gap-2">
        {t.name ? <Badge tone="accent">{t.name}</Badge> : <Badge tone="neutral">inline</Badge>}
        {t.kind === 'unresolved' && <Badge tone="warn">unresolved</Badge>}
        {/* CI flags every sqlExpr for engineering review, so say it here. */}
        {t.reviewFlagged && <Badge tone="warn">flagged for review</Badge>}
        {t.fallthrough && <span className="text-ink-mute">else {t.fallthrough}</span>}
      </span>
      {t.description && <span className="font-sans text-ink-soft">{t.description}</span>}
      {t.steps.map((s, i) => (
        <span key={`${s.op}-${i}`}>{s.op}{s.detail ? ` ${s.detail}` : ''}</span>
      ))}
      {t.cases.map((c, i) => (
        <span key={`${c.when}-${i}`}>{c.when} → {c.then}</span>
      ))}
      {t.sqlExpr && (
        <pre className="whitespace-pre-wrap break-all text-ink">{t.sqlExpr}</pre>
      )}
    </div>
  );
}

export interface MappingRowDetailProps {
  row: MappingRow;
  /** The parsed mapping body — the named definitions are resolved from it. */
  body: Record<string, unknown>;
}

export function MappingRowDetail({ row, body }: MappingRowDetailProps) {
  const drivingTable = typeof body.drivingTable === 'string' ? body.drivingTable : null;
  const join = resolveJoin(row.source, drivingTable);
  const silver = resolveSilverTable(row.source.table, body);
  const transform = resolveTransform(row.transform, body);
  const derivation = row.derivation as Record<string, unknown> | null;
  const pick = row.kind === 'nested' ? resolvePick((row as NestedRow).pick) : null;
  const scope = row.kind === 'conditional'
    ? resolveScope((row as ConditionalRow).scope, body)
    : null;

  return (
    <div className="flex flex-col" style={{ gap: 8 }}>
      <Row label="JOIN" testId="detail-join">
        {join.kind === 'driving' ? (
          <span className="text-ink-soft">
            {join.table} is the driving table — no join.
            {join.filter ? ` WHERE ${join.filter}` : ''}
          </span>
        ) : join.kind === 'none' ? (
          <span className="text-ink-mute">No source declared.</span>
        ) : (
          <div className="flex flex-col">
            <span>{join.type.toUpperCase()} JOIN {join.table}</span>
            {join.on.length === 0 ? (
              // The contract REQUIRES a join for every non-driving source. A
              // file missing one is a CI finding; showing the gap is honest,
              // inventing a key is not.
              <span className="text-signal-warn">
                declares no ON keys — the contract requires them for a non-driving source
              </span>
            ) : (
              join.on.map((p, i) => (
                <span key={p.left}>{i === 0 ? '  ON ' : ' AND '}{p.left} = {p.right}</span>
              ))
            )}
            {join.filter && <span>{`WHERE ${join.filter}`}</span>}
          </div>
        )}
      </Row>

      {silver && (
        <Row label="SILVER" testId="detail-silver">
          {/* `{env}` is left templated, as the artifact declares it — the
            * registry does not know which environment you are reading for. */}
          {silver}
        </Row>
      )}

      {scope && (
        <Row label="SCOPE" testId="detail-scope">
          <div className="flex flex-col">
            <span className="flex items-center gap-2">
              {scope.label}
              {scope.kind === 'unresolved' && <Badge tone="warn">unresolved</Badge>}
            </span>
            {scope.predicate && <span className="text-ink-soft">{scope.predicate}</span>}
          </div>
        </Row>
      )}

      {transform.kind !== 'none' && (
        <Row label="XFORM" testId="detail-xform"><TransformBody t={transform} /></Row>
      )}

      {derivation && (
        <Row label="DERIVATION" testId="detail-derivation">
          <div className="flex flex-col" style={{ gap: 4 }}>
            {/* ADR-0008: the query is the contract. The compiler wraps it as a
              * CTE and nothing else, so the SQL shown here is the SQL that
              * runs. */}
            <pre className="whitespace-pre-wrap break-all">{String(derivation.sql ?? '')}</pre>
            {Array.isArray(derivation.keyedBy) && (
              <span className="text-ink-soft">
                keyed by{' '}
                {derivation.keyedBy
                  .map((k) => {
                    const pair = k as Record<string, unknown>;
                    return `${String(pair.queryColumn)} → ${String(pair.entityColumn)}`;
                  })
                  .join(', ')}
              </span>
            )}
            {derivation.reads !== undefined && derivation.reads !== null && (
              <span className="text-ink-mute">
                {/* Computed by the validator from the parsed query and
                  * re-checked in CI — never typed by hand. */}
                reads{' '}
                {Object.entries(derivation.reads as Record<string, unknown>)
                  .map(([alias, cols]) =>
                    `${alias} (${(Array.isArray(cols) ? cols : []).join(', ')})`)
                  .join(' · ')}
              </span>
            )}
          </div>
        </Row>
      )}

      {pick && (
        <Row label="PICK" testId="detail-pick">
          <div className="flex flex-col">
            <span>one row per {pick.partitionBy.join(', ')}</span>
            {pick.orderBy.map((o, i) => (
              <span key={`${o.expr}-${i}`} className="text-ink-soft">
                {i === 0 ? 'ordered by ' : '     then '}{o.expr} {o.direction}
              </span>
            ))}
          </div>
        </Row>
      )}
    </div>
  );
}
