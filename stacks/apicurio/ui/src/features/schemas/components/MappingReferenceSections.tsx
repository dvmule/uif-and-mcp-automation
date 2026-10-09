import { useState, type ReactNode } from 'react';
import { ChevronDown, ChevronRight } from 'lucide-react';
import { Badge } from '@/components/ui/Badge';
import type { MappingReference } from '../mapping-body';

/**
 * The named shared definitions, each readable whole.
 *
 * These exist because a named definition is a SINGLE source of truth and the
 * row detail resolves it inline: a transform rule used by twelve columns is
 * shown twelve times and nowhere once. The rows resolve it so you never have
 * to leave the row; these hold it so you can read the set. Design spec §4.4.
 *
 * Every section hides when its collection is empty — product's mapping
 * declares no silverTables at all, and an empty accordion is worse than no
 * accordion.
 */

function Section({ name, count, testId, children }: {
  name: string; count: number; testId: string; children: ReactNode;
}) {
  // Collapsed by default: these are a reference, not the thing you came for.
  const [open, setOpen] = useState(false);
  const Chevron = open ? ChevronDown : ChevronRight;
  return (
    <div className="bg-paper-card border border-paper-line overflow-hidden"
         style={{ borderRadius: 12 }}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full items-center gap-2 text-left text-[13px] font-medium text-ink"
        style={{ padding: '10px 14px' }}
      >
        <Chevron size={14} className="text-ink-mute" />
        {name}
        <Badge tone="neutral">{count}</Badge>
      </button>
      {open && (
        <div data-testid={testId} className="border-t border-paper-divider"
             style={{ padding: '10px 14px' }}>
          {children}
        </div>
      )}
    </div>
  );
}

export interface MappingReferenceSectionsProps {
  reference: MappingReference;
}

export function MappingReferenceSections({ reference }: MappingReferenceSectionsProps) {
  const { rowGroups, transforms, silverTables, entityKey } = reference;
  if (
    rowGroups.length === 0 && transforms.length === 0 &&
    silverTables.length === 0 && entityKey.length === 0
  ) {
    return null;
  }

  // A complement follows its base automatically in the contract, so it follows
  // it here too — reading the pair apart is how they drift in someone's head.
  const orderedGroups = [
    ...rowGroups.filter((g) => !g.complementOf),
    ...rowGroups.filter((g) => g.complementOf),
  ];

  return (
    <div className="flex flex-wrap" style={{ gap: 10 }}>
      {rowGroups.length > 0 && (
        <Section name="Row groups" count={rowGroups.length} testId="reference-row-groups">
          <dl className="flex flex-col" style={{ gap: 8 }}>
            {orderedGroups.map((g) => (
              <div key={g.name}>
                <dt className="font-mono text-xs text-accent">{g.name}</dt>
                <dd className="font-mono text-xs text-ink">{g.predicate}</dd>
                {g.description && (
                  <dd className="text-xs text-ink-soft">{g.description}</dd>
                )}
              </div>
            ))}
          </dl>
        </Section>
      )}

      {transforms.length > 0 && (
        <Section name="Transforms" count={transforms.length} testId="reference-transforms">
          <dl className="flex flex-col" style={{ gap: 8 }}>
            {transforms.map((t) => (
              <div key={t.name}>
                <dt className="flex items-center gap-2 font-mono text-xs text-accent">
                  {t.name}
                  {/* The count is what earns this catalog its place. */}
                  <span className="text-ink-mute">
                    {t.usedBy} column{t.usedBy === 1 ? '' : 's'}
                  </span>
                  {t.resolved.reviewFlagged && <Badge tone="warn">review</Badge>}
                </dt>
                {t.resolved.description && (
                  <dd className="text-xs text-ink-soft">{t.resolved.description}</dd>
                )}
                {t.resolved.steps.map((s, i) => (
                  <dd key={`${s.op}-${i}`} className="font-mono text-xs text-ink">
                    {s.op}{s.detail ? ` ${s.detail}` : ''}
                  </dd>
                ))}
                {t.resolved.cases.map((c, i) => (
                  <dd key={`${c.when}-${i}`} className="font-mono text-xs text-ink">
                    {c.when} → {c.then}
                  </dd>
                ))}
                {t.resolved.sqlExpr && (
                  <dd className="font-mono text-xs text-ink break-all">{t.resolved.sqlExpr}</dd>
                )}
              </div>
            ))}
          </dl>
        </Section>
      )}

      {silverTables.length > 0 && (
        <Section name="Silver tables" count={silverTables.length} testId="reference-silver-tables">
          <dl className="flex flex-col" style={{ gap: 6 }}>
            {silverTables.map((t) => (
              <div key={t.name} className="flex items-baseline gap-2">
                <dt className="font-mono text-xs text-accent">{t.name}</dt>
                <dd className="font-mono text-xs text-ink">{t.qualified}</dd>
                {t.driving && <Badge tone="neutral">driving</Badge>}
              </div>
            ))}
          </dl>
        </Section>
      )}

      {entityKey.length > 0 && (
        <Section name="Entity key" count={entityKey.length} testId="reference-entity-key">
          <dl className="flex flex-col" style={{ gap: 6 }}>
            {/* Declared order, which IS the grain. */}
            {entityKey.map((p) => (
              <div key={p.goldColumn} className="flex items-baseline gap-2">
                <dt className="font-mono text-xs text-accent">{p.goldColumn}</dt>
                <dd className="font-mono text-xs text-ink">← {p.drivingColumn}</dd>
              </div>
            ))}
          </dl>
        </Section>
      )}
    </div>
  );
}
