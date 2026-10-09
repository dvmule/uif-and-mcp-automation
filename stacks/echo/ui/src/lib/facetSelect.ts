// A MultiSelect over one URL facet, where an absent key means every value is shown. Pure.
import type { MultiSelectItem } from './multiSelect'
import { freshnessView, pipelineStatusView } from './statusVocabulary'
import type { PipelineStatus } from './types'
import type { FreshnessLevel } from './utils'

export interface FacetOption {
  key: string
  label: string
}

/** The Status facet's values on the sync and gold lists (lib/filters). */
export const PIPELINE_STATUS_OPTIONS: FacetOption[] = (
  ['RUNNING', 'SUCCEEDED', 'WARNING', 'FAILED', 'SKIPPED', 'DISABLED'] as PipelineStatus[]
).map((s) => ({ key: s, label: pipelineStatusView(s).label }))

/** The Freshness facet's values on the sync and gold lists (lib/filters). */
export const FRESHNESS_OPTIONS: FacetOption[] = (['fresh', 'stale', 'critical', 'unknown'] as FreshnessLevel[]).map(
  (f) => ({ key: f, label: freshnessView(f).label }),
)

/** The facet's options: the known vocabulary first, then loaded and URL values it lacks; `meta` counts the rows. */
export function facetItems(
  rowValues: Array<string | null | undefined>,
  { vocab, selected = [], label = (k: string) => k }: {
    vocab?: FacetOption[]
    selected?: string[]
    label?: (key: string) => string
  } = {},
): MultiSelectItem[] {
  const counts = new Map<string, number>()
  for (const v of rowValues) if (v) counts.set(v, (counts.get(v) ?? 0) + 1)
  const known = new Set((vocab ?? []).map((o) => o.key))
  const extra = Array.from(new Set([...counts.keys(), ...selected]))
    .filter((k) => !known.has(k))
    .sort((a, b) => label(a).localeCompare(label(b)))
  return [
    ...(vocab ?? []).map((o) => ({ key: o.key, label: o.label, meta: counts.get(o.key) ?? 0 })),
    ...extra.map((k) => ({ key: k, label: label(k), meta: counts.get(k) ?? 0 })),
  ]
}

/** What the MultiSelect shows ticked: every option when the URL holds none. */
export function facetShown(items: MultiSelectItem[], urlValues: string[]): string[] {
  return urlValues.length === 0 ? items.map((it) => it.key) : urlValues
}

/** The values to write back. Every option or none drops the key (there is no "show nothing" state);
 *  from the all state, a click on one option keeps only that option. */
export function facetUrlValues(items: MultiSelectItem[], urlValues: string[], next: string[]): string[] {
  const keys = items.map((it) => it.key)
  if (next.length === 0 || keys.every((k) => next.includes(k))) return []
  if (urlValues.length === 0) {
    const dropped = keys.filter((k) => !next.includes(k))
    if (dropped.length === 1) return dropped
  }
  return next
}
