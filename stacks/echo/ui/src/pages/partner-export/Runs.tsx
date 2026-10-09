import { Link } from 'react-router-dom'
import { useAllExportRuns } from '@/hooks/useExportRuns'

const STATUS_CLASS: Record<string, string> = {
  FAILED: 'text-red-600 font-medium',
  SUCCEEDED: 'text-emerald-700',
}

export function Runs() {
  const q = useAllExportRuns()
  const rows = q.data?.items ?? []
  return (
    <div className="px-6 py-6">
      <h1 className="text-xl font-semibold text-slate-900 mb-4">Runs</h1>
      {q.isLoading ? (
        <div className="text-sm text-slate-500">Loading…</div>
      ) : rows.length === 0 ? (
        <div className="text-sm text-slate-500">No runs in the last 24h.</div>
      ) : (
        <table className="w-full text-sm bg-white border rounded-md">
          <thead className="text-slate-500 text-left">
            <tr>{['Run', 'Export', 'Status', 'Started', 'Rows', 'Error'].map(h => <th key={h} className="px-3 py-2">{h}</th>)}</tr>
          </thead>
          <tbody>
            {rows.map(r => (
              <tr key={r.run_id} className="border-t">
                <td className="px-3 py-2 font-mono text-xs">{r.run_id}</td>
                <td className="px-3 py-2">
                  <Link to={`/m/partner-export/exports/${r.export_id}`} className="text-indigo-600 hover:underline">{r.export_id}</Link>
                </td>
                <td className={`px-3 py-2 ${STATUS_CLASS[r.status] ?? 'text-slate-700'}`}>{r.status}</td>
                <td className="px-3 py-2 text-xs text-slate-600">{r.started_at}</td>
                <td className="px-3 py-2 text-right">{r.rows_written ?? '—'}</td>
                <td className="px-3 py-2 text-xs text-red-600 truncate max-w-[24ch]">{r.error_message ?? ''}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}
