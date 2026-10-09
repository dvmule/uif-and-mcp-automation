import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { CloudBadge } from '@/components/pipeline/CloudBadge'
import { SyncRowActions } from '@/components/data-sync/SyncRowActions'
import { DataTable, RowActionsCell, StatusBadge, type DataTableColumn } from '@/components/relay'
import { Skeleton } from '@/components/ui/skeleton'
import { pipelineCloud } from '@/lib/cloud'
import { formatDailyScheduleLabel } from '@/lib/dailySchedule'
import { pipelineStatus } from '@/lib/filterMatchers'
import { formatRunTime } from '@/lib/syncRow'
import { freshnessView, pipelineStatusView } from '@/lib/statusVocabulary'
import { freshness, formatRows, relativeTime } from '@/lib/utils'
import type { PipelineConfig, RunMetrics } from '@/lib/types'
import type { SortField, SortState, VisibleColumns } from './SyncsToolbar'

interface Props {
  pipelines: PipelineConfig[]
  lastRunByPipeline: Map<string, RunMetrics | undefined>
  /** An entry exists only once that pipeline's runs have loaded. */
  runsByPipeline: Map<string, RunMetrics[]>
  visibleColumns: VisibleColumns
  sort: SortState
  onSortChange: (next: SortState) => void
  onDelete: (p: PipelineConfig) => void
  loading?: boolean
  empty?: ReactNode
}

/** "1,284 → 1,284"; a missing side is an em dash and no numbers at all is one dash. */
function rowsCell(run: RunMetrics | undefined): string {
  if (run?.rows_read == null && run?.rows_written == null) return '—'
  return `${formatRows(run?.rows_read)} → ${formatRows(run?.rows_written)}`
}

const syncHref = (p: PipelineConfig) => `/m/data-sync/syncs/${p.pipeline_id}`

const LAYER_PILL = 'inline-flex items-center whitespace-nowrap rounded-full bg-pearson-mist px-2 py-0 text-label font-bold text-accent'

/** The layers a sync writes, as accent pills: bronze always, silver when enabled. */
function LayerPills({ pipeline }: { pipeline: PipelineConfig }) {
  return (
    <div className="flex items-center gap-1">
      <span className={LAYER_PILL}>Bronze</span>
      {pipeline.silver?.enabled === true && <span className={LAYER_PILL}>Silver</span>}
    </div>
  )
}

function CellSkeleton() {
  return <Skeleton className="h-3 w-16" aria-hidden="true" />
}

export function SyncsList({
  pipelines,
  lastRunByPipeline,
  runsByPipeline,
  visibleColumns,
  sort,
  onSortChange,
  onDelete,
  loading,
  empty,
}: Props) {
  const runsPending = (p: PipelineConfig) => !runsByPipeline.has(p.pipeline_id)

  const all: Array<DataTableColumn<PipelineConfig> & { visible?: keyof VisibleColumns }> = [
    {
      key: 'table',
      header: 'Table',
      sortable: true,
      cell: p => (
        <>
          <Link
            to={syncHref(p)}
            className="font-semibold text-accent hover:underline"
          >
            {p.extract.table}
          </Link>
          <div className="text-meta text-ink-mute">
            {p.metadata?.domain ? `${p.pipeline_id} · ${p.metadata.domain}` : p.pipeline_id}
          </div>
        </>
      ),
    },
    {
      key: 'cloud',
      header: 'Cloud',
      sortable: true,
      visible: 'cloud',
      cell: p => <CloudBadge cloud={pipelineCloud(p)} />,
    },
    {
      key: 'layers',
      header: 'Layers',
      visible: 'layers',
      cell: p => <LayerPills pipeline={p} />,
    },
    {
      key: 'schedule',
      header: 'Schedule',
      sortable: true,
      visible: 'schedule',
      className: 'text-ink-soft',
      cell: p => formatDailyScheduleLabel(p.schedule),
    },
    {
      key: 'lastRun',
      header: 'Last run',
      sortable: true,
      visible: 'lastRun',
      cell: p => {
        if (runsPending(p)) return <CellSkeleton />
        const run = lastRunByPipeline.get(p.pipeline_id)
        if (!run?.started_at) return '—'
        return (
          <>
            <div className="tnum">{formatRunTime(run.started_at)}</div>
            <div className="text-meta text-ink-mute">{relativeTime(run.started_at)}</div>
          </>
        )
      },
    },
    {
      key: 'status',
      header: 'Status',
      sortable: true,
      visible: 'status',
      cell: p => {
        // A disabled sync reads Disabled whatever its runs say, so it need not wait for them.
        if (p.enabled && runsPending(p)) return <CellSkeleton />
        const status = pipelineStatus(p, lastRunByPipeline.get(p.pipeline_id), runsByPipeline.get(p.pipeline_id))
        return <StatusBadge view={pipelineStatusView(status)} size="sm" />
      },
    },
    {
      key: 'freshness',
      header: 'Freshness',
      sortable: true,
      visible: 'freshness',
      cell: p => {
        if (runsPending(p)) return <CellSkeleton />
        const run = lastRunByPipeline.get(p.pipeline_id)
        return (
          <StatusBadge
            view={freshnessView(freshness(p, run?.completed_at), { neverRan: !run })}
            size="sm"
          />
        )
      },
    },
    {
      key: 'rows',
      header: 'Rows read → written',
      align: 'right',
      className: 'tnum whitespace-nowrap',
      visible: 'rows',
      cell: p => (runsPending(p) ? <CellSkeleton /> : rowsCell(lastRunByPipeline.get(p.pipeline_id))),
    },
    {
      key: 'actions',
      header: <span className="sr-only">Actions</span>,
      align: 'right',
      cell: p => (
        <RowActionsCell open={{ to: syncHref(p), name: p.extract.table }}>
          <SyncRowActions pipeline={p} lastRun={lastRunByPipeline.get(p.pipeline_id)} onDelete={onDelete} />
        </RowActionsCell>
      ),
    },
  ]

  const columns = all.filter(c => !c.visible || visibleColumns[c.visible])

  const onSort = (key: string) => {
    const field = key as SortField
    onSortChange({ field, dir: sort.field === field && sort.dir === 'asc' ? 'desc' : 'asc' })
  }

  return (
    <DataTable
      ariaLabel="Syncs"
      columns={columns}
      rows={pipelines}
      rowKey={p => p.pipeline_id}
      sort={{ key: sort.field, direction: sort.dir }}
      onSort={onSort}
      loading={loading}
      empty={empty ?? 'No syncs to show'}
      framed={false}
    />
  )
}
