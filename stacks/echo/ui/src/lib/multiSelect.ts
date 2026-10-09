// Pure rules behind MultiSelect: closed-box wording, search, select all (ported from Beacon).
import type { ReactNode } from 'react'
import type { LucideIcon } from 'lucide-react'

export interface MultiSelectItem {
  key: string
  label: string
  /** Quiet text on the right of the option, a count for one. */
  meta?: ReactNode
  icon?: LucideIcon
}

export interface SelectionLabel {
  text: string
  /** Set for a partial pick of several: "2 statuses selected" of N. */
  of?: number
  empty: boolean
}

/** One pick by name, several as a count, all as "All <noun> (n)", none as the placeholder. */
export function selectionLabel(
  items: MultiSelectItem[],
  selected: string[],
  noun: string,
  placeholder = 'Choose…',
): SelectionLabel {
  const picked = items.filter((it) => selected.includes(it.key))
  if (picked.length === 0) return { text: placeholder, empty: true }
  if (picked.length === 1) return { text: picked[0].label, empty: false }
  if (picked.length === items.length) return { text: `All ${noun} (${items.length})`, empty: false }
  return { text: `${picked.length} ${noun} selected`, of: items.length, empty: false }
}

/** Options whose label contains the search text, ignoring case and edge spaces. */
export function filterItems(items: MultiSelectItem[], query: string | null | undefined): MultiSelectItem[] {
  const q = (query ?? '').trim().toLowerCase()
  if (!q) return items
  return items.filter((it) => it.label.toLowerCase().includes(q))
}

/** True when every option is picked (and there is at least one option). */
export function allSelected(items: MultiSelectItem[], selected: string[]): boolean {
  return items.length > 0 && items.every((it) => selected.includes(it.key))
}

/** Select all when anything is missing, clear all when everything is picked; keys outside `items` stay. */
export function toggleAll(items: MultiSelectItem[], selected: string[]): string[] {
  const keys = items.map((it) => it.key)
  if (allSelected(items, selected)) return selected.filter((k) => !keys.includes(k))
  return [...new Set([...selected, ...keys])]
}

/** One option on or off. */
export function toggleOne(selected: string[], key: string): string[] {
  return selected.includes(key) ? selected.filter((k) => k !== key) : [...selected, key]
}
