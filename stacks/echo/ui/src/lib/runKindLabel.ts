import type { RunMetrics } from './types'

export function isBronzeJob(jobName: string): boolean {
  return jobName.includes('bronze-ingest')
}

export function isSilverJob(jobName: string): boolean {
  return /silver-transform|silver-bq-native/.test(jobName)
}

/** Gold transform or reconcile. Checked before isReconcileJob, whose
 *  substring match would otherwise claim uif-echo-*-gold-reconcile. */
export function isGoldJob(jobName: string): boolean {
  return /gold-transform|gold-reconcile/.test(jobName)
}

export function isReconcileJob(jobName: string): boolean {
  return jobName.includes('reconcile')
}

export function isGcsReplicatorJob(jobName: string): boolean {
  return jobName.includes('gcs-replicator')
}

export function isSilverBqReplicatorJob(jobName: string): boolean {
  return jobName.includes('silver-bq-replicator')
}

export function isReplicatorJob(jobName: string): boolean {
  return isGcsReplicatorJob(jobName) || isSilverBqReplicatorJob(jobName)
}

/** Bronze/Silver Glue ingest — excludes reconcile and GCP mirror Lambdas. */
export function isIngestJob(jobName: string): boolean {
  if (!jobName) return true
  if (isReplicatorJob(jobName) || isReconcileJob(jobName)) return false
  return isBronzeJob(jobName) || isSilverJob(jobName)
}

export type RunLayer = 'bronze' | 'silver' | 'gold' | 'reconcile' | 'replicate' | 'unknown'

export function resolveRunLayer(jobName: string): RunLayer {
  if (isReplicatorJob(jobName)) return 'replicate'
  if (isGoldJob(jobName)) return 'gold'
  if (isReconcileJob(jobName)) return 'reconcile'
  if (isBronzeJob(jobName)) return 'bronze'
  if (isSilverJob(jobName)) return 'silver'
  return 'unknown'
}

export function resolveRunPhase(
  run: Pick<RunMetrics, 'phase' | 'load_phase'>,
): 'initial' | 'incremental' {
  const resolved = run.phase ?? run.load_phase
  return resolved === 'initial' ? 'initial' : 'incremental'
}

const LAYER_LABELS: Record<RunLayer, string> = {
  bronze: 'Bronze',
  silver: 'Silver',
  gold: 'Gold',
  reconcile: 'Reconcile',
  replicate: 'Replicate',
  unknown: '—',
}

/** Short layer label for a run-history column: "Bronze", "Gold", "—". */
export function formatRunLayerLabel(jobName: string): string {
  return LAYER_LABELS[resolveRunLayer(jobName ?? '')]
}

/** Human label for run history: "Bronze · initial", "Silver · incremental", "Reconcile". */
export function formatRunKindLabel(run: Pick<RunMetrics, 'job_name' | 'phase' | 'load_phase'>): string {
  const jobName = run.job_name ?? ''
  if (isGcsReplicatorJob(jobName)) return 'Bronze Replicator'
  if (isSilverBqReplicatorJob(jobName)) return 'Silver Replicator'
  const layer = resolveRunLayer(jobName)
  // No phase suffix for gold: it writes no phase/load_phase, and
  // resolveRunPhase would silently label every run "incremental".
  if (layer === 'gold') return 'Gold'
  if (layer === 'reconcile') return 'Reconcile'
  if (layer === 'bronze') return `Bronze · ${resolveRunPhase(run)}`
  if (layer === 'silver') return `Silver · ${resolveRunPhase(run)}`
  return resolveRunPhase(run)
}
