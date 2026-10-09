import { Link } from 'react-router-dom'
import { EmptyState, Note, StatusBadge } from '@/components/relay'
import { Skeleton } from '@/components/ui/skeleton'
import { formatClockUtc, type Attention, type AttentionRow } from '@/lib/homeAttention'
import { formatRunTime } from '@/lib/syncRow'
import { relativeTime } from '@/lib/utils'

export interface NeedsAttentionProps {
  attention: Attention
  loading: boolean
  /** The activity feed could not be read, so an empty list proves nothing. */
  activityFailed: boolean
  /** The gold stats could not be read, so stale tables cannot be listed. */
  goldFailed: boolean
  windowHours?: number
  /** `generated_at` of the summary, for "checked HH:MM UTC". */
  checkedAt?: string
}

const CARD = 'rounded-card border border-white/70 bg-paper-card shadow-card'
const LINK = 'font-bold text-inherit underline-offset-2 hover:underline'

function Row({ row }: { row: AttentionRow }) {
  return (
    <Note tone={row.view.tone === 'warn' ? 'warn' : 'fail'} className="my-0 mb-2">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <StatusBadge view={row.view} />
        <span className="min-w-[12rem] flex-1">
          <Link to={row.href} className={LINK}>
            {row.subject}
          </Link>
          {` · ${row.area}`}
          {row.message && ` · ${row.message}`}
        </span>
        {row.ts && (
          <span className="whitespace-nowrap text-meta opacity-70">
            {formatRunTime(row.ts)} · {relativeTime(row.ts)}
          </span>
        )}
        <Link to={row.href} aria-label={`Open ${row.subject}`} className={LINK}>
          Open
        </Link>
      </div>
    </Note>
  )
}

/** Failed and stopped jobs plus stale gold tables, or a plain "nothing to do". */
export function NeedsAttention({
  attention,
  loading,
  activityFailed,
  goldFailed,
  windowHours,
  checkedAt,
}: NeedsAttentionProps) {
  const { rows, total, more } = attention
  const within = windowHours ? `in the last ${windowHours} hours` : 'recently'
  const checked = formatClockUtc(checkedAt)

  let body
  if (loading) {
    body = (
      <div aria-hidden="true" data-testid="attention-loading" className="space-y-3 py-1">
        <Skeleton className="h-5 w-full" />
        <Skeleton className="h-5 w-4/5" />
      </div>
    )
  } else {
    body = (
      <>
        {rows.map((row) => (
          <Row key={row.key} row={row} />
        ))}
        {more > 0 && (
          <div className="py-1 text-meta">
            <Link to="/m/data-sync/runs" className="font-semibold text-accent hover:underline">
              +{more} more
            </Link>
          </div>
        )}
        {rows.length === 0 && activityFailed && (
          <Note tone="fail">Couldn&apos;t read recent activity, so failures can&apos;t be listed.</Note>
        )}
        {rows.length === 0 && !activityFailed && !goldFailed && (
          <div className={CARD}>
            <EmptyState title="Nothing needs you right now">
              All syncs and gold tables ran as expected {within}
              {checked && ` · checked ${checked} UTC`}
            </EmptyState>
          </div>
        )}
        {rows.length === 0 && !activityFailed && goldFailed && (
          <div className={CARD}>
            <EmptyState title={`No failed jobs ${within}`} />
          </div>
        )}
        {goldFailed && <Note tone="mute">Gold freshness unavailable right now.</Note>}
      </>
    )
  }

  return (
    <section aria-labelledby="home-attention-title" className="mb-5">
      <div className="mb-2 flex items-center gap-3">
        <h2 id="home-attention-title" className="text-[18px] font-extrabold tracking-tight text-ink">
          Needs attention
        </h2>
        {!loading && total > 0 && (
          <span className="text-meta font-bold text-signal-fail">
            {total} {total === 1 ? 'item' : 'items'}
          </span>
        )}
      </div>
      {body}
    </section>
  )
}
