// The Quality › Checks stat strip and row model, from the checks list alone. Pure: no React.
import { dqRunStatusView, pipelineStatusView, type StatusView } from './statusVocabulary'
import { passesFacet, type FacetValues } from './facetFilters'
import { silverTableFqn } from './cloud'
import type { DqCheckConfig, PipelineConfig } from './types'
import { parseDateMs } from './utils'

export interface DqStats {
  total: number
  active: number
  disabled: number
  passed24h: number
  failed24h: number
  /** Skipped runs: the old Overview counted them as warnings. */
  warning24h: number
  /** `database.table` of each check whose last run in the window failed. */
  failedSubjects: string[]
}

const DAY_MS = 24 * 60 * 60 * 1000

export function subjectFqn(c: Pick<DqCheckConfig, 'subject'>): string {
  return `${c.subject.database}.${c.subject.table}`
}

/** Counts over every check: active by `enabled`, the 24 h tallies by each check's last run. */
export function computeDqStats(checks: DqCheckConfig[], now: Date = new Date()): DqStats {
  const cutoff = now.getTime() - DAY_MS
  const stats: DqStats = {
    total: checks.length, active: 0, disabled: 0, passed24h: 0, failed24h: 0, warning24h: 0, failedSubjects: [],
  }
  for (const c of checks) {
    if (c.enabled) stats.active += 1
    else stats.disabled += 1
    const at = parseDateMs(c.last_run?.ts)
    if (at === null || at < cutoff) continue
    switch (c.last_run?.status) {
      case 'SUCCEEDED': stats.passed24h += 1; break
      case 'FAILED': stats.failed24h += 1; stats.failedSubjects.push(subjectFqn(c)); break
      case 'SKIPPED': stats.warning24h += 1; break
    }
  }
  return stats
}

/** "silver_d.t" or "silver_d.t, silver_d.u +2 more"; "runs" when nothing failed. */
export function failedHint(stats: Pick<DqStats, 'failedSubjects'>): string {
  const names = stats.failedSubjects
  if (names.length === 0) return 'runs'
  const shown = names.slice(0, 2).join(', ')
  return names.length > 2 ? `${shown} +${names.length - 2} more` : shown
}

// ---- rows ---------------------------------------------------------------

export const CHECK_FACET_KEYS = ['kind', 'status', 'layer'] as const

/** The Status facet value of a check: DISABLED when off, else its last run's status, NONE if it never ran. */
export function checkStatusKey(c: DqCheckConfig): string {
  if (c.enabled === false) return 'DISABLED'
  return c.last_run?.status ?? 'NONE'
}

/** Status column: Disabled when off, the last run's status otherwise, null when it never ran. */
export function checkStatusView(c: DqCheckConfig): StatusView | null {
  if (c.enabled === false) return pipelineStatusView('DISABLED')
  return c.last_run?.status ? dqRunStatusView(c.last_run.status) : null
}

/** `silver · database.table`. */
export function checkSubjectLabel(c: Pick<DqCheckConfig, 'subject'>): string {
  return `${c.subject.layer} · ${subjectFqn(c)}`
}

/** The sync whose silver table is the check's subject; undefined for gold subjects or when none matches. */
export function linkedSync(
  pipelines: PipelineConfig[] | undefined,
  c: Pick<DqCheckConfig, 'subject'>,
): PipelineConfig | undefined {
  if (c.subject.layer !== 'silver' || !pipelines) return undefined
  const target = subjectFqn(c).toLowerCase()
  return pipelines.find((p) => silverTableFqn(p)?.toLowerCase() === target)
}

/** Search on check id and subject table, then the URL facets. */
export function filterChecks(checks: DqCheckConfig[], search: string, facets: FacetValues): DqCheckConfig[] {
  const q = search.trim().toLowerCase()
  return checks.filter((c) => {
    if (q && !`${c.check_id} ${subjectFqn(c)}`.toLowerCase().includes(q)) return false
    if (!passesFacet(facets.kind, c.kind)) return false
    if (!passesFacet(facets.status, checkStatusKey(c))) return false
    if (!passesFacet(facets.layer, c.subject.layer)) return false
    return true
  })
}
