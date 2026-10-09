import type { PipelineConfig } from '@/lib/types'

export type Severity = 'warn' | 'err'

export interface Violation {
  severity: Severity
  section: 'identity' | 'extraction' | 'glue-runtime' | 'silver' | 'quality' | 'reconcile'
  field?: string
  message: string
}

const HEAVY_INCR_PREDICATE_THRESHOLD = 20

// Real `PipelineExtract` is flat — the runtime field for predicates is
// `jdbc_predicates` (see SyncDetail.tsx line 439 reading the same path).
// `PipelineExtract` in types.ts doesn't declare `jdbc_predicates` yet, so we
// read it through a loose cast.
interface ExtractWithPredicates {
  jdbc_predicates?: string[] | null
}

export function validateDraft(draft: PipelineConfig): { errors: Violation[]; warnings: Violation[] } {
  const violations: Violation[] = []

  const ext = draft.extract as (typeof draft.extract & ExtractWithPredicates) | undefined
  const preds = ext?.jdbc_predicates ?? []
  if (preds.length > HEAVY_INCR_PREDICATE_THRESHOLD) {
    violations.push({
      severity: 'warn',
      section: 'extraction',
      field: 'jdbc_predicates',
      message: `${preds.length} incremental predicates exceeds recommended cap of ${HEAVY_INCR_PREDICATE_THRESHOLD} — runs may time out`,
    })
  }

  if (draft.silver?.enabled) {
    if (!draft.silver.primary_key_cols || draft.silver.primary_key_cols.length === 0) {
      violations.push({
        severity: 'err',
        section: 'silver',
        field: 'primary_key_cols',
        message: 'Silver primary key is required when Silver is enabled',
      })
    }
    if (!draft.silver.database || !draft.silver.table) {
      violations.push({
        severity: 'err',
        section: 'silver',
        message: 'Silver database and table are required',
      })
    }
  }

  return {
    errors: violations.filter(v => v.severity === 'err'),
    warnings: violations.filter(v => v.severity === 'warn'),
  }
}
