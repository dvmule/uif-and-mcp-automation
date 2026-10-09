import { cn } from '@/lib/utils'

export interface WindowSelectProps<T extends string> {
  value: T
  onChange: (next: T) => void
  /** [value, label] pairs, in order. */
  options: ReadonlyArray<readonly [T, string]>
  /** Accessible name of the select. */
  label: string
  /** For a ToolbarField caption's htmlFor. */
  id?: string
  /** 'card' sits in a CardHead (30px); 'toolbar' matches the 38px toolbar controls. */
  size?: 'card' | 'toolbar'
}

/** A card's time-window picker: a native select (ported from Beacon). */
export function WindowSelect<T extends string>({
  value,
  onChange,
  options,
  label,
  id,
  size = 'card',
}: WindowSelectProps<T>) {
  return (
    <select
      id={id}
      aria-label={label}
      value={value}
      onChange={(e) => onChange(e.target.value as T)}
      className={cn(
        'rounded-[10px] border border-line bg-paper-card px-2.5 text-ink outline-none focus:border-accent focus:ring-2 focus:ring-accent/25',
        size === 'toolbar' ? 'h-[38px] min-w-[160px] text-body font-semibold' : 'h-[30px] text-meta',
      )}
    >
      {options.map(([v, l]) => (
        <option key={v} value={v}>
          {l}
        </option>
      ))}
    </select>
  )
}
