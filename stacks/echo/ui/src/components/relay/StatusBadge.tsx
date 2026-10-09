import { cn } from '@/lib/utils'
import { TONE_CLASS, type StatusView } from '@/lib/statusVocabulary'

export interface StatusBadgeProps {
  view: StatusView
  size?: 'sm' | 'md'
  /** Show the leading icon (or, for a running status, the pulsing dot). */
  showIcon?: boolean
  /** Hover text. Defaults to the raw wire value. */
  title?: string
}

/** Soft pill for any status; build `view` with the functions in lib/statusVocabulary. */
export function StatusBadge({ view, size = 'md', showIcon = true, title }: StatusBadgeProps) {
  const Icon = view.icon
  return (
    <span
      title={title ?? view.raw}
      className={cn(
        'inline-flex items-center gap-1.5 whitespace-nowrap rounded-full text-[12px] font-bold',
        size === 'sm' ? 'px-2 py-0' : 'px-[11px] py-[3px]',
        TONE_CLASS[view.tone],
      )}
    >
      {showIcon && view.tone === 'ok' && (
        <span aria-hidden="true" className="h-[7px] w-[7px] shrink-0 rounded-full bg-current" data-dot="ok" />
      )}
      {showIcon &&
        (view.tone === 'live' ? (
          <span aria-hidden="true" className="h-1.5 w-1.5 shrink-0 rounded-full bg-current pulse-ring" />
        ) : (
          <Icon size={12} strokeWidth={2} aria-hidden="true" className="shrink-0" />
        ))}
      {view.label}
    </span>
  )
}
