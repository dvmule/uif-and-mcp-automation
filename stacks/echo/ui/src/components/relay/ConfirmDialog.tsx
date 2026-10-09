import { useRef, type ReactNode } from 'react'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { BusyButton } from './BusyButton'

export interface ConfirmDialogProps {
  open: boolean
  title: string
  description?: ReactNode
  confirmLabel?: string
  /** Styles the confirm button as destructive. */
  danger?: boolean
  /** The confirm call is running: both buttons lock and the dialog cannot be dismissed. */
  busy?: boolean
  /** Holds the confirm button until a guard in `children` is satisfied (a typed name, say). */
  confirmDisabled?: boolean
  onConfirm: () => void
  onCancel: () => void
  children?: ReactNode
}

export function ConfirmDialog({
  open,
  title,
  description,
  confirmLabel = 'Confirm',
  danger = false,
  busy = false,
  confirmDisabled = false,
  onConfirm,
  onCancel,
  children,
}: ConfirmDialogProps) {
  const confirmRef = useRef<HTMLButtonElement>(null)
  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next && !busy) onCancel()
      }}
    >
      {/* with no description, opt out of Radix's missing-description warning */}
      <DialogContent
        showCloseButton={false}
        overlayClassName="flex items-start justify-center bg-pearson-purple/45 py-[4vh]"
        className="top-[4vh] translate-y-0 rounded-card bg-paper-card px-[26px] py-[22px] text-ink shadow-[0_24px_64px_rgb(13_0_77_/_0.35)] sm:max-w-[520px]"
        {...(description ? {} : { 'aria-describedby': undefined })}
        onOpenAutoFocus={(e) => {
          // Focus the confirm button; while it is held, Radix focuses the first field (the guard).
          const confirm = confirmRef.current
          if (confirm && !confirm.disabled) {
            e.preventDefault()
            confirm.focus()
          }
        }}
      >
        <DialogHeader>
          <DialogTitle className="text-[18px] font-extrabold">{title}</DialogTitle>
          {description && <DialogDescription>{description}</DialogDescription>}
        </DialogHeader>
        {children}
        <DialogFooter>
          <Button type="button" variant="outline" disabled={busy} onClick={onCancel}>
            Cancel
          </Button>
          <BusyButton
            ref={confirmRef}
            type="button"
            variant={danger ? 'destructive' : 'default'}
            busy={busy}
            disabled={confirmDisabled}
            onClick={onConfirm}
          >
            {confirmLabel}
          </BusyButton>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
