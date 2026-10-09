import type { MappingField } from '@/lib/types'

const TYPES: ReadonlyArray<MappingField['type']> = ['string', 'int', 'decimal', 'date', 'datetime', 'bool']

export interface MappingTableProps {
  rows: MappingField[]
  onAdd: () => void
  onUpdate: (idx: number, patch: Partial<MappingField>) => void
  onRemove: (idx: number) => void
  /** When true, inputs render as plain text (used on ExportDetail). */
  readOnly?: boolean
}

const CELL = 'w-full px-2 py-1 border rounded font-mono text-xs'

export function MappingTable({ rows, onAdd, onUpdate, onRemove, readOnly = false }: MappingTableProps) {
  return (
    <div className="border rounded-md bg-white">
      <table className="w-full text-sm">
        <thead className="bg-slate-50 text-left text-xs text-slate-600">
          <tr>{['Source column', 'Output field', 'Type', 'Format', ''].map((h, i) => <th key={i} className="px-3 py-2">{h}</th>)}</tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i} className="border-t">
              <td className="px-3 py-1.5"><input aria-label={`source_col_${i}`} value={r.source_col} readOnly={readOnly} onChange={e => onUpdate(i, { source_col: e.target.value })} className={CELL} /></td>
              <td className="px-3 py-1.5"><input aria-label={`output_field_${i}`} value={r.output_field} readOnly={readOnly} onChange={e => onUpdate(i, { output_field: e.target.value })} className={CELL} /></td>
              <td className="px-3 py-1.5">
                <select aria-label={`type_${i}`} value={r.type} disabled={readOnly} onChange={e => onUpdate(i, { type: e.target.value as MappingField['type'] })} className="w-full px-2 py-1 border rounded text-xs">
                  {TYPES.map(t => <option key={t} value={t}>{t}</option>)}
                </select>
              </td>
              <td className="px-3 py-1.5"><input aria-label={`format_${i}`} value={r.format ?? ''} readOnly={readOnly} placeholder="optional" onChange={e => onUpdate(i, { format: e.target.value || null })} className={CELL} /></td>
              <td className="px-2 py-1.5 text-right">
                {!readOnly && <button type="button" onClick={() => onRemove(i)} className="text-xs text-red-600 hover:underline">Remove</button>}
              </td>
            </tr>
          ))}
          {rows.length === 0 && (<tr><td colSpan={5} className="px-3 py-3 text-center text-slate-500 text-xs">No fields yet.</td></tr>)}
        </tbody>
      </table>
      {!readOnly && (
        <div className="px-3 py-2 border-t bg-slate-50">
          <button type="button" onClick={onAdd} className="text-xs text-indigo-600 hover:underline">+ Add row</button>
        </div>
      )}
    </div>
  )
}
