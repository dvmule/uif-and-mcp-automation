import { cn } from '@/lib/utils'

export interface SegmentedControlProps<T extends string> {
  options: ReadonlyArray<{ value: T; label: string }>
  value: T
  onChange: (next: T) => void
  /** Accessible name of the group. */
  label: string
  /** 'sm' sits at a toolbar control's 38px height; 'md' is the header size. */
  size?: 'sm' | 'md'
  className?: string
}

/** A row of pressed/unpressed buttons for one choice (Beacon's segmented pill). */
export function SegmentedControl<T extends string>({
  options,
  value,
  onChange,
  label,
  size = 'md',
  className,
}: SegmentedControlProps<T>) {
  return (
    <div
      role="group"
      aria-label={label}
      className={cn(
        'inline-flex items-center gap-0.5 rounded-lg border border-line bg-paper-card p-1 shadow-card',
        size === 'sm' && 'h-[38px]',
        className,
      )}
    >
      {options.map((o) => {
        const on = o.value === value
        return (
          <button
            key={o.value}
            type="button"
            aria-pressed={on}
            onClick={() => onChange(o.value)}
            className={cn(
              'rounded-md font-semibold transition-colors',
              size === 'sm' ? 'px-2 py-0.5 text-label' : 'px-2.5 py-1 text-meta',
              on ? 'bg-pearson-purple text-white shadow-card' : 'text-ink-mute hover:text-ink',
            )}
          >
            {o.label}
          </button>
        )
      })}
    </div>
  )
}
