// New replication: step labels, when Next is allowed, and the answer each done step shows.
import { echoGlueDatabase } from '@/lib/echoPaths'
import type { NewSyncState } from '@/lib/newSyncConfig'
import {
  validateBqDataset,
  validateBqLocation,
  validateBqProject,
  validateGcsPath,
  validateSecretArn,
} from '@/lib/validation'

export const STEP_LABELS = [
  'Source',
  'Destination',
  'Schema & load',
  'Silver',
  'Schedule & storage',
  'Review & deploy',
] as const

export const REVIEW_STEP = STEP_LABELS.length - 1

/** The daily slot window the scheduler assigns from; shown wherever the schedule is summarised. */
export const DAILY_SLOT_WINDOW = '05:30–11:00 UTC'

export function canAdvance(step: number, s: NewSyncState): boolean {
  switch (step) {
    // Connection + Table Name
    case 0: return !!s.connection && !!s.analyzeResult
    case 1: return s.destinationCloud === 'aws' || s.destinationCloud === 'gcp'
    // Schema Review + Extraction
    case 2: return !!s.analyzeResult && (s.extractMode === 'full' || !!s.watermarkCol)
    case 3: {
      if (s.silverEnabled && s.pkCols.length === 0) return false
      if (s.destinationCloud === 'gcp' && s.silverEnabled) {
        if (validateBqProject(s.bqProject)) return false
        if (validateBqDataset(s.bqDataset)) return false
        if (validateBqLocation(s.bqLocation)) return false
      }
      return true
    }
    // Storage & Replication + Schedule & Quality
    case 4: return storageOk(s) && !!s.frequency
    default: return true
  }
}

function storageOk(s: NewSyncState): boolean {
  if (s.destinationCloud === 'gcp') {
    if (validateSecretArn(s.gcpSecretArn)) return false
    if (validateGcsPath(s.bronzeGcsPath, 'Bronze GCS path')) return false
    if (s.silverEnabled && validateGcsPath(s.silverGcsPath, 'Silver GCS path')) return false
    return true
  }
  const gcsOk = !s.replicationEnabled || (
    !!s.gcsBucket.trim() &&
    !!s.gcsPrefix.trim() &&
    /^arn:aws:secretsmanager:[a-z0-9-]+:\d{12}:secret:.+$/.test(s.gcsSecretArn)
  )
  if (!gcsOk) return false
  if (s.silverBqEnabled) {
    if (!s.silverBqBucket.trim()) return false
    if (!s.silverBqPrefix.trim()) return false
    if (validateSecretArn(s.silverBqSecretArn)) return false
    if (validateBqProject(s.silverBqProject)) return false
    if (validateBqDataset(s.silverBqDataset)) return false
    if (validateBqLocation(s.silverBqLocation)) return false
  }
  return true
}

/** Bare table name silver writes to (no schema prefix), as the saved config has it. */
export function silverTableName(s: NewSyncState): string {
  const table = s.analyzeResult?.table ?? ''
  return table.split('.').pop() ?? table
}

/** Where silver lands: the Glue database on AWS, the BigQuery dataset on GCP. */
export function silverTarget(s: NewSyncState, env: string): string {
  const db = s.destinationCloud === 'gcp'
    ? s.bqDataset || '—'
    : s.connection ? echoGlueDatabase(env, 'silver', s.connection.name) : '—'
  return `${db}.${silverTableName(s)}`
}

export function scheduleSummary(s: NewSyncState): string {
  return s.frequency === 'daily' ? `daily · ${DAILY_SLOT_WINDOW}` : s.frequency
}

/** One-line answer for a step, shown in the stepper once it is done. */
export function stepAnswer(s: NewSyncState, index: number, env: string): string | undefined {
  switch (index) {
    case 0: {
      const table = s.analyzeResult?.table ?? s.tableName.trim()
      if (!table || !s.connection) return undefined
      return `${table} · ${s.connection.name}`
    }
    case 1: return s.destinationCloud.toUpperCase()
    case 2:
      return s.extractMode === 'incremental'
        ? `incremental · ${s.watermarkCol ?? '—'}`
        : 'full'
    case 3: return s.silverEnabled ? `on · ${silverTarget(s, env)}` : 'off'
    case 4: return scheduleSummary(s)
    default: return undefined
  }
}
