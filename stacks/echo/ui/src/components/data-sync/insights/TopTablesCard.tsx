import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { CardHead, ChartLegend, ListCard, WindowSelect } from '@/components/relay'
import { ACTIVITY_SPAN, OUTCOME_LEGEND, WINDOW_OPTIONS, compact, rankByRows, runsBetween } from '@/lib/activity'
import type { RunRow, Span } from '@/lib/runsTable'

// The smallest bar an object with finished runs gets, so a 0-row failure still shows.
const MIN_BAR_PCT = 3

export interface TopTablesCardProps {
  rows: RunRow[]
  /** Epoch ms the window ends at. */
  now: number
}

/** The five objects that wrote the most rows in the window, each bar split by run outcome. */
export function TopTablesCard({ rows, now }: TopTablesCardProps) {
  const [span, setSpan] = useState<Span>('24h')
  const ranked = useMemo(() => {
    const { n, spanMs } = ACTIVITY_SPAN[span]
    return rankByRows(runsBetween(rows, now - n * spanMs, now))
  }, [rows, now, span])
  const maxRows = Math.max(1, ...ranked.map((r) => r.rows))

  return (
    <ListCard className="px-[26px] py-[22px]">
      <CardHead
        title="Top tables by rows"
        right={<WindowSelect label="Top tables window" value={span} onChange={setSpan} options={WINDOW_OPTIONS} />}
      />
      {ranked.length ? (
        <div data-testid="top-tables">
          {ranked.map((r) => {
            const runs = r.succeeded + r.warning + r.failed
            const bar = Math.max(MIN_BAR_PCT, (r.rows / maxRows) * 100)
            const width = (count: number) => ({ width: `${(count / runs) * bar}%` })
            return (
              <div key={r.key} className="my-2.5 grid grid-cols-[170px_minmax(0,1fr)_72px] items-center gap-3 text-body">
                {r.href ? (
                  <Link to={r.href} className="truncate text-ink hover:underline">
                    {r.label}
                  </Link>
                ) : (
                  <span className="truncate text-ink">{r.label}</span>
                )}
                <span
                  className="flex h-3.5 overflow-hidden rounded-[3px] bg-accent-soft"
                  title={`${r.succeeded} succeeded · ${r.warning} warning · ${r.failed} failed`}
                >
                  <b className="bg-dot-ok" style={width(r.succeeded)} />
                  <b className="bg-signal-warn-line" style={width(r.warning)} />
                  <b className="bg-dot-fail" style={width(r.failed)} />
                </span>
                <span className="tnum text-meta text-ink-mute">{compact(r.rows)} rows</span>
              </div>
            )
          })}
        </div>
      ) : (
        <p className="text-body text-ink-mute">No finished runs in this window.</p>
      )}
      <ChartLegend items={OUTCOME_LEGEND} />
    </ListCard>
  )
}
