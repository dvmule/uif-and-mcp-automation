import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { AlertCircle, Clock, Database, SearchX } from 'lucide-react'
import { Button } from '@/components/ui/button'
import {
  CountFigure,
  CountLine,
  CountSegment,
  DataTable,
  EmptyState,
  ListCard,
  OpenLink,
  PageHeader,
  StatusBadge,
  Toolbar,
  ToolbarField,
  ToolbarSearch,
  type DataTableColumn,
} from '@/components/relay'
import { ResetWatermarkDialog } from '@/components/data-sync/detail/overflow/ResetWatermarkDialog'
import { usePipelines } from '@/hooks/usePipelines'
import { useResetWatermark } from '@/hooks/useResetWatermark'
import { BEHIND_SCHEDULE_VIEW, type StatusView } from '@/lib/statusVocabulary'
import { watermarkRows, type WatermarkRow } from '@/lib/watermarkRows'
import { relativeTime } from '@/lib/utils'

const BEHIND: StatusView = { ...BEHIND_SCHEDULE_VIEW, label: 'Behind' }
const FULL_LOAD: StatusView = { label: 'full load', tone: 'mute', icon: Clock, raw: 'full' }

const syncPath = (id: string) => `/m/data-sync/syncs/${id}`

/** Replicate › Watermarks: where each incremental sync is up to, with a per-row reset. */
export function Watermarks() {
  const { data: pipelines, isLoading, isError, refetch } = usePipelines()
  const resetWm = useResetWatermark()
  const [resetting, setResetting] = useState<WatermarkRow | null>(null)
  const [search, setSearch] = useState('')
  const rows = useMemo(() => watermarkRows(pipelines ?? []), [pipelines])

  // Find: client-side over the sync name, its id and the watermark column.
  const needle = search.trim().toLowerCase()
  const shown = needle
    ? rows.filter((r) => `${r.table} ${r.pipelineId} ${r.column ?? ''}`.toLowerCase().includes(needle))
    : rows
  const incremental = shown.filter((r) => r.incremental).length
  const behind = shown.filter((r) => r.incremental && r.behind).length

  const columns: DataTableColumn<WatermarkRow>[] = [
    {
      key: 'sync',
      header: 'Sync',
      cell: (r) => (
        <>
          <Link to={syncPath(r.pipelineId)} className="font-semibold text-accent hover:underline">
            {r.table}
          </Link>
          <span className="block text-meta text-ink-mute">{r.pipelineId}</span>
        </>
      ),
    },
    { key: 'column', header: 'Column', className: 'font-mono text-meta', cell: (r) => r.column ?? '—' },
    { key: 'value', header: 'Current value', className: 'font-mono text-meta text-ink-soft', cell: (r) => r.value ?? '—' },
    {
      key: 'advanced',
      header: 'Advanced',
      cell: (r) =>
        !r.incremental ? (
          <StatusBadge view={FULL_LOAD} size="sm" showIcon={false} />
        ) : (
          <span className="inline-flex flex-wrap items-center gap-2">
            {r.advancedAt ? relativeTime(r.advancedAt) : '—'}
            {r.behind && <StatusBadge view={BEHIND} size="sm" />}
          </span>
        ),
    },
    {
      key: 'action',
      header: <span className="sr-only">Actions</span>,
      align: 'right',
      // Reset… stays visible (not a hover action): it is the row's one real action.
      cell: (r) => (
        <div className="flex items-center justify-end gap-1 whitespace-nowrap">
          {r.incremental && (
            <Button type="button" variant="ghost" size="xs" className="text-accent" onClick={() => setResetting(r)}>
              Reset…
            </Button>
          )}
          <OpenLink to={syncPath(r.pipelineId)} name={r.table} />
        </div>
      ),
    },
  ]

  return (
    <div>
      <PageHeader title="Watermarks" info="Where each incremental sync is up to." />
      <ListCard>
        <Toolbar>
          <ToolbarField label="Find" htmlFor="watermarks-find">
            <ToolbarSearch
              id="watermarks-find"
              value={search}
              onChange={setSearch}
              placeholder="Sync or column"
              ariaLabel="Search sync or column"
            />
          </ToolbarField>
        </Toolbar>
        <DataTable
          ariaLabel="Watermarks"
          columns={columns}
          rows={shown}
          rowKey={(r) => r.pipelineId}
          loading={isLoading}
          framed={false}
          empty={
            isError ? (
              <EmptyState
                icon={AlertCircle}
                title="Couldn't load syncs"
                action={
                  <Button type="button" variant="outline" size="sm" onClick={() => void refetch()}>
                    Retry
                  </Button>
                }
              />
            ) : rows.length > 0 ? (
              <EmptyState
                icon={SearchX}
                title="No syncs match this search"
                action={
                  <Button type="button" variant="outline" size="sm" onClick={() => setSearch('')}>
                    Clear search
                  </Button>
                }
              />
            ) : (
              <EmptyState
                icon={Database}
                title="No syncs configured"
                action={
                  <Button asChild>
                    <Link to="/m/data-sync/syncs/new">New replication</Link>
                  </Button>
                }
              >
                Each replication records its watermark here.
              </EmptyState>
            )
          }
        />
        {!isError && (
          <CountLine>
            <CountSegment testid="watermarks-total" strong>
              <CountFigure loading={isLoading}>{shown.length}</CountFigure> {shown.length === 1 ? 'sync' : 'syncs'}
            </CountSegment>
            {' · '}
            <CountSegment testid="watermarks-incremental">
              <CountFigure loading={isLoading}>{incremental}</CountFigure> incremental
            </CountSegment>
            {' · '}
            <CountSegment testid="watermarks-behind">
              <CountFigure loading={isLoading} tone={behind > 0 ? 'warn' : undefined}>
                {behind}
              </CountFigure>{' '}
              behind
            </CountSegment>
            {' · '}
            <CountSegment testid="watermarks-full">
              <CountFigure loading={isLoading}>{shown.length - incremental}</CountFigure> full load
            </CountSegment>
            {shown.length !== rows.length && ` · filtered from ${rows.length}`}
          </CountLine>
        )}
      </ListCard>
      <ResetWatermarkDialog
        open={resetting !== null}
        currentValue={resetting?.value ?? '—'}
        onCancel={() => setResetting(null)}
        onConfirm={() => {
          if (resetting) resetWm.mutate(resetting.pipelineId)
          setResetting(null)
        }}
      />
    </div>
  )
}
