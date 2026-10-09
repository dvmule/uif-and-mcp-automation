// Wizard states for the buildPipelineConfig snapshot; expected configs were captured from the pre-split wizard.
import type { AnalyzeResult } from '@/lib/types'
import type { NewSyncState } from '@/lib/newSyncConfig'

const ANALYZE: AnalyzeResult = {
  table: 'FIPM.X',
  row_count: 1284,
  columns: [
    { name: 'INVENTORY_ITEM_ID', data_type: 'NUMBER', nullable: false },
    { name: 'ORGANIZATION_ID', data_type: 'NUMBER', nullable: false },
    { name: 'LAST_UPDATE_DATE', data_type: 'DATE', nullable: true },
    { name: 'CREATION_DATE', data_type: 'DATE', nullable: true },
    { name: 'ACTIVE_FLAG', data_type: 'VARCHAR2', nullable: true },
  ],
}

const BASE: NewSyncState = {
  connection: null,
  destinationCloud: 'aws',
  gcpSecretArn: '',
  bronzeGcsPath: '',
  silverGcsPath: '',
  bqProject: '',
  bqDataset: '',
  bqLocation: 'EU',
  tableName: 'FIPM.X',
  analyzeResult: ANALYZE,
  extractMode: 'incremental',
  watermarkCol: null,
  watermarkFallbackCol: null,
  partitionCol: null,
  jdbcLowerBound: null,
  jdbcUpperBound: null,
  jdbcPartitions: 8,
  silverEnabled: false,
  pkCols: [],
  softDeleteCol: null,
  replicationEnabled: false,
  gcsBucket: '',
  gcsPrefix: '',
  gcsSecretArn: '',
  silverBqEnabled: false,
  silverBqBucket: '',
  silverBqPrefix: '',
  silverBqSecretArn: '',
  silverBqProject: '',
  silverBqDataset: '',
  silverBqLocation: 'EU',
  frequency: 'daily',
  notNullCols: [],
  minRowsExpected: null,
  workerType: 'G.1X',
  numWorkers: 2,
  pipelineId: '',
}

const SECRET = 'arn:aws:secretsmanager:eu-west-1:123456789012:secret:echo/dev/gcp-sa'

/** AWS, incremental, silver on, GCS replication and Silver→BQ mirror on. */
export const AWS_INCREMENTAL_SILVER: NewSyncState = {
  ...BASE,
  connection: { name: 'fipm', arn: 'arn:aws:secretsmanager:eu-west-1:123456789012:secret:echo/dev/oracle-fipm' },
  watermarkCol: 'LAST_UPDATE_DATE',
  watermarkFallbackCol: 'CREATION_DATE',
  partitionCol: 'ORGANIZATION_ID',
  jdbcLowerBound: 1,
  jdbcUpperBound: 9000,
  jdbcPartitions: 8,
  silverEnabled: true,
  pkCols: ['INVENTORY_ITEM_ID', 'ORGANIZATION_ID'],
  softDeleteCol: 'ACTIVE_FLAG',
  replicationEnabled: true,
  gcsBucket: 'echo-dev-bronze',
  gcsPrefix: 'bronze/fipm/x',
  gcsSecretArn: SECRET,
  silverBqEnabled: true,
  silverBqBucket: 'echo-dev-silver',
  silverBqPrefix: 'silver/fipm/x/',
  silverBqSecretArn: SECRET,
  silverBqProject: 'informatica-etl-nonprd',
  silverBqDataset: 'echo_dev_silver_fipm',
  silverBqLocation: 'EU',
  frequency: 'daily',
  notNullCols: ['INVENTORY_ITEM_ID', 'ORGANIZATION_ID'],
  minRowsExpected: 1155,
  workerType: 'G.2X',
  numWorkers: 5,
  pipelineId: 'fipm-x-daily',
}

/** GCP, full load, silver off, no minimum row count. */
export const GCP_FULL_NO_SILVER: NewSyncState = {
  ...BASE,
  destinationCloud: 'gcp',
  connection: { name: 'eds', arn: 'arn:aws:secretsmanager:eu-west-1:123456789012:secret:echo/dev/oracle-eds' },
  gcpSecretArn: SECRET,
  bronzeGcsPath: 'gs://echo-dev-bronze/fipm/x/',
  extractMode: 'full',
  watermarkCol: 'LAST_UPDATE_DATE',
  frequency: 'weekly',
  notNullCols: ['INVENTORY_ITEM_ID'],
  minRowsExpected: null,
  pipelineId: 'fipm-x-weekly',
}

/** GCP with silver on but a bad bronze path and no BigQuery target: fails validation. */
export const GCP_INVALID: NewSyncState = {
  ...GCP_FULL_NO_SILVER,
  bronzeGcsPath: 's3://wrong/',
  silverEnabled: true,
  pkCols: ['INVENTORY_ITEM_ID'],
  pipelineId: 'fipm-x-bad',
}
