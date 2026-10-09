import { Link } from 'react-router-dom'
import { usePartnerExports } from '@/hooks/usePartnerExports'
import { ExportRow } from './ExportRow'

export interface LinkedExportsListProps {
  /** Canonical silver-or-gold FQN, e.g. "silver.smoke_silver.author_data". */
  sourceFqn: string
}

export function LinkedExportsList({ sourceFqn }: LinkedExportsListProps) {
  const q = usePartnerExports({ source: sourceFqn })
  if (q.isLoading) return <div className="text-sm text-slate-500 py-3">Loading exports…</div>
  const exports = q.data ?? []
  if (exports.length === 0) {
    return (
      <div className="text-sm text-slate-500 py-3 flex items-center justify-between border rounded-md px-4 bg-slate-50">
        <span>No exports yet for this dataset.</span>
        <Link to="/m/partner-export/exports/new" className="text-indigo-700 hover:underline">+ Create export</Link>
      </div>
    )
  }
  return (
    <div className="border rounded-md bg-white">
      {exports.map(e => <ExportRow key={e.export_id} exportItem={e} />)}
    </div>
  )
}
