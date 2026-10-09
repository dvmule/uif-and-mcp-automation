import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { Copy } from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet'
import { StatusBadge } from '@/components/relay'
import { EXPLAIN_TONE, explainRun } from '@/lib/runExplain'
import { isInFlightStatus } from '@/lib/runLifecycle'
import {
  RUN_LAYER_LABEL,
  formatUtcClockTime,
  formatUtcStamp,
  runRowStatusView,
  statusBucket,
  type RunRow,
} from '@/lib/runsTable'
import { cn, formatDuration, formatRows } from '@/lib/utils'
import { useRunDrawerParam } from './useRunDrawerParam'

interface Props {
  /** The run to show; null keeps the drawer closed. */
  row: RunRow | null
  /** Pipeline (bronze/silver) rows only. Gold rows have no retry. */
  onRetry?: (row: RunRow) => void
  retryDisabled?: boolean
  /** Gold rows only: start a new run of the entity (the page confirms first). */
  onRunNow?: (row: RunRow) => void
  runNowDisabled?: boolean
}

async function copyText(text: string, what: string) {
  try {
    await navigator.clipboard.writeText(text)
    toast.success(`Copied ${what}`)
  } catch {
    toast.error(`Could not copy ${what}`)
  }
}

function bytes(n: number): string {
  if (n < 1024) return `${n} B`
  const units = ['KB', 'MB', 'GB', 'TB']
  let value = n / 1024
  let i = 0
  while (value >= 1024 && i < units.length - 1) {
    value /= 1024
    i += 1
  }
  return `${value.toFixed(value < 10 ? 1 : 0)} ${units[i]}`
}

export function RunDrawer({ row, onRetry, retryDisabled, onRunNow, runNowDisabled }: Props) {
  const { close } = useRunDrawerParam()
  if (!row) return null

  const run = row.run
  const bucket = statusBucket(row.status)
  const attention = bucket === 'FAILED' || bucket === 'WARNING'
  const running = isInFlightStatus(row.status)
  const gold = row.layer === 'gold'
  const explanation = explainRun(row)
  const phase = run.phase ?? run.load_phase
  const errorText = run.error_message?.trim() || null

  const ended = running
    ? 'Still running'
    : run.completed_at
      ? `${formatUtcClockTime(run.completed_at)}${row.durationSec != null ? ` · ${formatDuration(row.durationSec)}` : ''}`
      : row.durationSec != null
        ? formatDuration(row.durationSec)
        : '—'

  const delivery: Array<[string, string]> = []
  if (run.replication_status) delivery.push(['Replication', run.replication_status])
  if (run.bq_status) delivery.push(['BigQuery', run.bq_status])
  if (run.files_replicated != null) delivery.push(['Files replicated', formatRows(run.files_replicated)])
  if (run.files_skipped != null) delivery.push(['Files skipped', formatRows(run.files_skipped)])
  if (run.files_failed != null) delivery.push(['Files failed', formatRows(run.files_failed)])
  if (run.bytes_replicated != null) delivery.push(['Bytes replicated', bytes(run.bytes_replicated)])

  return (
    <Sheet open onOpenChange={(o) => { if (!o) close() }}>
      <SheetContent
        side="right"
        className="gap-0 overflow-y-auto border-l border-line bg-paper-card p-0 data-[side=right]:w-full data-[side=right]:sm:max-w-[560px]"
      >
        <SheetHeader className="gap-1 px-5 pb-1 pt-5 pr-12">
          <div className="flex items-start justify-between gap-3">
            <SheetTitle className="min-w-0 break-words text-base font-semibold text-ink">
              {row.objectHref ? (
                <Link to={row.objectHref} className="text-accent hover:underline">
                  {row.object}
                </Link>
              ) : (
                row.object
              )}
            </SheetTitle>
            <StatusBadge view={runRowStatusView(row)} />
          </div>
          <SheetDescription asChild>
            <div className="flex items-center gap-1.5 font-mono text-meta text-ink-mute">
              <span className="break-all">{row.runId}</span>
              <button
                type="button"
                aria-label="Copy run id"
                onClick={() => void copyText(row.runId, 'run id')}
                className="grid place-items-center rounded text-ink-mute hover:text-ink"
              >
                <Copy size={12} aria-hidden="true" />
              </button>
            </div>
          </SheetDescription>
        </SheetHeader>

        <div className="px-5 pb-5">
          <Section title="What happened">
            <div
              data-testid="run-explain"
              className={cn('rounded-lg border px-3 py-2.5 text-body text-ink', EXPLAIN_TONE[explanation.tone])}
            >
              <b className="font-semibold">{explanation.headline}</b> {explanation.detail}
              {explanation.pattern && (
                <span className="ml-1 text-meta text-ink-mute">(pattern: {explanation.pattern})</span>
              )}
            </div>
          </Section>

          <Section title="Properties">
            <Grid>
              <Kv label="Kind" value={row.kind} />
              <Kv label="Layer" value={RUN_LAYER_LABEL[row.layer]} />
              <Kv label="Started" value={formatUtcStamp(row.startedAt)} />
              <Kv label="Ended" value={ended} />
              {run.job_name && <Kv label="Job" value={run.job_name} mono />}
              {!gold && phase && <Kv label="Phase" value={phase} />}
            </Grid>
          </Section>

          <Section title="Results">
            <Grid>
              <Kv label="Rows read" value={formatRows(row.rowsRead)} />
              <Kv label="Rows written" value={formatRows(row.rowsWritten)} />
              <Kv label="Failed quality" value={formatRows(run.rows_quality_failed)} />
              {gold ? (
                <Kv
                  label="Affected keys"
                  value={formatRows('affected_keys' in run ? run.affected_keys : null)}
                />
              ) : (
                <Kv label="New watermark" value={running ? '—' : (run.new_watermark ?? 'unchanged')} />
              )}
            </Grid>
          </Section>

          {delivery.length > 0 && (
            <Section title="Delivery">
              <Grid>
                {delivery.map(([label, value]) => (
                  <Kv key={label} label={label} value={value} />
                ))}
              </Grid>
            </Section>
          )}

          {(errorText || attention) && (
            <Section title="Technical detail">
              <details open={attention}>
                <summary className="cursor-pointer text-meta font-semibold text-accent">
                  Raw error message
                </summary>
                <pre className="mt-2 whitespace-pre-wrap break-words rounded-md border border-line bg-paper-head px-2.5 py-2 font-mono text-[11.5px] text-ink-soft">
                  {errorText ?? 'No error message captured.'}
                </pre>
              </details>
            </Section>
          )}

          <div className="mt-5 flex flex-wrap items-center gap-2 border-t border-line pt-4">
            {row.pipelineId && onRetry && (
              <Button type="button" size="sm" disabled={retryDisabled} onClick={() => onRetry(row)}>
                Retry run
              </Button>
            )}
            {gold && onRunNow && (
              <Button type="button" size="sm" disabled={runNowDisabled} onClick={() => onRunNow(row)}>
                Run now
              </Button>
            )}
            {row.objectHref && (
              <Button asChild variant="outline" size="sm">
                <Link to={row.objectHref}>{gold ? 'Open gold table' : 'Open sync'}</Link>
              </Button>
            )}
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="text-accent"
              onClick={() => void copyText(window.location.href, 'link')}
            >
              Copy link
            </Button>
            <span className="flex-1" />
            <Button type="button" variant="ghost" size="sm" onClick={close}>
              Close
            </Button>
          </div>
        </div>
      </SheetContent>
    </Sheet>
  )
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="mt-4">
      <h3 className="mb-1.5 text-label font-semibold uppercase tracking-wider text-ink-mute">{title}</h3>
      {children}
    </section>
  )
}

function Grid({ children }: { children: ReactNode }) {
  return <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-body">{children}</dl>
}

function Kv({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="min-w-0">
      <dt className="text-label text-ink-mute">{label}</dt>
      <dd className={cn('tnum break-words font-semibold text-ink', mono && 'font-mono text-meta font-medium')}>
        {value}
      </dd>
    </div>
  )
}
