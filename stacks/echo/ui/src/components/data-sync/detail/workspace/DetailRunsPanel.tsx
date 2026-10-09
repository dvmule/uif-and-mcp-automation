import type { ReactNode } from 'react'
import type { DataTableColumn } from '@/components/relay'
import { RunsTable, type RunsTableColumn } from '@/components/data-sync/runs/RunsTable'
import { sortRunRows, type RunRow } from '@/lib/runsTable'
import { CardLink } from '../DetailCard'

export const DETAIL_RUNS_LIMIT = 10

interface Props {
  rows: RunRow[]
  /** "All runs for this sync →" and where it goes. */
  allRunsHref: string
  allRunsLabel: string
  onOpen: (runId: string) => void
  selectedRunId: string | null
  hideColumns?: RunsTableColumn[]
  extraColumns?: DataTableColumn<RunRow>[]
  rowAction?: (row: RunRow) => ReactNode
  /** Replaces the table, e.g. when run history is not deployed here. */
  placeholder?: ReactNode
}

/** A detail page's Runs tab: the ten newest rows of the shared runs table, no pager, the all-runs link above. */
export function DetailRunsPanel({
  rows,
  allRunsHref,
  allRunsLabel,
  onOpen,
  selectedRunId,
  hideColumns,
  extraColumns,
  rowAction,
  placeholder,
}: Props) {
  const newest = sortRunRows(rows, 'desc').slice(0, DETAIL_RUNS_LIMIT)
  return (
    <div data-testid="recent-runs">
      {placeholder ?? (
        <>
          <div className="mb-2 flex justify-end text-meta">
            <CardLink to={allRunsHref}>{allRunsLabel}</CardLink>
          </div>
          <RunsTable
            rows={newest}
            onOpen={(row) => onOpen(row.runId)}
            selectedRunId={selectedRunId}
            hidePager
            framed={false}
            hideColumns={hideColumns}
            extraColumns={extraColumns}
            rowAction={rowAction}
            ariaLabel="Recent runs"
            empty="No runs recorded yet."
          />
        </>
      )}
    </div>
  )
}
