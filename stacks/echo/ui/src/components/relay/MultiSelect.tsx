import { useEffect, useId, useRef, useState } from 'react'
import { Check, ChevronDown, Search } from 'lucide-react'
import {
  allSelected,
  filterItems,
  selectionLabel,
  toggleAll,
  toggleOne,
  type MultiSelectItem,
} from '@/lib/multiSelect'
import { cn } from '@/lib/utils'

export interface MultiSelectProps {
  items: MultiSelectItem[]
  /** Picked keys, in any order. */
  selected: string[]
  onChange: (next: string[]) => void
  /** Plural noun for the wording: "2 statuses selected", "Search statuses". */
  noun?: string
  placeholder?: string
  /** Accessible name of the closed button. */
  label?: string
  className?: string
  emptyText?: string
  /** 'sm' = 38px toolbar control; 'md' = form field. */
  size?: 'md' | 'sm'
}

function Tick({ on }: { on: boolean }) {
  return (
    <span
      aria-hidden="true"
      className={cn(
        'grid h-[18px] w-[18px] shrink-0 place-items-center rounded-[4px] border-[1.5px]',
        on ? 'border-accent bg-accent text-white' : 'border-ink-mute bg-paper-card',
      )}
    >
      {on && <Check size={12} strokeWidth={3} />}
    </span>
  )
}

/** Multi-select dropdown with search and Select all (ported from Beacon's MultiSelect). */
export function MultiSelect({
  items,
  selected,
  onChange,
  noun = 'items',
  placeholder = 'Choose…',
  label,
  className,
  emptyText = 'Nothing to choose from yet.',
  size = 'sm',
}: MultiSelectProps) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const box = useRef<HTMLDivElement>(null)
  const search = useRef<HTMLInputElement>(null)
  const listId = useId()

  // Close on a click outside; the search box takes focus on open.
  useEffect(() => {
    if (!open) return undefined
    const onDown = (e: MouseEvent) => {
      if (box.current && !box.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onDown)
    const t = setTimeout(() => search.current?.focus(), 20)
    return () => {
      document.removeEventListener('mousedown', onDown)
      clearTimeout(t)
    }
  }, [open])

  const summary = items.length
    ? selectionLabel(items, selected, noun, placeholder)
    : { text: `No ${noun} yet`, empty: true, of: undefined }
  const shown = filterItems(items, query)
  const every = allSelected(items, selected)
  const pickedCount = selected.filter((k) => items.some((it) => it.key === k)).length
  const narrowed = pickedCount > 0 && pickedCount < items.length

  return (
    <div
      ref={box}
      className={cn('relative inline-block min-w-[220px] max-w-full', className)}
      onKeyDown={(e) => {
        if (e.key === 'Escape' && open) {
          e.stopPropagation()
          setOpen(false)
        }
      }}
    >
      <button
        type="button"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={listId}
        aria-label={label}
        onClick={() => {
          setQuery('')
          setOpen((o) => !o)
        }}
        className={cn(
          'flex w-full items-center justify-between gap-2.5 border bg-paper-card text-left text-body transition-colors',
          size === 'sm' ? 'h-[38px] rounded-[10px] px-3' : 'rounded-[10px] px-3 py-2',
          open
            ? 'border-accent ring-2 ring-accent/20'
            : narrowed
              ? 'border-accent'
              : 'border-line hover:border-pearson-light-purple',
        )}
      >
        <span className={cn('min-w-0 truncate', summary.empty ? 'text-ink-mute' : 'font-semibold text-ink')}>
          {summary.text}
          {summary.of != null && <span className="ml-1 font-medium text-ink-mute"> of {summary.of}</span>}
        </span>
        <ChevronDown
          size={16}
          aria-hidden="true"
          className={cn('shrink-0 text-ink-mute transition-transform', open && 'rotate-180')}
        />
      </button>

      {open && (
        <div className="absolute left-0 top-full z-30 mt-1.5 min-w-full rounded-[12px] border border-line bg-paper-card p-1.5 shadow-lg">
          <div className="relative mb-1.5 mt-0.5">
            <Search
              size={14}
              aria-hidden="true"
              className="absolute left-2.5 top-1/2 -translate-y-1/2 text-ink-mute"
            />
            <input
              ref={search}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder={`Search ${noun}`}
              aria-label={`Search ${noun}`}
              className="w-full rounded-[8px] border border-line bg-paper-card py-1.5 pl-8 pr-2.5 text-body text-ink outline-none focus:border-accent"
            />
          </div>
          <button
            type="button"
            onClick={() => onChange(toggleAll(items, selected))}
            disabled={!items.length}
            className="mb-1 flex w-full items-center gap-2.5 rounded-t-[8px] border-b border-line px-2.5 py-2 text-left text-body font-semibold text-ink hover:bg-paper-head"
          >
            <Tick on={every} />
            <span>{every ? 'Clear all' : 'Select all'}</span>
            <span className="ml-auto text-meta font-medium text-ink-mute">
              {pickedCount} / {items.length}
            </span>
          </button>
          <div id={listId} role="listbox" aria-multiselectable="true" className="max-h-[264px] overflow-y-auto">
            {!items.length && <div className="px-2.5 py-2 text-meta text-ink-mute">{emptyText}</div>}
            {items.length > 0 && !shown.length && (
              <div className="px-2.5 py-2 text-meta text-ink-mute">No match for “{query.trim()}”.</div>
            )}
            {shown.map((it) => {
              const on = selected.includes(it.key)
              const Icon = it.icon
              return (
                <button
                  key={it.key}
                  type="button"
                  role="option"
                  aria-selected={on}
                  onClick={() => onChange(toggleOne(selected, it.key))}
                  className="flex w-full items-center gap-2.5 whitespace-nowrap rounded-[8px] px-2.5 py-2 text-left text-body text-ink hover:bg-paper-head"
                >
                  <Tick on={on} />
                  {Icon && <Icon size={15} strokeWidth={1.75} aria-hidden="true" className="shrink-0 text-accent" />}
                  <span className="min-w-0 truncate">{it.label}</span>
                  {it.meta != null && <span className="ml-auto pl-4 text-meta text-ink-mute">{it.meta}</span>}
                </button>
              )
            })}
          </div>
        </div>
      )}
    </div>
  )
}
