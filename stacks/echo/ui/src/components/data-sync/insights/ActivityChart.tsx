import { axisTicks, bucketLabel, bucketRows, compact, countOutcomes, type Bucket } from '@/lib/activity'
import type { Span } from '@/lib/runsTable'

const U = 10
const H = 100

const SEGMENTS = [
  ['succeeded', 'var(--color-dot-ok)'],
  ['warning', 'var(--color-signal-warn-line)'],
  ['failed', 'var(--color-dot-fail)'],
] as const

export interface ActivityChartProps {
  buckets: Bucket[]
  window: Span
  /** Runs stacked by outcome instead of rows written. */
  stacked?: boolean
}

/** Beacon's Activity bars: rows (one colour) or runs stacked by outcome, with an
 *  hour or day axis. An empty bucket is a faint stub, never a gap. */
export function ActivityChart({ buckets: bs, window, stacked = false }: ActivityChartProps) {
  const n = bs.length
  const W = n * U
  const bw = n > 24 ? 7.4 : 6.6
  const vals = bs.map((b) => (stacked ? b.runs.length : bucketRows(b)))
  const max = Math.max(1, ...vals)
  const ticks = axisTicks(n)

  return (
    <div className="min-w-0">
      <svg
        viewBox={`0 0 ${W} ${H}`}
        preserveAspectRatio="none"
        className="block h-[92px] w-full overflow-visible"
        aria-hidden="true"
      >
        {[1, H / 2].map((y) => (
          <line
            key={y}
            x1="0"
            x2={W}
            y1={y}
            y2={y}
            stroke="var(--color-line)"
            strokeDasharray="3 4"
            vectorEffect="non-scaling-stroke"
          />
        ))}
        {bs.map((b, i) => {
          const x = i * U + (U - bw) / 2
          const label = bucketLabel(b.from, window)
          if (!stacked) {
            const h = vals[i] ? Math.max(3, (vals[i] / max) * (H - 4)) : 2
            return (
              <rect key={i} x={x} y={H - h} width={bw} height={h} fill="var(--color-accent)" opacity={vals[i] ? 1 : 0.25}>
                <title>{`${label} · ${compact(vals[i])} rows`}</title>
              </rect>
            )
          }
          const c = countOutcomes(b.runs)
          if (!(c.succeeded + c.warning + c.failed)) {
            return (
              <rect key={i} x={x} y={H - 2} width={bw} height={2} fill="var(--color-dot-ok)" opacity={0.25}>
                <title>{`${label} · no finished runs`}</title>
              </rect>
            )
          }
          const tip = `${label} · ${c.succeeded} succeeded · ${c.warning} warning · ${c.failed} failed`
          let y = H
          return SEGMENTS.map(([k, fill]) => {
            if (!c[k]) return null
            const h = (c[k] / max) * (H - 4)
            y -= h
            return (
              <rect key={`${i}-${k}`} x={x} y={y} width={bw} height={h} fill={fill}>
                <title>{tip}</title>
              </rect>
            )
          })
        })}
      </svg>
      <div className="tnum mt-1.5 flex justify-between gap-2 border-t border-line pt-1.5 text-label text-ink-mute">
        {ticks.map((t, i) => (
          <span key={t}>{i === ticks.length - 1 && window === '24h' ? 'now' : bucketLabel(bs[t].from, window)}</span>
        ))}
      </div>
    </div>
  )
}
