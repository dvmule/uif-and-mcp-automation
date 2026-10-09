import type { Partner } from '@/lib/types'

export interface PartnerGroupHeaderProps {
  partner: Partner
  exportCount: number
  aggregateHealth: 'healthy' | 'failing' | 'paused' | 'unknown'
}

const HEALTH_CLASS: Record<PartnerGroupHeaderProps['aggregateHealth'], string> = {
  healthy: 'bg-white border-slate-200',
  failing: 'bg-red-50 border-red-400',
  paused:  'bg-slate-50 border-slate-300',
  unknown: 'bg-white border-slate-200',
}
const HEALTH_LABEL = { healthy: 'Healthy', failing: 'Failing', paused: 'Paused', unknown: 'Unknown' } as const
const PILL = { failing: 'bg-red-600', healthy: 'bg-emerald-600', paused: 'bg-slate-400', unknown: 'bg-slate-400' } as const

export function PartnerGroupHeader({ partner, exportCount, aggregateHealth }: PartnerGroupHeaderProps) {
  return (
    <div data-testid={`partner-group-header-${partner.partner_id}`} className={`flex items-center justify-between gap-4 px-4 py-3 border rounded-t-md ${HEALTH_CLASS[aggregateHealth]}`}>
      <div className="flex items-baseline gap-3">
        <h3 className="text-base font-semibold text-slate-900">{partner.name}</h3>
        <span className="text-xs text-slate-500">{exportCount} exports</span>
        {partner.contact_email && (
          <a href={`mailto:${partner.contact_email}`} className="text-xs text-indigo-600 hover:underline">{partner.contact_email}</a>
        )}
      </div>
      <span className={`text-xs font-medium px-2 py-0.5 rounded text-white ${PILL[aggregateHealth]}`}>
        {HEALTH_LABEL[aggregateHealth]}
      </span>
    </div>
  )
}
