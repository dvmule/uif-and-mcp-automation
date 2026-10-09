import { Link } from 'react-router-dom'
import { DataTable, EmptyState, Note, StatusBadge, type DataTableColumn } from '@/components/relay'
import { areaLabel } from '@/lib/homeAttention'
import { formatRunTime } from '@/lib/syncRow'
import { dqRunStatusView, runStatusView } from '@/lib/statusVocabulary'
import type { ActivityItem, DqRunStatus } from '@/lib/types'
import { relativeTime } from '@/lib/utils'

export interface RecentJobsProps {
  items: ActivityItem[]
  loading: boolean
  failed: boolean
  windowHours?: number
}

interface JobRow {
  index: number
  item: ActivityItem
}

/** Quality items read "Passed" for a success; every other area reads "Succeeded". */
function statusViewOf(item: ActivityItem) {
  return item.module === 'dq' ? dqRunStatusView(item.status as DqRunStatus) : runStatusView(item.status)
}

const COLUMNS: DataTableColumn<JobRow>[] = [
  {
    key: 'time',
    header: 'Time',
    cell: ({ item }) => (
      <div className="whitespace-nowrap">
        <div className="text-ink">{formatRunTime(item.ts)}</div>
        <div className="text-meta text-ink-mute">{relativeTime(item.ts)}</div>
      </div>
    ),
  },
  { key: 'area', header: 'Area', cell: ({ item }) => areaLabel(item.module) },
  {
    key: 'object',
    header: 'Object',
    cell: ({ item }) => (
      <Link to={item.link_path} className="font-semibold text-accent hover:underline">
        {item.subject}
      </Link>
    ),
  },
  { key: 'status', header: 'Status', cell: ({ item }) => <StatusBadge view={statusViewOf(item)} /> },
  {
    key: 'message',
    header: 'Message',
    cell: ({ item }) => (
      <span className="block max-w-[28rem] truncate text-ink-mute" title={item.message}>
        {item.message}
      </span>
    ),
  },
]

/** The activity feed on the shared table, newest first as the API sends it. */
export function RecentJobs({ items, loading, failed, windowHours }: RecentJobsProps) {
  const rows: JobRow[] = items.map((item, index) => ({ index, item }))
  let body
  if (failed) {
    body = (
      <div className="px-4">
        <Note tone="fail">Couldn&apos;t load recent jobs.</Note>
      </div>
    )
  } else if (!loading && rows.length === 0) {
    body = (
      <EmptyState title={windowHours ? `No activity in the last ${windowHours} hours` : 'No recent activity'} />
    )
  } else {
    body = (
      <DataTable
        ariaLabel="Recent jobs"
        framed={false}
        columns={COLUMNS}
        rows={rows}
        loading={loading}
        rowKey={({ index, item }) => `${item.module}|${item.subject}|${item.ts}|${index}`}
      />
    )
  }

  return (
    <section aria-labelledby="home-recent-title" className="mb-5">
      <div className="mb-2 flex items-center justify-between gap-3">
        <h2 id="home-recent-title" className="text-[18px] font-extrabold tracking-tight text-ink">
          Recent jobs
        </h2>
        <Link to="/m/data-sync/runs" className="text-meta font-bold text-accent hover:underline">
          All jobs →
        </Link>
      </div>
      <div className="overflow-hidden rounded-card border border-white/70 bg-paper-card shadow-card">{body}</div>
    </section>
  )
}
