import type { ReactNode } from 'react'
import { cn } from '@/lib/utils'

export type PillTone = 'mute' | 'accent' | 'ok' | 'warn' | 'fail'

const PILL_TONE: Record<PillTone, string> = {
  mute: 'bg-paper-head text-ink-soft',
  accent: 'bg-pearson-mist text-accent',
  ok: 'bg-signal-ok-bg text-signal-ok',
  warn: 'bg-signal-warn-bg text-signal-warn',
  fail: 'bg-signal-fail-bg text-signal-fail',
}

export interface PillProps {
  tone?: PillTone
  children: ReactNode
  className?: string
}

/** A soft fact pill (id, cloud, layers, domain); status keeps StatusBadge. */
export function Pill({ tone = 'mute', children, className }: PillProps) {
  return (
    <span
      className={cn(
        'inline-flex items-center rounded-full px-2.5 py-0.5 text-meta font-semibold',
        PILL_TONE[tone],
        className,
      )}
    >
      {children}
    </span>
  )
}
