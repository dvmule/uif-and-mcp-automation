import type { ReactNode } from 'react'
import { cn } from '@/lib/utils'

export interface ListCardProps {
  children: ReactNode
  className?: string
}

/** The flush card a list page holds its toolbar, table and count line in.
 *  Not overflow-hidden, so a toolbar dropdown can hang past a short table. */
export function ListCard({ children, className }: ListCardProps) {
  return (
    <div className={cn('rounded-card border border-white/70 bg-paper-card shadow-card', className)}>{children}</div>
  )
}

// Full class names, not built strings, so Tailwind can see them.
const TONE = {
  ok: 'text-signal-ok',
  warn: 'text-signal-warn',
  fail: 'text-signal-fail',
} as const

export interface CountFigureProps {
  loading?: boolean
  /** Colour the figure, e.g. fail when something failed. */
  tone?: keyof typeof TONE
  children: ReactNode
}

/** One number inside a CountSegment; a small shimmer while it loads. */
export function CountFigure({ loading = false, tone, children }: CountFigureProps) {
  if (loading) {
    return (
      <span
        data-slot="skeleton"
        aria-hidden="true"
        className="inline-block h-3 w-5 animate-pulse rounded-md bg-paper-line/60 align-middle"
      />
    )
  }
  return <span className={cn('tnum', tone && TONE[tone])}>{children}</span>
}
