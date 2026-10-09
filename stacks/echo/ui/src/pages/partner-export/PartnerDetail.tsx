import { useParams } from 'react-router-dom'
import { usePartner } from '@/hooks/usePartners'
import { usePartnerExports } from '@/hooks/usePartnerExports'
import { ExportRow } from '@/components/partner-export/ExportRow'

export function PartnerDetail() {
  const { partnerId } = useParams<{ partnerId: string }>()
  const partnerQuery = usePartner(partnerId)
  const exportsQuery = usePartnerExports(partnerId ? { partner: partnerId } : {})

  if (partnerQuery.isLoading) return <div className="p-6 text-sm text-slate-500">Loading…</div>
  if (!partnerQuery.data) return <div className="p-6 text-sm text-red-600">Partner not found.</div>

  const p = partnerQuery.data
  return (
    <div className="px-6 py-6">
      <header className="mb-4">
        <h1 className="text-xl font-semibold text-slate-900">{p.name}</h1>
        <div className="text-xs text-slate-500 mt-1">
          {p.contact_email ?? '—'} · default delivery: {p.default_method ?? '—'}
        </div>
        {p.sla_notes && <p className="text-sm text-slate-600 mt-2">{p.sla_notes}</p>}
      </header>

      <h2 className="text-sm font-semibold text-slate-700 mb-2">Exports</h2>
      <div className="border rounded-md bg-white">
        {(exportsQuery.data ?? []).map(e => (
          <ExportRow key={e.export_id} exportItem={e} />
        ))}
        {(exportsQuery.data ?? []).length === 0 && (
          <div className="px-4 py-3 text-sm text-slate-500">No exports for this partner.</div>
        )}
      </div>
    </div>
  )
}
