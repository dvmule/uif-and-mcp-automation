export type ChartLegendTone = 'ok' | 'warn' | 'fail' | 'accent'

// Full class names, not built strings, so Tailwind can see them.
const SWATCH: Record<ChartLegendTone, string> = {
  ok: 'bg-dot-ok',
  warn: 'bg-signal-warn-line',
  fail: 'bg-dot-fail',
  accent: 'bg-accent',
}

export interface ChartLegendProps {
  items: ReadonlyArray<{ label: string; tone: ChartLegendTone }>
}

/** A row of colour swatches under a chart (Beacon's Legend3). */
export function ChartLegend({ items }: ChartLegendProps) {
  return (
    <div className="mt-3 flex flex-wrap gap-4 text-meta text-ink-soft">
      {items.map((item) => (
        <span key={item.label} className="inline-flex items-center gap-1.5">
          <i aria-hidden="true" className={`inline-block h-2.5 w-2.5 rounded-[2px] ${SWATCH[item.tone]}`} />
          {item.label}
        </span>
      ))}
    </div>
  )
}
