import type { ReactNode } from 'react'
import { Skeleton } from '@/components/ui/skeleton'

export type FigureTone = 'ok' | 'warn' | 'fail' | 'live' | 'accent'

// Full class names, not built strings, so Tailwind can see them.
const DISC: Record<FigureTone, string> = {
  ok: 'bg-dot-ok',
  warn: 'bg-signal-warn-line',
  fail: 'bg-dot-fail',
  live: 'bg-signal-live',
  accent: 'bg-accent',
}

export interface FigureProps {
  icon: ReactNode
  tone: FigureTone
  title: ReactNode
  sub: ReactNode
  /** undefined shows a skeleton while the number loads. */
  value: ReactNode | number
  foot?: ReactNode
  testid?: string
  /** Hover text on the whole figure. */
  tip?: string
}

/** One figure of a figures card: icon disc, title, sub line, big number, foot (Beacon's Figure). */
export function Figure({ icon, tone, title, sub, value, foot, testid, tip }: FigureProps) {
  return (
    <div
      data-testid={testid}
      title={tip}
      className="min-w-0 sm:border-l sm:border-line sm:px-5 sm:first:border-l-0 sm:first:pl-0"
    >
      <div className="flex items-center gap-2 text-[15px] font-bold text-ink">
        <span
          aria-hidden="true"
          className={`grid h-[18px] w-[18px] place-items-center rounded-full text-[10px] font-extrabold text-white ${DISC[tone]}`}
        >
          {icon}
        </span>
        {title}
      </div>
      <div className="mt-0.5 text-meta text-ink-mute">{sub}</div>
      <div className="tnum my-1.5 text-[32px] font-extrabold leading-none text-accent">
        {value === undefined ? <Skeleton className="h-8 w-12" /> : value}
      </div>
      {foot && <div className="text-meta text-ink-mute">{foot}</div>}
    </div>
  )
}

export interface FigureRowProps {
  children: ReactNode
}

/** The figures in a row: two across on small screens, five on wide ones. */
export function FigureRow({ children }: FigureRowProps) {
  return <div className="grid gap-y-4 sm:grid-cols-2 xl:grid-cols-5">{children}</div>
}
