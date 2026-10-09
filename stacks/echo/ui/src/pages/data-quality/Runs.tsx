import { useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useQueries } from '@tanstack/react-query'
import { AlertCircle, History, SearchX } from 'lucide-react'
import { Button } from '@/components/ui/button'
import {
  CountFigure,
  CountLine,
  CountSegment,
  DataTable,
  EmptyState,
  ListCard,
  PageHeader,
  StatusBadge,
  TablePager,
  Toolbar,
  ToolbarField,
  ToolbarSearch,
  type DataTableColumn,
} from '@/components/relay'
import { KindBadge } from '@/components/data-quality/KindBadge'
import { kindLabel } from '@/components/data-quality/wizard/kindCopy'
import { useDqChecks } from '@/hooks/useDqChecks'
import { api } from '@/lib/api'
import { formatUtcStamp } from '@/lib/runsTable'
import { dqRunStatusView } from '@/lib/statusVocabulary'
import type { DqCheckKind, DqRunStatus } from '@/lib/types'

const PAGE_SIZE = 25

type RunRecord = Record<string, unknown>

const checkHref = (id: string) => `/m/data-quality/checks/${encodeURIComponent(id)}`

/** Quality › Runs: every check's runs, newest first. One runs call per check (backlog: a fleet endpoint). */
export function Runs() {
  const navigate = useNavigate()
  const { data: checks, isLoading: checksLoading, isError: checksError, refetch: refetchChecks } = useDqChecks()
  const runQueries = useQueries({
    queries: (checks ?? []).map(c => ({
      queryKey: ['dq', 'check', c.check_id, 'runs'],
      queryFn:  () => api.dq.listCheckRuns(c.check_id).then(r => r.runs),
    })),
  })
  const [pageIndex, setPageIndex] = useState(0)
  const [search, setSearch] = useState('')

  const kindOf = useMemo(() => new Map((checks ?? []).map(c => [c.check_id, c.kind])), [checks])
  const all = runQueries.flatMap(q => (q.data ?? []) as RunRecord[])
  all.sort((a, b) => String(b.started_at ?? '').localeCompare(String(a.started_at ?? '')))

  // Find: client-side over the check id, its kind and the run id.
  const needle = search.trim().toLowerCase()
  const shown = needle
    ? all.filter(r => {
        const id = String(r.check_id ?? '')
        const kind = kindOf.get(id)
        return `${id} ${kind ?? ''} ${kind ? kindLabel(kind) : ''} ${String(r.run_id ?? '')}`.toLowerCase().includes(needle)
      })
    : all

  const loading = checksLoading || (all.length === 0 && runQueries.some(q => q.isPending))
  const page = Math.min(pageIndex, Math.max(0, Math.ceil(shown.length / PAGE_SIZE) - 1))
  const paged = shown.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE)

  const columns: DataTableColumn<RunRecord>[] = [
    {
      key: 'started',
      header: 'Started',
      cell: (r) => (
        <>
          <span className="tnum whitespace-nowrap">{formatUtcStamp(r.started_at as string | undefined)}</span>
          <span className="block font-mono text-label text-ink-mute">{String(r.run_id ?? '')}</span>
        </>
      ),
    },
    {
      key: 'check',
      header: 'Check',
      cell: (r) => (
        <Link
          to={checkHref(String(r.check_id ?? ''))}
          onClick={(e) => e.stopPropagation()}
          className="font-semibold text-accent hover:underline"
        >
          {String(r.check_id ?? '')}
        </Link>
      ),
    },
    {
      key: 'kind',
      header: 'Kind',
      cell: (r) => {
        const kind = kindOf.get(String(r.check_id ?? ''))
        return kind ? <KindBadge kind={kind as DqCheckKind} /> : <span className="text-ink-mute">—</span>
      },
    },
    {
      key: 'status',
      header: 'Status',
      cell: (r) => <StatusBadge view={dqRunStatusView((r.status ?? undefined) as DqRunStatus | undefined)} size="sm" />,
    },
    { key: 'summary', header: 'Summary', className: 'text-ink-soft', cell: (r) => String(r.summary ?? '') },
  ]

  return (
    <div>
      <PageHeader title="Quality" info="Every check run, newest first." />
      <ListCard>
        <Toolbar>
          <ToolbarField label="Find" htmlFor="dq-runs-find">
            <ToolbarSearch
              id="dq-runs-find"
              value={search}
              onChange={(next) => {
                setSearch(next)
                setPageIndex(0)
              }}
              placeholder="Check or run id"
              ariaLabel="Search check or run id"
            />
          </ToolbarField>
          {shown.length > 0 && (
            <TablePager inline pageIndex={page} pageSize={PAGE_SIZE} total={shown.length} onPageChange={setPageIndex} />
          )}
        </Toolbar>
        <DataTable
          ariaLabel="Check runs"
          columns={columns}
          rows={paged}
          rowKey={(r) => `${String(r.check_id ?? '')}:${String(r.run_id ?? '')}:${String(r.started_at ?? '')}`}
          loading={loading}
          framed={false}
          empty={
            checksError ? (
              <EmptyState
                icon={AlertCircle}
                title="Couldn't load check runs"
                action={
                  <Button type="button" variant="outline" size="sm" onClick={() => void refetchChecks()}>
                    Retry
                  </Button>
                }
              />
            ) : all.length > 0 ? (
              <EmptyState
                icon={SearchX}
                title="No runs match this search"
                action={
                  <Button type="button" variant="outline" size="sm" onClick={() => setSearch('')}>
                    Clear search
                  </Button>
                }
              />
            ) : (
              <EmptyState icon={History} title="No runs yet">
                A check records a run each time it executes.
              </EmptyState>
            )
          }
          onRowClick={(r) => navigate(checkHref(String(r.check_id ?? '')))}
        />
        {!checksError && (
          <CountLine>
            <CountSegment testid="dq-runs-total" strong>
              <CountFigure loading={loading}>{shown.length}</CountFigure> {shown.length === 1 ? 'run' : 'runs'}
            </CountSegment>
            {shown.length !== all.length && ` · filtered from ${all.length}`}
          </CountLine>
        )}
      </ListCard>
    </div>
  )
}
