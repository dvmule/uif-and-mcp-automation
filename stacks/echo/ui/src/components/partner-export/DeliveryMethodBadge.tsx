import type { PartnerDeliveryMethod } from '@/lib/types'

const META: Record<PartnerDeliveryMethod, { label: string; icon: string; klass: string }> = {
  sftp:  { label: 'SFTP',  icon: '⇋', klass: 'badge-sftp text-emerald-700 bg-emerald-50' },
  https: { label: 'HTTPS', icon: '↥', klass: 'badge-https text-sky-700 bg-sky-50' },
  s3:    { label: 'S3',    icon: '◰', klass: 'badge-s3 text-amber-700 bg-amber-50' },
  gcs:   { label: 'GCS',   icon: '◳', klass: 'badge-gcs text-violet-700 bg-violet-50' },
}

export interface DeliveryMethodBadgeProps { method: PartnerDeliveryMethod }

export function DeliveryMethodBadge({ method }: DeliveryMethodBadgeProps) {
  const m = META[method]
  if (!m) {
    return <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium text-slate-700 bg-slate-100">{String(method).toUpperCase()}</span>
  }
  return (
    <span data-testid={`delivery-badge-${method}`} className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs font-medium ${m.klass}`}>
      <span aria-hidden>{m.icon}</span>{m.label}
    </span>
  )
}
