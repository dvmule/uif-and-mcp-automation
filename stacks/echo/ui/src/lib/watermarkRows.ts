// Replicate › Watermarks row model, from the pipeline list alone. Pure: no React.
import type { PipelineConfig } from './types'
import { freshness, parseDateMs } from './utils'

export interface WatermarkRow {
  pipelineId: string
  table: string
  incremental: boolean
  column: string | null
  /** extract.watermark_value, as the page has always shown it. */
  value: string | null
  /** The value read as a time, when it is one: when the watermark last moved. */
  advancedAt: string | null
  /** The Syncs-list stale rule (`freshness` stale or critical) applied to `advancedAt`. */
  behind: boolean
}

export function watermarkRow(p: PipelineConfig): WatermarkRow {
  const incremental = p.extract?.mode === 'incremental'
  const value = incremental ? (p.extract?.watermark_value ?? null) : null
  const advancedAt = value && parseDateMs(value) !== null ? value : null
  const level = advancedAt ? freshness(p, advancedAt) : 'unknown'
  return {
    pipelineId: p.pipeline_id,
    table: p.extract?.table || p.pipeline_id,
    incremental,
    column: incremental ? (p.extract?.watermark_col ?? null) : null,
    value,
    advancedAt,
    behind: level === 'stale' || level === 'critical',
  }
}

/** Incremental syncs first, then full loads, each by table name. */
export function watermarkRows(pipelines: PipelineConfig[]): WatermarkRow[] {
  return pipelines
    .map(watermarkRow)
    .sort((a, b) => Number(b.incremental) - Number(a.incremental) || a.table.localeCompare(b.table))
}
