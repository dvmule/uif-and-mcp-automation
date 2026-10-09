// Shared helpers for the Extraction + Glue runtime edit sections.
// Mirrors the legacy two-profile flex shape from SyncDetail.tsx so that
// pipelines persisted with either flat top-level JDBC fields or nested
// {initial, incremental} blocks render correctly.

import type { WorkerType } from '@/lib/types'
import { HIGH_CADENCE_FREQUENCIES } from '@/lib/scheduleVocabulary'

export const HEAVY_INCR_PREDICATE_THRESHOLD = 4

export type Phase = 'initial' | 'incremental'

export interface ExtractProfile {
  jdbc_partition_col?: string | null
  jdbc_lower_bound?: number | null
  jdbc_upper_bound?: number | null
  jdbc_partitions?: number | null
  jdbc_predicates?: string[] | null
  allow_heavy?: boolean
}

export interface GlueProfile {
  worker_type: WorkerType
  num_workers: number
  timeout_minutes: number
}

export type FlexExtract = Partial<ExtractProfile> & {
  initial?: ExtractProfile
  incremental?: ExtractProfile
  [k: string]: unknown
}
export type FlexGlue = Partial<GlueProfile> & {
  initial?: GlueProfile
  incremental?: GlueProfile
  [k: string]: unknown
}
export type FlexDraft = {
  extract?: FlexExtract
  glue?: FlexGlue
  schedule?: { frequency?: string }
}

const LEAN_INCR_EXTRACT_DEFAULTS: ExtractProfile = {
  jdbc_partition_col: null,
  jdbc_lower_bound: null,
  jdbc_upper_bound: null,
  jdbc_partitions: null,
  jdbc_predicates: [],
}

const LEAN_INCR_GLUE_DEFAULTS: GlueProfile = {
  worker_type: 'G.1X',
  num_workers: 1,
  timeout_minutes: 60,
}

export function readExtractProfile(extract: FlexExtract | undefined, phase: Phase): ExtractProfile {
  const phaseBlock = extract?.[phase]
  if (phaseBlock) return phaseBlock
  if (phase === 'initial') {
    return {
      jdbc_partition_col: extract?.jdbc_partition_col ?? null,
      jdbc_lower_bound: extract?.jdbc_lower_bound ?? null,
      jdbc_upper_bound: extract?.jdbc_upper_bound ?? null,
      jdbc_partitions: extract?.jdbc_partitions ?? null,
      jdbc_predicates: extract?.jdbc_predicates ?? [],
    }
  }
  return { ...LEAN_INCR_EXTRACT_DEFAULTS }
}

export function readGlueProfile(glue: FlexGlue | undefined, phase: Phase): GlueProfile {
  const phaseBlock = glue?.[phase]
  if (phaseBlock) {
    return {
      worker_type: phaseBlock.worker_type ?? LEAN_INCR_GLUE_DEFAULTS.worker_type,
      num_workers: phaseBlock.num_workers ?? LEAN_INCR_GLUE_DEFAULTS.num_workers,
      timeout_minutes: phaseBlock.timeout_minutes ?? LEAN_INCR_GLUE_DEFAULTS.timeout_minutes,
    }
  }
  if (phase === 'initial' && glue?.worker_type) {
    return {
      worker_type: glue.worker_type,
      num_workers: glue.num_workers ?? LEAN_INCR_GLUE_DEFAULTS.num_workers,
      timeout_minutes: glue.timeout_minutes ?? LEAN_INCR_GLUE_DEFAULTS.timeout_minutes,
    }
  }
  return { ...LEAN_INCR_GLUE_DEFAULTS }
}

// Mirror of lambda/pipeline_controller.py:_profile_violations.
export function profileViolations(draft: FlexDraft): string[] {
  const ext: FlexExtract = draft.extract ?? {}
  const inc: ExtractProfile = ext.incremental ?? {}
  const g: FlexGlue = draft.glue ?? {}
  const gInc: Partial<GlueProfile> = g.incremental ?? {}
  const freq = (draft.schedule?.frequency ?? '').toLowerCase()

  if (inc.allow_heavy === true) return []

  const out: string[] = []
  const preds = inc.jdbc_predicates
  if (Array.isArray(preds) && preds.length > HEAVY_INCR_PREDICATE_THRESHOLD) {
    out.push(
      `extract.incremental.jdbc_predicates has ${preds.length} entries (limit=${HEAVY_INCR_PREDICATE_THRESHOLD}). Set predicates to <=${HEAVY_INCR_PREDICATE_THRESHOLD} or enable allow_heavy.`
    )
  }
  const nw = gInc.num_workers
  if (typeof nw === 'number' && nw > 4 && HIGH_CADENCE_FREQUENCIES.includes(freq)) {
    out.push(
      `glue.incremental.num_workers=${nw} with schedule.frequency=${freq} multiplies concurrent Oracle sessions. Reduce to <=4 or enable allow_heavy.`
    )
  }
  const wt: string | undefined = gInc.worker_type
  if (wt === 'G.4X' || wt === 'G.8X') {
    out.push(
      `glue.incremental.worker_type=${wt} is oversized for incremental phase (lean default: G.1X). Enable allow_heavy to override.`
    )
  }
  const parts = inc.jdbc_partitions
  if (typeof parts === 'number' && parts > 8) {
    out.push(
      `extract.incremental.jdbc_partitions=${parts} is heavy. Set to <=8 or enable allow_heavy.`
    )
  }
  return out
}
