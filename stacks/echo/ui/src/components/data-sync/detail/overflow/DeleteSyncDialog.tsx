import { ConfirmDialog } from '@/components/relay'

interface Props {
  open: boolean
  onCancel: () => void
  onConfirm: () => void
  confirming?: boolean
}

export function DeleteSyncDialog({ open, onCancel, onConfirm, confirming = false }: Props) {
  return (
    <ConfirmDialog
      open={open}
      title="Remove sync"
      description="Are you sure to remove the data sync? If you need to restore the sync in future, you will need to get in touch with Data Sync Administrator."
      confirmLabel={confirming ? 'Removing…' : 'Remove sync'}
      danger
      busy={confirming}
      onConfirm={onConfirm}
      onCancel={onCancel}
    />
  )
}
