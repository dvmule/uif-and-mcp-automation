import { useRef, useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import {
  X,
  ChevronDown,
  RefreshCw,
  Check,
  FilePlus,
  Rows2,
  Rows4,
} from 'lucide-react';
import { cn } from '@/lib/utils';
import type { SchemaSummary, DataClassification, Lifecycle } from '../types/schema.types';
import type {
  SchemaUrlState,
  SchemaView,
} from '../hooks/use-schema-url-state';
import {
  SEGMENTS,
  VIEW_LABELS,
} from '../hooks/use-schema-url-state';

export type Density = 'compact' | 'cozy';

interface SchemaToolbarProps {
  items: SchemaSummary[];
  state: SchemaUrlState;
  density: Density;
  /** A refresh of the compliance cache is in flight. */
  isRefreshing: boolean;
  onViewChange: (view: SchemaView) => void;
  onFacetChange: (key: 'domain' | 'classification' | 'lifecycle', value: string | null) => void;
  onDensityChange: (density: Density) => void;
  onRefresh: () => void;
  onClearAll: () => void;
}

export function SchemaToolbar({
  items,
  state,
  density,
  isRefreshing,
  onViewChange,
  onFacetChange,
  onDensityChange,
  onRefresh,
  onClearAll,
}: SchemaToolbarProps) {
  const domains = [...new Set(items.map((i) => i.domain).filter((d): d is string => d !== null))].sort();
  const classifications: DataClassification[] = ['public', 'internal', 'confidential', 'restricted'];
  const lifecycles: Lifecycle[] = ['active', 'deprecated'];

  // Search is not in this list: it lives in the app header now, and it has its
  // own clear affordance (Escape). `Clear filters` clears the three facets.
  const hasFacets = Boolean(state.domain || state.classification || state.lifecycle);

  return (
    <div
      className="flex flex-col bg-paper-card"
      style={{ border: '1px solid #EDECF6', borderRadius: 16, padding: '14px 16px', gap: 12 }}
    >
      {/* One row. Group-by, the sort control and the `n / m subjects` pill are
        * gone from it. Sorting is still driven from the column headers, which is
        * where a table's sort belongs and which the footer already reports in
        * words; the subject total is on the glance card directly above, and two
        * counts of the same thing in one viewport invite the question of which
        * is right.
        *
        * `flex-wrap` stays, so a narrow viewport folds the row rather than
        * pushing `Publish a schema` off the card. */}
      <div className="flex items-center flex-wrap" style={{ gap: 10 }}>
        <Partition active={state.view} onSelect={onViewChange} />

        <FilterDropdown
          label="Domain"
          options={domains}
          active={state.domain}
          onSelect={(v) => onFacetChange('domain', v)}
        />
        <FilterDropdown
          label="Classification"
          options={classifications}
          active={state.classification}
          onSelect={(v) => onFacetChange('classification', v)}
        />
        <FilterDropdown
          label="Lifecycle"
          options={lifecycles}
          active={state.lifecycle}
          onSelect={(v) => onFacetChange('lifecycle', v)}
        />

        {hasFacets && (
          <button
            type="button"
            onClick={onClearAll}
            className="flex items-center gap-1 text-[13px] font-medium text-accent hover:underline"
          >
            <X size={12} />
            Clear filters
          </button>
        )}


        {/* One group with `ml-auto`, not a `flex-1` spacer: a zero-width spacer
          * collapses when the row wraps and drops these to the left margin,
          * whereas the margin keeps them right-aligned on whichever line they
          * land on. */}
        <div className="ml-auto flex items-center" style={{ gap: 10 }}>
          <button
            type="button"
            onClick={onRefresh}
            disabled={isRefreshing}
            aria-label="Re-check compliance"
            title="Re-check every subject against the metadata rules"
            className="flex items-center justify-center shrink-0 border border-light bg-paper-card text-accent disabled:text-ink-mute"
            style={{ width: 34, height: 34, borderRadius: 999 }}
          >
            <RefreshCw size={15} className={cn(isRefreshing && 'animate-spin motion-reduce:animate-none')} />
          </button>

          <DensityToggle density={density} onDensityChange={onDensityChange} />

          <Link
            to="/schemas/publish"
            className="flex items-center gap-2 shrink-0 whitespace-nowrap bg-ink text-white text-[13px] font-semibold transition-shadow duration-150 hover:shadow-lg motion-reduce:transition-none"
            style={{ borderRadius: 999, padding: '14px 26px' }}
          >
            <FilePlus size={15} />
            Publish a schema
          </Link>
        </div>
      </div>
    </div>
  );
}

/**
 * The segmented partition that replaced the `Home · Browse all ·
 * Non-compliant · Recently changed` tab bar. Three segments, because Home is
 * gone; `?view=overview` resolves to `All` in `useSchemaUrlState`.
 */
function Partition({
  active,
  onSelect,
}: {
  active: SchemaView;
  onSelect: (view: SchemaView) => void;
}) {
  return (
    <div
      role="tablist"
      aria-label="Filter by state"
      className="flex shrink-0 bg-paper"
      style={{ borderRadius: 999, padding: 3 }}
    >
      {SEGMENTS.map((seg) => {
        const isActive = seg === active;
        return (
          <button
            key={seg}
            type="button"
            role="tab"
            aria-selected={isActive}
            onClick={() => onSelect(seg)}
            className={cn(
              'text-[13px] whitespace-nowrap transition-colors duration-150 motion-reduce:transition-none',
              isActive ? 'bg-accent text-white font-semibold' : 'bg-transparent text-ink-soft font-medium',
            )}
            style={{ borderRadius: 999, padding: '7px 15px' }}
          >
            {VIEW_LABELS[seg]}
          </button>
        );
      })}
    </div>
  );
}

function DensityToggle({
  density,
  onDensityChange,
}: {
  density: Density;
  onDensityChange: (d: Density) => void;
}) {
  const cells: { value: Density; Icon: typeof Rows2; label: string }[] = [
    { value: 'compact', Icon: Rows4, label: 'Compact rows' },
    { value: 'cozy', Icon: Rows2, label: 'Cozy rows' },
  ];
  return (
    <div
      className="flex shrink-0 overflow-hidden border border-light"
      style={{ borderRadius: 4 }}
    >
      {cells.map(({ value, Icon, label }) => {
        const isActive = density === value;
        return (
          <button
            key={value}
            type="button"
            onClick={() => onDensityChange(value)}
            aria-label={label}
            aria-pressed={isActive}
            className={cn(
              'flex items-center justify-center',
              isActive ? 'bg-ink text-white' : 'bg-paper-card text-accent',
            )}
            style={{ width: 34, height: 34 }}
          >
            <Icon size={15} />
          </button>
        );
      })}
    </div>
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
              onClick={() => { onSelect(null); setOpen(false); }}
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
              onClick={() => { onSelect(active === opt ? null : opt); setOpen(false); }}
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
