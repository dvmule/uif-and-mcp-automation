import type { PipelineStatus } from './types'
import type { FreshnessLevel } from './utils'

export interface SyncFilterState {
  domain:    string[]
  source:    string[]
  tag:       string[]
  status:    PipelineStatus[]
  freshness: FreshnessLevel[]
}

export const EMPTY_FILTERS: SyncFilterState = {
  domain:    [],
  source:    [],
  tag:       [],
  status:    [],
  freshness: [],
}

// Domain-first order — same order the bar renders in.
const KEYS = ['domain', 'source', 'tag', 'status', 'freshness'] as const

function parseList(raw: string | null): string[] {
  if (!raw) return []
  return raw.split(',').map(s => s.trim()).filter(Boolean)
}

export function parseFilters(params: URLSearchParams): SyncFilterState {
  return {
    domain:    parseList(params.get('domain')),
    source:    parseList(params.get('source')),
    tag:       parseList(params.get('tag')),
    status:    parseList(params.get('status'))    as PipelineStatus[],
    freshness: parseList(params.get('freshness')) as FreshnessLevel[],
  }
}

// Build a new URLSearchParams that preserves any unrelated query keys the
// caller passes in (so we don't clobber search params owned by other UI).
export function serializeFilters(
  state: SyncFilterState,
  base?: URLSearchParams,
): URLSearchParams {
  const next = new URLSearchParams(base?.toString() ?? '')
  for (const k of KEYS) {
    const values = state[k]
    if (values.length === 0) next.delete(k)
    else next.set(k, values.join(','))
  }
  // Drop a stale `cloud=` param from older bookmarks — Cloud is no longer
  // a user-facing filter (it remains as a column on the table).
  next.delete('cloud')
  return next
}

export function isEmptyFilters(state: SyncFilterState): boolean {
  return KEYS.every(k => state[k].length === 0)
}

export function countActive(state: SyncFilterState): number {
  return KEYS.reduce((sum, k) => sum + state[k].length, 0)
}
