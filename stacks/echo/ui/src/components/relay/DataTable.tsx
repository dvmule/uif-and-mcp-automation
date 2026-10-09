import { cloneElement, isValidElement, type ReactElement, type ReactNode } from 'react'
import { ArrowDown, ArrowUp, ArrowUpDown, ChevronLeft, ChevronRight } from 'lucide-react'
import { Button } from '@/components/ui/button'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { cn } from '@/lib/utils'
import { EmptyState, type EmptyStateProps } from './EmptyState'

export interface DataTableColumn<T> {
  key: string
  header: ReactNode
  cell: (row: T) => ReactNode
  align?: 'left' | 'right'
  /** Header becomes a button that calls `onSort(key)`. */
  sortable?: boolean
  /** Extra classes for the body cells of this column. */
  className?: string
}

export interface DataTableSort {
  key: string
  direction: 'asc' | 'desc'
}

export interface DataTableProps<T> {
  columns: DataTableColumn<T>[]
  rows: T[]
  rowKey: (row: T) => string
  sort?: DataTableSort
  onSort?: (key: string) => void
  /** First load in flight: show shimmer rows instead of data. */
  loading?: boolean
  /** Shown as a full-width row when there are no rows. An `<EmptyState>` element is used as is. */
  empty?: ReactNode
  /** Accessible name of the table. */
  ariaLabel?: string
  /** Rendered under the table, inside the frame (a TablePager, usually). */
  footer?: ReactNode
  /** Makes body rows clickable. Put a button in one cell so the keyboard can reach it too. */
  onRowClick?: (row: T) => void
  /** Highlights the row the page has open (a drawer, for one). */
  isRowSelected?: (row: T) => boolean
  /** Wrap in the rounded, bordered card. Default true. */
  framed?: boolean
  className?: string
}

function ariaSort(col: { key: string; sortable?: boolean }, sort?: DataTableSort) {
  if (!col.sortable) return undefined
  if (sort?.key !== col.key) return 'none'
  return sort.direction === 'asc' ? 'ascending' : 'descending'
}

export function DataTable<T>({
  columns,
  rows,
  rowKey,
  sort,
  onSort,
  loading = false,
  empty,
  ariaLabel,
  footer,
  onRowClick,
  isRowSelected,
  framed = true,
  className,
}: DataTableProps<T>) {
  const table = (
    <Table className="border-collapse text-body" aria-label={ariaLabel}>
      <TableHeader className="[&_tr]:border-0">
        <TableRow className="border-0 hover:bg-transparent">
          {columns.map((col) => (
            <TableHead
              key={col.key}
              aria-sort={ariaSort(col, sort)}
              className={cn(
                'h-auto border-b-2 border-ink px-3 py-2.5 text-meta font-bold text-ink',
                col.align === 'right' ? 'text-right' : 'text-left',
              )}
            >
              {col.sortable ? (
                <button
                  type="button"
                  onClick={() => onSort?.(col.key)}
                  className={cn(
                    'inline-flex items-center gap-1 font-bold',
                    col.align === 'right' && 'flex-row-reverse',
                  )}
                >
                  {col.header}
                  {sort?.key === col.key ? (
                    sort.direction === 'asc' ? (
                      <ArrowUp size={12} aria-hidden="true" />
                    ) : (
                      <ArrowDown size={12} aria-hidden="true" />
                    )
                  ) : (
                    <ArrowUpDown size={12} aria-hidden="true" className="text-ink-mute" />
                  )}
                </button>
              ) : (
                col.header
              )}
            </TableHead>
          ))}
        </TableRow>
      </TableHeader>
      <TableBody>
        {loading ? (
          <SkeletonRows cols={columns.length} />
        ) : rows.length === 0 ? (
          empty != null &&
          (isValidElement(empty) && empty.type === EmptyState ? (
            cloneElement(empty as ReactElement<EmptyStateProps>, { colSpan: columns.length })
          ) : (
            <EmptyState colSpan={columns.length}>{empty}</EmptyState>
          ))
        ) : (
          rows.map((row) => (
            <TableRow
              key={rowKey(row)}
              onClick={onRowClick ? () => onRowClick(row) : undefined}
              className={cn(
                'group border-0 hover:bg-paper-head',
                onRowClick && 'cursor-pointer',
                isRowSelected?.(row) && 'bg-accent-soft hover:bg-accent-soft',
              )}
            >
              {columns.map((col) => (
                <TableCell
                  key={col.key}
                  className={cn(
                    'border-b border-line/60 px-3 py-2.5 align-middle',
                    col.align === 'right' && 'text-right',
                    col.className,
                  )}
                >
                  {col.cell(row)}
                </TableCell>
              ))}
            </TableRow>
          ))
        )}
      </TableBody>
    </Table>
  )

  if (!framed) {
    return (
      <div className={className}>
        {table}
        {footer}
      </div>
    )
  }
  return (
    <div className={cn('overflow-hidden rounded-card border border-white/70 bg-paper-card shadow-card', className)}>
      {table}
      {footer}
    </div>
  )
}

/** Shimmer rows for a table whose first load is in flight. Widths vary so it reads as content. */
export function SkeletonRows({ cols, rows = 4 }: { cols: number; rows?: number }) {
  return Array.from({ length: rows }, (_, r) => (
    <TableRow key={`skeleton-${r}`} aria-hidden="true" className="border-0 hover:bg-transparent">
      {Array.from({ length: cols }, (__, c) => (
        <TableCell key={c} className="border-b border-line/60 px-3 py-2.5 align-middle">
          <span
            className="block h-3 animate-pulse rounded bg-paper-line/60"
            style={{ width: `${55 + ((r * 7 + c * 13) % 40)}%` }}
          />
        </TableCell>
      ))}
    </TableRow>
  ))
}

export interface TablePagerProps {
  /** Zero-based. */
  pageIndex: number
  pageSize: number
  total: number
  onPageChange: (pageIndex: number) => void
  /** Sit at the right end of a Toolbar instead of the table footer. */
  inline?: boolean
}

/** "1–25 of 93" with previous and next pills; the page pair is in the buttons' titles. */
export function TablePager({ pageIndex, pageSize, total, onPageChange, inline = false }: TablePagerProps) {
  const pages = Math.max(1, Math.ceil(total / pageSize))
  const first = total === 0 ? 0 : pageIndex * pageSize + 1
  const last = Math.min(total, (pageIndex + 1) * pageSize)
  const title = `Page ${pageIndex + 1} of ${pages}`
  return (
    <div
      className={cn(
        'text-meta text-ink-mute',
        inline
          ? 'ml-auto flex shrink-0 items-center gap-2'
          : 'flex items-center justify-between gap-3 border-t border-line px-3 py-2',
      )}
    >
      <span className="tnum">{total ? `${first}–${last} of ${total}` : '0'}</span>
      <div className="flex items-center gap-1">
        <Button
          type="button"
          variant="outline"
          size="sm"
          className="w-7 px-0"
          aria-label="Previous page"
          title={title}
          disabled={pageIndex <= 0}
          onClick={() => onPageChange(pageIndex - 1)}
        >
          <ChevronLeft aria-hidden="true" />
        </Button>
        <Button
          type="button"
          variant="outline"
          size="sm"
          className="w-7 px-0"
          aria-label="Next page"
          title={title}
          disabled={pageIndex >= pages - 1}
          onClick={() => onPageChange(pageIndex + 1)}
        >
          <ChevronRight aria-hidden="true" />
        </Button>
      </div>
    </div>
  )
}
