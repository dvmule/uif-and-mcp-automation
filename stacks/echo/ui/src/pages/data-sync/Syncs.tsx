import { useEffect, useMemo, useReducer, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { Database, SearchX } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { DeleteSyncDialog } from '@/components/data-sync/detail/overflow/DeleteSyncDialog'
import {
  CountFigure,
  CountLine,
  CountSegment,
  EmptyState,
  ListCard,
  Note,
  PageHeader,
  TablePager,
} from '@/components/relay'
import {
  SyncsToolbar,
  type SortField,
  type SortState,
  type ViewMode,
  type VisibleColumns,
} from '@/components/data-sync/SyncsToolbar'
import { SyncsList } from '@/components/data-sync/SyncsList'
import { SyncsGrid } from '@/components/data-sync/SyncsGrid'
import { useFilteredPipelines } from '@/components/data-sync/useFilteredPipelines'
import { usePipelines, useDeletePipeline } from '@/hooks/usePipelines'
import { useSyncsActiveRunPolls } from '@/hooks/useRuns'
import { pipelineCloud } from '@/lib/cloud'
import { getEnv } from '@/lib/envConfig'
import { EMPTY_FILTERS, serializeFilters } from '@/lib/filters'
import { pipelineStatus } from '@/lib/filterMatchers'
import { pipelineRow, rowSearchText } from '@/lib/syncRow'
import { computeSyncStats } from '@/lib/syncStats'
import { freshness, parseDateMs } from '@/lib/utils'
import type { PipelineConfig, RunMetrics } from '@/lib/types'

const PAGE_SIZE = 25

interface PageState {
  view: ViewMode
  sort: SortState
  visibleColumns: VisibleColumns
  pageIndex: number
}

type Action =
  | { type: 'set-view';     payload: ViewMode }
  | { type: 'set-sort';     payload: SortState }
  | { type: 'set-columns';  payload: VisibleColumns }
  | { type: 'set-page';     payload: number }
  | { type: 'reset-page' }

const INITIAL: PageState = {
  view: 'list',
  sort: { field: 'table', dir: 'asc' },
  visibleColumns: {
    cloud: true, layers: true, schedule: true,
    lastRun: true, status: true, freshness: true, rows: true,
  },
  pageIndex: 0,
}

function reducer(s: PageState, a: Action): PageState {
  switch (a.type) {
    case 'set-view':    return { ...s, view: a.payload }
    case 'set-sort':    return { ...s, sort: a.payload, pageIndex: 0 }
    case 'set-columns': return { ...s, visibleColumns: a.payload }
    case 'set-page':    return { ...s, pageIndex: a.payload }
    case 'reset-page':  return { ...s, pageIndex: 0 }
  }
}

const NEW_REPLICATION = '/m/data-sync/syncs/new'

export function Syncs() {
  const [search, setSearch] = useState('')
  const [pendingDelete, setPendingDelete] = useState<PipelineConfig | null>(null)
  const [s, dispatch] = useReducer(reducer, INITIAL)
  const [searchParams, setSearchParams] = useSearchParams()

  const { data: pipelines, isLoading, isError, refetch } = usePipelines()
  const deletePipeline = useDeletePipeline()
  const { filtered, lastRunByPipeline, runsByPipeline } =
    useFilteredPipelines(pipelines ?? [])
  useSyncsActiveRunPolls((pipelines ?? []).map((p) => p.pipeline_id))

  // The count line counts every sync, not the filtered view: it describes the estate.
  const stats = useMemo(
    () => computeSyncStats(pipelines ?? [], lastRunByPipeline, runsByPipeline),
    [pipelines, lastRunByPipeline, runsByPipeline],
  )

  // Search (table name, pipeline id) — in-memory, not URL-persisted.
  const searched = useMemo(() => {
    if (!search) return filtered
    const q = search.toLowerCase()
    return filtered.filter(p => rowSearchText(pipelineRow(p), getEnv()).includes(q))
  }, [filtered, search])

  // Sort
  const sorted = useMemo(
    () => sortPipelines(searched, lastRunByPipeline, runsByPipeline, s.sort),
    [searched, lastRunByPipeline, runsByPipeline, s.sort],
  )

  // Pager — snap pageIndex into range if filters shrank the dataset.
  const total = sorted.length
  const maxPageIndex = Math.max(0, Math.ceil(total / PAGE_SIZE) - 1)
  const pageIndex = Math.min(s.pageIndex, maxPageIndex)
  const start = pageIndex * PAGE_SIZE
  const paged = sorted.slice(start, start + PAGE_SIZE)

  // Reset to page 0 whenever search or URL filter state changes.
  const filterKey = searchParams.toString()
  useEffect(() => {
    dispatch({ type: 'reset-page' })
  }, [search, filterKey])

  const handleConfirmDelete = () => {
    if (!pendingDelete) return
    deletePipeline.mutate(pendingDelete.pipeline_id, {
      onSuccess: () => setPendingDelete(null),
    })
  }

  const clearFiltersAndSearch = () => {
    setSearch('')
    setSearchParams(serializeFilters(EMPTY_FILTERS, searchParams), { replace: true })
  }

  const loaded = pipelines !== undefined
  const noSyncsYet = loaded && pipelines.length === 0
  const failedToLoad = isError && !loaded
  const runFiguresPending = isLoading || stats.pending > 0

  const filteredEmpty = (
    <EmptyState
      icon={SearchX}
      title="No syncs match these filters"
      action={
        <Button type="button" variant="outline" size="sm" onClick={clearFiltersAndSearch}>
          Clear all
        </Button>
      }
    />
  )

  const pager = total > 0 && (
    <TablePager
      inline
      pageIndex={pageIndex}
      pageSize={PAGE_SIZE}
      total={total}
      onPageChange={n => dispatch({ type: 'set-page', payload: n })}
    />
  )

  const countLine = (
    <CountLine>
      <CountSegment testid="stat-total" strong>
        <CountFigure loading={isLoading}>{stats.total}</CountFigure> {stats.total === 1 ? 'sync' : 'syncs'}
      </CountSegment>
      {' · '}
      <CountSegment testid="stat-active">
        <CountFigure loading={isLoading}>{stats.active}</CountFigure> active
      </CountSegment>
      {' · '}
      <CountSegment testid="stat-failing">
        <CountFigure loading={runFiguresPending} tone={stats.failing > 0 ? 'fail' : undefined}>
          {stats.failing}
        </CountFigure>{' '}
        failing
      </CountSegment>
      {' · '}
      <CountSegment testid="stat-behind">
        <CountFigure loading={runFiguresPending} tone={stats.behind > 0 ? 'warn' : undefined}>
          {stats.behind}
        </CountFigure>{' '}
        behind schedule
      </CountSegment>
      {' · '}
      <CountSegment testid="stat-running">
        <CountFigure loading={runFiguresPending}>{stats.running}</CountFigure> running now
      </CountSegment>
      {' · '}
      <CountSegment testid="stat-disabled">
        <CountFigure loading={isLoading}>{stats.disabled}</CountFigure> disabled
      </CountSegment>
    </CountLine>
  )

  return (
    <div className="space-y-4">
      <PageHeader
        className="mb-0"
        title="Replicate"
        info="Oracle tables replicated into bronze and silver."
        actions={
          <Button asChild>
            <Link to={NEW_REPLICATION}>New replication</Link>
          </Button>
        }
      />

      {failedToLoad ? (
        <Note tone="fail">
          <span className="flex flex-wrap items-center gap-3">
            Couldn&apos;t load syncs.
            <Button type="button" variant="outline" size="xs" onClick={() => void refetch()}>
              Retry
            </Button>
          </span>
        </Note>
      ) : noSyncsYet ? (
        <ListCard>
          <EmptyState
            icon={Database}
            title="No syncs yet"
            action={
              <Button asChild>
                <Link to={NEW_REPLICATION}>New replication</Link>
              </Button>
            }
          >
            Create the first replication.
          </EmptyState>
          {countLine}
        </ListCard>
      ) : (
        <ListCard>
          <SyncsToolbar
            pipelines={pipelines ?? []}
            lastRunByPipeline={lastRunByPipeline}
            runsByPipeline={runsByPipeline}
            search={search}
            onSearchChange={setSearch}
            view={s.view}
            onViewChange={v => dispatch({ type: 'set-view', payload: v })}
            sort={s.sort}
            onSortChange={next => dispatch({ type: 'set-sort', payload: next })}
            visibleColumns={s.visibleColumns}
            onVisibleColumnsChange={next => dispatch({ type: 'set-columns', payload: next })}
            pager={pager}
          />

          {isLoading || s.view === 'list' ? (
            <SyncsList
              pipelines={paged}
              lastRunByPipeline={lastRunByPipeline}
              runsByPipeline={runsByPipeline}
              visibleColumns={s.visibleColumns}
              sort={s.sort}
              onSortChange={next => dispatch({ type: 'set-sort', payload: next })}
              onDelete={setPendingDelete}
              loading={isLoading}
              empty={filteredEmpty}
            />
          ) : paged.length === 0 ? (
            filteredEmpty
          ) : (
            <div className="p-3">
              <SyncsGrid
                pipelines={paged}
                lastRunByPipeline={lastRunByPipeline}
                runsByPipeline={runsByPipeline}
                onDelete={setPendingDelete}
              />
            </div>
          )}

          {countLine}
        </ListCard>
      )}

      <DeleteSyncDialog
        open={!!pendingDelete}
        confirming={deletePipeline.isPending}
        onCancel={() => setPendingDelete(null)}
        onConfirm={handleConfirmDelete}
      />
    </div>
  )
}

// ─── Sort helpers ───────────────────────────────────────────

const FRESHNESS_RANK: Record<string, number> = {
  fresh: 0, stale: 1, critical: 2, unknown: 3,
}
const STATUS_RANK: Record<string, number> = {
  RUNNING: 0, SUCCEEDED: 1, SKIPPED: 2, WARNING: 3, FAILED: 4, DISABLED: 5,
}

function sortPipelines(
  pipelines: PipelineConfig[],
  lastRunByPipeline: Map<string, RunMetrics | undefined>,
  runsByPipeline: Map<string, RunMetrics[]>,
  sort: SortState,
): PipelineConfig[] {
  const sign = sort.dir === 'asc' ? 1 : -1
  return [...pipelines].sort((a, b) => {
    const av = sortValue(a, lastRunByPipeline, runsByPipeline, sort.field)
    const bv = sortValue(b, lastRunByPipeline, runsByPipeline, sort.field)
    if (av < bv) return -1 * sign
    if (av > bv) return  1 * sign
    return a.pipeline_id.localeCompare(b.pipeline_id)
  })
}

function sortValue(
  p: PipelineConfig,
  lastRunByPipeline: Map<string, RunMetrics | undefined>,
  runsByPipeline: Map<string, RunMetrics[]>,
  field: SortField,
): string | number {
  const lastRun = lastRunByPipeline.get(p.pipeline_id)
  const allRuns = runsByPipeline.get(p.pipeline_id)
  switch (field) {
    case 'table':     return p.extract.table.toLowerCase()
    case 'cloud':     return pipelineCloud(p)
    case 'status':    return STATUS_RANK[pipelineStatus(p, lastRun, allRuns) ?? 'SKIPPED'] ?? 99
    case 'lastRun':   return -1 * (parseDateMs(lastRun?.started_at) ?? 0)
    case 'freshness': return FRESHNESS_RANK[freshness(p, lastRun?.completed_at)] ?? 99
    case 'schedule':  return p.schedule?.frequency ?? '~'
  }
}
