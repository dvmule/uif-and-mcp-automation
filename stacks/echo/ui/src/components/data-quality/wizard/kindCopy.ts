// Display names and one-line descriptions for check kinds, and the short answers the New check stepper shows.
import type { DqCheckKind } from '@/lib/types'

export const KIND_COPY: Record<DqCheckKind, { label: string; description: string }> = {
  reconcile: {
    label: 'Reconcile',
    description: 'Finds rows deleted at the Oracle source by comparing primary keys. Dry-run first, with a cap on deletes.',
  },
  'null-rate': { label: 'Null rate', description: 'Share of nulls in a column stays under a limit.' },
  'row-count': { label: 'Row count', description: 'Row count stays within an expected range.' },
  freshness: { label: 'Freshness', description: 'Newest row is younger than N hours.' },
  drift: { label: 'Drift', description: 'Column values stay close to a reference run.' },
  uniqueness: { label: 'Uniqueness', description: 'No duplicate keys.' },
  'custom-sql': { label: 'Custom SQL', description: 'Your query, your threshold.' },
}

export function kindLabel(kind: string): string {
  return KIND_COPY[kind as DqCheckKind]?.label ?? kind
}

/** Short summary of step 3's answers, e.g. "UNIT_PRICE · max 5% nulls". */
export function paramsSummary(kind: DqCheckKind | null, params: Record<string, unknown>): string | undefined {
  if (kind === 'null-rate') {
    const pct = typeof params.threshold_pct === 'number' ? Math.round(params.threshold_pct * 1000) / 10 : null
    return `${(params.column as string) || '—'}${pct !== null ? ` · max ${pct}% nulls` : ''}`
  }
  if (kind === 'reconcile') {
    const pks = Array.isArray(params.pk_cols) ? (params.pk_cols as string[]).join(', ') : ''
    return `PK ${pks || '—'} · ${params.dry_run ? 'dry-run' : 'deletes'}`
  }
  return undefined
}
