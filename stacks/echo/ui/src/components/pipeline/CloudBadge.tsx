import { cn, CHIP_SURFACE } from '@/lib/utils'
import type { DestinationCloud } from '@/lib/types'

interface Props {
  cloud: DestinationCloud
  className?: string
}

/** Lavender pill identifying destination cloud — turq dot for AWS, accent for GCP. */
export function CloudBadge({ cloud, className }: Props) {
  const isGcp = cloud === 'gcp'
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1.5 rounded-full border h-[20px] px-2 text-[10px] font-bold uppercase tracking-wide',
        CHIP_SURFACE,
        className,
      )}
    >
      <span className={cn('h-1.5 w-1.5 shrink-0 rounded-full', isGcp ? 'bg-accent' : 'bg-turq')} />
      {isGcp ? 'GCP' : 'AWS'}
    </span>
  )
}
