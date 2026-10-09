import { Link } from 'react-router-dom'
import { Skeleton } from '@/components/ui/skeleton'
import { isUndeployedRouteError } from '@/lib/apiError'
import { useGoldStats } from '@/hooks/useGoldStats'
import {
  formatDayLabel,
  formatGeneratedAt,
  isTrendStale,
  sparkPoints,
  trendTotals,
} from '@/lib/goldStats'
import type { GoldTrendPoint } from '@/lib/goldStats'

/** UIFPE-544: the gold fleet's last 30 days, under the Transform list.
 *
 *  This is the nightly half of the fleet statistics; the live half is the stat
 *  strip above the list. It is a file the nightly job writes, so when that file
 *  is missing or old the card SAYS so rather than drawing a flat line at zero. */

const CARD = 'rounded-card border border-line bg-paper-card shadow-card'

function Spark({
  label,
  values,
  days,
  stroke,
  series,
  testId,
}: {
  label: string
  values: number[]
  days: string[]
  stroke: string
  series: 'runs' | 'failed' | 'rows'
  testId: string
}) {
  return (
    <div data-testid={testId}>
      <p className="text-meta font-medium text-ink">{label}</p>
      <svg viewBox="0 0 120 28" className="mt-1 h-7 w-full" preserveAspectRatio="none"
           role="img" aria-label={`${label} over the last ${values.length} days`}>
        <polyline points={sparkPoints(values)} fill="none" stroke={stroke} strokeWidth="1.5" data-series={series} />
      </svg>
      <div className="flex justify-between text-label text-ink-mute">
        <span>{days.length ? formatDayLabel(days[0]) : ''}</span>
        <span>{days.length ? formatDayLabel(days[days.length - 1]) : ''}</span>
      </div>
    </div>
  )
}

export function GoldTrendCard() {
  const { data, isLoading, error } = useGoldStats()

  if (isLoading) {
    return <Skeleton className="h-40 rounded-card" data-testid="gold-fleet-loading" />
  }

  // Until the controller carrying this route is applied in an environment it
  // answers a bare 404. That is a deployment state, not an outage, and is said
  // plainly rather than shown as zeros.
  if (error) {
    return (
      <div className={`${CARD} p-5 text-body text-ink-mute`} data-testid="gold-fleet-unavailable">
        {isUndeployedRouteError(error)
          ? 'Gold statistics are not available in this environment yet.'
          : 'Could not load gold statistics.'}
      </div>
    )
  }

  const fleet: GoldTrendPoint[] = data?.trend?.fleet ?? []
  const totals = trendTotals(fleet)
  const days = fleet.map(p => p.day)
  const trendStale = isTrendStale(data?.trend_age_minutes)
  const computed = formatGeneratedAt(data?.trend_generated_at)

  return (
    <div className={`${CARD} p-5`}>
      <div className="flex items-baseline justify-between gap-3">
        <h2 className="text-body font-semibold text-ink">Last {totals.days || 30} days</h2>
        <p className="text-meta text-ink-mute" data-testid="gold-trend-source">
          {!data?.trend_available
            ? 'Not computed yet'
            : trendStale
              ? `Computed ${computed} — a night was missed`
              : `Computed nightly · ${computed}`}
        </p>
      </div>

      {data?.trend_available && fleet.length > 0 ? (
        <>
          <div className="mt-4 grid grid-cols-1 gap-6 sm:grid-cols-3">
            <Spark testId="gold-trend-runs" label="Runs" days={days}
                   values={fleet.map(p => p.runs)} series="runs" stroke="var(--color-accent)" />
            <Spark testId="gold-trend-failed" label="Failed runs" days={days}
                   values={fleet.map(p => p.failed)} series="failed" stroke="var(--color-dot-fail)" />
            <Spark testId="gold-trend-rows" label="Rows written" days={days}
                   values={fleet.map(p => p.rows_written)} series="rows" stroke="var(--color-ink)" />
          </div>
          <dl className="mt-4 grid grid-cols-3 gap-4 text-body" data-testid="gold-trend-totals">
            <div>
              <dt className="text-ink-mute">Runs</dt>
              <dd className="tnum font-medium text-ink">{totals.runs}</dd>
            </div>
            <div>
              <dt className="text-ink-mute">Failed</dt>
              <dd className="tnum font-medium text-ink">{totals.failed}</dd>
            </div>
            <div>
              <dt className="text-ink-mute">Met freshness</dt>
              <dd className="tnum font-medium text-ink">
                {/* An em dash, not 0%: no entity-days means nothing to report. */}
                {totals.freshShare === null ? '—' : `${Math.round(totals.freshShare * 100)}%`}
              </dd>
            </div>
          </dl>
        </>
      ) : (
        <p className="mt-3 text-body text-ink-mute" data-testid="gold-trend-missing">
          The nightly job has not written a summary yet. It runs at 04:30 UTC.
        </p>
      )}

      <p className="mt-4 text-meta text-ink-mute">
        <Link to="/m/data-sync/runs" className="underline">See every run</Link>
      </p>
    </div>
  )
}
