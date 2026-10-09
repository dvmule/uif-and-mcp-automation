export type Env = 'dev' | 'uat' | 'prod'

export type PipelineStatus = 'RUNNING' | 'SUCCEEDED' | 'FAILED' | 'WARNING' | 'SKIPPED' | 'DISABLED'
export type RunStatus = 'RUNNING' | 'SUCCEEDED' | 'FAILED' | 'WARNING' | 'SKIPPED'
export type Frequency = 'hourly' | 'daily' | 'weekly' | 'reconcile'
export type ExtractMode = 'full' | 'incremental'
export type WorkerType = 'G.1X' | 'G.2X' | 'G.4X'

// Destination cloud. AWS = Bronze on S3 + Silver on Iceberg+Glue catalog.
// GCP = Bronze on GCS + Silver on Iceberg+BigLake. The shape of `target` and
// `silver` differs per cloud (s3_path vs gcs_path, database vs bq_*). Defaults
// to 'aws' when `destination` block is absent.
export type DestinationCloud = 'aws' | 'gcp'

export interface PipelineDestination {
  cloud?: DestinationCloud
  gcp_credentials_secret_arn?: string
}

export interface PipelineSource {
  secret_arn: string
  jdbc_extra_options?: string
}

export interface PipelineExtract {
  table: string
  mode: ExtractMode
  watermark_col?: string | null
  watermark_fallback_col?: string | null
  watermark_value?: string | null
  jdbc_partition_col?: string | null
  jdbc_lower_bound?: number | null
  jdbc_upper_bound?: number | null
  jdbc_partitions?: number | null
  custom_sql?: string | null
}

export interface PipelineTarget {
  // AWS pipelines write to s3_path. GCP pipelines write to gcs_path.
  // Exactly one is populated per pipeline; helpers in lib/cloud.ts normalize.
  s3_path?: string
  gcs_path?: string
  compression?: string
}

export interface PipelineQuality {
  not_null_cols?: string[]
  min_rows_expected?: number
}

export interface PipelineGlue {
  worker_type: WorkerType
  num_workers: number
  timeout_minutes: number
}

export interface PipelineSchedule {
  frequency: Frequency
  /** Daily Bronze stagger slot (0–11). 05:30 UTC = slot 0 … 11:00 UTC = slot 11. Missing → slot 0. */
  daily_slot?: number
}

export interface PipelineSilver {
  enabled?: boolean
  // Storage path — s3_path for AWS, gcs_path for GCP.
  s3_path?: string
  gcs_path?: string
  // Catalog target — `database` for AWS Glue, `bq_*` trio for GCP BigQuery.
  database?: string
  table?: string
  bq_project?: string
  bq_dataset?: string
  bq_location?: string
  primary_key_cols: string[]
  soft_delete?: {
    flag_col: string
    inactive_val: string
  } | null
}

export interface PipelineReconcile {
  enabled: boolean
  dry_run?: boolean
  pk_partition_col?: string
  pk_lower_bound?: number
  pk_upper_bound?: number
  pk_partitions?: number
  max_deletes_per_run?: number
}

export interface PipelineReplicationGcs {
  enabled: boolean
  bucket: string
  prefix: string
  credentials_secret_arn: string
}

export interface PipelineReplication {
  gcs?: PipelineReplicationGcs
  /** Silver mirror to GCS + BigLake; the wizard writes it (lib/newSyncConfig.ts). */
  silver_bq?: PipelineReplicationGcs & { bq_project?: string; bq_dataset?: string; bq_location?: string }
}

export interface PipelineMetadata {
  registered_at?: string
  last_toggled_at?: string
  owner?: string
  // Filter dropdowns on Syncs list read these. Server auto-inits when
  // absent (see pipeline_controller._apply_metadata_defaults).
  domain?: string         // 'Inventory' | 'Catalog' | 'General Ledger' | …
  source_system?: string  // 'Oracle EBS' for every sync today
  tags?: string[]         // free-form, e.g. ['PII', 'production-critical']
  deleted_at?: string     // set by soft-delete; item hidden from list UI
}

export interface PipelineConfig {
  pipeline_id: string
  version: string
  enabled: boolean
  destination?: PipelineDestination
  source: PipelineSource
  extract: PipelineExtract
  target: PipelineTarget
  quality?: PipelineQuality
  glue: PipelineGlue
  schedule: PipelineSchedule
  silver?: PipelineSilver
  reconcile?: PipelineReconcile
  replication?: PipelineReplication
  metadata?: PipelineMetadata
}

export interface QualityResult {
  not_null_checks?: Record<string, { passed: boolean; null_count: number }>
  min_rows?: { passed: boolean; actual: number; expected: number }
}

export interface RunMetrics {
  job_name: string
  run_id: string
  pipeline_id: string
  status: RunStatus
  started_at: string
  completed_at?: string | null
  duration_sec?: number | null
  /** Rows read from source (Oracle on Bronze; Bronze staging on Silver). */
  rows_read?: number | null
  /** Rows written to destination (Bronze GCS/S3 or Silver BQ/Iceberg). */
  rows_written?: number | null
  /** @deprecated Prefer rows_read — kept for legacy callers. */
  rows_extracted?: number | null
  rows_quality_failed?: number | null
  new_watermark?: string | null
  error_message?: string | null
  quality_result?: QualityResult | null
  phase?: 'initial' | 'incremental' | null
  /** Bronze Glue metrics field — normalized into `phase` at fetch time. */
  load_phase?: 'initial' | 'incremental' | null
  /** Set by gcs_replicator / silver_bq_replicator Lambdas (not Glue `status`). */
  replication_status?: string | null
  bq_status?: string | null
  files_replicated?: number | null
  files_skipped?: number | null
  files_failed?: number | null
  bytes_replicated?: number | null
  gcs_destination?: string | null
  metadata_pointer?: string | null
}

/** One Silver table a gold entity reads. `silver_table` is `db.table` and the
 *  db keeps an unresolved `{env}` placeholder, resolved at the point of use. */
export interface GoldSource {
  alias: string
  silver_table: string
}

/** A gold pipeline config, as GET /gold-pipelines returns it. Only the fields
 *  the UI reads are declared; the row carries far more. */
export interface GoldPipelineConfig {
  gold_id: string
  enabled?: boolean
  domain?: string
  sources?: GoldSource[]
  /** The Gold table this entity writes. `database` carries an unresolved
   *  `{env}` placeholder, like the source names. */
  target?: { database?: string; table?: string }
  /** Gold cadences are `gold-24h`, `gold-1h`…; `start_time` is UTC `HH:MM`. */
  schedule?: {
    frequency?: string
    start_time?: string
    /** gold-weekly only (UIFPE-532): UTC weekdays, MON..SUN. Console-owned like start_time. */
    weekly_days?: string[]
    failure_cooldown_minutes?: number
    recompute?: { every_n_runs?: number; undetectable_columns?: string[] }
  }
  /** When the entity last ran — not how it ended; that needs /gold-runs. */
  metadata?: { last_run_at?: string; last_run_id?: string; domain?: string; owner?: string }
  glue?: {
    worker_type?: string
    num_workers?: number
    timeout_minutes?: number
    full_load?: { worker_type?: string; num_workers?: number; timeout_minutes?: number }
  }
  /** Runtime-owned (ADR-0020): ONE bookmark per entity - the start of the
   *  last run that processed changes - and the silver snapshot each source
   *  was last read from. */
  watermark?: {
    bookmark?: string | null
    silver_snapshots?: Record<string, string>
    /** @deprecated pre-ADR-0020 per-source map; the first run after UIFPE-522
     *  removes it from the row. Never render it. */
    last_processed_silver_updated_at?: Record<string, string>
  }
  snapshot_gate?: boolean
}

/** What the shared run cards (history chart, story line) actually read: a run
 *  of either layer. Bronze/silver carry pipeline_id, gold carries gold_id. */
export type RunLike = Omit<RunMetrics, 'pipeline_id'> & { pipeline_id?: string }

/** A gold transform run: the same metrics row as RunMetrics, but keyed by the
 *  gold entity — gold writes no pipeline_id, phase or watermark. */
export interface GoldRunMetrics extends Omit<RunMetrics, 'pipeline_id'> {
  gold_id: string
  gold_table?: string | null
  /** Keys CDC selected for this run. NOT a rival to rows_written, which is
   *  rows whose hash actually moved — 0 on a success means nothing changed. */
  affected_keys?: number | null
}

export interface GoldRunPage {
  items: GoldRunMetrics[]
  truncated: boolean
}

export interface RunPage {
  items: RunMetrics[]
  next_token?: string | null
}

export interface AnalyzeColumn {
  name: string
  data_type: string
  nullable: boolean
  sample_values?: string[]
}

export interface AnalyzeResult {
  table: string
  row_count: number
  columns: AnalyzeColumn[]
  min_pk?: number | null
  max_pk?: number | null
  suggested_watermark_col?: string | null
  suggested_pk_cols?: string[]
  suggested_partition_col?: string | null
  // Partition strategy recommendations
  partition_strategy?: 'numeric_range' | 'date_id_range' | 'date_hash' | 'date_range' | null
  partition_date_col?: string | null
  partition_id_col?: string | null
  partition_hash_col?: string | null
  // JDBC partition configuration
  jdbc_partition_col?: string | null
  jdbc_lower_bound?: number | null
  jdbc_upper_bound?: number | null
  jdbc_partitions?: number | null
  jdbc_predicates?: string[] | null
  jdbc_predicates_count?: number | null
  // Partition bounds context
  date_min?: string | null
  date_max?: string | null
  id_min?: number | null
  id_max?: number | null
  // Sizing recommendations
  total_gb?: number | null
  worker_type?: 'G.1X' | 'G.2X' | 'G.4X' | null
  num_workers?: number | null
  // Analysis metadata
  warning?: string | null
  analyzed_at?: string | null
  status?: string | null
}

export interface Connection {
  name: string
  arn: string
  description?: string
}

// ── Gold schedule (UIFPE-513 / 532 / 564) ───────────────────────────
/** What the schedule dialog saves: always UTC, always the runtime's shape.
 *  Only the fields the operator changed are present. */
export interface GoldScheduleChange {
  start_time?: string
  /** A rung from GOLD_CADENCE_RUNGS; absent when the cadence did not change. */
  frequency?: string
  weekly_days?: string[]
}

/** The PATCH response: the schedule as STORED, which is how the console tells
 *  an applied change from a deployment that ignored a field it does not know. */
export interface GoldSchedulePatchResult {
  gold_id: string
  schedule: {
    frequency?: string
    start_time?: string
    weekly_days?: string[]
    [key: string]: unknown
  }
}

// ── Gold notifications (UIFPE-543) ──────────────────────────────────
// A row in the gold alert-subscribers table. `gold_ids === null` = every
// table plus the fleet-level alarms (the operator row); a list = only those
// tables, never fleet alarms (a self-service business user).
export interface AlertSubscriber {
  email: string
  enabled: boolean
  gold_ids: string[] | null
  added_by?: string | null
  added_at?: string | null
  updated_at?: string | null
}

export interface AlertSubscriberPatch {
  enabled?: boolean
  gold_ids?: string[] | null
  remove_gold_ids?: string[]
}

/** One gold CloudWatch alarm as the controller reads it: live, read-only. */
export interface AlertRuleStatus {
  name: string
  gold_id: string | null
  condition: string
  state: 'OK' | 'ALARM' | 'INSUFFICIENT_DATA' | string
  state_reason?: string | null
  state_updated_at?: string | null
  description?: string | null
  metric?: string | null
  namespace?: string | null
  threshold?: number | null
  comparison?: string | null
  period_seconds?: number | null
  evaluation_periods?: number | null
  emails: boolean
}

// ── Partner Export ─────────────────────────────────────────────────
// Mirrors `partner_export_config_{env}` DynamoDB schema introduced by Plan 7a.

export type PartnerDeliveryMethod = 'sftp' | 'https' | 's3' | 'gcs'
export type ExportFormat = 'csv' | 'json' | 'parquet' | 'fixed_width' | 'xml'
export type ExportFrequency = 'hourly' | 'daily' | 'weekly' | 'cron'
export type ExportRunStatus = RunStatus

export interface MappingField {
  source_col: string
  output_field: string
  type: 'string' | 'int' | 'decimal' | 'date' | 'datetime' | 'bool'
  format?: string | null
}

export interface ExportSourceRef {
  layer: 'silver' | 'gold'
  dataset: string
}

export interface ExportFormatConfig {
  kind: ExportFormat
  csv?: { delimiter: string; quote: string; header_row: boolean }
  json?: { lines: boolean }
  sql_transform?: string | null
  python_template?: string | null
}

export interface ExportDeliveryConfig {
  method: PartnerDeliveryMethod
  partner_id: string
  connection_arn: string
  path_template: string
}

export interface ExportScheduleConfig {
  frequency: ExportFrequency
  run_at?: string | null
  cron_expression?: string | null
  watermark_window?: string | null
  empty_file_behavior: 'skip' | 'empty' | 'fail'
  retention_days: number
  alerts: {
    on_failure: boolean
    on_partner_http_error: boolean
    on_zero_rows: boolean
    recipients: string[]
  }
}

export interface PartnerExport {
  export_id: string
  name: string
  enabled: boolean
  source: ExportSourceRef
  format: ExportFormatConfig
  mapping: MappingField[]
  delivery: ExportDeliveryConfig
  schedule: ExportScheduleConfig
  health?: 'healthy' | 'failing' | 'paused' | 'unknown'
  last_run?: ExportRun | null
}

export interface Partner {
  partner_id: string
  name: string
  contact_email?: string | null
  sla_notes?: string | null
  default_method?: PartnerDeliveryMethod | null
  export_count?: number
  health?: 'healthy' | 'failing' | 'paused' | 'unknown'
}

export interface FormatTemplate {
  format_id: string
  name: string
  description?: string | null
  config: ExportFormatConfig
  default_mapping?: MappingField[]
}

export interface ExportRun {
  run_id: string
  export_id: string
  status: ExportRunStatus
  started_at: string
  completed_at?: string | null
  duration_sec?: number | null
  rows_written?: number | null
  bytes_written?: number | null
  artifact_path?: string | null
  delivery_response?: string | null
  error_message?: string | null
}

export interface ExportRunPage {
  items: ExportRun[]
  next_token?: string | null
}

export interface ExportPreviewRequest {
  source: ExportSourceRef
  format: ExportFormatConfig
  mapping: MappingField[]
  sample_size?: number
}

export interface ExportPreviewResponse {
  rendered: string
  sample_rows: Array<Record<string, unknown>>
  row_count: number
  warnings: string[]
}

// Operations Dashboard (Home) — cross-module aggregations exposed at GET /home/*

export interface ModuleKpis {
  ok: number
  fail: number
  in_24h: number
}

export interface HomeSummary {
  syncs:   ModuleKpis
  exports: ModuleKpis
  dq:      ModuleKpis
  window_hours: number
  generated_at: string
}

export type ActivityModule = 'sync' | 'export' | 'dq'
export type ActivityStatus = 'SUCCEEDED' | 'FAILED' | 'RUNNING' | 'TIMEOUT' | 'STOPPED' | string

export interface ActivityItem {
  ts: string
  module: ActivityModule
  subject: string
  status: ActivityStatus
  message: string
  link_path: string
}

export interface RecentActivityResponse {
  items: ActivityItem[]
  count: number
}

// ── Catalog lineage ────────────────────────────────────────────────
// Cross-module surface returned by GET /catalog/lineage.
// Dataset grammar (resolved decision #3): <layer>.<cloud>.<database>.<table>

export interface LineageUpstream {
  sync_id: string
  sync_name: string
}

export interface LineageDownstreamExport {
  export_id: string
  name: string
}

export interface LineageDownstreamCheck {
  check_id: string
  name: string
}

export interface DatasetLineage {
  dataset: string
  upstream: LineageUpstream | null
  downstream_exports: LineageDownstreamExport[]
  downstream_checks: LineageDownstreamCheck[]
}

// A row in the layer-scoped Catalog browse list.
export interface CatalogDataset {
  // canonical reference, e.g. "silver.gcp.smoke_silver.author_data"
  dataset: string
  layer: 'bronze' | 'silver' | 'gold'
  cloud: 'aws' | 'gcp'
  database: string
  table: string
  // pipeline_id of the sync producing this dataset (when known).
  sync_id?: string | null
}

// ── Data Quality module ────────────────────────────────────────────
// Pluggable check kinds. v1 ships `reconcile` and `null-rate` live; the rest
// are stubs in the backend kinds catalog. UI renders them as "coming-soon".

export type DqCheckKind =
  | 'reconcile'
  | 'null-rate'
  | 'row-count'
  | 'freshness'
  | 'drift'
  | 'uniqueness'
  | 'custom-sql'

export interface DqCheckSubject {
  layer: 'silver' | 'gold'
  database: string
  table: string
}

export interface DqCheckSchedule {
  frequency: 'hourly' | 'daily' | 'weekly' | 'cron'
  cron?: string | null
}

export type DqRunStatus = 'SUCCEEDED' | 'FAILED' | 'SKIPPED' | 'NOT_IMPLEMENTED' | 'RUNNING'

export interface DqLastRun {
  ts: string
  status: DqRunStatus | null
  summary?: string
  run_id?: string
}

export interface DqCheckConfig {
  check_id:   string
  version:    'latest' | string
  kind:       DqCheckKind
  enabled:    boolean
  subject:    DqCheckSubject
  schedule:   DqCheckSchedule
  parameters: Record<string, unknown>
  metadata:   { created_at: string; owner: string; description?: string }
  last_run?:  DqLastRun | null
}
