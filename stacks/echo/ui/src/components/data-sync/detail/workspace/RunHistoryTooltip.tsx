import { runStatusView } from '@/lib/statusVocabulary'
import { formatDuration, formatRows } from '@/lib/utils'

interface RunHistoryTooltipProps {
  active?: boolean
  // Recharts ContentType passes the full TooltipContentProps when content
  // is a ReactElement; payload is the array of entries for the hovered bar.
  payload?: ReadonlyArray<{ payload?: unknown }>
}

/** Beacon's hover box: when, status · duration, rows read → written; then kind and watermark. */
export function RunHistoryTooltip({ active, payload }: RunHistoryTooltipProps) {
  if (!active || !payload?.length) return null
  const d = payload[0].payload as {
    label: string
    status?: string | null
    rows: number
    rowsWritten: number
    duration: number
    kindLabel?: string | null
    watermark?: string | null
  }
  return (
    <div className="min-w-[180px] space-y-0.5 rounded-[10px] border border-line bg-paper-card px-2.5 py-2 text-meta text-ink shadow-card">
      <b className="block font-semibold">{d.label}</b>
      <div className="text-ink-soft">
        {runStatusView(d.status).label} · {formatDuration(d.duration)}
      </div>
      <div className="tnum text-ink-soft">
        {formatRows(d.rows)} → {formatRows(d.rowsWritten)} rows
      </div>
      {(d.kindLabel || d.watermark) && (
        <div className="truncate text-label text-ink-mute">
          {[d.kindLabel, d.watermark].filter(Boolean).join(' · ')}
        </div>
      )}
    </div>
  )
}
