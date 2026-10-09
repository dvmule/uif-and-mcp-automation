import { useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Download, History, SearchX } from 'lucide-react'
import { CountSegment, EmptyState, ListCard, Note, PageHeader, SegmentedControl } from '@/components/relay'
import { Button } from '@/components/ui/button'
import { RunDrawer } from '@/components/data-sync/detail/drawer/RunDrawer'
import { useRunDrawerParam } from '@/components/data-sync/detail/drawer/useRunDrawerParam'
import { ObjectsTable } from '@/components/data-sync/runs/ObjectsTable'
import { RunsFilters, type ObjectOption } from '@/components/data-sync/runs/RunsFilters'
import { RunsTable } from '@/components/data-sync/runs/RunsTable'
import { useAllRuns } from '@/components/data-sync/runs/useAllRuns'
import { useTriggerRun } from '@/hooks/usePipelines'
import { isInFlightStatus } from '@/lib/runLifecycle'
import {
  EMPTY_RUNS_FILTER,
  filterRunRows,
  groupRunsByObject,
  parseRunsFilter,
  runObjectKey,
  runsToCsv,
  serializeRunsFilter,
  sortRunRows,
  statusBucket,
  type RunRow,
  type RunsFilter,
  type RunsWindow,
} from '@/lib/runsTable'
import { objectSchedule } from '@/lib/upcoming'

const RANGES: Array<{ value: RunsWindow; label: string }> = [
  { value: '24h', label: '24 hours' },
  { value: '7d', label: '7 days' },
  { value: 'all', label: 'All loaded' },
]

const RANGE_WORDS: Record<RunsWindow, string> = { '24h': 'Last 24 hours', '7d': 'Last 7 days', all: 'All loaded' }

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`

/** Save the rows as a CSV file. Only what is loaded and filtered on screen. */
function downloadCsv(rows: RunRow[]) {
  try {
    const blob = new Blob([runsToCsv(rows)], { type: 'text/csv;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = `echo-jobs-${new Date().toISOString().slice(0, 10)}.csv`
    document.body.appendChild(link)
    link.click()
    link.remove()
    URL.revokeObjectURL(url)
  } catch {
    // a browser that cannot build the file: nothing to save, nothing to break
  }
}

/** Insights › Jobs (UIFPE-681): every run of every layer, filterable, each row
 *  opening the run drawer; or one row per object (`?view=objects`). */
export function Runs() {
  const [searchParams, setSearchParams] = useSearchParams()
  const filter = useMemo(() => parseRunsFilter(searchParams), [searchParams])
  const [sortDirection, setSortDirection] = useState<'asc' | 'desc'>('desc')
  const [pageIndex, setPageIndex] = useState(0)

  // A filter on some objects means only their pipelines' runs are fetched.
  const all = useAllRuns(filter.object.length > 0 ? filter.object : null)
  const { runId, open } = useRunDrawerParam()
  const trigger = useTriggerRun()

  const changeFilter = (next: RunsFilter) => {
    setSearchParams(serializeRunsFilter(next, searchParams), { replace: true })
    setPageIndex(0)
  }
  // The view is not a filter, so Clear all keeps it.
  const clearAll = () => changeFilter({ ...EMPTY_RUNS_FILTER, view: filter.view })

  const objects = useMemo<ObjectOption[]>(
    () => [
      ...all.pipelines.map((p) => ({ id: p.pipeline_id, label: p.extract?.table ?? p.pipeline_id })),
      ...all.golds.map((g) => ({ id: g.gold_id, label: g.gold_id })),
    ],
    [all.pipelines, all.golds],
  )

  const filtered = useMemo(
    () => sortRunRows(filterRunRows(all.rows, filter), sortDirection),
    [all.rows, filter, sortDirection],
  )
  const objectCount = useMemo(() => new Set(filtered.map(runObjectKey)).size, [filtered])

  // eslint-disable-next-line react-hooks/exhaustive-deps
  const now = useMemo(() => new Date(), [all.rows, all.pipelines, all.golds])
  // Grouped after filtering, so a status filter keeps the objects that have a matching run.
  const groups = useMemo(
    () => (filter.view === 'objects' ? groupRunsByObject(filtered) : []),
    [filter.view, filtered],
  )
  const health = useMemo(() => {
    let healthy = 0
    let failing = 0
    let disabled = 0
    for (const g of groups) {
      if (objectSchedule(g.key, all.pipelines, all.golds, now)?.disabled) disabled += 1
      else if (g.latest && statusBucket(g.latest.status) === 'SUCCEEDED') healthy += 1
      else if (g.latest && statusBucket(g.latest.status) === 'FAILED') failing += 1
    }
    return { healthy, failing, disabled }
  }, [groups, all.pipelines, all.golds, now])

  // The drawer looks in every loaded run, so narrowing the table never closes it.
  const drawerRow = runId ? (all.rows.find((r) => r.runId === runId) ?? null) : null
  const drawerPipeline = drawerRow?.pipelineId
    ? all.pipelines.find((p) => p.pipeline_id === drawerRow.pipelineId)
    : undefined
  const drawerBusy =
    !!drawerRow?.pipelineId &&
    (trigger.isPending ||
      all.rows.some((r) => r.pipelineId === drawerRow.pipelineId && isInFlightStatus(r.status)))

  const waiting = all.pendingPipelines > 0 || all.goldPending
  const loading = all.loading || (all.rows.length === 0 && waiting)
  const noRunsAtAll = all.rows.length === 0 && !waiting
  const byObject = filter.view === 'objects'

  const empty = noRunsAtAll ? (
    <EmptyState icon={History} title="No runs yet">
      Runs appear here once a sync or gold table has run.
    </EmptyState>
  ) : (
    <EmptyState
      icon={SearchX}
      title={byObject ? 'No objects match these filters' : 'No runs match these filters'}
      action={
        <Button type="button" variant="outline" size="sm" onClick={clearAll}>
          Clear all
        </Button>
      }
    >
      Try a wider time window or clear a filter.
    </EmptyState>
  )

  const range = RANGE_WORDS[filter.window]
  const rangeLine =
    loading && all.rows.length === 0
      ? `${range} · loading runs`
      : `${range} · ${plural(filtered.length, 'run', 'runs')} across ${plural(objectCount, 'object', 'objects')}`

  const loadedFrom = `${filtered.length !== all.rows.length ? 'filtered from' : 'from'} ${all.rows.length} loaded runs`
  const objectsFootnote =
    groups.length > 0 ? (
      <span data-testid="objects-count">
        <CountSegment strong>{plural(groups.length, 'object', 'objects')}</CountSegment>
        {` · ${health.healthy} healthy · ${health.failing} failing · ${health.disabled} disabled · ${loadedFrom}`}
      </span>
    ) : undefined

  return (
    <div>
      <PageHeader
        title="Jobs"
        info="Every run across bronze, silver and gold, newest first."
        sub={<span className="tnum">{rangeLine}</span>}
        actions={
          <>
            <SegmentedControl
              label="Time range"
              options={RANGES}
              value={filter.window}
              onChange={(window) => changeFilter({ ...filter, window })}
            />
            {/* The object view exports the same filtered runs: there is no per-object CSV. */}
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={filtered.length === 0}
              onClick={() => downloadCsv(filtered)}
            >
              <Download aria-hidden="true" />
              Export CSV
            </Button>
          </>
        }
      />

      {all.pendingPipelines > 0 && !loading && (
        <Note tone="mute">Some syncs&apos; runs are still loading. The table fills in as they arrive.</Note>
      )}
      {all.failedPipelines > 0 && (
        <Note tone="warn">
          Runs for {all.failedPipelines} {all.failedPipelines === 1 ? 'sync' : 'syncs'} could not be loaded, so
          they are missing below.
        </Note>
      )}
      {all.goldError && (
        <Note tone="fail">Gold runs unavailable right now. The rows below are bronze and silver only.</Note>
      )}

      <ListCard>
        <RunsFilters filter={filter} onChange={changeFilter} objects={objects} rows={all.rows} />
        {byObject ? (
          <ObjectsTable
            groups={groups}
            pipelines={all.pipelines}
            golds={all.golds}
            now={now}
            loading={loading}
            pageIndex={pageIndex}
            onPageChange={setPageIndex}
            empty={empty}
            footnote={objectsFootnote}
          />
        ) : (
          <RunsTable
            rows={filtered}
            sortDirection={sortDirection}
            onToggleSort={() => setSortDirection((d) => (d === 'desc' ? 'asc' : 'desc'))}
            loading={loading}
            onOpen={(row) => open(row.runId)}
            selectedRunId={runId}
            pageIndex={pageIndex}
            onPageChange={setPageIndex}
            empty={empty}
            footnote={
              filtered.length !== all.rows.length && all.rows.length > 0
                ? `Filtered from ${all.rows.length} loaded runs`
                : undefined
            }
            framed={false}
          />
        )}
      </ListCard>

      <RunDrawer
        row={drawerRow}
        onRetry={(row) => {
          if (row.pipelineId) trigger.mutate(row.pipelineId)
        }}
        retryDisabled={drawerBusy || (!!drawerRow?.pipelineId && drawerPipeline?.enabled === false)}
      />

      {all.collector}
    </div>
  )
}
