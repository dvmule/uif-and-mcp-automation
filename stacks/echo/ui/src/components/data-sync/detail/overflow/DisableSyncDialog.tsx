import { ConfirmDialog } from '@/components/relay'

interface Props {
  open: boolean
  onCancel: () => void
  onConfirm: () => void
}

export function DisableSyncDialog({ open, onCancel, onConfirm }: Props) {
  return (
    <ConfirmDialog
      open={open}
      title="Disable this sync?"
      description="Scheduled runs will be paused. Ad-hoc runs from this page are still allowed. You can re-enable any time from the hero."
      confirmLabel="Disable sync"
      danger
      onConfirm={onConfirm}
      onCancel={onCancel}
    />
  )
}
