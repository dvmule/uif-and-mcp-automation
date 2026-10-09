// Beacon's range control; stateless until a fleet runs source brings value/onChange.
export function RangeControl() {
  return (
    <div
      role="group"
      aria-label="Time range"
      className="inline-flex items-center gap-0.5 rounded-lg border border-line bg-paper-card p-1 shadow-card"
    >
      <button
        type="button"
        aria-pressed="true"
        className="rounded-md bg-pearson-purple px-2.5 py-1 text-meta font-semibold text-white shadow-card"
      >
        24 hours
      </button>
      <button
        type="button"
        aria-pressed="false"
        disabled
        title="Not available here yet"
        className="rounded-md px-2.5 py-1 text-meta font-semibold text-ink-mute disabled:opacity-45"
      >
        7 days
      </button>
      <button
        type="button"
        aria-pressed="false"
        disabled
        title="Not available here yet"
        className="rounded-md px-2.5 py-1 text-meta font-semibold text-ink-mute disabled:opacity-45"
      >
        30 days
      </button>
    </div>
  )
}
