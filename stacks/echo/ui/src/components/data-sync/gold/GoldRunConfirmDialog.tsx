import { ConfirmDialog } from '@/components/relay'
import { getEnv } from '@/lib/envConfig'
import { goldTableFqn } from '@/lib/syncRow'
import type { GoldPipelineConfig } from '@/lib/types'

interface Props {
  /** The entity about to run; null closes the dialog. */
  gold: GoldPipelineConfig | null
  pending?: boolean
  onCancel: () => void
  onConfirm: () => void
}

/** Run is confirmed because it starts a real Glue job now, and until UIFPE-516
 *  is deployed the server does not stop a second concurrent run on the same
 *  gold table. Shared by the Transform list and the gold detail page. */
export function GoldRunConfirmDialog({ gold, pending = false, onCancel, onConfirm }: Props) {
  const name = gold ? (goldTableFqn(gold, getEnv()) ?? gold.gold_id) : ''
  return (
    <ConfirmDialog
      open={!!gold}
      title="Run gold transform"
      description={
        <>
          Start a gold transform run for <span className="font-semibold">{name}</span> now?
        </>
      }
      confirmLabel={pending ? 'Starting…' : 'Run now'}
      busy={pending}
      onConfirm={onConfirm}
      onCancel={onCancel}
    >
      <p className="text-body text-ink-soft">
        This runs the Glue transform outside its schedule. The server does not stop a second run while one is in
        progress, so Run will stay unavailable for this entity until the page is reloaded.
      </p>
    </ConfirmDialog>
  )
}
