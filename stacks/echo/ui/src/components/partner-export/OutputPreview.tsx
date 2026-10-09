import { useMemo } from 'react'
import type { ExportFormatConfig, MappingField } from '@/lib/types'

export interface OutputPreviewProps {
  format: ExportFormatConfig
  mapping: MappingField[]
  sampleRows: Array<Record<string, unknown>>
}

function csvEscape(value: unknown, delimiter: string, quote: string): string {
  if (value == null) return ''
  const s = String(value)
  return (s.includes(delimiter) || s.includes('\n') || s.includes(quote))
    ? quote + s.replaceAll(quote, quote + quote) + quote
    : s
}

function renderCsv(mapping: MappingField[], rows: Array<Record<string, unknown>>, cfg: NonNullable<ExportFormatConfig['csv']>): string {
  const { delimiter, quote, header_row } = cfg
  const row = (vals: unknown[]) => vals.map(v => csvEscape(v, delimiter, quote)).join(delimiter)
  const lines: string[] = []
  if (header_row) lines.push(row(mapping.map(m => m.output_field)))
  for (const r of rows) lines.push(row(mapping.map(m => r[m.source_col])))
  return lines.join('\n')
}

function renderJson(mapping: MappingField[], rows: Array<Record<string, unknown>>, lines: boolean): string {
  const projected = rows.map(r => Object.fromEntries(mapping.map(m => [m.output_field, r[m.source_col]])))
  return lines ? projected.map(o => JSON.stringify(o)).join('\n') : JSON.stringify(projected, null, 2)
}

export function OutputPreview({ format, mapping, sampleRows }: OutputPreviewProps) {
  const body = useMemo(() => {
    if (mapping.length === 0) return null
    if (format.kind === 'csv') return renderCsv(mapping, sampleRows, format.csv ?? { delimiter: ',', quote: '"', header_row: true })
    if (format.kind === 'json') return renderJson(mapping, sampleRows, format.json?.lines ?? true)
    return null
  }, [format, mapping, sampleRows])

  if (mapping.length === 0) {
    return <div className="px-4 py-3 border rounded-md bg-slate-50 text-sm text-slate-500">Add at least one mapping row to preview output.</div>
  }
  if (format.kind === 'parquet') {
    return (
      <div className="border rounded-md bg-white">
        <div className="px-3 py-2 text-xs text-slate-500 border-b">Parquet schema</div>
        <ul className="px-4 py-3 text-xs font-mono space-y-1">
          {mapping.map(m => <li key={m.output_field}>{m.output_field}: {m.type}</li>)}
        </ul>
      </div>
    )
  }
  if (format.kind === 'fixed_width' || format.kind === 'xml') {
    return <div className="px-4 py-3 border rounded-md bg-slate-50 text-sm text-slate-500">Preview for <code>{format.kind}</code> ships in v1.1.</div>
  }
  return (
    <div className="border rounded-md bg-slate-900 text-slate-100">
      <div className="px-3 py-1.5 text-xs text-slate-400 border-b border-slate-700">
        {format.kind.toUpperCase()} preview ({sampleRows.length} row{sampleRows.length === 1 ? '' : 's'})
      </div>
      <pre data-testid="output-preview-pre" className="px-3 py-3 text-xs overflow-auto max-h-64">{body ?? ''}</pre>
    </div>
  )
}
