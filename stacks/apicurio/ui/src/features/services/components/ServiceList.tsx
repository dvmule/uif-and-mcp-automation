import { useState, useRef, useEffect, useMemo } from 'react';
import { useSearchParams, Link } from 'react-router-dom';
import { Search, X, ChevronDown, ChevronRight, CircleAlert, Check } from 'lucide-react';
import { Badge } from '@/components/ui/Badge';
import { DOMAIN_COLOURS } from '@/features/schemas';
import { cn } from '@/lib/utils';
import { useServiceList } from '../hooks/use-service-list';
import type { ServiceSummary } from '../types/service.types';

/**
 * 6a — the service list, read-only by design.
 *
 * The registry does not create, edit or delete services: they come from the
 * CMDB. So there are no checkboxes, no row actions and no CTA anywhere on this
 * screen — not disabled ones, none at all. An affordance that cannot work is
 * worse than its absence.
 */

/**
 * The design's grid: `3px minmax(0,1.5fr) 128px minmax(0,2.2fr) 96px 96px`
 * with `gap: 14px`.
 *
 * This is a CSS grid with ARIA table roles rather than a `<table>`, because
 * two of the six columns are fractional and in a ratio (1.5 : 2.2). A
 * `table-fixed` layout divides its unsized columns equally, so Service and
 * Application would have come out 1 : 1 — and the widths here are the design's
 * judgement about how much of the row an application name deserves.
 */
const COLS = '3px minmax(0,1.5fr) 128px minmax(0,2.2fr) 96px 96px';
const ROW = 'grid items-center';
const ROW_STYLE = { gridTemplateColumns: COLS, gap: 14 } as const;

/**
 * The label is written out rather than produced by a `capitalize` class: CSS
 * text-transform does not change an element's accessible name, so the button
 * announced itself as "application" while reading as "Application".
 */
const GROUP_MODES = [
  { value: 'domain', label: 'Domain' },
  { value: 'application', label: 'Application' },
] as const;

export function ServiceList() {
  const [searchParams, setSearchParams] = useSearchParams();
  const q = searchParams.get('q') ?? '';
  const domainFilter = searchParams.get('domain');
  const groupBy = searchParams.get('groupBy') as 'domain' | 'application' | null;

  const { data, isLoading, error } = useServiceList();

  function setParam(key: string, value: string | null) {
    const next = new URLSearchParams(searchParams);
    if (value) next.set(key, value);
    else next.delete(key);
    setSearchParams(next, { replace: true });
  }

  const allDomains = data ? [...new Set(data.map((s) => s.domain))].sort() : [];

  // The filter matches `name` and `domain` only, never `app_name` — the
  // placeholder says "name or domain", and a search that silently also matched
  // the application column would return rows whose visible name has nothing to
  // do with the query.
  const filtered = (data ?? []).filter((s) => {
    const needle = q.toLowerCase();
    const matchesSearch =
      !q || s.name.toLowerCase().includes(needle) || s.domain.toLowerCase().includes(needle);
    const matchesDomain = !domainFilter || s.domain === domainFilter;
    return matchesSearch && matchesDomain;
  });

  /**
   * The CMDB returns 16 entries for 14 services: `content-svc` and
   * `pricing-svc` each arrive twice under the same URN.
   *
   * Pickers de-duplicate (see `SchemaPublishForm`) because a URN is an
   * identity and offering it twice is a bug. A list is different: hiding a row
   * would misreport what the CMDB actually holds, so the repeats are marked
   * and kept. The duplication is upstream, and the list says so.
   *
   * Which rows are repeats is computed here rather than assumed: the second
   * and later occurrence of a URN is a repeat, in the order the CMDB returned
   * them.
   */
  const { repeatKeys, distinctUrns, duplicateNames } = useMemo(() => {
    const seen = new Set<string>();
    const repeats = new Set<string>();
    const dupes = new Set<string>();
    (data ?? []).forEach((s, i) => {
      if (seen.has(s.urn)) {
        repeats.add(`${i}`);
        dupes.add(s.name);
      } else {
        seen.add(s.urn);
      }
    });
    return { repeatKeys: repeats, distinctUrns: seen.size, duplicateNames: [...dupes].sort() };
  }, [data]);

  /** Row identity has to be the index, not the ci_id: the duplicate entries
    * carry the same ci_id as their original. */
  const keyOf = (s: ServiceSummary) => `${(data ?? []).indexOf(s)}`;

  const hasFilters = Boolean(q || domainFilter);

  function clearAll() {
    const next = new URLSearchParams(searchParams);
    next.delete('q');
    next.delete('domain');
    setSearchParams(next, { replace: true });
  }

  const grouped: Map<string, ServiceSummary[]> = new Map();
  if (groupBy === 'domain') {
    for (const s of filtered) {
      const bucket = grouped.get(s.domain) ?? [];
      bucket.push(s);
      grouped.set(s.domain, bucket);
    }
    const sorted = new Map([...grouped.entries()].sort(([a], [b]) => a.localeCompare(b)));
    grouped.clear();
    for (const [k, v] of sorted) grouped.set(k, v);
  } else if (groupBy === 'application') {
    const byApp: Map<string, ServiceSummary[]> = new Map();
    const unassigned: ServiceSummary[] = [];
    for (const s of [...filtered].sort((a, b) => a.name.localeCompare(b.name))) {
      if (!s.app_name) {
        unassigned.push(s);
      } else {
        const bucket = byApp.get(s.app_name) ?? [];
        bucket.push(s);
        byApp.set(s.app_name, bucket);
      }
    }
    const sorted = new Map([...byApp.entries()].sort(([a], [b]) => a.localeCompare(b)));
    for (const [k, v] of sorted) grouped.set(k, v);
    if (unassigned.length) grouped.set('Unassigned', unassigned);
  }

  const filteredDistinct = new Set(filtered.map((s) => s.urn)).size;

  return (
    <div className="flex flex-col" style={{ gap: 14 }}>
      <div>
        <h1 className="text-[26px] font-semibold text-ink">Services</h1>
        <p className="mt-1 text-sm text-ink-mute">
          Read from the CMDB. The registry does not create or edit services — everything here
          is a view.
        </p>
      </div>

      {/* ------------------------------------------------------- toolbar */}
      <div
        className="flex items-center flex-wrap bg-paper-card"
        style={{ border: '1px solid #EDECF6', borderRadius: 16, padding: '14px 16px', gap: 10 }}
      >
        <SearchInput value={q} onChange={(v) => setParam('q', v || null)} />

        <FilterDropdown
          label="Domain"
          options={allDomains}
          active={domainFilter}
          onSelect={(v) => setParam('domain', v)}
        />

        {hasFilters && (
          <button
            type="button"
            onClick={clearAll}
            className="flex items-center gap-1 text-[13px] font-medium text-accent hover:underline"
          >
            <X size={12} />
            Clear filters
          </button>
        )}

        <span className="text-[13px] text-ink-mute">Group</span>
        {/* Two options in one track, and re-clicking the active one ungroups —
          * so there is no third "off" segment to explain. */}
        <div className="flex shrink-0 bg-paper" style={{ borderRadius: 999, padding: 3 }}>
          {GROUP_MODES.map(({ value, label }) => {
            const isActive = groupBy === value;
            return (
              <button
                key={value}
                type="button"
                aria-pressed={isActive}
                onClick={() => setParam('groupBy', isActive ? null : value)}
                className={cn(
                  'text-[13px] whitespace-nowrap',
                  isActive
                    ? 'bg-accent text-white font-semibold'
                    : 'bg-transparent text-ink-soft font-medium',
                )}
                style={{ borderRadius: 999, padding: '7px 15px' }}
              >
                {label}
              </button>
            );
          })}
        </div>

        <div className="flex-1" />

        <span
          role="status"
          aria-label={`${filteredDistinct} services across ${filtered.length} entries`}
          className="flex items-center shrink-0 whitespace-nowrap bg-amber text-ink text-[13px] font-semibold tabular-nums"
          style={{ height: 34, padding: '0 14px', borderRadius: 999 }}
        >
          {filteredDistinct}
          <span className="font-normal opacity-70 mx-1">/</span>
          {filtered.length} entries
        </span>
      </div>

      {/* --------------------------------------------- duplicate notice */}
      {duplicateNames.length > 0 && (
        <div
          className="flex items-start gap-2.5 bg-amber-row border border-light"
          style={{ borderRadius: 12, padding: '12px 16px' }}
        >
          <CircleAlert size={18} className="shrink-0 mt-px text-signal-warn" aria-hidden />
          <p className="text-[13px] leading-[1.45] text-ink-soft">
            The CMDB returns {data?.length} entries for {distinctUrns} services —{' '}
            {duplicateNames.map((name, i) => (
              <span key={name}>
                {i > 0 && (i === duplicateNames.length - 1 ? ' and ' : ', ')}
                <code className="font-mono">{name}</code>
              </span>
            ))}{' '}
            {duplicateNames.length === 1 ? 'arrives' : 'each arrive'} twice under the same URN.
            The repeats are marked rather than hidden, because the duplication is upstream.
          </p>
        </div>
      )}

      {/* --------------------------------------------------------- table */}
      {isLoading && <p className="py-4 text-sm text-ink-mute">Loading services…</p>}

      {error && <p className="py-4 font-mono text-sm text-signal-fail">Could not load services</p>}

      {!isLoading && !error && filtered.length === 0 && (
        <p className="py-4 text-sm italic text-ink-mute">
          {hasFilters ? 'No services match the current filters.' : 'No services found.'}
        </p>
      )}

      {!isLoading && !error && filtered.length > 0 && (
        <div className="overflow-x-auto">
          <div role="table" aria-label="Services" style={{ minWidth: 860 }}>
            <div role="rowgroup">
              <div
                role="row"
                className={cn(ROW, 'bg-paper-raised text-[11px] font-medium text-accent')}
                style={{ ...ROW_STYLE, minHeight: 40, paddingLeft: 0, paddingRight: 12 }}
              >
                <span aria-hidden />
                <span role="columnheader">Service</span>
                <span role="columnheader">Domain</span>
                <span role="columnheader">Application</span>
                <span role="columnheader">Produces</span>
                <span role="columnheader">Consumes</span>
              </div>
            </div>

            {groupBy ? (
              <GroupedBody
                groups={grouped}
                repeatKeys={repeatKeys}
                keyOf={keyOf}
                getAccent={groupBy === 'domain' ? (d) => DOMAIN_COLOURS[d] : undefined}
              />
            ) : (
              <div role="rowgroup">
                {filtered.map((s) => (
                  <ServiceRow
                    key={keyOf(s)}
                    service={s}
                    isRepeat={repeatKeys.has(keyOf(s))}
                  />
                ))}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function GroupedBody({
  groups,
  repeatKeys,
  keyOf,
  getAccent,
}: {
  groups: Map<string, ServiceSummary[]>;
  repeatKeys: Set<string>;
  keyOf: (s: ServiceSummary) => string;
  getAccent?: (key: string) => string | undefined;
}) {
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());

  function toggle(key: string) {
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  return (
    <>
      {[...groups.entries()].map(([key, services]) => {
        const isCollapsed = collapsed.has(key);
        const colour = getAccent?.(key);
        return (
          <div role="rowgroup" key={key}>
            <div role="row" className="bg-paper-group" style={{ padding: '8px 18px' }}>
              <span role="gridcell">
                <button
                  type="button"
                  onClick={() => toggle(key)}
                  className="flex items-center gap-2 text-ink"
                  aria-expanded={!isCollapsed}
                >
                  {isCollapsed ? <ChevronRight size={12} /> : <ChevronDown size={12} />}
                  {colour && (
                    <span
                      aria-hidden
                      className="shrink-0 self-stretch"
                      style={{ width: 3, backgroundColor: colour }}
                    />
                  )}
                  <span className="font-mono text-xs font-semibold">{key}</span>
                  <span className="text-xs font-normal text-ink-mute">{services.length}</span>
                </button>
              </span>
            </div>
            {!isCollapsed &&
              services.map((s) => (
                <ServiceRow key={keyOf(s)} service={s} isRepeat={repeatKeys.has(keyOf(s))} />
              ))}
          </div>
        );
      })}
    </>
  );
}

function ServiceRow({ service: s, isRepeat }: { service: ServiceSummary; isRepeat: boolean }) {
  const colour = DOMAIN_COLOURS[s.domain];
  return (
    <div
      role="row"
      className={cn(
        ROW,
        'border-b border-paper-divider',
        isRepeat ? 'bg-amber-row' : 'bg-transparent',
      )}
      style={{ ...ROW_STYLE, minHeight: 42, paddingRight: 12 }}
    >
      <span
        aria-hidden
        className="self-stretch"
        style={{ backgroundColor: colour ?? 'transparent' }}
      />
      <span role="gridcell" className="flex items-center gap-2 min-w-0">
        <Link
          to={`/services/${encodeURIComponent(s.urn)}`}
          className="truncate font-mono text-[12.5px] text-accent hover:underline"
        >
          {s.name}
        </Link>
        {/* Marked, not hidden: the row is a real entry the CMDB returned. */}
        {isRepeat && <Badge tone="warn">repeat</Badge>}
      </span>
      <span role="gridcell">
        <Badge tone="accent">{s.domain}</Badge>
      </span>
      <span role="gridcell" className="min-w-0">
        {s.app_name ? (
          <span className="block truncate text-xs text-ink-soft" title={s.app_id ?? undefined}>
            {s.app_name}
          </span>
        ) : (
          <span className="text-xs italic text-ink-mute">—</span>
        )}
      </span>
      <Count role="gridcell" value={s.produces_count} />
      <Count role="gridcell" value={s.consumes_count} />
    </div>
  );
}

/** Amethyst when there is something there, muted at zero — so a row of zeros
  * reads as "nothing flows through this service" at a glance. */
function Count({ value, role }: { value: number; role: string }) {
  return (
    <span
      role={role}
      className={cn('font-mono text-[12.5px]', value > 0 ? 'text-accent' : 'text-ink-mute')}
    >
      {value}
    </span>
  );
}

interface FilterDropdownProps {
  label: string;
  options: string[];
  active: string | null;
  onSelect: (value: string | null) => void;
}

function FilterDropdown({ label, options, active, onSelect }: FilterDropdownProps) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    function handler(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [open]);

  return (
    <div ref={ref} className="relative shrink-0">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className={cn(
          'flex items-center gap-1.5 text-[13px] border',
          active ? 'bg-ink text-white border-ink' : 'bg-paper-card text-ink-soft border-light',
        )}
        style={{ height: 34, padding: '0 12px', borderRadius: 4 }}
      >
        <span>{active ?? label}</span>
        <ChevronDown
          size={12}
          className={cn('transition-transform', active ? 'text-light' : 'text-ink-mute', open && 'rotate-180')}
        />
      </button>

      {open && options.length > 0 && (
        <div
          role="menu"
          className="absolute top-full left-0 mt-1 z-30 bg-paper-card shadow-xl"
          style={{ minWidth: 168, border: '1px solid #EDECF6', borderRadius: 12, padding: 6 }}
        >
          {active && (
            <button
              type="button"
              role="menuitem"
              onClick={() => {
                onSelect(null);
                setOpen(false);
              }}
              className="flex items-center gap-2 w-full text-left text-[13px] text-ink-mute hover:bg-paper-raised"
              style={{ padding: '8px 11px', borderRadius: 8 }}
            >
              <X size={12} />
              Clear {label.toLowerCase()}
            </button>
          )}
          {options.map((opt) => (
            <button
              key={opt}
              type="button"
              role="menuitem"
              onClick={() => {
                onSelect(active === opt ? null : opt);
                setOpen(false);
              }}
              className="flex items-center justify-between w-full text-left text-[13px] text-ink-soft hover:bg-paper-raised"
              style={{ padding: '8px 11px', borderRadius: 8 }}
            >
              {opt}
              {opt === active && <Check size={14} className="text-accent" />}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function SearchInput({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const ref = useRef<HTMLInputElement>(null);
  return (
    <div
      className="relative flex-1 bg-paper-card border border-light focus-within:border-accent"
      style={{ minWidth: 240, height: 34, borderRadius: 4 }}
    >
      <Search
        size={14}
        className="absolute left-2.5 top-1/2 -translate-y-1/2 text-ink-mute pointer-events-none"
        aria-hidden
      />
      <input
        ref={ref}
        type="text"
        aria-label="Search services"
        placeholder="Search by name or domain…"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="w-full h-full bg-transparent pl-8 pr-8 text-[13px] text-ink placeholder:text-ink-mute focus:outline-none"
      />
      {value && (
        <button
          type="button"
          onClick={() => {
            onChange('');
            ref.current?.focus();
          }}
          className="absolute right-2.5 top-1/2 -translate-y-1/2 text-ink-mute hover:text-ink"
          aria-label="Clear search"
        >
          <X size={12} />
        </button>
      )}
    </div>
  );
}
