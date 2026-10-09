import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { SearchX } from 'lucide-react'
import { Button } from '@/components/ui/button'
import {
  CountFigure,
  CountLine,
  CountSegment,
  DataTable,
  EmptyState,
  ListCard,
  PageHeader,
  Toolbar,
  ToolbarField,
  ToolbarSearch,
  type DataTableColumn,
} from '@/components/relay'
import { usePipelines } from '@/hooks/usePipelines'
import { arnTail, groupSourcesBySecret, type SourceGroup } from '@/lib/connections'

/** Replicate › Sources: which Oracle secret feeds which syncs. */
export function Sources() {
  const { data: pipelines, isLoading } = usePipelines()
  const [search, setSearch] = useState('')
  const groups = useMemo(() => groupSourcesBySecret(pipelines ?? []), [pipelines])

  // Find: client-side over the source name and every table or sync name in it.
  const needle = search.trim().toLowerCase()
  const shown = needle
    ? groups.filter((g) =>
        `${g.name} ${g.syncs.map((s) => `${s.table} ${s.id}`).join(' ')}`.toLowerCase().includes(needle),
      )
    : groups
  const syncCount = shown.reduce((n, g) => n + g.syncs.length, 0)

  const columns: DataTableColumn<SourceGroup>[] = [
    { key: 'source', header: 'Source', cell: (g) => <b className="font-semibold text-ink">{g.name}</b> },
    {
      key: 'secret',
      header: 'Secret',
      className: 'font-mono text-meta text-ink-soft',
      cell: (g) => <span title={g.arn}>{arnTail(g.arn)}</span>,
    },
    { key: 'syncs', header: 'Syncs', align: 'right', className: 'tnum', cell: (g) => g.syncs.length },
    {
      key: 'tables',
      header: 'Tables',
      cell: (g) => (
        <span className="flex flex-wrap gap-x-1">
          {g.syncs.map((s, i) => (
            <span key={s.id}>
              <Link to={`/m/data-sync/syncs/${s.id}`} className="font-semibold text-accent hover:underline">
                {s.table}
              </Link>
              {i < g.syncs.length - 1 && ','}
            </span>
          ))}
        </span>
      ),
    },
  ]

  return (
    <div>
      <PageHeader
        title="Sources"
        info="Which Oracle secret feeds which syncs."
        actions={
          <Button asChild variant="ghost" className="text-accent">
            <Link to="/settings/connections">Manage in Settings › Connections →</Link>
          </Button>
        }
      />
      <ListCard>
        <Toolbar>
          <ToolbarField label="Find" htmlFor="sources-find">
            <ToolbarSearch
              id="sources-find"
              value={search}
              onChange={setSearch}
              placeholder="Source or table"
              ariaLabel="Search source or table"
            />
          </ToolbarField>
        </Toolbar>
        <DataTable
          ariaLabel="Sources"
          columns={columns}
          rows={shown}
          rowKey={(g) => g.arn}
          loading={isLoading}
          framed={false}
          empty={
            groups.length > 0 ? (
              <EmptyState
                icon={SearchX}
                title="No sources match this search"
                action={
                  <Button type="button" variant="outline" size="sm" onClick={() => setSearch('')}>
                    Clear search
                  </Button>
                }
              />
            ) : (
              'No sources registered yet.'
            )
          }
        />
        <CountLine>
          <CountSegment testid="sources-total" strong>
            <CountFigure loading={isLoading}>{shown.length}</CountFigure> {shown.length === 1 ? 'source' : 'sources'}
          </CountSegment>
          {' · '}
          <CountSegment testid="sources-syncs">
            <CountFigure loading={isLoading}>{syncCount}</CountFigure> {syncCount === 1 ? 'sync' : 'syncs'}
          </CountSegment>
          {shown.length !== groups.length && ` · filtered from ${groups.length}`}
        </CountLine>
      </ListCard>
    </div>
  )
}
