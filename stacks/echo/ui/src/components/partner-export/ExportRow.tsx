import { Link } from 'react-router-dom'
import type { PartnerExport } from '@/lib/types'
import { DeliveryMethodBadge } from './DeliveryMethodBadge'

export interface ExportRowProps {
  exportItem: PartnerExport
}

function fmtRows(n?: number | null): string {
  if (n == null) return '—'
  return n.toLocaleString()
}

function fmtSchedule(s: PartnerExport['schedule']): string {
  if (s.frequency === 'cron') return s.cron_expression ?? 'cron'
  if (s.frequency === 'daily') return `Daily ${s.run_at ?? '00:00'} UTC`
  if (s.frequency === 'weekly') return `Weekly ${s.run_at ?? '00:00'} UTC`
  return 'Hourly'
}

export function ExportRow({ exportItem: e }: ExportRowProps) {
  return (
    <Link to={`/m/partner-export/exports/${e.export_id}`} className="grid grid-cols-12 items-center gap-3 px-4 py-2 border-b border-slate-100 hover:bg-slate-50">
      <div className="col-span-4">
        <div className="text-sm font-medium text-slate-900">{e.name}</div>
        <div className="text-xs text-slate-500">{e.source.dataset}</div>
      </div>
      <div className="col-span-2 text-xs uppercase tracking-wide text-slate-600">{e.format.kind}</div>
      <div className="col-span-2"><DeliveryMethodBadge method={e.delivery.method} /></div>
      <div className="col-span-2 text-xs text-slate-600">{fmtSchedule(e.schedule)}</div>
      <div className="col-span-2 text-xs text-right">
        <span className="text-slate-700">{fmtRows(e.last_run?.rows_written)}</span>
        <span className="ml-1 text-slate-400">rows</span>
      </div>
    </Link>
  )
}
