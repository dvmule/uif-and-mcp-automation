import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { CardHead, ChartLegend, Figure, FigureRow, ListCard, Note, PageHeader, WindowSelect } from '@/components/relay'
import { GoldTrendCard } from '@/components/data-sync/gold/GoldTrendCard'
import { ActivityChart } from '@/components/data-sync/insights/ActivityChart'
import { TopTablesCard } from '@/components/data-sync/insights/TopTablesCard'
import { useAllRuns } from '@/components/data-sync/runs/useAllRuns'
import { Skeleton } from '@/components/ui/skeleton'
import { useGoldStats } from '@/hooks/useGoldStats'
import {
  ACTIVITY_SPAN,
  OUTCOME_LEGEND,
  WINDOW_OPTIONS,
  bucketLabel,
  buckets,
  compact,
  countOutcomes,
  peakBucket,
  runRows,
  runsBetween,
} from '@/lib/activity'
import { trendTotals } from '@/lib/goldStats'
import { isInFlightStatus } from '@/lib/runLifecycle'
import { countByStatus, filterRunRows, parseSpan, type RunRow, type Span, type StatusCounts } from '@/lib/runsTable'
import { buildUpcoming, type LastRun } from '@/lib/upcoming'
import { parseDateMs } from '@/lib/utils'

const CARD = 'rounded-card border border-line bg-paper-card shadow-card'
const SPAN_WORDS: Record<Span, string> = { '24h': '24 hours', '7d': '7 days' }
const ACTIVITY_ROW = 'grid items-center gap-2.5 py-3.5 xl:grid-cols-[minmax(150px,210px)_minmax(0,1fr)] xl:gap-7'

type CountKey = 'succeeded' | 'warning' | 'failed'

/** "4 replicate · 2 gold": which layers a figure's number comes from. */
function layerSplit(counts: StatusCounts, key: CountKey): string {
  const gold = counts.byLayer.gold[key]
  return `${counts[key] - gold} replicate · ${gold} gold`
}

/** Up to two names, then how many more. */
function runningHint(names: string[]): string {
  if (names.length === 0) return 'Nothing running'
  const shown = names.slice(0, 2).join(', ')
  return names.length > 2 ? `${shown} +${names.length - 2} more` : shown
}

function FreshnessShareCard() {
  const { data, isLoading, error } = useGoldStats()
  const totals = trendTotals(data?.trend?.fleet)

  return (
    <div className={`${CARD} p-5`}>
      <div className="flex items-baseline justify-between gap-3">
        <h2 className="text-body font-semibold text-ink">Freshness share</h2>
        <p className="text-meta text-ink-mute">gold, {totals.days || 30} days</p>
      </div>
      {isLoading ? (
        <Skeleton className="mt-3 h-8 w-20" data-testid="freshness-share-loading" />
      ) : error ? (
        <p className="mt-3 text-body text-ink-mute">Could not load gold statistics.</p>
      ) : totals.freshShare === null ? (
        <p className="mt-3 text-body text-ink-mute" data-testid="freshness-share-missing">
          Not computed yet. The nightly job writes it.
        </p>
      ) : (
        <>
          <div className="tnum mt-3 text-kpi font-semibold tracking-tight text-ink" data-testid="freshness-share">
            {Math.round(totals.freshShare * 100)}%
          </div>
          <p className="mt-1.5 text-meta text-ink-mute">of table-days inside their freshness target</p>
        </>
      )}
      <Note tone="mute">
        Silver and bronze trends need a fleet-level runs endpoint that does not exist yet; only gold has a
        nightly history.
      </Note>
    </div>
  )
}

/** Rows written and runs by outcome, in hourly or daily bars. */
function ActivityCard({ rows, now }: { rows: RunRow[]; now: number }) {
  const [span, setSpan] = useState<Span>('24h')
  const act = useMemo(() => {
    const { n, spanMs } = ACTIVITY_SPAN[span]
    const since = now - n * spanMs
    const runs = runsBetween(rows, since, now)
    const bs = buckets(runs, since, n, spanMs)
    return {
      bs,
      runs: runs.length,
      written: runs.reduce((sum, row) => sum + runRows(row), 0),
      peak: peakBucket(bs),
      outcomes: countOutcomes(runs),
    }
  }, [rows, now, span])

  return (
    <ListCard className="px-[26px] py-[22px]">
      <CardHead
        title="Activity"
        right={<WindowSelect label="Activity window" value={span} onChange={setSpan} options={WINDOW_OPTIONS} />}
      />
      <div className={ACTIVITY_ROW} data-testid="activity-rows">
        <div>
          <div className="text-body text-ink-soft">Rows written</div>
          <div className="tnum mt-0.5 text-[32px] font-extrabold leading-none text-ink">{compact(act.written)}</div>
          <div className="mt-1.5 text-meta text-ink-soft">
            {act.peak.index >= 0 ? (
              <>
                Peak <b className="text-ink">{compact(act.peak.rows)}</b> · {bucketLabel(act.bs[act.peak.index].from, span)}
                <br />≈ {compact(Math.round(act.written / act.bs.length))} per {span === '24h' ? 'hour' : 'day'}
              </>
            ) : (
              'Nothing written in this window'
            )}
          </div>
        </div>
        <ActivityChart buckets={act.bs} window={span} />
      </div>
      <div className={`${ACTIVITY_ROW} border-t border-line`} data-testid="activity-runs">
        <div>
          <div className="text-body text-ink-soft">Runs</div>
          <div className="tnum mt-0.5 text-[32px] font-extrabold leading-none text-ink">{act.runs}</div>
          <div className="mt-1.5 text-meta text-ink-soft">
            <b className="text-signal-ok">{act.outcomes.succeeded}</b> succeeded ·{' '}
            <b className="text-signal-warn">{act.outcomes.warning}</b> warning ·{' '}
            <b className="text-signal-fail">{act.outcomes.failed}</b> failed
          </div>
        </div>
        <ActivityChart buckets={act.bs} window={span} stacked />
      </div>
      <ChartLegend items={OUTCOME_LEGEND} />
    </ListCard>
  )
}

/** Insights › Overview (UIFPE-681, UIFPE-735): Beacon's Jobs figures, activity and
 *  top tables for the runs loaded now, and the gold nightly trend. */
export function Overview() {
  const [searchParams, setSearchParams] = useSearchParams()
  const span = parseSpan(searchParams.get('window'))
  const all = useAllRuns()

  // A page left open keeps its windows moving.
  const [tick, setTick] = useState(0)
  useEffect(() => {
    const id = setInterval(() => setTick((t) => t + 1), 60_000)
    return () => clearInterval(id)
  }, [])
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const nowDate = useMemo(() => new Date(), [tick, all.rows, all.pipelines, all.golds])
  const now = nowDate.getTime()

  const setSpan = (next: Span) =>
    setSearchParams(
      (prev) => {
        const params = new URLSearchParams(prev)
        if (next === '24h') params.delete('window')
        else params.set('window', next)
        return params
      },
      { replace: true },
    )

  const counts = useMemo(
    () =>
      countByStatus(
        filterRunRows(all.rows, { window: span, status: [], layer: [], object: [], search: '' }, nowDate),
      ),
    [all.rows, span, nowDate],
  )
  // A run in flight counts even when it started before the window opened.
  const running = useMemo(() => {
    const inFlight = all.rows.filter((row) => isInFlightStatus(row.status))
    return { count: inFlight.length, names: [...new Set(inFlight.map((row) => row.object))] }
  }, [all.rows])

  // The same projection Coming up draws, over the next 24 hours.
  const upcoming = useMemo(() => {
    const lastRuns = new Map<string, LastRun & { ms: number }>()
    for (const row of all.rows) {
      const key = row.pipelineId ?? `gold:${row.goldId}`
      const ms = parseDateMs(row.startedAt)
      if (ms === null) continue
      const held = lastRuns.get(key)
      if (!held || ms > held.ms) lastRuns.set(key, { at: row.startedAt, status: row.status, ms })
    }
    const list = buildUpcoming(all.pipelines, all.golds, lastRuns, nowDate, 24)
    const gold = list.filter((r) => r.key.startsWith('gold:')).length
    return { count: list.length, syncs: list.length - gold, gold }
  }, [all.rows, all.pipelines, all.golds, nowDate])

  const pending = all.loading || all.pendingPipelines > 0 || all.goldPending
  const upcomingPending = all.loading || all.goldsLoading
  const windowQuery = span === '24h' ? '' : `&window=${span}`
  const words = SPAN_WORDS[span]
  const linked = (n: number, href: string, isPending = pending): ReactNode =>
    isPending ? undefined : (
      <Link to={href} className="hover:underline">
        {n}
      </Link>
    )

  return (
    <div>
      <PageHeader title="Insights" info="Runs across bronze, silver and gold, computed from the runs loaded now." />

      {all.goldError && (
        <Note tone="fail">Gold runs unavailable right now. The counts below leave gold out.</Note>
      )}
      {all.failedPipelines > 0 && (
        <Note tone="warn">
          Runs for {all.failedPipelines} {all.failedPipelines === 1 ? 'sync' : 'syncs'} could not be loaded, so
          the counts leave them out.
        </Note>
      )}

      <ListCard className="mb-5 px-[26px] py-[22px]">
        <CardHead
          title="Jobs"
          right={<WindowSelect label="Jobs window" value={span} onChange={setSpan} options={WINDOW_OPTIONS} />}
        />
        <FigureRow>
          <Figure
            testid="figure-succeeded"
            icon="✓"
            tone="ok"
            title="Succeeded"
            sub={words}
            value={linked(counts.succeeded, `/m/data-sync/runs?status=SUCCEEDED${windowQuery}`)}
            foot={pending ? undefined : layerSplit(counts, 'succeeded')}
          />
          <Figure
            testid="figure-warning"
            icon="!"
            tone="warn"
            title="Warning"
            sub={words}
            tip="A warning is a run that finished but tripped a quality gate, stopped early, or found an empty bronze."
            value={linked(counts.warning, `/m/data-sync/runs?status=WARNING${windowQuery}`)}
            foot={pending ? undefined : layerSplit(counts, 'warning')}
          />
          <Figure
            testid="figure-failed"
            icon="✕"
            tone="fail"
            title="Failed"
            sub={words}
            value={linked(counts.failed, `/m/data-sync/runs?status=FAILED${windowQuery}`)}
            foot={pending ? undefined : layerSplit(counts, 'failed')}
          />
          <Figure
            testid="figure-running"
            icon="▶"
            tone="live"
            title="Running"
            sub="now"
            value={linked(running.count, '/m/data-sync/runs?status=RUNNING&window=all')}
            foot={pending ? undefined : runningHint(running.names)}
          />
          <Figure
            testid="figure-upcoming"
            icon="◔"
            tone="accent"
            title="Coming up"
            sub="next 24 hours"
            value={linked(upcoming.count, '/m/data-sync/upcoming', upcomingPending)}
            foot={
              upcomingPending
                ? undefined
                : `${upcoming.syncs} ${upcoming.syncs === 1 ? 'sync' : 'syncs'} · ${upcoming.gold} gold`
            }
          />
        </FigureRow>
      </ListCard>

      <div className="mb-5 grid gap-5 xl:grid-cols-2">
        <ActivityCard rows={all.rows} now={now} />
        <TopTablesCard rows={all.rows} now={now} />
      </div>

      <div className="grid gap-4 xl:grid-cols-[2fr_1fr]">
        <GoldTrendCard />
        <FreshnessShareCard />
      </div>

      {all.collector}
    </div>
  )
}
