import { formatUtcStamp, runRowStatusView, statusBucket, type RunRow, type StatusBucket } from '@/lib/runsTable'

const SLOTS = 5

// A plain join, not cn: tailwind-merge does not know the outline-* token colours.
const join = (...parts: string[]) => parts.join(' ')

// Full class names so Tailwind sees them; colours are theme tokens only.
const DOT: Record<Exclude<StatusBucket, 'RUNNING'>, { fill: string; ring: string; glyph: string }> = {
  SUCCEEDED: { fill: 'bg-dot-ok text-white', ring: 'outline-dot-ok', glyph: '✓' },
  WARNING: { fill: 'bg-signal-warn-line text-white', ring: 'outline-signal-warn-line', glyph: '!' },
  FAILED: { fill: 'bg-dot-fail text-white', ring: 'outline-dot-fail', glyph: '✕' },
  SKIPPED: { fill: 'bg-dot-mute text-white', ring: 'outline-dot-mute', glyph: '–' },
}

const SIZE = { last: 'h-[26px] w-[26px] text-[13px]', other: 'h-5 w-5 text-[11px]' }
const RING = 'outline outline-2 outline-offset-2'

function dotTip(run: RunRow): string {
  return `${runRowStatusView(run).label} · ${formatUtcStamp(run.startedAt)} · ${run.runId}`
}

function RunDot({ run, last }: { run: RunRow; last: boolean }) {
  const tip = dotTip(run)
  const bucket = statusBucket(run.status)
  if (bucket === 'RUNNING') {
    return (
      <span
        title={tip}
        aria-label={tip}
        className={join(
          'inline-block animate-spin rounded-full border-[3px] border-signal-live/40 border-t-signal-live motion-reduce:animate-none',
          last ? join(SIZE.last, RING, 'outline-signal-live') : SIZE.other,
        )}
      />
    )
  }
  const dot = DOT[bucket]
  return (
    <span
      title={tip}
      aria-label={tip}
      className={join(
        'inline-grid place-items-center rounded-full font-extrabold',
        dot.fill,
        last ? join(SIZE.last, RING, dot.ring) : SIZE.other,
      )}
    >
      {dot.glyph}
    </span>
  )
}

export interface RunDotsProps {
  /** Oldest → newest, at most five. */
  runs: RunRow[]
}

/** An object's last five runs as dots: oldest on the left, the latest ringed, dashed circles for missing runs. */
export function RunDots({ runs }: RunDotsProps) {
  const shown = runs.slice(-SLOTS)
  const pad = SLOTS - shown.length
  const label =
    shown.length === 0
      ? 'No runs'
      : `Last ${shown.length} ${shown.length === 1 ? 'run' : 'runs'}, oldest first: ${shown
          .map((r) => runRowStatusView(r).label)
          .join(', ')}`
  return (
    <span role="img" aria-label={label} className="inline-flex items-center gap-1.5">
      {Array.from({ length: pad }, (_, i) => (
        <span
          key={`pad-${i}`}
          data-slot="run-dot-empty"
          title="no run"
          className="inline-block h-5 w-5 rounded-full border-[1.5px] border-dashed border-line"
        />
      ))}
      {shown.map((r, i) => (
        <RunDot key={r.key} run={r} last={i === shown.length - 1} />
      ))}
    </span>
  )
}
