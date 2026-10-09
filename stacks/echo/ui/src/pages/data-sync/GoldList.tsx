import { useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { Layers } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import {
  CountFigure,
  CountLine,
  CountSegment,
  DataTable,
  EmptyState,
  FacetSelect,
  ListCard,
  Note,
  PageHeader,
  RowActionsCell,
  StatusBadge,
  Toolbar,
  ToolbarField,
  ToolbarSearch,
  type DataTableColumn,
} from '@/components/relay'
import { GoldRowActions } from '@/components/data-sync/GoldRowActions'
import { GoldScheduleDialog } from '@/components/data-sync/GoldScheduleDialog'
import { GoldRunConfirmDialog } from '@/components/data-sync/gold/GoldRunConfirmDialog'
import { GoldTrendCard } from '@/components/data-sync/gold/GoldTrendCard'
import { useGoldPipelines } from '@/hooks/useGoldPipelines'
import { useSetGoldSchedule, useTriggerGoldRun } from '@/hooks/useGoldPipelineMutations'
import { useGoldRuns } from '@/hooks/useGoldRuns'
import { useGoldStats } from '@/hooks/useGoldStats'
import { getEnv } from '@/lib/envConfig'
import { FRESHNESS_OPTIONS, PIPELINE_STATUS_OPTIONS, facetItems } from '@/lib/facetSelect'
import { EMPTY_FILTERS, parseFilters, serializeFilters, type SyncFilterState } from '@/lib/filters'
import { goldDetailPath } from '@/lib/goldDetail'
import { freshnessCaption, type GoldLiveWindow } from '@/lib/goldStats'
import { freshnessView, pipelineStatusView, runStatusView } from '@/lib/statusVocabulary'
import {
  formatGoldScheduleLabel,
  goldEnabled,
  goldFreshness,
  goldMatchesFilters,
  goldRow,
  goldRunStatus,
  goldTableFqn,
  formatRunTime,
  latestGoldRunByEntity,
  rowSearchText,
} from '@/lib/syncRow'
import { formatRows, parseDateMs, relativeTime } from '@/lib/utils'
import { TRANSFORM_UI_NEW_PATH, TRANSFORM_UI_SUBMISSIONS_PATH } from '@/lib/echoPaths'
import type { GoldPipelineConfig, GoldRunMetrics } from '@/lib/types'

/** Facets the Transform list answers: gold has no source system or tags. */
type GoldFacet = 'domain' | 'status' | 'freshness'

type SortKey = 'table' | 'status' | 'lastRun'
interface SortState { key: SortKey; direction: 'asc' | 'desc' }

const STATUS_RANK: Record<string, number> = {
  RUNNING: 0, SUCCEEDED: 1, SKIPPED: 2, WARNING: 3, FAILED: 4, DISABLED: 5,
}

const domainOf = (g: GoldPipelineConfig) => g.domain ?? g.metadata?.domain

/** Transform landing (UIFPE-679): every gold table, with its real latest run. */
export function GoldList() {
  const [search, setSearch] = useState('')
  const [sort, setSort] = useState<SortState>({ key: 'table', direction: 'asc' })
  const [pendingRun, setPendingRun] = useState<GoldPipelineConfig | null>(null)
  const [pendingSchedule, setPendingSchedule] = useState<GoldPipelineConfig | null>(null)
  const [searchParams, setSearchParams] = useSearchParams()

  const goldQuery = useGoldPipelines()
  const runsQuery = useGoldRuns()
  const statsQuery = useGoldStats()
  const triggerRun = useTriggerGoldRun()
  const setSchedule = useSetGoldSchedule()
  const state = useMemo(() => parseFilters(searchParams), [searchParams])

  const golds = useMemo(() => goldQuery.data ?? [], [goldQuery.data])
  const loaded = goldQuery.data !== undefined
  const runsFailed = !!runsQuery.error
  const runsLoading = runsQuery.isLoading

  // Newest run per entity; empty when the runs call failed, so every enabled row reads Unknown.
  const latestByGold = useMemo(
    () => (runsFailed ? new Map<string, GoldRunMetrics>() : latestGoldRunByEntity(runsQuery.data?.items ?? [])),
    [runsFailed, runsQuery.data],
  )

  const facetOptions = useMemo(
    () => ({
      domain: facetItems(golds.map(domainOf), { selected: state.domain }),
      status: facetItems(
        runsLoading ? [] : golds.map(g => goldRunStatus(g, latestByGold.get(g.gold_id))),
        { vocab: PIPELINE_STATUS_OPTIONS, selected: state.status },
      ),
      freshness: facetItems(golds.map(goldFreshness), { vocab: FRESHNESS_OPTIONS, selected: state.freshness }),
    }),
    [golds, latestByGold, runsLoading, state],
  )

  const setFacet = (key: GoldFacet) => (values: string[]) =>
    setSearchParams(serializeFilters({ ...state, [key]: values } as SyncFilterState, searchParams), { replace: true })

  const rows = useMemo(() => {
    const facets = { ...state, source: [], tag: [] }
    const q = search.toLowerCase()
    const matched = golds.filter(g => {
      if (!goldMatchesFilters(g, facets, goldRunStatus(g, latestByGold.get(g.gold_id)))) return false
      return !q || rowSearchText(goldRow(g), getEnv()).includes(q)
    })
    const sign = sort.direction === 'asc' ? 1 : -1
    const value = (g: GoldPipelineConfig): string | number => {
      switch (sort.key) {
        case 'table':   return (g.target?.table || g.gold_id).toLowerCase()
        case 'status':  return STATUS_RANK[goldRunStatus(g, latestByGold.get(g.gold_id)) ?? ''] ?? 99
        case 'lastRun': return -1 * (parseDateMs(latestByGold.get(g.gold_id)?.started_at ?? g.metadata?.last_run_at) ?? 0)
      }
    }
    return [...matched].sort((a, b) => {
      const av = value(a), bv = value(b)
      if (av < bv) return -1 * sign
      if (av > bv) return 1 * sign
      return a.gold_id.localeCompare(b.gold_id)
    })
  }, [golds, latestByGold, search, sort, state])

  const onSort = (key: string) =>
    setSort(s => ({
      key: key as SortKey,
      direction: s.key === key && s.direction === 'asc' ? 'desc' : 'asc',
    }))

  const clearFiltersAndSearch = () => {
    setSearch('')
    setSearchParams(serializeFilters(EMPTY_FILTERS, searchParams), { replace: true })
  }

  const handleConfirmRun = () => {
    if (!pendingRun) return
    triggerRun.mutate(pendingRun.gold_id, { onSuccess: () => setPendingRun(null) })
  }
  const runPendingId = triggerRun.isPending ? (triggerRun.variables ?? null) : null

  const env = getEnv()
  const columns: DataTableColumn<GoldPipelineConfig>[] = [
    {
      key: 'table',
      header: 'Gold table',
      sortable: true,
      cell: g => {
        const domain = domainOf(g)
        return (
          <>
            <Link to={goldDetailPath(g)} className="font-semibold text-accent hover:underline">
              {g.target?.table || g.gold_id}
            </Link>
            <div className="text-meta text-ink-mute">
              <span>{goldTableFqn(g, env) ?? g.gold_id}</span>
              {domain && (
                <>
                  {' · '}
                  <span>{domain}</span>
                </>
              )}
            </div>
          </>
        )
      },
    },
    {
      key: 'sources',
      header: 'Sources',
      cell: g => {
        const n = g.sources?.length
        return n == null ? '—' : `${n} silver ${n === 1 ? 'table' : 'tables'}`
      },
    },
    {
      key: 'schedule',
      header: 'Schedule',
      className: 'text-ink-soft',
      // Cadence on the first line, the clock times beneath, so the column stays narrow.
      cell: g => {
        const [cadence, ...times] = formatGoldScheduleLabel(g.schedule).split(' · ')
        return (
          <>
            <div>{cadence}</div>
            {times.length > 0 && <div className="text-meta text-ink-mute">{times.join(' · ')}</div>}
          </>
        )
      },
    },
    {
      key: 'lastRun',
      header: 'Last run',
      sortable: true,
      cell: g => {
        if (runsLoading && goldEnabled(g)) return <Skeleton className="h-3 w-16" aria-hidden="true" />
        const at = latestByGold.get(g.gold_id)?.started_at ?? g.metadata?.last_run_at
        if (!at) return '—'
        return (
          <>
            <div className="tnum">{formatRunTime(at)}</div>
            <div className="text-meta text-ink-mute">{relativeTime(at)}</div>
          </>
        )
      },
    },
    {
      key: 'status',
      header: 'Status',
      sortable: true,
      cell: g => {
        if (!goldEnabled(g)) return <StatusBadge view={pipelineStatusView('DISABLED')} size="sm" />
        if (runsLoading) return <Skeleton className="h-3 w-16" aria-hidden="true" />
        const latest = latestByGold.get(g.gold_id)
        const noChanges = latest?.status === 'SUCCEEDED' && (latest.rows_written ?? 0) === 0
        return <StatusBadge view={runStatusView(latest?.status, { goldNoChanges: noChanges })} size="sm" />
      },
    },
    {
      key: 'freshness',
      header: 'Freshness',
      cell: g => (
        <StatusBadge
          view={freshnessView(goldFreshness(g), { neverRan: !g.metadata?.last_run_at })}
          size="sm"
        />
      ),
    },
    {
      key: 'rows',
      header: 'Rows written',
      align: 'right',
      className: 'tnum',
      cell: g => (runsLoading && goldEnabled(g) ? <Skeleton className="ml-auto h-3 w-12" aria-hidden="true" /> : formatRows(latestByGold.get(g.gold_id)?.rows_written)),
    },
    {
      key: 'actions',
      header: <span className="sr-only">Actions</span>,
      align: 'right',
      cell: g => (
        <RowActionsCell open={{ to: goldDetailPath(g), name: g.target?.table || g.gold_id }}>
          <GoldRowActions
            gold={g}
            onRun={setPendingRun}
            onEditSchedule={setPendingSchedule}
            runPending={runPendingId === g.gold_id}
          />
        </RowActionsCell>
      ),
    },
  ]

  const filteredEmpty = (
    <>
      <b className="font-semibold text-ink">No gold tables match these filters</b>
      <span className="mt-2 block">
        <Button type="button" variant="ghost" size="xs" onClick={clearFiltersAndSearch}>
          Clear all
        </Button>
      </span>
    </>
  )

  const countLine = (
    <GoldCountLine live={statsQuery.data?.live} loading={statsQuery.isLoading} failed={!!statsQuery.error} />
  )

  return (
    <div className="space-y-4">
      <PageHeader
        className="mb-0"
        title="Transform"
        info="Gold tables built from silver."
        actions={
          <>
            <Button asChild variant="outline">
              <a href={TRANSFORM_UI_SUBMISSIONS_PATH}>My submissions</a>
            </Button>
            <Button asChild>
              <a href={TRANSFORM_UI_NEW_PATH}>New transformation</a>
            </Button>
          </>
        }
      />

      {goldQuery.isError && !loaded ? (
        <Note tone="fail">
          <span className="flex flex-wrap items-center gap-3">
            Couldn&apos;t load gold tables.
            <Button type="button" variant="outline" size="xs" onClick={() => void goldQuery.refetch()}>
              Retry
            </Button>
          </span>
        </Note>
      ) : loaded && golds.length === 0 ? (
        <ListCard>
          <EmptyState icon={Layers} title="No gold tables yet">
            Gold tables appear here once a transformation is registered.
          </EmptyState>
          {countLine}
        </ListCard>
      ) : (
        <>
          {runsFailed && <Note tone="mute">Run status unavailable right now.</Note>}

          <ListCard>
            <Toolbar>
              <ToolbarField label="Find" htmlFor="gold-find">
                <ToolbarSearch
                  id="gold-find"
                  value={search}
                  onChange={setSearch}
                  placeholder="Gold table"
                  ariaLabel="Search gold table"
                />
              </ToolbarField>
              <FacetSelect
                caption="Domain"
                noun="domains"
                items={facetOptions.domain}
                values={state.domain}
                onChange={setFacet('domain')}
              />
              <FacetSelect
                caption="Status"
                noun="statuses"
                items={facetOptions.status}
                values={state.status}
                onChange={setFacet('status')}
              />
              <FacetSelect
                caption="Freshness"
                noun="freshness"
                items={facetOptions.freshness}
                values={state.freshness}
                onChange={setFacet('freshness')}
              />
            </Toolbar>
            <DataTable
              ariaLabel="Gold tables"
              columns={columns}
              rows={rows}
              rowKey={g => g.gold_id}
              sort={{ key: sort.key, direction: sort.direction }}
              onSort={onSort}
              loading={goldQuery.isLoading}
              empty={filteredEmpty}
              framed={false}
            />
            {countLine}
          </ListCard>

          <GoldTrendCard />
        </>
      )}

      <GoldScheduleDialog
        gold={pendingSchedule}
        onClose={() => setPendingSchedule(null)}
        saving={setSchedule.isPending}
        onSave={(id, change) =>
          setSchedule.mutate({ id, change }, { onSuccess: () => setPendingSchedule(null) })
        }
      />

      <GoldRunConfirmDialog
        gold={pendingRun}
        pending={triggerRun.isPending}
        onCancel={() => setPendingRun(null)}
        onConfirm={handleConfirmRun}
      />
    </div>
  )
}

/** The live half of the fleet statistics, straight from GET /gold-stats. */
function GoldCountLine({
  live, loading, failed,
}: { live: GoldLiveWindow | undefined; loading: boolean; failed: boolean }) {
  if (failed || (!loading && !live)) {
    return (
      <CountLine>
        <CountSegment testid="gold-kpi-entities" strong>Gold tables: Not available here</CountSegment>
        {' · '}
        <CountSegment testid="gold-kpi-succeeded">Succeeded in 24 h: Not available here</CountSegment>
        {' · '}
        <CountSegment testid="gold-kpi-failed">Failed in 24 h: Not available here</CountSegment>
        {' · '}
        <CountSegment testid="gold-kpi-fresh">Inside freshness: Not available here</CountSegment>
      </CountLine>
    )
  }
  const caption = freshnessCaption(live)
  return (
    <CountLine>
      <CountSegment testid="gold-kpi-entities">
        <CountSegment strong>
          <CountFigure loading={loading}>{live?.entities}</CountFigure>{' '}
          {live?.entities === 1 ? 'gold table' : 'gold tables'}
        </CountSegment>
        {' · '}
        <CountFigure loading={loading}>{live?.enabled}</CountFigure> enabled
      </CountSegment>
      {' · '}
      <CountSegment testid="gold-kpi-succeeded">
        <CountFigure loading={loading}>{live?.succeeded}</CountFigure> succeeded in 24 h
      </CountSegment>
      {' · '}
      <CountSegment testid="gold-kpi-failed">
        <CountFigure loading={loading} tone={live && live.failed > 0 ? 'fail' : undefined}>
          {live?.failed}
        </CountFigure>{' '}
        failed in 24 h
      </CountSegment>
      {' · '}
      <CountSegment testid="gold-kpi-fresh">
        <CountFigure loading={loading}>{live?.fresh}</CountFigure> of{' '}
        <CountFigure loading={loading}>{live?.entities}</CountFigure> inside freshness
        {caption && ` · ${caption.charAt(0).toLowerCase()}${caption.slice(1)}`}
      </CountSegment>
    </CountLine>
  )
}
