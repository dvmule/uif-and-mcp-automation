// The run row every runs table and the run drawer share (UIFPE-681). Pure: no React.
import {
  formatRunKindLabel,
  resolveRunLayer,
  resolveRunPhase,
} from './runKindLabel'
import { runSourceRows, runTargetRows } from './runRowCounts'
import { isInFlightStatus, normalizeRunStatus } from './runLifecycle'
import { runStatusView, type StatusView } from './statusVocabulary'
import type { GoldPipelineConfig, GoldRunMetrics, PipelineConfig, RunMetrics } from './types'
import { parseDateMs } from './utils'

export type RunLayer = 'bronze' | 'silver' | 'gold' | 'reconcile' | 'other'

export const RUN_LAYERS: RunLayer[] = ['bronze', 'silver', 'gold', 'reconcile', 'other']

export const RUN_LAYER_LABEL: Record<RunLayer, string> = {
  bronze: 'Bronze',
  silver: 'Silver',
  gold: 'Gold',
  reconcile: 'Reconcile',
  other: 'Other',
}

export interface RunRow {
  key: string
  runId: string
  object: string
  objectSub?: string
  /** Where the object opens; null when the run has no page of its own. */
  objectHref: string | null
  layer: RunLayer
  kind: string
  status: string
  startedAt: string | null
  durationSec: number | null
  rowsRead: number | null
  rowsWritten: number | null
  run: RunMetrics | GoldRunMetrics
  /** Set on bronze/silver/reconcile rows. Retry needs it. */
  pipelineId?: string
  /** Set on gold rows. */
  goldId?: string
}

function pipelineLayer(jobName: string): RunLayer {
  const layer = resolveRunLayer(jobName ?? '')
  return layer === 'bronze' || layer === 'silver' || layer === 'gold' || layer === 'reconcile'
    ? layer
    : 'other'
}

/** Short kind for the Kind column: the phase for bronze and silver, since the
 *  Layer column already says which. */
function pipelineKind(run: RunMetrics, layer: RunLayer): string {
  if (layer === 'bronze' || layer === 'silver') {
    const phase = resolveRunPhase(run)
    return phase.charAt(0).toUpperCase() + phase.slice(1)
  }
  if (layer === 'reconcile') return 'Reconcile'
  return formatRunKindLabel(run)
}

export function pipelineRunRow(p: PipelineConfig, run: RunMetrics): RunRow {
  const layer = pipelineLayer(run.job_name)
  const table = p.extract?.table
  return {
    key: `${p.pipeline_id}:${run.run_id}`,
    runId: run.run_id,
    object: table ?? p.pipeline_id,
    objectSub: table && table !== p.pipeline_id ? p.pipeline_id : undefined,
    objectHref: `/m/data-sync/syncs/${p.pipeline_id}`,
    layer,
    kind: pipelineKind(run, layer),
    status: run.status,
    startedAt: run.started_at ?? null,
    durationSec: run.duration_sec ?? null,
    rowsRead: runSourceRows(run),
    rowsWritten: runTargetRows(run),
    run,
    pipelineId: p.pipeline_id,
  }
}

export function goldRunRow(run: GoldRunMetrics, gold?: GoldPipelineConfig): RunRow {
  const table = run.gold_table ?? gold?.target?.table ?? undefined
  return {
    key: `gold:${run.gold_id}:${run.run_id}`,
    runId: run.run_id,
    object: run.gold_id,
    objectSub: table && table !== run.gold_id ? table : undefined,
    objectHref: `/m/data-sync/gold/${run.gold_id}`,
    layer: 'gold',
    kind: 'Transform',
    status: run.status,
    startedAt: run.started_at ?? null,
    durationSec: run.duration_sec ?? null,
    // A gold run's rows_read is the silver side; the table has no comparable
    // "read" figure of its own, so the left side of the arrow stays empty.
    rowsRead: run.rows_read ?? null,
    rowsWritten: run.rows_written ?? null,
    run,
    goldId: run.gold_id,
  }
}

export type StatusBucket = 'SUCCEEDED' | 'WARNING' | 'FAILED' | 'RUNNING' | 'SKIPPED'

/** The five words the page filters and counts by. STOPPED is an operator
 *  action, not a failure, so it counts as a warning (as runStatusView shows it). */
export function statusBucket(status: string | null | undefined): StatusBucket {
  if ((status ?? '').toUpperCase() === 'STOPPED') return 'WARNING'
  return normalizeRunStatus(status)
}

/** A gold success that wrote nothing means no silver change moved a hash. */
export function isGoldNoChanges(row: RunRow): boolean {
  return row.layer === 'gold' && statusBucket(row.status) === 'SUCCEEDED' && row.rowsWritten === 0
}

export function runRowStatusView(row: RunRow): StatusView {
  return runStatusView(row.status, { goldNoChanges: isGoldNoChanges(row) })
}

// ---- filter -------------------------------------------------------------

export type RunsWindow = '24h' | '7d' | 'all'

/** Jobs shows one row per run, or one row per object. */
export type RunsView = 'runs' | 'objects'

export interface RunsFilter {
  window: RunsWindow
  status: string[]
  layer: RunLayer[]
  /** pipeline_id or gold_id. */
  object: string[]
  search: string
  view: RunsView
}

/** The 24 h / 7 d window select on Overview and Coming up. */
export type Span = '24h' | '7d'

export function parseSpan(raw: string | null): Span {
  return raw === '7d' ? '7d' : '24h'
}

export const DEFAULT_RUNS_WINDOW: RunsWindow = '24h'

export const EMPTY_RUNS_FILTER: RunsFilter = {
  window: DEFAULT_RUNS_WINDOW,
  status: [],
  layer: [],
  object: [],
  search: '',
  view: 'runs',
}

const WINDOW_HOURS: Record<Exclude<RunsWindow, 'all'>, number> = { '24h': 24, '7d': 168 }
const BUCKETS: StatusBucket[] = ['SUCCEEDED', 'WARNING', 'FAILED', 'RUNNING', 'SKIPPED']

function list(raw: string | null): string[] {
  if (!raw) return []
  return raw.split(',').map((s) => s.trim()).filter(Boolean)
}

function unique<T>(values: T[]): T[] {
  return Array.from(new Set(values))
}

export function parseRunsWindow(raw: string | null): RunsWindow {
  return raw === '7d' || raw === 'all' ? raw : DEFAULT_RUNS_WINDOW
}

/** URL keys: window, status, layer, object, q, view. The old `sync=` link from a
 *  sync's Recent runs card still works: it reads as an object filter, and with
 *  no window given it looks back as far as the runs loaded ("View all N runs"
 *  must not hide a sync's older runs behind the 24 h default). */
export function parseRunsFilter(params: URLSearchParams): RunsFilter {
  const status = unique(
    list(params.get('status'))
      .map((s) => s.toUpperCase())
      .filter((s): s is StatusBucket => (BUCKETS as string[]).includes(s)),
  )
  const layer = unique(
    list(params.get('layer'))
      .map((s) => s.toLowerCase())
      .filter((s): s is RunLayer => (RUN_LAYERS as string[]).includes(s)),
  )
  const legacy = params.get('sync')?.trim()
  const object = unique([...list(params.get('object')), ...(legacy ? [legacy] : [])])
  return {
    window: legacy && !params.has('window') ? 'all' : parseRunsWindow(params.get('window')),
    status,
    layer,
    object,
    search: params.get('q')?.trim() ?? '',
    view: params.get('view') === 'objects' ? 'objects' : 'runs',
  }
}

/** A new URLSearchParams with the filter keys rewritten and every other key
 *  (`run`, for one) kept. Defaults are left out so the plain page has a plain URL. */
export function serializeRunsFilter(f: RunsFilter, base?: URLSearchParams): URLSearchParams {
  const next = new URLSearchParams(base?.toString() ?? '')
  next.delete('sync')
  if (f.window === DEFAULT_RUNS_WINDOW) next.delete('window')
  else next.set('window', f.window)
  const setList = (key: string, values: string[]) => {
    if (values.length === 0) next.delete(key)
    else next.set(key, values.join(','))
  }
  setList('status', f.status)
  setList('layer', f.layer)
  setList('object', f.object)
  if (f.search.trim()) next.set('q', f.search.trim())
  else next.delete('q')
  if (f.view === 'objects') next.set('view', 'objects')
  else next.delete('view')
  return next
}

/** The view is not a filter, so it is not looked at. */
export function isDefaultRunsFilter(f: Omit<RunsFilter, 'view'>): boolean {
  return (
    f.window === DEFAULT_RUNS_WINDOW &&
    f.status.length === 0 &&
    f.layer.length === 0 &&
    f.object.length === 0 &&
    f.search.trim() === ''
  )
}

export function filterRunRows(rows: RunRow[], f: Omit<RunsFilter, 'view'>, now: Date = new Date()): RunRow[] {
  const cutoff = f.window === 'all' ? null : now.getTime() - WINDOW_HOURS[f.window] * 3_600_000
  const needle = f.search.trim().toLowerCase()
  return rows.filter((row) => {
    if (cutoff !== null) {
      const at = parseDateMs(row.startedAt)
      if (at === null || at < cutoff) return false
    }
    if (f.status.length > 0 && !f.status.includes(statusBucket(row.status))) return false
    if (f.layer.length > 0 && !f.layer.includes(row.layer)) return false
    if (f.object.length > 0) {
      const id = row.pipelineId ?? row.goldId
      if (!id || !f.object.includes(id)) return false
    }
    if (needle) {
      const hay = `${row.object} ${row.objectSub ?? ''} ${row.runId} ${row.pipelineId ?? ''} ${row.goldId ?? ''}`
      if (!hay.toLowerCase().includes(needle)) return false
    }
    return true
  })
}

/** Newest first; a run with no start time sinks. */
export function sortRunRows(rows: RunRow[], direction: 'asc' | 'desc' = 'desc'): RunRow[] {
  const dir = direction === 'desc' ? -1 : 1
  return [...rows].sort((a, b) => {
    const x = parseDateMs(a.startedAt) ?? (direction === 'desc' ? -Infinity : Infinity)
    const y = parseDateMs(b.startedAt) ?? (direction === 'desc' ? -Infinity : Infinity)
    if (x === y) return 0
    return x < y ? -dir : dir
  })
}

// ---- objects ------------------------------------------------------------

export interface ObjectGroup {
  /** pipeline_id, or `gold:{gold_id}` (the key lib/upcoming uses). */
  key: string
  object: string
  objectHref: string | null
  /** Distinct, in bronze → silver → gold → reconcile order. */
  layers: RunLayer[]
  /** Newest first. */
  runs: RunRow[]
  /** Oldest → newest, at most five. */
  lastFive: RunRow[]
  latest: RunRow | null
  finished: number
  /** SUCCEEDED + WARNING, as Beacon counts. */
  succeeded: number
  /** Null when no run has finished. */
  successPct: number | null
}

/** The object a run belongs to, keyed as lib/upcoming keys it. */
export function runObjectKey(row: RunRow): string {
  if (row.pipelineId) return row.pipelineId
  if (row.goldId) return `gold:${row.goldId}`
  return row.object
}

/** One group per object, the most recently run first. Pure. */
export function groupRunsByObject(rows: RunRow[]): ObjectGroup[] {
  const byKey = new Map<string, RunRow[]>()
  for (const row of rows) {
    const key = runObjectKey(row)
    const held = byKey.get(key)
    if (held) held.push(row)
    else byKey.set(key, [row])
  }
  const groups = Array.from(byKey, ([key, members]): ObjectGroup => {
    const runs = sortRunRows(members, 'desc')
    const buckets = runs.map((r) => statusBucket(r.status))
    const finished = buckets.filter((b) => b === 'SUCCEEDED' || b === 'WARNING' || b === 'FAILED').length
    const succeeded = buckets.filter((b) => b === 'SUCCEEDED' || b === 'WARNING').length
    const present = new Set(runs.map((r) => r.layer))
    return {
      key,
      object: runs[0].object,
      objectHref: runs[0].objectHref,
      layers: RUN_LAYERS.filter((l) => present.has(l)),
      runs,
      lastFive: runs.slice(0, 5).reverse(),
      latest: runs[0] ?? null,
      finished,
      succeeded,
      successPct: finished ? Math.round((succeeded / finished) * 100) : null,
    }
  })
  const at = (g: ObjectGroup) => parseDateMs(g.latest?.startedAt) ?? -Infinity
  return groups.sort((a, b) => (at(a) === at(b) ? 0 : at(a) < at(b) ? 1 : -1))
}

// ---- counts -------------------------------------------------------------

type CountKey = 'succeeded' | 'warning' | 'failed' | 'running'
type Counts = Record<CountKey, number>

export interface StatusCounts extends Counts {
  byLayer: Record<RunLayer, Counts>
}

function zero(): Counts {
  return { succeeded: 0, warning: 0, failed: 0, running: 0 }
}

/** Skipped runs are in no bucket: they are neither good nor bad news. */
export function countByStatus(rows: RunRow[]): StatusCounts {
  const total = zero()
  const byLayer = Object.fromEntries(RUN_LAYERS.map((l) => [l, zero()])) as Record<RunLayer, Counts>
  for (const row of rows) {
    const bucket = isInFlightStatus(row.status) ? 'RUNNING' : statusBucket(row.status)
    const key: CountKey | null =
      bucket === 'SUCCEEDED' ? 'succeeded'
      : bucket === 'WARNING' ? 'warning'
      : bucket === 'FAILED' ? 'failed'
      : bucket === 'RUNNING' ? 'running'
      : null
    if (!key) continue
    total[key] += 1
    byLayer[row.layer][key] += 1
  }
  return { ...total, byLayer }
}

// ---- formatting ---------------------------------------------------------

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

/** "29 Sep 2026, 11:59 UTC"; an em dash when the value is missing or unreadable. */
export function formatUtcStamp(iso: string | null | undefined): string {
  const ms = parseDateMs(iso)
  if (ms === null) return '—'
  const d = new Date(ms)
  const hh = String(d.getUTCHours()).padStart(2, '0')
  const mm = String(d.getUTCMinutes()).padStart(2, '0')
  return `${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]} ${d.getUTCFullYear()}, ${hh}:${mm} UTC`
}

/** "12:16 UTC". */
export function formatUtcClockTime(iso: string | null | undefined): string {
  const ms = parseDateMs(iso)
  if (ms === null) return '—'
  return `${new Date(ms).toISOString().slice(11, 16)} UTC`
}

// ---- csv ----------------------------------------------------------------

function csvCell(value: string | number | null | undefined): string {
  const text = value == null ? '' : String(value)
  return /[",\r\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text
}

const CSV_HEADER = [
  'Started (UTC)', 'Object', 'Layer', 'Kind', 'Status',
  'Duration (s)', 'Rows read', 'Rows written', 'Run id',
]

/** RFC 4180: a header line, one line per row, CRLF between lines, and quotes
 *  around any cell holding a comma, a quote or a line break. */
export function runsToCsv(rows: RunRow[]): string {
  const lines = [CSV_HEADER.map(csvCell).join(',')]
  for (const row of rows) {
    lines.push(
      [
        row.startedAt ?? '',
        row.object,
        RUN_LAYER_LABEL[row.layer],
        row.kind,
        runRowStatusView(row).label,
        row.durationSec,
        row.rowsRead,
        row.rowsWritten,
        row.runId,
      ].map(csvCell).join(','),
    )
  }
  return lines.join('\r\n')
}
