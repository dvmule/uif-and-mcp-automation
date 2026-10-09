import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { CountLine, DataTable, Pill, RowActionsCell, TablePager, type DataTableColumn } from '@/components/relay'
import { statusBucket, type ObjectGroup } from '@/lib/runsTable'
import type { GoldPipelineConfig, PipelineConfig } from '@/lib/types'
import { formatNextAt, objectSchedule } from '@/lib/upcoming'
import { parseDateMs } from '@/lib/utils'
import { RunDots } from './RunDots'
import { RUNS_PAGE_SIZE } from './RunsTable'

interface Props {
  groups: ObjectGroup[]
  pipelines: PipelineConfig[]
  golds: GoldPipelineConfig[]
  now: Date
  loading?: boolean
  pageIndex: number
  onPageChange: (pageIndex: number) => void
  /** Shown when no object matches. */
  empty: ReactNode
  /** The count line under the pager. */
  footnote?: ReactNode
}

/** "12 min" up to 90 minutes, then "3 h": a stuck run should not read as 1,571 min. */
function runningFor(iso: string | null, now: Date): string {
  const ms = parseDateMs(iso)
  const minutes = ms === null ? 1 : Math.max(1, Math.round((now.getTime() - ms) / 60_000))
  return minutes <= 90 ? `${minutes} min` : `${Math.round(minutes / 60)} h`
}

/** Insights › Jobs, object view: one row per object with its last five runs, success share and next run. */
export function ObjectsTable({
  groups,
  pipelines,
  golds,
  now,
  loading,
  pageIndex,
  onPageChange,
  empty,
  footnote,
}: Props) {
  const pages = Math.max(1, Math.ceil(groups.length / RUNS_PAGE_SIZE))
  const page = Math.min(pageIndex, pages - 1)
  const visible = groups.slice(page * RUNS_PAGE_SIZE, (page + 1) * RUNS_PAGE_SIZE)
  const schedule = (g: ObjectGroup) => objectSchedule(g.key, pipelines, golds, now)

  const columns: DataTableColumn<ObjectGroup>[] = [
    {
      key: 'object',
      header: 'Object',
      cell: (g) => (
        <div className="min-w-0">
          {g.objectHref ? (
            <Link to={g.objectHref} className="font-semibold text-ink hover:text-accent hover:underline">
              {g.object}
            </Link>
          ) : (
            <span className="font-semibold text-ink">{g.object}</span>
          )}
          <div className="text-meta text-ink-mute">{g.layers.join(' · ')}</div>
        </div>
      ),
    },
    {
      key: 'schedule',
      header: 'Schedule',
      cell: (g) => {
        const s = schedule(g)
        if (!s) return <span className="text-ink-mute">—</span>
        return (
          <div>
            <Pill tone="mute">{s.cadence}</Pill>
            {s.disabled && <div className="mt-0.5 text-meta text-ink-mute">disabled</div>}
          </div>
        )
      },
    },
    {
      key: 'last5',
      header: <span title="Oldest on the left, the latest run ringed on the right">Last 5 runs</span>,
      className: 'whitespace-nowrap',
      cell: (g) => (
        <div>
          <RunDots runs={g.lastFive} />
          {g.latest && statusBucket(g.latest.status) === 'RUNNING' && (
            <div className="mt-1 text-meta text-signal-live">
              In progress · {runningFor(g.latest.startedAt, now)}
            </div>
          )}
        </div>
      ),
    },
    {
      key: 'success',
      header: 'Success',
      align: 'right',
      className: 'tnum',
      cell: (g) => (
        <span
          title={
            g.finished
              ? `${g.succeeded} of ${g.finished} finished ${g.finished === 1 ? 'run' : 'runs'}`
              : 'no finished runs in range'
          }
        >
          {g.successPct === null ? '—' : `${g.successPct}%`}
        </span>
      ),
    },
    {
      key: 'next',
      header: 'Next run',
      cell: (g) => {
        const at = schedule(g)?.nextAt
        if (!at) return <span className="text-ink-mute">—</span>
        const label = formatNextAt(at, 'UTC', now)
        return (
          <span className="tnum text-meta text-ink-soft">
            {label.utc} · {label.relative}
          </span>
        )
      },
    },
    {
      key: 'action',
      header: <span className="sr-only">Actions</span>,
      align: 'right',
      cell: (g) => (g.objectHref ? <RowActionsCell open={{ to: g.objectHref, name: g.object }} /> : null),
    },
  ]

  return (
    <DataTable
      ariaLabel="Objects"
      columns={columns}
      rows={visible}
      rowKey={(g) => g.key}
      loading={loading}
      empty={empty}
      framed={false}
      footer={
        <>
          <TablePager pageIndex={page} pageSize={RUNS_PAGE_SIZE} total={groups.length} onPageChange={onPageChange} />
          {footnote && <CountLine>{footnote}</CountLine>}
        </>
      }
    />
  )
}
