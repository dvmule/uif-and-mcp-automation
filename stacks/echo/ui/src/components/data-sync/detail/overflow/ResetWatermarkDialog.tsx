import { ConfirmDialog } from '@/components/relay'

interface Props {
  open: boolean
  currentValue: string
  onCancel: () => void
  onConfirm: () => void
}

export function ResetWatermarkDialog({ open, currentValue, onCancel, onConfirm }: Props) {
  return (
    <ConfirmDialog
      open={open}
      title="Reset watermark to NULL?"
      description="Next scheduled run will re-extract from the beginning. Existing Bronze and Silver data are not affected — they will be overwritten or merged as the watermark advances."
      confirmLabel="Reset watermark"
      danger
      onConfirm={onConfirm}
      onCancel={onCancel}
    >
      <div className="rounded-brand bg-accent-soft p-3 text-meta">
        <div>
          <span className="text-label font-semibold uppercase tracking-wider text-accent">Current</span>
          &nbsp; <code className="font-mono">{currentValue}</code>
        </div>
        <div className="mt-1">
          <span className="text-label font-semibold uppercase tracking-wider text-accent">After reset</span>
          &nbsp; <code className="font-mono">NULL</code>
        </div>
      </div>
    </ConfirmDialog>
  )
}
