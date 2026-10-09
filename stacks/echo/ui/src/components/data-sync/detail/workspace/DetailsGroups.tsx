import { Kv, KvGroup, Note, Pill } from '@/components/relay'
import { silverTableFqn } from '@/lib/cloud'
import { arnTail, secretName } from '@/lib/connections'
import { formatMinutesAhead } from '@/lib/detailHeader'
import { goldScheduleFacts, goldWorkerLabel, syncBronzePath, syncScheduleFacts } from '@/lib/detailFacts'
import { browserTimeZone } from '@/lib/goldWeekly'
import type { GoldPipelineConfig, PipelineConfig } from '@/lib/types'
import { relativeTime } from '@/lib/utils'
import { CopyLink } from '../rail/CopyLink'

function NextRunPill({ min }: { min: number | null }) {
  if (min === null) return null
  return (
    <>
      {' '}
      <Pill tone="mute" className="ml-1">
        next in {formatMinutesAhead(min)}
      </Pill>
    </>
  )
}

interface SyncProps {
  pipeline: PipelineConfig
  /** The newest run's watermark, or an em dash. */
  watermarkValue: string
  nextRunInMin: number | null
  onReset: () => void
}

/** A sync's Details tab: source and load, silver, schedule and runtime. */
export function SyncDetailsGroups({ pipeline, watermarkValue, nextRunInMin, onReset }: SyncProps) {
  const arn = pipeline.source?.secret_arn
  const bronzePath = syncBronzePath(pipeline)
  const silverFqn = silverTableFqn(pipeline)
  const pk = pipeline.silver?.primary_key_cols ?? []
  const tags = pipeline.metadata?.tags ?? []
  const s = syncScheduleFacts(pipeline)

  return (
    <div className="space-y-4">
      <KvGroup title="Source and load" testid="details-source">
        <Kv label="Connection">
          {arn ? <span title={arn}>{secretName(arn)} · {arnTail(arn)}</span> : '—'}
        </Kv>
        <Kv label="Load">
          {pipeline.extract?.mode ?? 'incremental'} · watermark {pipeline.extract?.watermark_col ?? '—'} ·{' '}
          <span className="break-all font-mono text-meta">{watermarkValue}</span> ·{' '}
          <button type="button" onClick={onReset} className="font-semibold text-accent hover:underline">
            Reset…
          </button>
        </Kv>
        {bronzePath && (
          <Kv label="Bronze">
            <span className="break-all font-mono text-meta">{bronzePath}</span>{' '}
            <CopyLink value={bronzePath} label="Bronze" />
          </Kv>
        )}
      </KvGroup>

      <KvGroup title="Silver" testid="details-silver">
        <Kv label="Table">
          {silverFqn ? (
            <>
              <span className="break-all font-mono text-meta">{silverFqn}</span>
              {pk.length > 0 && <> · PK {pk.join(', ')}</>} <CopyLink value={silverFqn} label="Silver" />
            </>
          ) : (
            <span className="text-ink-mute">Silver layer not enabled</span>
          )}
        </Kv>
        <Kv label="Owner · tags">
          <span className="inline-flex flex-wrap items-center gap-1.5">
            <span>{pipeline.metadata?.owner ?? 'Unassigned'}</span>
            {tags.map((t) => (
              <Pill key={t} tone="mute">
                {t}
              </Pill>
            ))}
          </span>
        </Kv>
      </KvGroup>

      <KvGroup title="Schedule and runtime" testid="details-schedule">
        <Kv label="Schedule">
          {s.frequency}
          {s.dailyWindow && <> at {s.dailyWindow}</>}
          <NextRunPill min={nextRunInMin} />
        </Kv>
        {s.reconcileWindow && <Kv label="Reconcile window">{s.reconcileWindow}</Kv>}
        <Kv label="Glue">
          <span className="tnum">
            {s.worker} · {s.timeoutMin} min timeout
          </span>
        </Kv>
      </KvGroup>
    </div>
  )
}

interface GoldProps {
  gold: GoldPipelineConfig
  nextRunInMin: number | null
}

/** A gold entity's Details tab: schedule and runtime, and its one bookmark (ADR-0020 decision 1). */
export function GoldDetailsGroups({ gold, nextRunInMin }: GoldProps) {
  const s = gold.schedule ?? {}
  const g = gold.glue ?? {}
  const f = goldScheduleFacts(gold, browserTimeZone())
  const gate = gold.snapshot_gate
  // Never the retired per-source map that may still sit on the row.
  const bookmark = gold.watermark?.bookmark ?? null
  const completed = gold.metadata?.last_run_at ?? null
  const glue = [
    `${goldWorkerLabel(g)} incremental`,
    ...(g.full_load ? [`${goldWorkerLabel(g.full_load)} full`] : []),
    ...(g.timeout_minutes != null ? [`${g.timeout_minutes} min timeout`] : []),
    ...(s.recompute?.every_n_runs != null ? [`full load every ${s.recompute.every_n_runs} runs`] : []),
  ]

  return (
    <div className="space-y-4">
      <KvGroup title="Schedule and runtime" testid="details-schedule">
        <Kv label="Schedule">
          {f.cadence}
          {f.cadenceRaw && <> <span className="text-meta text-ink-mute">({f.cadenceRaw})</span></>}
          {f.runDays && (
            <>
              {' '}· {f.runDays}
              {f.runDaysUtc && <> <span className="text-meta text-ink-mute">({f.runDaysUtc})</span></>}
            </>
          )}{' '}
          · {f.start}
          <NextRunPill min={nextRunInMin} />
        </Kv>
        <Kv label="Failure cooldown">
          {s.failure_cooldown_minutes != null ? `${s.failure_cooldown_minutes} min` : '—'}
        </Kv>
        <Kv label="Glue">
          <span className="tnum">{glue.join(' · ')}</span>
        </Kv>
      </KvGroup>

      <div>
        <KvGroup title="Bookmark" testid="details-bookmark">
          {gate != null && (
            <Kv label="Snapshot gate">
              <Pill tone={gate ? 'ok' : 'mute'}>{gate ? 'on' : 'off'}</Pill>
            </Kv>
          )}
          <Kv label="Bookmark">
            {bookmark ? (
              <>
                <span className="break-all font-mono text-meta" data-testid="gold-bookmark">
                  {bookmark}
                </span>{' '}
                <span className="text-meta text-ink-mute">· {relativeTime(bookmark)}</span>
              </>
            ) : (
              <span className="text-ink-mute">Not written yet — set by the next run that processes changes.</span>
            )}
          </Kv>
          <Kv label="Last run completed">
            <span className="tnum" title={completed ?? undefined}>
              {completed ? relativeTime(completed) : '—'}
            </span>
          </Kv>
        </KvGroup>
        <Note>
          One bookmark per table: the start of the last run that processed rows. Idle and failed runs do not move it.
        </Note>
      </div>
    </div>
  )
}
