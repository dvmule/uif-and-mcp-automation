import type { ReactNode } from 'react';
import { useOverviewStats } from '../hooks/use-overview-stats';
import type { SchemaView } from '../hooks/use-schema-url-state';

interface GlanceCardProps {
  /** The figure. A string because the third card reads `0 of 12`. */
  value: ReactNode;
  label: string;
  /** Applied to the figure only — the label is always UX Black. */
  valueClass: string;
  isLoading: boolean;
  isError: boolean;
  onClick?: () => void;
}

function GlanceCard({ value, label, valueClass, isLoading, isError, onClick }: GlanceCardProps) {
  const body = (
    <>
      {isLoading ? (
        <div className="h-7 w-12 bg-paper-raised rounded animate-pulse" aria-hidden />
      ) : isError ? (
        // A query that failed is not a number, and it is not a zero. Both of
        // those assert something about the registry that we did not learn.
        <span className="text-[15px] font-semibold text-signal-warn whitespace-nowrap">
          Couldn&rsquo;t load
        </span>
      ) : (
        <span className={`text-[28px] font-bold leading-none tabular-nums ${valueClass}`}>
          {value}
        </span>
      )}
      <span className="text-[12.5px] leading-[1.35] text-ink-soft">{label}</span>
    </>
  );

  const shell = 'flex items-baseline gap-3 bg-paper-card border border-paper-line text-left';
  const geometry = { borderRadius: 12, padding: '14px 16px' } as const;

  // Only the first two cards have somewhere to go. The third is deliberately
  // inert: there is no "has a compatibility rule" filter to navigate to, and
  // wiring it to the unfiltered list — which is what it used to do — dressed
  // an arbitrary navigation as a drill-down.
  if (!onClick) {
    return (
      <div className={shell} style={geometry}>
        {body}
      </div>
    );
  }

  return (
    <button
      type="button"
      onClick={onClick}
      className={`${shell} transition-shadow duration-150 hover:shadow-lg motion-reduce:transition-none`}
      style={geometry}
    >
      {body}
    </button>
  );
}

interface RegistryOverviewProps {
  onNavigate: (view: SchemaView) => void;
  /**
   * The compliance query failed. `non_compliant_schemas` comes from a
   * different endpoint and may well have succeeded, but showing "1 failing a
   * metadata rule" above a table in which every row reads `unknown` is
   * incoherent — and the figure people act on is the one in the table. When
   * compliance is unknown, this card says so too.
   */
  complianceError?: boolean;
}

export function RegistryOverview({ onNavigate, complianceError = false }: RegistryOverviewProps) {
  const { data: stats, isLoading, isError } = useOverviewStats();

  return (
    <div className="grid gap-3" style={{ gridTemplateColumns: 'repeat(3, minmax(0,1fr))' }}>
      <GlanceCard
        value={stats?.total_schemas}
        label="subjects registered"
        valueClass="text-ink"
        isLoading={isLoading}
        isError={isError}
        onClick={() => onNavigate('all')}
      />
      <GlanceCard
        value={stats?.non_compliant_schemas}
        label="failing a metadata rule"
        valueClass="text-signal-fail"
        isLoading={isLoading}
        isError={isError || complianceError}
        onClick={() => onNavigate('non-compliant')}
      />
      {/*
        The number this page exists to show. `schemas_with_compatibility_rules`
        is 0: until it equals the subject count, nothing in the registry is
        checked against its previous version when a new one is published, so a
        breaking change registers silently. It is stated as a bare ratio in
        warn, with no reassuring caption around it.
      */}
      <GlanceCard
        value={stats ? `${stats.schemas_with_compatibility_rules} of ${stats.total_schemas}` : undefined}
        label="have a compatibility rule set"
        valueClass="text-signal-warn"
        isLoading={isLoading}
        isError={isError}
      />
    </div>
  );
}
