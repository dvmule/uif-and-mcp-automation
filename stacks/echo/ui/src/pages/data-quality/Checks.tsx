import { useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { AlertCircle, SearchX, ShieldCheck } from 'lucide-react'
import { Button } from '@/components/ui/button'
import {
  BusyButton,
  CountFigure,
  CountLine,
  CountSegment,
  DataTable,
  EmptyState,
  FacetSelect,
  ListCard,
  PageHeader,
  RowActionsCell,
  StatusBadge,
  TablePager,
  Toolbar,
  ToolbarField,
  ToolbarSearch,
  type DataTableColumn,
} from '@/components/relay'
import { KindBadge } from '@/components/data-quality/KindBadge'
import { KIND_COPY } from '@/components/data-quality/wizard/kindCopy'
import { useDqChecks, useTriggerDqCheck } from '@/hooks/useDqChecks'
import {
  CHECK_FACET_KEYS,
  checkStatusKey,
  checkStatusView,
  checkSubjectLabel,
  computeDqStats,
  filterChecks,
} from '@/lib/dqStats'
import { clearFacets, parseFacets, setFacet } from '@/lib/facetFilters'
import { facetItems, type FacetOption } from '@/lib/facetSelect'
import { dqRunStatusView } from '@/lib/statusVocabulary'
import { relativeTime } from '@/lib/utils'
import type { DqCheckConfig, DqCheckKind } from '@/lib/types'

const NEW_CHECK = '/m/data-quality/checks/new'
const PAGE_SIZE = 25

const KIND_OPTIONS: FacetOption[] = (Object.keys(KIND_COPY) as DqCheckKind[]).map((k) => ({
  key: k,
  label: KIND_COPY[k].label,
}))
const STATUS_OPTIONS: FacetOption[] = [
  ...(['SUCCEEDED', 'FAILED', 'SKIPPED', 'RUNNING'] as const).map((s) => ({ key: s, label: dqRunStatusView(s).label })),
  { key: 'NONE', label: 'Never ran' },
  { key: 'DISABLED', label: 'Disabled' },
]
const LAYER_OPTIONS: FacetOption[] = [
  { key: 'silver', label: 'Silver' },
  { key: 'gold', label: 'Gold' },
]

const checkHref = (id: string) => `/m/data-quality/checks/${encodeURIComponent(id)}`

/** Quality › Checks: the checks table with its facets and the old Overview figures in the count line. */
export function Checks() {
  const { data: checks, isLoading, isError, refetch } = useDqChecks()
  const trigger = useTriggerDqCheck()
  const [searchParams, setSearchParams] = useSearchParams()
  const [search, setSearch] = useState('')
  const facets = useMemo(() => parseFacets(searchParams, CHECK_FACET_KEYS), [searchParams])

  const all = useMemo(() => checks ?? [], [checks])
  const stats = useMemo(() => computeDqStats(all), [all])
  const rows = useMemo(() => filterChecks(all, search, facets), [all, search, facets])
  const [page, setPage] = useState(0)
  const pageIndex = Math.min(page, Math.max(0, Math.ceil(rows.length / PAGE_SIZE) - 1))
  const paged = rows.slice(pageIndex * PAGE_SIZE, (pageIndex + 1) * PAGE_SIZE)

  const items = useMemo(
    () => ({
      kind: facetItems(all.map((c) => c.kind), { vocab: KIND_OPTIONS, selected: facets.kind }),
      status: facetItems(all.map(checkStatusKey), { vocab: STATUS_OPTIONS, selected: facets.status }),
      layer: facetItems(all.map((c) => c.subject.layer), { vocab: LAYER_OPTIONS, selected: facets.layer }),
    }),
    [all, facets],
  )

  const writeFacet = (key: (typeof CHECK_FACET_KEYS)[number]) => (values: string[]) => {
    setSearchParams(setFacet(searchParams, key, values), { replace: true })
    setPage(0)
  }

  const clearAll = () => {
    setSearch('')
    setPage(0)
    setSearchParams(clearFacets(searchParams, CHECK_FACET_KEYS), { replace: true })
  }

  const columns: DataTableColumn<DqCheckConfig>[] = [
    {
      key: 'check',
      header: 'Check',
      cell: (c) => (
        <Link to={checkHref(c.check_id)} className="font-semibold text-accent hover:underline">
          {c.check_id}
        </Link>
      ),
    },
    { key: 'kind', header: 'Kind', cell: (c) => <KindBadge kind={c.kind} /> },
    {
      key: 'subject',
      header: 'Subject',
      className: 'font-mono text-meta text-ink-soft',
      cell: (c) => checkSubjectLabel(c),
    },
    { key: 'schedule', header: 'Schedule', cell: (c) => c.schedule?.frequency ?? '—' },
    {
      key: 'last-run',
      header: 'Last run',
      cell: (c) => (
        <>
          <span>{c.last_run?.ts ? relativeTime(c.last_run.ts) : '—'}</span>
          <span className="block text-meta text-ink-mute">{c.last_run?.summary ?? (c.last_run ? '' : 'never ran')}</span>
        </>
      ),
    },
    {
      key: 'status',
      header: 'Status',
      cell: (c) => {
        const view = checkStatusView(c)
        return view ? <StatusBadge view={view} size="sm" /> : <span className="text-ink-mute">—</span>
      },
    },
    {
      key: 'action',
      header: <span className="sr-only">Actions</span>,
      align: 'right',
      cell: (c) => (
        <RowActionsCell open={{ to: checkHref(c.check_id), name: c.check_id }}>
          <BusyButton
            type="button"
            variant="ghost"
            size="xs"
            className="text-accent"
            busy={trigger.isPending && trigger.variables === c.check_id}
            onClick={() => trigger.mutate(c.check_id)}
          >
            Run now
          </BusyButton>
        </RowActionsCell>
      ),
    },
  ]

  const loaded = checks !== undefined
  const noChecks = loaded && all.length === 0

  const countLine = (
    <CountLine>
      <CountSegment testid="kpi-total" strong>
        <CountFigure loading={isLoading}>{stats.total}</CountFigure> {stats.total === 1 ? 'check' : 'checks'}
      </CountSegment>
      {' · '}
      <CountSegment testid="kpi-active">
        <CountFigure loading={isLoading}>{stats.active}</CountFigure> active
      </CountSegment>
      {' · '}
      <CountSegment testid="kpi-pass">
        <CountFigure loading={isLoading} tone={stats.passed24h > 0 ? 'ok' : undefined}>
          {stats.passed24h}
        </CountFigure>{' '}
        passed
      </CountSegment>
      {' · '}
      <CountSegment testid="kpi-fail">
        <CountFigure loading={isLoading} tone={stats.failed24h > 0 ? 'fail' : undefined}>
          {stats.failed24h}
        </CountFigure>{' '}
        failed
      </CountSegment>
      {' · '}
      <CountSegment testid="kpi-warn">
        <CountFigure loading={isLoading} tone={stats.warning24h > 0 ? 'warn' : undefined}>
          {stats.warning24h}
        </CountFigure>{' '}
        warning in 24 h
      </CountSegment>
    </CountLine>
  )

  return (
    <div className="space-y-4">
      <PageHeader
        className="mb-0"
        title="Quality"
        info="Checks that verify a silver or gold table on a schedule."
        actions={
          <Button asChild>
            <Link to={NEW_CHECK}>New check</Link>
          </Button>
        }
      />

      {isError && !loaded ? (
        <div className="rounded-card border border-line bg-paper-card shadow-card">
          <EmptyState
            icon={AlertCircle}
            title="Couldn't load checks"
            action={
              <Button type="button" variant="outline" size="sm" onClick={() => void refetch()}>
                Retry
              </Button>
            }
          />
        </div>
      ) : noChecks ? (
        <ListCard>
          <EmptyState
            icon={ShieldCheck}
            title="No data quality checks yet"
            action={
              <Button asChild>
                <Link to={NEW_CHECK}>New check</Link>
              </Button>
            }
          >
            Create the first check to verify a silver or gold table.
          </EmptyState>
          {countLine}
        </ListCard>
      ) : (
        <ListCard>
          <Toolbar>
            <ToolbarField label="Find" htmlFor="checks-find">
              <ToolbarSearch
                id="checks-find"
                value={search}
                onChange={(next) => {
                  setSearch(next)
                  setPage(0)
                }}
                placeholder="Check or table"
                ariaLabel="Search check or table"
              />
            </ToolbarField>
            <FacetSelect caption="Kind" noun="kinds" items={items.kind} values={facets.kind} onChange={writeFacet('kind')} />
            <FacetSelect
              caption="Status"
              noun="statuses"
              items={items.status}
              values={facets.status}
              onChange={writeFacet('status')}
            />
            <FacetSelect
              caption="Layer"
              noun="layers"
              items={items.layer}
              values={facets.layer}
              onChange={writeFacet('layer')}
            />
            {rows.length > 0 && (
              <TablePager inline pageIndex={pageIndex} pageSize={PAGE_SIZE} total={rows.length} onPageChange={setPage} />
            )}
          </Toolbar>
          <DataTable
            ariaLabel="Checks"
            columns={columns}
            rows={paged}
            rowKey={(c) => c.check_id}
            loading={isLoading}
            framed={false}
            empty={
              <EmptyState
                icon={SearchX}
                title="No checks match these filters"
                action={
                  <Button type="button" variant="outline" size="sm" onClick={clearAll}>
                    Clear all
                  </Button>
                }
              />
            }
          />
          {countLine}
        </ListCard>
      )}
    </div>
  )
}
