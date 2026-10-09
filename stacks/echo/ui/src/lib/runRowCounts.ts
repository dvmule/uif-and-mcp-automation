import type { RunMetrics } from './types'
import { isBronzeJob, isSilverJob } from './runKindLabel'

/** Most recent completed/in-flight run for a layer (runs assumed newest-first). */
export function latestLayerRun(
  runs: RunMetrics[],
  layer: 'bronze' | 'silver',
): RunMetrics | null {
  const pred = layer === 'bronze' ? isBronzeJob : isSilverJob
  return runs.find((r) => pred(r.job_name)) ?? null
}

/** End-to-end row summary: Oracle read (Bronze) → GCP target (Silver write, else Bronze write). */
export function pipelineRowSummary(runs: RunMetrics[]): {
  source: number | null
  target: number | null
} {
  const bronze = latestLayerRun(runs, 'bronze')
  const silver = latestLayerRun(runs, 'silver')
  return {
    source: bronze?.rows_read ?? bronze?.rows_extracted ?? null,
    target: silver?.rows_written ?? bronze?.rows_written ?? null,
  }
}

export function runSourceRows(
  run: Pick<RunMetrics, 'rows_read' | 'rows_extracted'>,
): number | null {
  return run.rows_read ?? run.rows_extracted ?? null
}

export function runTargetRows(
  run: Pick<RunMetrics, 'rows_written'>,
): number | null {
  return run.rows_written ?? null
}
