import { Link } from 'react-router-dom'
import { usePartners } from '@/hooks/usePartners'

export function Partners() {
  const q = usePartners()
  return (
    <div className="px-6 py-6">
      <h1 className="text-xl font-semibold text-slate-900 mb-4">Partners</h1>
      {q.isLoading ? (
        <div className="text-sm text-slate-500">Loading…</div>
      ) : !q.data?.length ? (
        <div className="text-sm text-slate-500">No partners yet.</div>
      ) : (
        <ul className="divide-y border rounded-md bg-white">
          {q.data.map(p => (
            <li key={p.partner_id}>
              <Link to={`/m/partner-export/partners/${p.partner_id}`} className="flex items-center justify-between px-4 py-3 hover:bg-slate-50">
                <div>
                  <div className="text-sm font-medium text-slate-900">{p.name}</div>
                  <div className="text-xs text-slate-500">{p.contact_email ?? 'no contact'}</div>
                </div>
                <div className="text-xs text-slate-600">
                  {(p.export_count ?? 0)} exports
                  {p.health === 'failing' && <span className="ml-2 text-red-600 font-medium">failing</span>}
                </div>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
