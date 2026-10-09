import type { AnalyzeColumn } from '@/lib/types'

interface Props {
  columns: AnalyzeColumn[] | undefined
  isLoading?: boolean
}

export function SchemaTable({ columns, isLoading }: Props) {
  if (isLoading) {
    return <p className="text-sm text-ink-mute">Loading schema…</p>
  }
  if (!columns || columns.length === 0) {
    return (
      <p className="text-sm text-ink-mute">
        Schema unavailable (no upstream sync resolved, or this is a Gold dataset —
        schema lookup against Glue catalog will land in a follow-up plan).
      </p>
    )
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm border-collapse">
        <thead>
          <tr className="border-b border-paper-line">
            <th className="text-left font-medium text-ink-mute py-2 px-3">Column</th>
            <th className="text-left font-medium text-ink-mute py-2 px-3">Type</th>
            <th className="text-left font-medium text-ink-mute py-2 px-3">Nullable</th>
          </tr>
        </thead>
        <tbody>
          {columns.map(c => (
            <tr key={c.name} className="border-b border-paper-line/50">
              <td className="py-2 px-3 font-mono text-ink">{c.name}</td>
              <td className="py-2 px-3 text-ink">{c.data_type}</td>
              <td className="py-2 px-3 text-ink-mute">{c.nullable ? 'YES' : 'NO'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
