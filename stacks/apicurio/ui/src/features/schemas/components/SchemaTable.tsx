import { useState, useEffect, useRef } from 'react';
import { ChevronUp, ChevronDown, ChevronsUpDown, ChevronRight } from 'lucide-react';
import { Badge } from '@/components/ui/Badge';
import { ComplianceBadge } from './ComplianceBadge';
import { cn } from '@/lib/utils';
import { groupSchemas, countDomains } from '../hooks/use-schema-filter';
import { splitSubject, DOMAIN_COLOURS } from '../utils';
import type { SchemaSummary, DataClassification, SchemaOwner } from '../types/schema.types';
import type { SortField, SortDir, GroupByField } from '../hooks/use-schema-url-state';
import { SORT_FIELD_LABELS, GROUP_BY_LABELS } from '../hooks/use-schema-url-state';
import type { Density } from './SchemaToolbar';

const classificationTone: Record<DataClassification, 'neutral' | 'accent' | 'warn'> = {
  public: 'neutral',
  internal: 'accent',
  confidential: 'warn',
  restricted: 'warn',
};

/**
 * stripe · checkbox · Subject · Classification · Owner · Lifecycle · Version ·
 * Last modified · Compliance.
 *
 * Nine, not the ten the previous `COL_COUNT` claimed — that count included a
 * `consumers` column which has never been rendered, so every grouped-table
 * header row carried a `colSpan` one wider than the table.
 */
const COL_COUNT = 9;

/**
 * The design's column widths:
 *   grid-template-columns: 3px 30px minmax(0,1.9fr) 104px 104px 94px 86px 104px 104px
 *   gap: 12px
 *
 * A grid's `gap` sits BETWEEN tracks, so each 104px is 104px of content. A
 * table has no gap — the separation has to come from cell padding, which sits
 * INSIDE the column. So each track carries the 12px as well, and the cells
 * take 6px a side, leaving the content box the width the design specified.
 *
 * Getting this wrong is not cosmetic: at 104px minus padding the Compliance
 * column had 92px for a `non-compliant` badge that needs 104px, so it either
 * wrapped to two lines or overflowed and was clipped by the scroll container.
 *
 * `null` is the flexible Subject column (`minmax(0,1.9fr)`) — the only one
 * that grows. The stripe carries no padding, so it takes no gap.
 */
const GAP = 12;
const COL_WIDTHS: (number | null)[] = [
  3,
  30 + GAP,
  null,
  104 + GAP,
  104 + GAP,
  94 + GAP,
  86 + GAP,
  104 + GAP,
  104 + GAP,
];

/** Below this the table scrolls rather than squeezing the subject column. */
const MIN_TABLE_WIDTH = COL_WIDTHS.reduce<number>((sum, w) => sum + (w ?? 220), 0);

interface SchemaTableProps {
  items: SchemaSummary[];
  /**
   * The unfiltered set, for the footer's "n of m domains shown". Without it
   * the footer can only compare the filtered set against itself, which always
   * reads "m of m" and never tells you a facet is hiding something.
   */
  allItems?: SchemaSummary[];
  nonCompliantIds: Set<string>;
  complianceLoading: boolean;
  /** The compliance query failed. `nonCompliantIds` is then empty because
    * nothing came back, not because every row passed — so no row may be
    * rendered as compliant. */
  complianceError: boolean;
  /**
   * The filter currently narrowing the list cannot be computed, because the
   * query feeding it failed. An empty result then means "unknown", not
   * "none", and the empty state must not say otherwise.
   */
  filterUnavailable?: boolean;
  /**
   * How many subjects the registry holds, from the list response's own
   * `total`, against however many were fetched.
   *
   * The fetch asks for 500 — the BFF's own ceiling — and there is no
   * pagination, so past that the table holds part of the registry while every
   * figure it prints describes that part as the whole thing. Given this, the
   * footer says what was loaded and the empty state stops claiming the
   * registry has nothing matching when it may simply not be here.
   */
  registryTotal?: number;
  sort: SortField;
  dir: SortDir;
  activeSubject: string | null;
  groupBy: GroupByField | null;
  selectedIds: Set<string>;
  showRawSubjects: boolean;
  density: Density;
  onSort: (field: SortField) => void;
  onRowClick: (item: SchemaSummary) => void;
  onComplianceBadgeClick: (item: SchemaSummary, e: React.MouseEvent) => void;
  onSelectionChange: (ids: Set<string>) => void;
  /** Clears whatever is narrowing the list. Omit and the footer's "Show all"
    * link is not rendered. */
  onShowAll?: () => void;
}

export function SchemaTable({
  items,
  allItems,
  nonCompliantIds,
  complianceLoading,
  complianceError,
  filterUnavailable = false,
  registryTotal,
  sort,
  dir,
  activeSubject,
  groupBy,
  selectedIds,
  showRawSubjects,
  density,
  onSort,
  onRowClick,
  onComplianceBadgeClick,
  onSelectionChange,
  onShowAll,
}: SchemaTableProps) {
  const [collapsedGroups, setCollapsedGroups] = useState<Set<string>>(new Set());
  const headerCheckboxRef = useRef<HTMLInputElement>(null);

  const allKeys = items.map((i) => `${i.group_id}/${i.artifact_id}`);
  const allSelected = items.length > 0 && allKeys.every((k) => selectedIds.has(k));
  const someSelected = !allSelected && allKeys.some((k) => selectedIds.has(k));

  useEffect(() => {
    if (headerCheckboxRef.current) {
      headerCheckboxRef.current.indeterminate = someSelected;
    }
  }, [someSelected]);

  function toggleAll() {
    if (allSelected) {
      const next = new Set(selectedIds);
      for (const k of allKeys) next.delete(k);
      onSelectionChange(next);
    } else {
      const next = new Set(selectedIds);
      for (const k of allKeys) next.add(k);
      onSelectionChange(next);
    }
  }

  function toggleRow(key: string, e: React.MouseEvent | React.KeyboardEvent) {
    e.stopPropagation();
    const next = new Set(selectedIds);
    if (next.has(key)) next.delete(key);
    else next.add(key);
    onSelectionChange(next);
  }

  function toggleGroup(key: string) {
    setCollapsedGroups((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  const truncated = typeof registryTotal === 'number' && (allItems?.length ?? 0) < registryTotal;

  if (items.length === 0) {
    // "No schemas match the current filters" is a finding about the registry.
    // On the Non-compliant segment with a failed compliance query it was a
    // finding about nothing at all: `nonCompliantIds` is empty either way, so
    // the segment read as a clean bill of health at the very moment every row
    // on All correctly reads `unknown`.
    return (
      <div className="py-12 text-center">
        {filterUnavailable ? (
          <>
            <p className="text-sm font-medium text-ink">Could not load the compliance list</p>
            <p className="mt-1 text-sm text-ink-mute">
              This view is built from that list, so an empty table here means unknown, not
              none.
            </p>
          </>
        ) : (
          <>
            <p className="text-sm text-ink-mute">No schemas match the current filters.</p>
            {truncated && (
              // Without this the sentence above is a claim about the
              // registry, and it would be false: only the first slice of it
              // is here to match against.
              <p className="mt-1 text-sm text-ink-mute">
                Only the first {allItems?.length} of {registryTotal} subjects are loaded, so a
                match may exist outside them.
              </p>
            )}
          </>
        )}
      </div>
    );
  }

  const cellPad = density === 'compact' ? 'py-2' : 'py-3';
  const rowHeight = density === 'compact' ? 44 : 52;

  const headerRow = (
    <tr style={{ height: 40 }}>
      <th className="p-0" />
      <th className="px-1.5">
        <input
          ref={headerCheckboxRef}
          type="checkbox"
          aria-label="Select all"
          checked={allSelected}
          onChange={toggleAll}
          className="cursor-pointer accent-ink"
        />
      </th>
      <SortHeader field="artifact_id" label="Subject" current={sort} dir={dir} onSort={onSort} />
      <SortHeader field="classification" label="Classification" current={sort} dir={dir} onSort={onSort} />
      <th className="px-1.5 text-left text-[11px] font-medium text-accent">Owner</th>
      <SortHeader field="lifecycle" label="Lifecycle" current={sort} dir={dir} onSort={onSort} />
      <SortHeader field="latest_version" label="Version" current={sort} dir={dir} onSort={onSort} />
      <SortHeader field="modified_on" label="Last modified" current={sort} dir={dir} onSort={onSort} />
      <SortHeader field="compliance" label="Compliance" current={sort} dir={dir} onSort={onSort} />
    </tr>
  );

  const renderRow = (item: SchemaSummary) => {
    const key = `${item.group_id}/${item.artifact_id}`;
    const isNonCompliant = nonCompliantIds.has(key);
    const passed = complianceLoading || complianceError ? undefined : !isNonCompliant;
    const isActive = activeSubject === item.artifact_id;
    const isSelected = selectedIds.has(key);
    const isDeprecated = item.lifecycle === 'deprecated';
    const domainColour = DOMAIN_COLOURS[item.domain ?? ''];
    const { base: subjectBase, suffix: subjectSuffix } = splitSubject(item.artifact_id);

    // `isNonCompliant` is only meaningful when the compliance query answered.
    // On error the set is empty, so this is false for every row and no row is
    // tinted — which is correct: an untinted row claims nothing.
    const failing = isNonCompliant && !complianceLoading && !complianceError;

    return (
      <tr
        key={key}
        onClick={() => onRowClick(item)}
        onKeyDown={(e) => { if (e.key === 'Enter') onRowClick(item); }}
        tabIndex={0}
        aria-selected={isActive}
        style={{ height: rowHeight }}
        className={cn(
          'cursor-pointer outline-none border-b border-paper-divider',
          'transition-colors duration-150 motion-reduce:transition-none',
          'hover:bg-paper-raised',
          failing ? 'bg-signal-fail-row' : 'bg-transparent',
          isActive && 'bg-accent-soft',
          isSelected && !isActive && 'bg-paper-raised',
        )}
      >
        {/* Domain stripe — dedicated 3px cell so the tooltip targets just the stripe */}
        <td
          className="p-0 select-none cursor-help"
          style={{ backgroundColor: domainColour ?? 'transparent' }}
          title={item.domain ?? undefined}
          onClick={(e) => e.stopPropagation()}
        />
        <td className={cn('px-1.5', cellPad)} onClick={(e) => e.stopPropagation()}>
          <input
            type="checkbox"
            aria-label={`Select ${item.artifact_id}`}
            checked={isSelected}
            onChange={(e) => toggleRow(key, e as unknown as React.MouseEvent)}
            onClick={(e) => e.stopPropagation()}
            className="cursor-pointer accent-ink"
          />
        </td>
        <td className={cn('px-1.5', cellPad)}>
          <span
            className={cn(
              'font-mono text-xs',
              isDeprecated ? 'line-through text-ink-mute' : 'text-accent',
            )}
            title={item.retirement_date ? `Retires: ${new Date(item.retirement_date).toLocaleDateString()}` : undefined}
          >
            {showRawSubjects ? (
              item.artifact_id
            ) : (
              <>
                <span>{subjectBase}</span>
                {subjectSuffix && (
                  <Badge tone="neutral" className="ml-1 text-[10px]">{subjectSuffix}</Badge>
                )}
              </>
            )}
          </span>
          {isDeprecated && item.successor_artifact_id && (
            <a
              href={`/schemas/${item.group_id}/${item.successor_artifact_id}`}
              className="ml-2 text-xs text-accent hover:underline font-mono"
              onClick={(e) => e.stopPropagation()}
            >
              successor →
            </a>
          )}
        </td>
        <td className={cn('px-1.5', cellPad)}>
          {item.classification ? (
            <Badge tone={classificationTone[item.classification]}>{item.classification}</Badge>
          ) : (
            <Muted>unset</Muted>
          )}
        </td>
        <td className={cn('px-1.5', cellPad)}>
          <OwnerCell owner={item.owner} />
        </td>
        <td className={cn('px-1.5', cellPad)}>
          {isDeprecated ? (
            <Badge tone="warn">deprecated</Badge>
          ) : item.lifecycle === 'active' ? (
            <Badge tone="neutral">active</Badge>
          ) : (
            <Muted>unset</Muted>
          )}
        </td>
        <td className={cn('px-1.5 font-mono text-xs', cellPad)}>
          {item.latest_version ? (
            <span className={failing ? 'text-signal-fail' : 'text-ink-mute'}>
              v{item.latest_version}
            </span>
          ) : (
            <Muted>—</Muted>
          )}
          {/* `version_count` is an unpopulated stub on the contract. When it is
            * null the cell reads `v3`, never `v3 / —`: a slash with nothing
            * after it asserts there is a total and we lost it. */}
          {item.version_count != null && (
            <span className="text-ink-mute"> / {item.version_count}</span>
          )}
        </td>
        <td className={cn('px-1.5 text-xs text-ink-mute', cellPad)}>
          {item.modified_on ? (
            <span title={new Date(item.modified_on).toISOString()}>
              {relativeTime(item.modified_on)}
            </span>
          ) : (
            <Muted>—</Muted>
          )}
        </td>
        <td
          className={cn('px-1.5', cellPad)}
          onClick={(e) => {
            // `passed === false` only: an unknown state (still checking, or the
            // compliance query failed) must not open the compliance view for a
            // schema nobody has established anything about.
            if (passed === false) {
              onComplianceBadgeClick(item, e);
              e.stopPropagation();
            }
          }}
        >
          <ComplianceBadge passed={passed} loading={complianceLoading} error={complianceError} />
        </td>
      </tr>
    );
  };

  const groups = groupBy ? [...groupSchemas(items, groupBy).entries()] : null;

  return (
    <div className="flex flex-col gap-2">
      {/* Tables scroll horizontally rather than compressing the subject column. */}
      <div className="overflow-x-auto">
        <table className="w-full text-sm table-fixed" style={{ minWidth: MIN_TABLE_WIDTH }}>
          <colgroup>
            {COL_WIDTHS.map((w, i) => (
              <col key={i} style={w === null ? undefined : { width: w }} />
            ))}
          </colgroup>
          <thead className="sticky top-0 z-10 bg-paper-raised">{headerRow}</thead>

          {groups ? (
            groups.map(([groupKey, groupItems]) => {
              const isCollapsed = collapsedGroups.has(groupKey);
              return (
                <tbody key={groupKey}>
                  <tr className="bg-paper-group">
                    <td colSpan={COL_COUNT} style={{ padding: '8px 18px' }}>
                      <button
                        type="button"
                        onClick={() => toggleGroup(groupKey)}
                        className="flex items-center gap-2 text-ink"
                        aria-expanded={!isCollapsed}
                      >
                        {isCollapsed ? <ChevronRight size={12} /> : <ChevronDown size={12} />}
                        {/* The swatch is the domain colour, so it only means
                          * anything when the grouping IS domain. */}
                        {groupBy === 'domain' && (
                          <span
                            aria-hidden
                            className="shrink-0 self-stretch"
                            style={{ width: 3, backgroundColor: DOMAIN_COLOURS[groupKey] ?? 'transparent' }}
                          />
                        )}
                        <span className="font-mono text-xs font-semibold">{groupKey}</span>
                        <span className="text-xs font-normal text-ink-mute">({groupItems.length})</span>
                      </button>
                    </td>
                  </tr>
                  {!isCollapsed && groupItems.map(renderRow)}
                </tbody>
              );
            })
          ) : (
            <tbody>{items.map(renderRow)}</tbody>
          )}
        </table>
      </div>

      <TableFooter
        items={items}
        allItems={allItems}
        groupBy={groupBy}
        sort={sort}
        onShowAll={onShowAll}
        registryTotal={registryTotal}
      />
    </div>
  );
}

/**
 * "n of m domains shown · grouped by domain, sorted by last modified".
 *
 * The two numbers are the domains present in the filtered set against those
 * in the registry, so they diverge exactly when a facet, a search or a segment
 * is hiding a domain — and `Show all` appears only then. The prototype showed
 * "3 of 8" because it hardcoded three domain groups to fit the canvas; here
 * the figures are derived, so an unnarrowed list honestly reads "8 of 8" with
 * no link beside it.
 */
function TableFooter({
  items,
  allItems,
  groupBy,
  sort,
  onShowAll,
  registryTotal,
}: {
  items: SchemaSummary[];
  allItems?: SchemaSummary[];
  groupBy: GroupByField | null;
  sort: SortField;
  onShowAll?: () => void;
  registryTotal?: number;
}) {
  if (!allItems) return null;

  const shownDomains = countDomains(items);
  const totalDomains = countDomains(allItems);
  const narrowed = items.length < allItems.length;
  const truncated = typeof registryTotal === 'number' && allItems.length < registryTotal;

  const describe = [
    groupBy ? `grouped by ${GROUP_BY_LABELS[groupBy].toLowerCase()}` : null,
    `sorted by ${SORT_FIELD_LABELS[sort].toLowerCase()}`,
  ]
    .filter(Boolean)
    .join(', ');

  return (
    <div className="flex items-baseline justify-between gap-4 text-xs text-ink-mute">
      <span>
        {shownDomains} of {totalDomains} domain{totalDomains === 1 ? '' : 's'} shown · {describe}
        {/* Both figures above count only what was FETCHED. Saying so is the
          * difference between "the registry has 8 domains" and "the 500
          * subjects loaded cover 8 domains". */}
        {truncated && ` · ${allItems.length} of ${registryTotal} subjects loaded`}
      </span>
      {narrowed && onShowAll && (
        <button type="button" onClick={onShowAll} className="font-medium text-accent hover:underline">
          Show all {allItems.length}
        </button>
      )}
    </div>
  );
}

function OwnerCell({ owner }: { owner: SchemaOwner }) {
  if (!owner.service) return <Muted>orphan</Muted>;
  const hasExtra = owner.team || owner.email;
  return (
    <div className="relative group/owner">
      <span className="font-mono text-xs cursor-default">{owner.service}</span>
      {hasExtra && (
        <div className="absolute left-0 top-full mt-1 z-20 hidden group-hover/owner:block bg-paper-card border border-paper-line rounded-xl shadow-lg p-3 min-w-[200px] space-y-1.5 text-xs">
          <div className="flex gap-2">
            <span className="text-ink-mute w-10 shrink-0">CI</span>
            <span className="font-mono">{owner.service}</span>
          </div>
          {owner.team && (
            <div className="flex gap-2">
              <span className="text-ink-mute w-10 shrink-0">Team</span>
              <span className="font-mono">{owner.team}</span>
            </div>
          )}
          {owner.email && (
            <div className="flex gap-2">
              <span className="text-ink-mute w-10 shrink-0">Email</span>
              <span>{owner.email}</span>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function SortHeader({
  field,
  label,
  current,
  dir,
  onSort,
}: {
  field: SortField;
  label: string;
  current: SortField;
  dir: SortDir;
  onSort: (field: SortField) => void;
}) {
  const isActive = field === current;
  return (
    <th className="px-1.5 text-left">
      <button
        type="button"
        onClick={() => onSort(field)}
        className="flex items-center gap-1 w-full text-left text-[11px] font-medium text-accent"
      >
        {label}
        {isActive ? (
          dir === 'asc' ? <ChevronUp size={11} /> : <ChevronDown size={11} />
        ) : (
          <ChevronsUpDown size={11} className="opacity-30" />
        )}
      </button>
    </th>
  );
}

function Muted({ children }: { children: React.ReactNode }) {
  return <span className="text-ink-mute italic text-xs">{children}</span>;
}

function relativeTime(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diff / 60_000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  if (days < 30) return `${days}d ago`;
  const months = Math.floor(days / 30);
  if (months < 12) return `${months}mo ago`;
  return `${Math.floor(months / 12)}y ago`;
}
