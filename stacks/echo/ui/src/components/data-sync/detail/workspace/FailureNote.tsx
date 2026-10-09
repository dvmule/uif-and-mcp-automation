import { Note } from '@/components/relay'
import { explainRun } from '@/lib/runExplain'
import { statusBucket, type RunRow } from '@/lib/runsTable'
import { clockAndAgo } from '@/lib/trust'

interface Props {
  /** The latest run; the note shows only when it failed or warned. */
  row: RunRow | null
  onOpenRun: (runId: string) => void
  now?: Date
}

/** The latest run's failure in plain words, as a tone note under the header, with the raw message one click away. */
export function FailureNote({ row, onOpenRun, now }: Props) {
  if (!row) return null
  const bucket = statusBucket(row.status)
  if (bucket !== 'FAILED' && bucket !== 'WARNING') return null

  const explanation = explainRun(row)
  const raw = row.run.error_message?.trim() || 'No error message captured.'

  return (
    <div data-testid="latest-failure">
      <Note tone={explanation.tone} className="mb-5">
        <div data-testid="latest-failure-explain" className="text-body text-ink">
          <b className="block font-semibold">{explanation.headline}</b>
          {explanation.detail}
          <span className="mt-1.5 block text-meta text-ink-mute">
            {explanation.pattern && <>pattern: {explanation.pattern} · </>}
            {clockAndAgo(row.startedAt, now)} ·{' '}
            <button
              type="button"
              onClick={() => onOpenRun(row.runId)}
              className="font-semibold text-accent hover:underline"
            >
              open run
            </button>
          </span>
        </div>
        <details className="mt-2 text-meta">
          <summary className="cursor-pointer font-semibold text-accent">Technical detail</summary>
          <pre className="mt-1.5 whitespace-pre-wrap break-words rounded-md border border-line bg-paper-head px-2.5 py-2 font-mono text-[11.5px] text-ink-soft">
            {raw}
          </pre>
        </details>
      </Note>
    </div>
  )
}
