// A sync's quality rules from its config, scored by the last run (UIFPE-682). Pure: no React.
import type { PipelineQuality, QualityResult } from './types'
import { formatRows } from './utils'

export interface QualityRule {
  name: string
  context: string
  /** 1 or 0 for the last run; total 0 means the last run did not evaluate it. */
  passed: number
  total: number
}

function score(passed: boolean | undefined): Pick<QualityRule, 'passed' | 'total'> {
  return passed == null ? { passed: 0, total: 0 } : { passed: passed ? 1 : 0, total: 1 }
}

function notNullVerdict(result: QualityResult | null | undefined, col: string): boolean | undefined {
  const checks = result?.not_null_checks
  if (!checks) return undefined
  if (checks[col]) return checks[col].passed
  const key = Object.keys(checks).find((k) => k.toLowerCase() === col.toLowerCase())
  return key ? checks[key].passed : undefined
}

export function qualityRules(
  quality: PipelineQuality | null | undefined,
  result: QualityResult | null | undefined,
): QualityRule[] {
  const rules: QualityRule[] = (quality?.not_null_cols ?? []).map((col) => ({
    name: col,
    context: 'not null',
    ...score(notNullVerdict(result, col)),
  }))
  if (typeof quality?.min_rows_expected === 'number') {
    rules.push({
      name: 'Minimum rows',
      context: `≥ ${formatRows(quality.min_rows_expected)} expected`,
      ...score(result?.min_rows?.passed),
    })
  }
  return rules
}
