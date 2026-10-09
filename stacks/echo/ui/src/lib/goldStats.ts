/** UIFPE-544: the gold fleet Overview.
 *
 *  Two halves, because the page asks two questions with different freshness
 *  needs. `live` is computed per request from the rows the controller already
 *  reads, so it is never more than seconds stale. `trend` is read from one
 *  file the nightly Glue job writes, because a Lambda cannot read an Iceberg
 *  table and the numbers only change once a night anyway.
 *
 *  Every rule a number on the page rests on lives here or in the server's
 *  gold_stats.py - never inside a component, where it cannot be tested. */

export interface GoldLiveWindow {
  as_of: string
  window_hours: number
  entities: number
  enabled: number
  runs: number
  succeeded: number
  failed: number
  skipped: number
  rows_written: number
  fresh: number
  stale: number
  unknown: number
  /** Named, so a tile can say WHICH table is behind rather than only how many. */
  stale_entities: string[]
}

export interface GoldTrendPoint {
  day: string
  entities: number
  runs: number
  failed: number
  rows_written: number
  fresh: number
}

export interface GoldTrend {
  schema_version: number
  generated_at: string
  days: number
  fleet: GoldTrendPoint[]
  entities: Record<string, Array<Omit<GoldTrendPoint, 'entities' | 'fresh'> & { fresh: boolean | null }>>
}

export interface GoldStatsResponse {
  live: GoldLiveWindow
  trend: GoldTrend | null
  trend_available: boolean
  trend_unavailable_reason: string | null
  trend_generated_at: string | null
  trend_age_minutes: number | null
  generated_at: string
}

/** How many days the page may ask for. The file holds 30; asking for more
 *  would silently return 30 and label it wrong. */
export const TREND_MAX_DAYS = 30
export const TREND_DEFAULT_DAYS = 30

/** Totals across the whole window, for the trend's own caption. Recomputed
 *  from the points the page is actually drawing, so the caption can never
 *  describe a different window from the chart above it. */
export function trendTotals(points: GoldTrendPoint[] | undefined) {
  const list = points ?? []
  const entityDays = list.reduce((n, p) => n + p.entities, 0)
  const fresh = list.reduce((n, p) => n + p.fresh, 0)
  return {
    days: list.length,
    runs: list.reduce((n, p) => n + p.runs, 0),
    failed: list.reduce((n, p) => n + p.failed, 0),
    rowsWritten: list.reduce((n, p) => n + p.rows_written, 0),
    entityDays,
    fresh,
    /** The share of entity-days that met their freshness target, or null when
     *  there are no entity-days to divide by - never 0%, which reads as an
     *  outage rather than as "nothing to report". */
    freshShare: entityDays > 0 ? fresh / entityDays : null,
  }
}

/** A tiny inline sparkline, as an SVG polyline, without a charting library.
 *
 *  Scaled against the series' PEAK from a zero baseline, never against its own
 *  min-max range. An 11% rise from 800 to 900 should read as an 11% rise;
 *  range scaling would draw it climbing the full height of the box, which is
 *  the exaggeration a trend chart exists to prevent. */
export function sparkPoints(
  values: number[],
  width = 120,
  height = 28,
): string {
  if (values.length === 0) return ''
  const peak = Math.max(...values, 0)
  const step = values.length > 1 ? width / (values.length - 1) : 0
  return values
    .map((v, i) => {
      const x = values.length > 1 ? i * step : width / 2
      const y = peak > 0 ? height - (v / peak) * height : height
      return `${round(x)},${round(y)}`
    })
    .join(' ')
}

function round(n: number): number {
  return Math.round(n * 10) / 10
}

/** What the freshness tile says. `stale` is the only state that needs a name
 *  attached, because it is the only one someone has to act on. */
export function freshnessCaption(live: GoldLiveWindow | undefined): string {
  if (!live) return ''
  if (live.stale > 0) {
    const names = live.stale_entities.slice(0, 3).join(', ')
    const more = live.stale_entities.length > 3 ? ` +${live.stale_entities.length - 3} more` : ''
    return `Behind: ${names}${more}`
  }
  if (live.unknown > 0 && live.fresh === 0) return 'No table has a last success to judge yet'
  if (live.unknown > 0) return `${live.unknown} without a cadence or a last success`
  return 'Every table is inside its freshness target'
}

/** Whether the pre-computed half is old enough to say so out loud. The job
 *  runs nightly, so anything past 36 hours means a night was missed. */
export const TREND_STALE_AFTER_MINUTES = 36 * 60

export function isTrendStale(ageMinutes: number | null | undefined): boolean {
  return typeof ageMinutes === 'number' && ageMinutes > TREND_STALE_AFTER_MINUTES
}

/** When the pre-computed half was computed, in UTC and said so.
 *
 *  UTC on purpose: every gold cadence and every start time on this module is
 *  UTC, and a browser-local rendering here would be the only local time on the
 *  page. Empty when there is nothing to date. */
export function formatGeneratedAt(iso: string | null | undefined): string {
  if (!iso) return ''
  const at = new Date(iso)
  if (Number.isNaN(at.getTime())) return ''
  const day = formatDayLabel(at.toISOString().slice(0, 10))
  const time = at.toISOString().slice(11, 16)
  return `${day}, ${time} UTC`
}

export function formatDayLabel(day: string): string {
  // 'YYYY-MM-DD' -> 'DD MMM', without pulling in a locale or a date library
  // for a label that only ever labels an axis.
  const [, month, dayOfMonth] = day.split('-')
  const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                  'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
  const name = months[Number(month) - 1]
  return name ? `${Number(dayOfMonth)} ${name}` : day
}
