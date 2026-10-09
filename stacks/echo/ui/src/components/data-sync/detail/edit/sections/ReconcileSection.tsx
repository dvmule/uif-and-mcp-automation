import { type ReactNode } from 'react'
import { useOutletContext } from 'react-router-dom'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import type { PipelineConfig, PipelineReconcile } from '@/lib/types'

interface OutletCtx {
  working: PipelineConfig
  update: (next: PipelineConfig) => void
}

function EditRow({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid grid-cols-[180px_1fr] gap-x-4 items-center py-1.5">
      <Label className="text-xs text-ink-mute uppercase tracking-wide font-medium">{label}</Label>
      <div>{children}</div>
    </div>
  )
}

export function ReconcileSection() {
  const { working, update } = useOutletContext<OutletCtx>()
  const r: PipelineReconcile = working.reconcile ?? { enabled: false }

  const setR = (patch: Partial<PipelineReconcile>) => {
    const next = structuredClone(working)
    next.reconcile = { ...r, ...patch }
    update(next)
  }

  return (
    <div className="space-y-4">
      <EditRow label="Enabled">
        <Switch checked={r.enabled} onCheckedChange={v => setR({ enabled: v })} />
      </EditRow>

      <EditRow label="Dry Run">
        <Switch
          checked={r.dry_run ?? false}
          onCheckedChange={v => setR({ dry_run: v })}
        />
      </EditRow>

      <EditRow label="PK Partition Col">
        <Input
          value={r.pk_partition_col ?? ''}
          onChange={e => setR({ pk_partition_col: e.target.value || undefined })}
        />
      </EditRow>

      <EditRow label="PK Lower Bound">
        <Input
          type="number"
          value={r.pk_lower_bound ?? ''}
          onChange={e => setR({
            pk_lower_bound: e.target.value === '' ? undefined : Number(e.target.value),
          })}
        />
      </EditRow>

      <EditRow label="PK Upper Bound">
        <Input
          type="number"
          value={r.pk_upper_bound ?? ''}
          onChange={e => setR({
            pk_upper_bound: e.target.value === '' ? undefined : Number(e.target.value),
          })}
        />
      </EditRow>

      <EditRow label="PK Partitions">
        <Input
          type="number"
          value={r.pk_partitions ?? ''}
          onChange={e => setR({
            pk_partitions: e.target.value === '' ? undefined : Number(e.target.value),
          })}
        />
      </EditRow>

      <EditRow label="Max Deletes / Run">
        <Input
          type="number"
          value={r.max_deletes_per_run ?? 5000}
          onChange={e => setR({
            max_deletes_per_run: e.target.value === '' ? undefined : Number(e.target.value),
          })}
        />
      </EditRow>
    </div>
  )
}
