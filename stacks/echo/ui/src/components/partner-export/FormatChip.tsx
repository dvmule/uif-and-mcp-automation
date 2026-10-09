import type { ExportFormat } from '@/lib/types'

const LIVE: ReadonlySet<ExportFormat> = new Set(['csv', 'json', 'parquet'])

export interface FormatChipProps {
  format: ExportFormat
  selected?: boolean
  onSelect: (fmt: ExportFormat) => void
}

export function FormatChip({ format, selected = false, onSelect }: FormatChipProps) {
  const live = LIVE.has(format)
  const cls = [
    'inline-flex items-center gap-2 px-3 py-1.5 rounded-full text-sm font-medium border transition',
    selected ? 'bg-indigo-600 text-white border-indigo-600' : 'bg-white text-slate-700 border-slate-300 hover:border-indigo-400',
    !live && 'opacity-50 cursor-not-allowed',
  ].filter(Boolean).join(' ')
  return (
    <button type="button" role="button" aria-pressed={selected} disabled={!live} onClick={() => live && onSelect(format)} className={cls}>
      <span>{format}</span>
      {!live && <span className="text-[10px] uppercase tracking-wide bg-slate-200 text-slate-600 px-1.5 py-0.5 rounded">soon</span>}
    </button>
  )
}
