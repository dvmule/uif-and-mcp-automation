import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { cn } from '@/lib/utils'

export interface FigureStripProps {
  className?: string
  children: ReactNode
}

/** Beacon's run-health strip: hairlines come from 1px gaps over the line colour. */
export function FigureStrip({ className, children }: FigureStripProps) {
  return (
    <div
      className={cn(
        'grid grid-cols-1 gap-px overflow-hidden rounded-b-card border-t border-line bg-line sm:grid-cols-2 lg:grid-cols-4',
        className,
      )}
    >
      {children}
    </div>
  )
}

export interface StripFigureProps {
  label: ReactNode
  value: ReactNode
  hint?: ReactNode
  /** When set, the whole figure is a link. */
  href?: string
  /** Show "Not available here" instead of the value. */
  unavailable?: boolean
  testid?: string
  dot?: 'accent' | 'ok' | 'fuchsia'
  valueTone?: 'accent' | 'fail'
}

// Full class names, not built strings, so Tailwind can see them.
const DOT = {
  accent: 'bg-accent',
  ok: 'bg-signal-ok',
  fuchsia: 'bg-fuchsia-brand',
} as const

const VALUE_TONE = {
  accent: 'text-accent',
  fail: 'text-signal-fail',
} as const

export function StripFigure({
  label,
  value,
  hint,
  href,
  unavailable,
  testid,
  dot = 'accent',
  valueTone = 'accent',
}: StripFigureProps) {
  const figure = (
    <div
      data-testid={testid}
      className={cn(
        'relative grid content-start gap-1.5 bg-paper-head px-6 py-4',
        href && 'h-full transition-colors hover:bg-paper-card',
      )}
    >
      <div className="flex items-center gap-2 text-meta font-bold text-ink">
        <span aria-hidden="true" className={cn('h-2 w-2 shrink-0 rounded-full', DOT[dot])} />
        {label}
      </div>
      {unavailable ? (
        <div className="text-body text-ink-mute">Not available here</div>
      ) : (
        <div className={cn('tnum text-kpi font-bold tracking-tight', VALUE_TONE[valueTone])}>{value}</div>
      )}
      {hint && <div className="text-meta text-ink-mute">{hint}</div>}
    </div>
  )
  if (!href) return figure
  return (
    <Link to={href} className="block h-full focus-visible:-outline-offset-2">
      {figure}
    </Link>
  )
}
