import type { ReactNode } from 'react'
import { Search } from 'lucide-react'
import { cn } from '@/lib/utils'

const CAPTION = 'mb-1 block text-[12.5px] font-bold text-ink'

export interface ToolbarProps {
  children: ReactNode
  className?: string
}

/** The filter row inside a list card: wraps, captions above, controls bottom-aligned. */
export function Toolbar({ children, className }: ToolbarProps) {
  return <div className={cn('flex flex-wrap items-end gap-3 border-b border-line px-3 py-2.5', className)}>{children}</div>
}

export interface ToolbarFieldProps {
  label: ReactNode
  /** Makes the caption a <label> for this control id. */
  htmlFor?: string
  children: ReactNode
  className?: string
}

/** A bold caption above one toolbar control. */
export function ToolbarField({ label, htmlFor, children, className }: ToolbarFieldProps) {
  return (
    <div className={className}>
      {htmlFor ? (
        <label htmlFor={htmlFor} className={CAPTION}>
          {label}
        </label>
      ) : (
        <span className={CAPTION}>{label}</span>
      )}
      {children}
    </div>
  )
}

export interface ToolbarSearchProps {
  id: string
  value: string
  onChange: (value: string) => void
  placeholder: string
  /** Defaults to the placeholder. */
  ariaLabel?: string
  className?: string
}

/** The Find box: a search input with a leading icon. */
export function ToolbarSearch({ id, value, onChange, placeholder, ariaLabel, className }: ToolbarSearchProps) {
  return (
    <span className={cn('relative block w-[240px] max-w-full', className)}>
      <Search
        size={14}
        aria-hidden="true"
        className="absolute left-2.5 top-1/2 -translate-y-1/2 text-ink-mute"
      />
      <input
        id={id}
        type="search"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        aria-label={ariaLabel ?? placeholder}
        className="h-[38px] w-full rounded-[10px] border border-line bg-paper-card pl-8 pr-3 text-body text-ink outline-none transition-colors placeholder:text-ink-mute/70 hover:border-paper-line focus:border-accent focus:ring-2 focus:ring-accent/25"
      />
    </span>
  )
}

export interface ToolbarCheckboxProps {
  id: string
  checked: boolean
  onChange: (checked: boolean) => void
  children: ReactNode
}

/** A checkbox that sits on the toolbar's baseline. */
export function ToolbarCheckbox({ id, checked, onChange, children }: ToolbarCheckboxProps) {
  return (
    <label htmlFor={id} className="flex items-center gap-1.5 pb-2.5 text-meta text-ink-mute">
      <input id={id} type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} />
      {children}
    </label>
  )
}
