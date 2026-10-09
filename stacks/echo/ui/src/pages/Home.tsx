import { useHomeSummary } from '@/hooks/useHomeSummary'
import { useRecentActivity } from '@/hooks/useRecentActivity'
import { useGoldStats } from '@/hooks/useGoldStats'
import { NeedsAttention } from '@/components/home/NeedsAttention'
import { RecentJobs } from '@/components/home/RecentJobs'
import { WelcomeCard } from '@/components/home/WelcomeCard'
import { FigureStrip, Note, StripFigure } from '@/components/relay'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { BEACON_PANEL_PATH } from '@/lib/echoPaths'
import { buildAttention, formatClockUtc } from '@/lib/homeAttention'
import { useAuthStore } from '@/store/authStore'

const SYNCS_PATH = '/m/data-sync/syncs'
const GOLD_PATH = '/m/data-sync/gold'
const DQ_PATH = '/m/data-quality/checks'

const FAIL = 'text-signal-fail'

function Of({ total }: { total: number }) {
  return <span className="ml-1 text-body font-medium text-ink-mute"> of {total}</span>
}

export function Home() {
  const username = useAuthStore((s) => s.username)
  const summary = useHomeSummary()
  const activity = useRecentActivity(20)
  const gold = useGoldStats()

  const summaryLoading = summary.isLoading
  // An errored or malformed summary is treated alike: no numbers, one Retry.
  const s = !summary.isError && summary.data?.syncs && summary.data.dq ? summary.data : undefined
  const summaryBad = !summaryLoading && !s
  const goldLoading = gold.isLoading
  const live = gold.data?.live
  const goldBad = !goldLoading && (gold.isError || !live)

  const windowHours = s?.window_hours
  const updated = formatClockUtc(s?.generated_at)

  const items = activity.data?.items ?? []
  const attention = buildAttention(items, live)

  const retry = () => {
    void summary.refetch?.()
    void activity.refetch?.()
    void gold.refetch?.()
  }

  const skeleton = <Skeleton className="h-7 w-16" />

  return (
    <div>
      <WelcomeCard username={username ?? undefined} windowHours={windowHours} updated={updated}>
        <FigureStrip>
          <StripFigure
            testid="tile-syncs"
            label="Syncs healthy"
            href={SYNCS_PATH}
            unavailable={summaryBad}
            value={
              s ? (
                <>
                  {s.syncs.ok}
                  <Of total={s.syncs.in_24h} />
                </>
              ) : (
                skeleton
              )
            }
            hint={
              s &&
              (s.syncs.fail > 0 ? (
                <>
                  <span className={FAIL}>{s.syncs.fail}</span> failed in the window
                </>
              ) : (
                'none failed'
              ))
            }
          />
          <StripFigure
            testid="tile-gold-fresh"
            label="Gold tables fresh"
            dot="ok"
            href={GOLD_PATH}
            unavailable={goldBad}
            value={
              live ? (
                <>
                  {live.fresh}
                  <Of total={live.entities} />
                </>
              ) : (
                skeleton
              )
            }
            hint={
              live &&
              (live.stale_entities.length > 0
                ? `Behind: ${live.stale_entities.join(', ')}`
                : 'all inside target')
            }
          />
          <StripFigure
            testid="tile-gold-failed"
            label="Gold runs failed"
            dot="fuchsia"
            valueTone={live && live.failed > 0 ? 'fail' : 'accent'}
            href={GOLD_PATH}
            unavailable={goldBad}
            value={live ? live.failed : skeleton}
            hint={live && `of ${live.runs} runs`}
          />
          <StripFigure
            testid="tile-dq-failed"
            label="Quality checks failing"
            valueTone={s && s.dq.fail > 0 ? 'fail' : 'accent'}
            href={DQ_PATH}
            unavailable={summaryBad}
            value={s ? s.dq.fail : skeleton}
            hint={s && `${s.dq.in_24h} ran`}
          />
        </FigureStrip>
      </WelcomeCard>

      {summaryBad && (
        <Note tone="fail" className="mt-0 mb-5">
          <span className="flex flex-wrap items-center gap-3">
            Couldn&apos;t reach the API.
            <Button type="button" variant="outline" size="xs" onClick={retry}>
              Retry
            </Button>
          </span>
        </Note>
      )}

      <NeedsAttention
        attention={attention}
        loading={activity.isLoading || goldLoading}
        activityFailed={activity.isError}
        goldFailed={goldBad}
        windowHours={windowHours}
        checkedAt={s?.generated_at}
      />

      <RecentJobs
        items={items}
        loading={activity.isLoading}
        failed={activity.isError}
        windowHours={windowHours}
      />

      <div className="flex items-center justify-between gap-3 rounded-card border border-white/70 bg-paper-card px-6 py-3.5 shadow-card">
        <p className="text-body text-ink-soft">
          <b className="font-semibold text-ink">Exports live in Beacon</b> — partner exports,
          destinations and their runs have their own console.
        </p>
        <Button asChild variant="outline" size="sm">
          <a href={BEACON_PANEL_PATH}>Open Beacon</a>
        </Button>
      </div>
    </div>
  )
}
