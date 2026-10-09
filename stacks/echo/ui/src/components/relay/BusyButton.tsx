import type { ComponentProps } from 'react'
import { Loader2 } from 'lucide-react'
import { Button } from '@/components/ui/button'

export type BusyButtonProps = ComponentProps<typeof Button> & { busy?: boolean }

/** Button that locks itself and shows a spinner while its call runs (no double submits). */
export function BusyButton({ busy = false, disabled, children, ...props }: BusyButtonProps) {
  return (
    <Button {...props} disabled={busy || disabled} aria-busy={busy || undefined}>
      {busy && <Loader2 className="size-3.5 animate-spin" aria-hidden="true" />}
      {children}
    </Button>
  )
}
