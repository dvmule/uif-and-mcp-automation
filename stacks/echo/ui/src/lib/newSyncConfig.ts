// The pipeline config the New replication wizard saves, built from its state (moved verbatim from the old StepReview).
import { echoBronzeBucket, echoSilverBucket, echoGlueDatabase } from '@/lib/echoPaths'
import type { PipelineConfig } from '@/lib/types'
import type { WizardState } from '@/store/wizardStore'
import {
  validateBqDataset,
  validateBqLocation,
  validateBqProject,
  validateGcsPath,
  validateSecretArn,
} from '@/lib/validation'

/** The wizard's data fields: the store without its step and actions. */
export type NewSyncState = Omit<
  WizardState,
  'step' | 'setStep' | 'setConnection' | 'setTableName' | 'setAnalyzeResult' | 'patch' | 'reset'
>

/** Thrown when the state fails the pre-deploy checks; the message is what the review step shows. */
export class NewSyncConfigError extends Error {}

export function buildPipelineConfig(s: NewSyncState, env: string): PipelineConfig {
  if (!s.connection || !s.analyzeResult) throw new NewSyncConfigError('Pick a connection and analyze a table first')

  const extract: PipelineConfig['extract'] = {
    table: s.analyzeResult.table,
    mode: s.extractMode,
    watermark_col: s.extractMode === 'incremental' ? s.watermarkCol : null,
    watermark_fallback_col:
      s.extractMode === 'incremental' ? s.watermarkFallbackCol : null,
    watermark_value: '1970-01-01T00:00:00',
    jdbc_partition_col: s.partitionCol,
    jdbc_lower_bound: s.jdbcLowerBound,
    jdbc_upper_bound: s.jdbcUpperBound,
    jdbc_partitions: s.partitionCol ? s.jdbcPartitions : null,
  }
  // Silver's table identifier is the bare table name (no schema prefix).
  const silverTable = s.analyzeResult.table.split('.').pop() ?? s.analyzeResult.table

  let config: PipelineConfig
  if (s.destinationCloud === 'gcp') {
    const errs: string[] = []
    const secretErr = validateSecretArn(s.gcpSecretArn)
    if (secretErr) errs.push(secretErr)
    const bronzeErr = validateGcsPath(s.bronzeGcsPath, 'Bronze GCS path')
    if (bronzeErr) errs.push(bronzeErr)
    if (s.silverEnabled) {
      const silverErr = validateGcsPath(s.silverGcsPath, 'Silver GCS path')
      if (silverErr) errs.push(silverErr)
      const projErr = validateBqProject(s.bqProject)
      if (projErr) errs.push(projErr)
      const dsErr = validateBqDataset(s.bqDataset)
      if (dsErr) errs.push(dsErr)
      const locErr = validateBqLocation(s.bqLocation)
      if (locErr) errs.push(locErr)
    }
    if (errs.length) {
      throw new NewSyncConfigError(errs.join('; '))
    }

    config = {
      pipeline_id: s.pipelineId,
      version: 'latest',
      enabled: true,
      destination: { cloud: 'gcp', gcp_credentials_secret_arn: s.gcpSecretArn },
      source: { secret_arn: s.connection.arn },
      extract,
      target: { gcs_path: s.bronzeGcsPath, compression: 'snappy' },
      quality: {
        not_null_cols: s.notNullCols,
        min_rows_expected: s.minRowsExpected ?? undefined,
      },
      glue: {
        worker_type: s.workerType,
        num_workers: s.numWorkers,
        timeout_minutes: 120,
      },
      schedule: { frequency: s.frequency },
      silver: s.silverEnabled && s.pkCols.length > 0 ? {
        enabled: true,
        table: silverTable,
        gcs_path: s.silverGcsPath,
        bq_project: s.bqProject,
        bq_dataset: s.bqDataset,
        bq_location: s.bqLocation,
        primary_key_cols: s.pkCols,
        soft_delete: s.softDeleteCol
          ? { flag_col: s.softDeleteCol, inactive_val: 'N' }
          : null,
      } : undefined,
      // No replication block: GCP pipelines write to GCS directly, and the
      // server rejects a replication block when destination.cloud === 'gcp'.
    }
  } else {
    // AWS branch — derive Bronze + Silver paths from the connection + table.
    const tableSlug = s.analyzeResult.table.toLowerCase().replace('.', '/')
    const bronzeS3 = `s3://${echoBronzeBucket(env)}/data/${tableSlug}/`
    const silverS3 = `s3://${echoSilverBucket(env)}/data/${tableSlug}/`

    if (s.silverBqEnabled) {
      const errs: string[] = []
      const secretErr = validateSecretArn(s.silverBqSecretArn)
      if (secretErr) errs.push(secretErr)
      const projErr = validateBqProject(s.silverBqProject)
      if (projErr) errs.push(projErr)
      const dsErr = validateBqDataset(s.silverBqDataset)
      if (dsErr) errs.push(dsErr)
      const locErr = validateBqLocation(s.silverBqLocation)
      if (locErr) errs.push(locErr)
      if (!s.silverBqBucket.trim()) errs.push('Silver→BQ bucket is required')
      if (!s.silverBqPrefix.trim()) errs.push('Silver→BQ prefix is required')
      if (errs.length) {
        throw new NewSyncConfigError(errs.join('; '))
      }
    }

    const gcsReplicationBlock = s.replicationEnabled ? {
      gcs: {
        enabled: true,
        bucket: s.gcsBucket,
        prefix: s.gcsPrefix.endsWith('/') ? s.gcsPrefix : `${s.gcsPrefix}/`,
        credentials_secret_arn: s.gcsSecretArn,
      },
    } : null
    const silverBqBlock = s.silverBqEnabled ? {
      silver_bq: {
        enabled: true,
        bucket: s.silverBqBucket,
        prefix: s.silverBqPrefix.endsWith('/') ? s.silverBqPrefix : `${s.silverBqPrefix}/`,
        credentials_secret_arn: s.silverBqSecretArn,
        bq_project: s.silverBqProject,
        bq_dataset: s.silverBqDataset,
        bq_location: s.silverBqLocation,
      },
    } : null
    const replicationOut: PipelineConfig['replication'] | undefined =
      (gcsReplicationBlock || silverBqBlock)
        ? { ...(gcsReplicationBlock ?? {}), ...(silverBqBlock ?? {}) } as PipelineConfig['replication']
        : undefined

    config = {
      pipeline_id: s.pipelineId,
      version: 'latest',
      enabled: true,
      source: { secret_arn: s.connection.arn },
      extract,
      target: { s3_path: bronzeS3, compression: 'snappy' },
      quality: {
        not_null_cols: s.notNullCols,
        min_rows_expected: s.minRowsExpected ?? undefined,
      },
      glue: {
        worker_type: s.workerType,
        num_workers: s.numWorkers,
        timeout_minutes: 120,
      },
      schedule: { frequency: s.frequency },
      // Silver needs enabled/database/table/s3_path or the backend leaves it disabled.
      silver: s.silverEnabled && s.pkCols.length > 0 ? {
        enabled: true,
        database: echoGlueDatabase(env, 'silver', s.connection.name),
        table: silverTable,
        s3_path: silverS3,
        primary_key_cols: s.pkCols,
        soft_delete: s.softDeleteCol
          ? { flag_col: s.softDeleteCol, inactive_val: 'N' }
          : null,
      } : undefined,
      replication: replicationOut,
    }
  }

  return config
}
