// A selectable wizard card; the selected one takes the accent border and tint, a disabled one carries a chip.
import type { ReactNode } from 'react'
import { Check } from 'lucide-react'
import { cn } from '@/lib/utils'

interface ChoiceCardProps {
  selected: boolean
  onSelect: () => void
  title: ReactNode
  children?: ReactNode
  disabled?: boolean
  /** Small label under the text, e.g. "Coming soon". */
  chip?: ReactNode
}

export function ChoiceCard({ selected, onSelect, title, children, disabled, chip }: ChoiceCardProps) {
  return (
    <button
      type="button"
      aria-pressed={selected}
      onClick={onSelect}
      disabled={disabled}
      className={cn(
        'flex items-start gap-3 rounded-xl border-[1.5px] bg-paper-card p-3 text-left transition-colors',
        selected ? 'border-accent bg-accent-soft' : 'border-line',
        disabled ? 'cursor-not-allowed opacity-60' : !selected && 'hover:border-accent',
      )}
    >
      <span
        className={cn(
          'mt-0.5 grid h-4 w-4 shrink-0 place-items-center rounded-full border-2',
          selected ? 'border-accent bg-accent' : 'border-paper-line',
        )}
      >
        {selected && <Check size={10} className="text-white" aria-hidden="true" />}
      </span>
      <span className="min-w-0">
        <span className="block text-body font-semibold text-ink">{title}</span>
        {children}
        {chip && (
          <span className="mt-1.5 inline-block rounded-full border border-line bg-paper-head px-2 text-label font-semibold text-ink-soft">
            {chip}
          </span>
        )}
      </span>
    </button>
  )
}
