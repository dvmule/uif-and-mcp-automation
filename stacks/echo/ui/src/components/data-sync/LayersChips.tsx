import { cn } from '@/lib/utils'
import type { PipelineConfig } from '@/lib/types'

interface Props {
  pipeline: Pick<PipelineConfig, 'silver'>
  className?: string
}

type LayerLabel = 'Bronze' | 'Silver' | 'Gold'

/** One medallion-layer pill. Solid means the layer exists for this row. */
export function LayerChip({ label, enabled }: { label: LayerLabel; enabled: boolean }) {
  return (
    <span
      className={cn(
        'inline-flex items-center rounded-full h-[20px] px-2 text-[10px] font-semibold uppercase tracking-wide border',
        enabled
          ? 'border-solid border-paper-line/60 bg-accent-soft text-ink'
          : 'border-dashed border-paper-line/70 text-ink-mute bg-transparent',
      )}
    >
      {label}
    </span>
  )
}

/** Bronze/Silver for a sync row. Gold is deliberately absent: a gold table is
 *  built from many silver tables, so it is its own row on this page rather
 *  than a layer of any one sync (UIFPE-511). */
export function LayersChips({ pipeline, className }: Props) {
  return (
    <div className={cn('flex items-center gap-1', className)}>
      <LayerChip label="Bronze" enabled />
      <LayerChip label="Silver" enabled={pipeline.silver?.enabled === true} />
    </div>
  )
}
