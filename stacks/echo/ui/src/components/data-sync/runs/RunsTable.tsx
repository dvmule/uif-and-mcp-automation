import { Link } from 'react-router-dom'
import { DataTable, StatusBadge, TablePager, type DataTableColumn } from '@/components/relay'
import { RUN_LAYER_LABEL, runRowStatusView, type RunRow } from '@/lib/runsTable'
import { formatRunTime } from '@/lib/syncRow'
import { formatDuration, formatRows, relativeTime } from '@/lib/utils'

export const RUNS_PAGE_SIZE = 25

export type RunsTableColumn = 'started' | 'object' | 'layer' | 'kind' | 'status' | 'duration' | 'rows'

interface Props {
  /** Already filtered and sorted. */
  rows: RunRow[]
  /** Omit onToggleSort for a fixed order (the Started header is then plain). */
  sortDirection?: 'asc' | 'desc'
  onToggleSort?: () => void
  loading?: boolean
  onOpen: (row: RunRow) => void
  selectedRunId?: string | null
  pageIndex?: number
  onPageChange?: (pageIndex: number) => void
  /** Show every row with no pager (a detail page's short list). */
  hidePager?: boolean
  /** Base columns to leave out, e.g. Object on a page that is already about one object. */
  hideColumns?: RunsTableColumn[]
  /** Extra columns, after the base ones. */
  extraColumns?: DataTableColumn<RunRow>[]
  /** A trailing action cell per row; return null for none. */
  rowAction?: (row: RunRow) => React.ReactNode
  /** Draw the table's own card frame. Default true. */
  framed?: boolean
  /** Shown when nothing matches; the page decides between "no runs yet" and "none match". */
  empty: React.ReactNode
  /** e.g. "filtered from 41 runs" under the pager. */
  footnote?: string
  /** Accessible name of the table. Default "Runs". */
  ariaLabel?: string
}

/** The one runs table (Insights › Jobs, and a detail page's Recent runs). Clicking
 *  a row opens the run drawer; the Started cell is a button so the keyboard can do the same. */
export function RunsTable({
  rows,
  sortDirection = 'desc',
  onToggleSort,
  loading,
  onOpen,
  selectedRunId,
  pageIndex = 0,
  onPageChange,
  hidePager = false,
  hideColumns = [],
  extraColumns = [],
  rowAction,
  framed = true,
  empty,
  footnote,
  ariaLabel = 'Runs',
}: Props) {
  const pages = Math.max(1, Math.ceil(rows.length / RUNS_PAGE_SIZE))
  const page = Math.min(pageIndex, pages - 1)
  const visible = hidePager ? rows : rows.slice(page * RUNS_PAGE_SIZE, (page + 1) * RUNS_PAGE_SIZE)

  const base: DataTableColumn<RunRow>[] = [
    {
      key: 'started',
      header: 'Started',
      sortable: !!onToggleSort,
      cell: (row) => (
        <button
          type="button"
          aria-label={`Open run ${row.runId}`}
          className="block text-left"
        >
          <span className="tnum block font-medium text-ink">{formatRunTime(row.startedAt)}</span>
          <span className="block text-meta text-ink-mute">{relativeTime(row.startedAt)}</span>
        </button>
      ),
    },
    {
      key: 'object',
      header: 'Object',
      cell: (row) => (
        <div className="min-w-0">
          {row.objectHref ? (
            <Link
              to={row.objectHref}
              onClick={(e) => e.stopPropagation()}
              className="font-semibold text-accent hover:underline"
            >
              {row.object}
            </Link>
          ) : (
            <span className="font-semibold text-ink">{row.object}</span>
          )}
          {row.objectSub && <div className="truncate text-meta text-ink-mute">{row.objectSub}</div>}
        </div>
      ),
    },
    { key: 'layer', header: 'Layer', cell: (row) => RUN_LAYER_LABEL[row.layer] },
    { key: 'kind', header: 'Kind', cell: (row) => row.kind },
    {
      key: 'status',
      header: 'Status',
      cell: (row) => <StatusBadge view={runRowStatusView(row)} size="sm" />,
    },
    {
      key: 'duration',
      header: 'Duration',
      align: 'right',
      className: 'tnum',
      cell: (row) => formatDuration(row.durationSec),
    },
    {
      key: 'rows',
      header: 'Rows read → written',
      align: 'right',
      className: 'tnum',
      cell: (row) => `${formatRows(row.rowsRead)} → ${formatRows(row.rowsWritten)}`,
    },
  ]

  const columns: DataTableColumn<RunRow>[] = [
    ...base.filter((col) => !hideColumns.includes(col.key as RunsTableColumn)),
    ...extraColumns,
    ...(rowAction
      ? [
          {
            key: 'action',
            header: <span className="sr-only">Actions</span>,
            align: 'right' as const,
            cell: (row: RunRow) => rowAction(row),
          },
        ]
      : []),
  ]

  const pager = !hidePager && (
    <TablePager
      pageIndex={page}
      pageSize={RUNS_PAGE_SIZE}
      total={rows.length}
      onPageChange={(n) => onPageChange?.(n)}
    />
  )

  return (
    <DataTable
      ariaLabel={ariaLabel}
      columns={columns}
      rows={visible}
      rowKey={(row) => row.key}
      sort={onToggleSort ? { key: 'started', direction: sortDirection } : undefined}
      onSort={onToggleSort}
      loading={loading}
      empty={empty}
      onRowClick={onOpen}
      isRowSelected={(row) => row.runId === selectedRunId}
      framed={framed}
      footer={
        (pager || footnote) && (
          <>
            {pager}
            {footnote && <div className="border-t border-line px-3 py-1.5 text-meta text-ink-mute">{footnote}</div>}
          </>
        )
      }
    />
  )
}
