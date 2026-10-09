import type { ReactNode } from 'react'
import { cn } from '@/lib/utils'

export interface CountLineProps {
  children: ReactNode
  className?: string
}

/** The quiet line under a list card's table; the caller joins segments with " · ". */
export function CountLine({ children, className }: CountLineProps) {
  return <p className={cn('border-t border-line px-3 py-2.5 text-meta text-ink-mute', className)}>{children}</p>
}

export interface CountSegmentProps {
  testid?: string
  strong?: boolean
  children: ReactNode
}

/** One figure in a CountLine; `strong` for the lead figure. */
export function CountSegment({ testid, strong = false, children }: CountSegmentProps) {
  return (
    <span data-testid={testid} className={strong ? 'font-semibold text-ink-soft' : undefined}>
      {children}
    </span>
  )
}
