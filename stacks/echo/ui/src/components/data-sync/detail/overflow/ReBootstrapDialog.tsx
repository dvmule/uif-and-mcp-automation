import { useState } from 'react'
import { ConfirmDialog } from '@/components/relay'

interface Props {
  open: boolean
  syncName: string
  onCancel: () => void
  onConfirm: () => void
}

export function ReBootstrapDialog({ open, syncName, onCancel, onConfirm }: Props) {
  const [typed, setTyped] = useState('')
  const cancel = () => {
    setTyped('')
    onCancel()
  }
  return (
    <ConfirmDialog
      open={open}
      title="Re-bootstrap this sync?"
      description="All Silver data will be dropped and the sync will re-extract from source as an initial run. Bronze data is preserved. This may take hours."
      confirmLabel="Re-bootstrap"
      danger
      confirmDisabled={typed !== syncName}
      onConfirm={() => {
        setTyped('')
        onConfirm()
      }}
      onCancel={cancel}
    >
      <label className="block rounded-brand border-l-[3px] border-signal-fail bg-signal-fail/5 p-3 text-meta text-ink">
        Type the sync name to confirm:
        <input
          value={typed}
          onChange={(e) => setTyped(e.target.value)}
          placeholder={syncName}
          autoComplete="off"
          className="mt-1.5 w-full rounded-brand border border-paper-line px-2.5 py-1.5 font-mono text-meta"
        />
      </label>
    </ConfirmDialog>
  )
}
