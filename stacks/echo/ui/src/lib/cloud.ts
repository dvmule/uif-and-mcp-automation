// Per-cloud accessors for PipelineConfig. Centralised so detail/list pages
// don't directly read `target.s3_path` (which is undefined for GCP-native
// pipelines and used to crash PipelineDetail.tsx).
//
// Server-side validation: lambda/pipeline_controller.py:_validate_destination_config
// (the source of truth for which cloud uses which field).

import type { DestinationCloud, PipelineConfig } from './types'

/** Returns the destination cloud, defaulting to 'aws' when the block is absent
 *  (matches the backend's `_dest_cloud` helper). */
export function pipelineCloud(cfg: PipelineConfig | undefined | null): DestinationCloud {
  const raw = (cfg?.destination?.cloud ?? 'aws').toLowerCase()
  return raw === 'gcp' ? 'gcp' : 'aws'
}

/** Bronze storage URI (s3:// or gs://) — null if neither is set. */
export function bronzePath(cfg: PipelineConfig | undefined | null): string | null {
  return cfg?.target?.gcs_path ?? cfg?.target?.s3_path ?? null
}

/** Silver storage URI (s3:// or gs://) — null if Silver disabled or absent. */
export function silverPath(cfg: PipelineConfig | undefined | null): string | null {
  return cfg?.silver?.gcs_path ?? cfg?.silver?.s3_path ?? null
}

/** Human-readable Silver catalog target.
 *  - AWS: the Glue database name.
 *  - GCP: "<bq_project>.<bq_dataset>". */
export function silverCatalog(cfg: PipelineConfig | undefined | null): string | null {
  if (pipelineCloud(cfg) === 'gcp') {
    const p = cfg?.silver?.bq_project
    const d = cfg?.silver?.bq_dataset
    return p && d ? `${p}.${d}` : null
  }
  return cfg?.silver?.database ?? null
}

/** Fully qualified Silver table id for display (Identity card, DQ subject, etc.). */
export function silverTableFqn(cfg: PipelineConfig | undefined | null): string | null {
  if (!cfg?.silver?.enabled) return null
  const table = cfg.silver.table
  if (!table) return null
  const catalog = silverCatalog(cfg)
  return catalog ? `${catalog}.${table}` : table
}

/** Label for the Bronze/Silver path row: "S3 Path" or "GCS Path". */
export function storageLabel(cfg: PipelineConfig | undefined | null): 'S3 Path' | 'GCS Path' {
  return pipelineCloud(cfg) === 'gcp' ? 'GCS Path' : 'S3 Path'
}
