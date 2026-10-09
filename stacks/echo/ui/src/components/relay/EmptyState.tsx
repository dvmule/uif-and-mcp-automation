import type { ReactNode } from 'react'
import { AlertCircle, AlertTriangle, CheckCircle2, Info, type LucideIcon } from 'lucide-react'
import { TableCell, TableRow } from '@/components/ui/table'
import { cn } from '@/lib/utils'

export interface EmptyStateProps {
  icon?: LucideIcon
  title?: ReactNode
  /** Renders the title as a heading of this level (a page whose whole content is the empty state). */
  headingLevel?: 1 | 2 | 3
  children?: ReactNode
  /** Buttons or links under the text: the page's primary action, Clear all, or Retry. */
  action?: ReactNode
  /** Set inside a table body: renders as a full-width row with this many columns. */
  colSpan?: number
}

export function EmptyState({ icon: Icon, title, headingLevel, children, action, colSpan }: EmptyStateProps) {
  const Heading = headingLevel ? (`h${headingLevel}` as const) : 'b'
  const content =
    Icon || title || action ? (
      <span className="flex flex-col items-center gap-1.5 text-body text-ink-mute">
        {Icon && (
          <span className="mb-1 grid h-9 w-9 place-items-center rounded-full bg-accent-soft text-accent">
            <Icon size={18} strokeWidth={1.75} aria-hidden="true" />
          </span>
        )}
        {title && <Heading className="text-body font-semibold text-ink">{title}</Heading>}
        {children && <span>{children}</span>}
        {action && <span className="mt-1.5 flex flex-wrap items-center justify-center gap-2">{action}</span>}
      </span>
    ) : (
      <span className="text-body text-ink-mute">{children}</span>
    )
  if (colSpan) {
    return (
      <TableRow className="border-0 hover:bg-transparent">
        <TableCell colSpan={colSpan} className="px-3 py-10 text-center whitespace-normal">
          {content}
        </TableCell>
      </TableRow>
    )
  }
  return <div className="py-10 text-center">{content}</div>
}

export type NoteTone = 'mute' | 'ok' | 'warn' | 'fail'

const NOTE_ICON: Record<NoteTone, LucideIcon> = {
  mute: Info,
  ok: CheckCircle2,
  warn: AlertTriangle,
  fail: AlertCircle,
}

const NOTE_TONE: Record<NoteTone, string> = {
  mute: 'border-pearson-light-purple bg-paper-head text-ink-soft',
  ok: 'border-signal-ok bg-signal-ok-bg text-signal-ok',
  warn: 'border-signal-warn-line bg-signal-warn-bg text-signal-warn',
  fail: 'border-signal-fail bg-signal-fail-bg text-signal-fail',
}

export interface NoteProps {
  tone?: NoteTone
  className?: string
  children: ReactNode
}

/** An inline callout with a leading icon so the tone reads without colour. */
export function Note({ tone = 'mute', className, children }: NoteProps) {
  const Icon = NOTE_ICON[tone]
  return (
    <div className={cn('my-2 flex items-start gap-2 rounded-r-[10px] border-l-[3px] px-3.5 py-2.5 text-[13px]', NOTE_TONE[tone], className)}>
      <Icon size={14} strokeWidth={1.9} aria-hidden="true" className="mt-0.5 shrink-0" />
      <div className="min-w-0 flex-1">{children}</div>
    </div>
  )
}
