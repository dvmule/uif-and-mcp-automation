// The card a wizard step sits in (a DetailCard with a footer bar) and that footer: Back · Step n of N · next action.
import type { ReactNode } from 'react'
import { ChevronLeft } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { DetailCard } from '@/components/data-sync/detail/DetailCard'

interface WizardCardProps {
  title: ReactNode
  aside?: ReactNode
  children: ReactNode
  /** Rendered below the body, above the footer bar (e.g. a failure Note). */
  notice?: ReactNode
  footer: ReactNode
}

export function WizardCard({ title, aside, children, notice, footer }: WizardCardProps) {
  return (
    <DetailCard title={title} aside={aside} bodyClassName="" testid="wizard-card">
      <div className="p-4">{children}</div>
      {notice && <div className="px-4 pb-3" data-testid="wizard-notice">{notice}</div>}
      {footer}
    </DetailCard>
  )
}

interface WizardFooterProps {
  /** Zero-based index of the current step. */
  index: number
  total: number
  onBack: () => void
  /** The right-hand action: Next, or the final submit button. */
  action: ReactNode
}

export function WizardFooter({ index, total, onBack, action }: WizardFooterProps) {
  return (
    <div className="flex items-center justify-between gap-3 border-t border-line bg-paper-head px-4 py-3">
      <Button type="button" variant="outline" onClick={onBack} disabled={index === 0}>
        <ChevronLeft aria-hidden="true" /> Back
      </Button>
      <span className="text-meta text-ink-mute">
        Step {index + 1} of {total}
      </span>
      {action}
    </div>
  )
}
