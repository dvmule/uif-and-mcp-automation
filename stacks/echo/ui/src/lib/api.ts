import axios from 'axios'
import { useAuthStore } from '@/store/authStore'
import { useEnvStore } from '@/store/envStore'
import { begin, end } from './apiActivity'
import { normalizeRunMetrics } from './runLifecycle'
import type { GoldStatsResponse } from '@/lib/goldStats'
import type {
  AlertRuleStatus,
  GoldScheduleChange,
  GoldSchedulePatchResult,
  AlertSubscriber,
  AlertSubscriberPatch,
  AnalyzeResult,
  Connection,
  DatasetLineage,
  DqCheckConfig,
  ExportPreviewRequest,
  ExportPreviewResponse,
  ExportRunPage,
  FormatTemplate,
  HomeSummary,
  Partner,
  PartnerExport,
  PipelineConfig,
  RecentActivityResponse,
  RunMetrics,
  RunPage,
  GoldPipelineConfig,
  GoldRunMetrics,
  GoldRunPage,
} from './types'

function client() {
  const baseURL = useEnvStore.getState().baseUrl
  if (!baseURL) {
    throw new Error(
      `API base URL is not configured for env "${useEnvStore.getState().env}". ` +
      `Set VITE_API_URL (and VITE_ECHO_ENV) at UI build time.`
    )
  }

  const token = useAuthStore.getState().token
  const instance = axios.create({
    baseURL,
    timeout: 60_000,
    headers: token ? { Authorization: `Bearer ${token}` } : undefined,
  })

  // A 401 means the session is gone or rejected. Clearing the store is the
  // whole redirect: RequireAuth subscribes to it, so the guard re-renders and
  // sends the user to /login. No navigate() call here — that would mean
  // importing the router into this module and closing an import cycle.
  //
  // The sign-in request itself does NOT go through this client (lib/auth.ts
  // uses fetch), so a rejected credential cannot trip this handler.
  instance.interceptors.response.use(
    (res) => res,
    (error) => {
      if (error?.response?.status === 401) useAuthStore.getState().clear()
      return Promise.reject(error)
    },
  )

  // Feeds the shell's progress bar; runs after the 401 handler above.
  instance.interceptors.request.use((config) => {
    begin()
    return config
  })
  instance.interceptors.response.use(
    (res) => {
      end()
      return res
    },
    (error) => {
      end()
      return Promise.reject(error)
    },
  )

  return instance
}

export const api = {
  // Pipelines — backend wraps list in { pipelines: [], count: N }
  getPipelines: (): Promise<PipelineConfig[]> =>
    client().get('/pipelines').then(r => r.data?.pipelines ?? r.data ?? []),

  getPipeline: (id: string): Promise<PipelineConfig> =>
    client().get(`/pipelines/${id}`).then(r => r.data),

  upsertPipeline: (id: string, config: PipelineConfig): Promise<void> =>
    client().put(`/pipelines/${id}`, config).then(r => r.data),

  togglePipeline: (id: string, enabled: boolean): Promise<{ enabled: boolean }> =>
    client().patch(`/pipelines/${id}/toggle`, { enabled }).then(r => r.data),

  // `layer` selects which Glue job to fire — 'bronze' runs eds-{env}-bronze-ingest
  // (default), 'reconcile' routes to eds-{env}-silver-reconcile-{aws,gcp}.
  triggerRun: (
    id: string,
    opts?: { layer?: 'bronze' | 'reconcile' },
  ): Promise<{ run_id: string; phase?: 'initial' | 'incremental'; started_at?: string }> =>
    client()
      .post(`/pipelines/${id}/run`, undefined, opts?.layer ? { params: { layer: opts.layer } } : undefined)
      .then(r => r.data),

  deletePipeline: (id: string): Promise<void> =>
    client().delete(`/pipelines/${id}`).then(r => r.data),

  getDraft: (id: string): Promise<PipelineConfig | null> =>
    client().get(`/pipelines/${id}/draft`).then(r => r.data ?? null).catch(() => null),

  putDraft: (id: string, config: PipelineConfig): Promise<void> =>
    client().put(`/pipelines/${id}/draft`, config).then(r => r.data),

  deleteDraft: (id: string): Promise<void> =>
    client().delete(`/pipelines/${id}/draft`).then(r => r.data),

  resetWatermark: (id: string): Promise<void> =>
    client().patch(`/pipelines/${id}/watermark`, { value: null }).then(r => r.data),

  reBootstrap: (id: string): Promise<void> =>
    client().post(`/pipelines/${id}/re-bootstrap`).then(r => r.data),

  // Backend returns { pipeline_id, runs: [], next_token? } — map to RunPage shape.
  // Glue writes rows_written/watermark_end/null_counts; normalize to UI field names.
  getRuns: (id: string, nextToken?: string): Promise<RunPage> =>
    client().get(`/pipelines/${id}/runs`, {
      params: nextToken ? { next_token: nextToken } : undefined,
    }).then(r => ({
      items: (r.data?.runs ?? r.data?.items ?? []).map((run: Record<string, unknown>) =>
        normalizeRunMetrics({
          ...run,
          quality_result: typeof run.null_counts === 'string'
            ? (() => { try { return { null_counts: JSON.parse(run.null_counts as string) } } catch { return null } })()
            : (run.quality_result ?? null),
        }),
      ),
      next_token: r.data?.next_token ?? null,
    })),

  getGoldPipelines: (): Promise<GoldPipelineConfig[]> =>
    client().get('/gold-pipelines').then(r => r.data?.items ?? r.data ?? []),

  // The body MUST carry a JSON boolean: the Lambda 400s without `enabled`, and
  // coerces with bool(), so the string "false" would store true.
  toggleGoldPipeline: (id: string, enabled: boolean): Promise<{ gold_id: string; enabled: boolean }> =>
    client().patch(`/gold-pipelines/${id}/toggle`, { enabled }).then(r => r.data),

  // Returns as soon as Glue accepts the job; `run_id` is the Glue JobRunId.
  triggerGoldRun: (
    id: string,
  ): Promise<{ gold_id: string; kind: 'transform' | 'reconcile'; run_id: string; rebuild: boolean }> =>
    client().post(`/gold-pipelines/${id}/run`, {}).then(r => r.data),

  // The console-owned schedule fields (UIFPE-513, UIFPE-532): start_time, and
  // on the weekly rung the UTC day set. Cadence stays in YAML. Strict 'HH:MM'
  // and MON..SUN tokens - the server rejects anything else with a 400. Only
  // the fields given are sent, so a plain time change never touches the days.
  // PATCH .../schedule - the console-owned schedule (UIFPE-513/532/564):
  // frequency, start_time and, on the weekly rung, weekly_days. ONLY the fields
  // the operator changed are sent, and the response echoes the STORED schedule
  // so the caller can prove a deployment understood them (useSetGoldSchedule).
  setGoldSchedule: (
    id: string,
    change: GoldScheduleChange,
  ): Promise<GoldSchedulePatchResult> =>
    client().patch(`/gold-pipelines/${id}/schedule`, {
      ...(change.frequency ? { frequency: change.frequency } : {}),
      ...(change.start_time ? { start_time: change.start_time } : {}),
      ...(change.weekly_days ? { weekly_days: change.weekly_days } : {}),
    }).then(r => r.data),

  // Gold runs come from one collection-level call, not per entity: they carry
  // gold_id and so are absent from the pipeline_id index /pipelines/{id}/runs
  // queries (UIFPE-502).
  getGoldRuns: (goldId?: string, limit?: number): Promise<GoldRunPage> =>
    client().get('/gold-runs', {
      params: {
        ...(goldId ? { gold_id: goldId } : {}),
        ...(limit ? { limit } : {}),
      },
    }).then(r => ({
      items: (r.data?.runs ?? []).map((run: Record<string, unknown>) =>
        normalizeRunMetrics(run) as unknown as GoldRunMetrics,
      ),
      truncated: Boolean(r.data?.truncated),
    })),

  getRun: (id: string, runId: string): Promise<RunMetrics> =>
    client().get(`/pipelines/${id}/runs/${runId}`).then(r => normalizeRunMetrics(r.data)),

  // Analysis — synchronous (legacy, for quick tables)
  analyzeTable: (tableRef: string, secretArn: string): Promise<AnalyzeResult> =>
    client().post('/analyze', { table: tableRef, secret_arn: secretArn }).then(r => r.data),

  // Analysis — asynchronous (for large tables, returns run_id for polling)
  analyzeTableAsync: (tableRef: string, secretArn: string): Promise<{ run_id: string; status: string }> =>
    client().post('/analyze-async', { table: tableRef, secret_arn: secretArn }).then(r => r.data),

  // Poll async analysis status and get results when complete
  getAnalysisStatus: (runId: string, tableRef: string): Promise<{
    run_id: string;
    status: 'RUNNING' | 'SUCCEEDED' | 'FAILED';
    result?: AnalyzeResult;
    glue_status?: string;
  }> =>
    client().get(`/analyze-async/${runId}`, { params: { table: tableRef } }).then(r => r.data),

  // Connections — backend returns flat array
  getConnections: (): Promise<Connection[]> =>
    client().get('/connections').then(r => Array.isArray(r.data) ? r.data : []),

  // GCS service-account secrets, tagged purpose=gcs-replication
  getGcsConnections: (): Promise<Connection[]> =>
    client().get('/connections/gcs').then(r => Array.isArray(r.data) ? r.data : []),

  // Silver→BigQuery service-account secrets, tagged purpose=silver-bq
  getSilverBqConnections: (): Promise<Connection[]> =>
    client().get('/connections/silver-bq').then(r => Array.isArray(r.data) ? r.data : []),

  // Gold notifications (UIFPE-543). POST is an upsert that MERGES: a second
  // table for an existing address is added to its set, never overwriting.
  // Omitting goldIds means "every table plus platform alerts".
  getAlertSubscribers: (): Promise<{ items: AlertSubscriber[]; total: number }> =>
    client().get('/notifications/subscribers').then(r => r.data),

  addAlertSubscriber: (email: string, goldIds?: string[]): Promise<AlertSubscriber> =>
    client().post('/notifications/subscribers',
      goldIds && goldIds.length ? { email, gold_ids: goldIds } : { email }).then(r => r.data),

  setAlertSubscriber: (email: string, patch: AlertSubscriberPatch): Promise<AlertSubscriber> =>
    client().patch(`/notifications/subscribers/${encodeURIComponent(email)}`, patch).then(r => r.data),

  removeAlertSubscriber: (email: string): Promise<void> =>
    client().delete(`/notifications/subscribers/${encodeURIComponent(email)}`).then(r => r.data),

  getAlertRules: (): Promise<{ items: AlertRuleStatus[]; total: number }> =>
    client().get('/notifications/rules').then(r => r.data),

  // The gold fleet Overview (UIFPE-544). ONE call for every entity: the live
  // 24h half is computed server-side from rows the controller already reads,
  // and the 30-day half is a file the nightly job writes.
  getGoldStats: (days?: number): Promise<GoldStatsResponse> =>
    client().get('/gold-stats', {
      params: { ...(days ? { days } : {}) },
    }).then(r => r.data),

  // Operations Dashboard (Home) — cross-module aggregations
  getHomeSummary: (): Promise<HomeSummary> =>
    client().get('/home/summary').then(r => r.data),

  getRecentActivity: (limit = 20): Promise<RecentActivityResponse> =>
    client().get('/home/recent-activity', { params: { limit } }).then(r => r.data),

  // Catalog — lineage for a single dataset.
  getLineage: (datasetRef: string): Promise<DatasetLineage> =>
    client().get('/catalog/lineage', { params: { dataset: datasetRef } }).then(r => r.data),

  // Data Quality — pluggable checks (reconcile + null-rate live in v1).
  dq: {
    listChecks: (params?: { subject?: string }): Promise<DqCheckConfig[]> =>
      client().get('/dq/checks', params?.subject ? { params } : undefined)
        .then(r => r.data?.checks ?? []),

    getCheck: (checkId: string): Promise<DqCheckConfig> =>
      client().get(`/dq/checks/${encodeURIComponent(checkId)}`).then(r => r.data),

    upsertCheck: (checkId: string, body: DqCheckConfig): Promise<DqCheckConfig> =>
      client().put(`/dq/checks/${encodeURIComponent(checkId)}`, body).then(r => r.data),

    deleteCheck: (checkId: string): Promise<{ deleted: string }> =>
      client().delete(`/dq/checks/${encodeURIComponent(checkId)}`,
                      { params: { cascade: true } }).then(r => r.data),

    triggerRun: (checkId: string): Promise<{ run_id: string; check_id: string; started_at: string }> =>
      client().post(`/dq/checks/${encodeURIComponent(checkId)}/trigger-run`).then(r => r.data),

    listCheckRuns: (checkId: string): Promise<{ check_id: string; runs: Array<Record<string, unknown>> }> =>
      client().get(`/dq/checks/${encodeURIComponent(checkId)}/runs`).then(r => r.data),

    listKinds: (): Promise<{ kinds: Array<{ kind: string; status: 'live' | 'coming-soon'; parameters: string[] }> }> =>
      client().get('/dq/kinds').then(r => r.data),
  },

  // Partner Export — Plan 7a backend contract.
  partnerExport: {
    listExports: (params?: { partner?: string; source?: string }): Promise<PartnerExport[]> =>
      client().get('/partner-export/exports', { params }).then(r => r.data?.exports ?? r.data ?? []),

    getExport: (id: string): Promise<PartnerExport> =>
      client().get(`/partner-export/exports/${id}`).then(r => r.data),

    upsertExport: (id: string, body: PartnerExport): Promise<void> =>
      client().put(`/partner-export/exports/${id}`, body).then(r => r.data),

    deleteExport: (id: string): Promise<void> =>
      client().delete(`/partner-export/exports/${id}`, { params: { cascade: true } }).then(r => r.data),

    runExport: (id: string): Promise<{ run_id: string }> =>
      client().post(`/partner-export/exports/${id}/trigger-run`).then(r => r.data),

    listExportRuns: (id: string, nextToken?: string): Promise<ExportRunPage> =>
      client()
        .get(`/partner-export/exports/${id}/runs`, {
          params: nextToken ? { next_token: nextToken } : undefined,
        })
        .then(r => ({
          items: r.data?.runs ?? r.data?.items ?? [],
          next_token: r.data?.next_token ?? null,
        })),

    listRuns: (nextToken?: string): Promise<ExportRunPage> =>
      client()
        .get('/partner-export/runs', {
          params: nextToken ? { next_token: nextToken } : undefined,
        })
        .then(r => ({
          items: r.data?.runs ?? r.data?.items ?? [],
          next_token: r.data?.next_token ?? null,
        })),

    listPartners: (): Promise<Partner[]> =>
      client().get('/partner-export/partners').then(r => r.data?.partners ?? r.data ?? []),

    getPartner: (id: string): Promise<Partner> =>
      client().get(`/partner-export/partners/${id}`).then(r => r.data),

    createPartner: (body: Omit<Partner, 'partner_id'> & { partner_id?: string }): Promise<Partner> =>
      client().post('/partner-export/partners', body).then(r => r.data),

    listFormats: (): Promise<FormatTemplate[]> =>
      client().get('/partner-export/formats').then(r => r.data?.formats ?? r.data ?? []),

    preview: (body: ExportPreviewRequest): Promise<ExportPreviewResponse> =>
      client().post('/partner-export/preview', body).then(r => r.data),
  },
}
