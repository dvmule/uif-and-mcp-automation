import { StatusBadge } from '@/components/relay'
import { dqRunStatusView } from '@/lib/statusVocabulary'
import type { DqRunStatus } from '@/lib/types'

// A check run's status in the shared vocabulary; a dash when it has never run.
export function CheckStatusBadge({ status }: { status: DqRunStatus | null | undefined }) {
  if (!status) return <span className="text-ink-mute">—</span>
  return (
    <span data-status={status} className="inline-flex">
      <StatusBadge view={dqRunStatusView(status)} size="sm" />
    </span>
  )
}
