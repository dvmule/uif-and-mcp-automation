import { Button } from '@/components/ui/button'

interface Props {
  pendingCount: number
  lastSavedRel: string | null
  blocked: boolean
  onDiscard: () => void
  onSaveDraft: () => void
  onSaveAndClose: () => void
}

/** The edit card's footer bar: pending count, then Discard / Save draft / Save changes. */
export function SaveFooter({ pendingCount, lastSavedRel, blocked, onDiscard, onSaveDraft, onSaveAndClose }: Props) {
  return (
    <>
      <span className="text-meta font-semibold text-ink-mute">
        {pendingCount} changes pending{lastSavedRel ? ` · last saved ${lastSavedRel}` : ''}
      </span>
      <div className="flex gap-2">
        <Button variant="ghost" onClick={onDiscard}>Discard</Button>
        <Button variant="outline" onClick={onSaveDraft} disabled={blocked}>Save draft</Button>
        <Button onClick={onSaveAndClose} disabled={blocked}>Save changes</Button>
      </div>
    </>
  )
}
