import { cn } from '@/lib/utils'
import { TONE_CLASS } from '@/lib/statusVocabulary'
import { kindLabel } from '@/components/data-quality/wizard/kindCopy'
import type { DqCheckKind } from '@/lib/types'

const LIVE_KINDS: ReadonlyArray<DqCheckKind> = ['reconcile', 'null-rate']

// A check kind as a small pill: live tone for the kinds v1 runs, mute for the planned ones.
export function KindBadge({ kind }: { kind: DqCheckKind }) {
  const live = LIVE_KINDS.includes(kind)
  return (
    <span
      data-status={live ? 'live' : 'coming-soon'}
      className={cn(
        'inline-flex items-center whitespace-nowrap rounded-full px-2 py-0 text-label font-bold',
        TONE_CLASS[live ? 'live' : 'mute'],
      )}
      title={live ? kind : `${kind} · coming soon`}
    >
      {kindLabel(kind)}
    </span>
  )
}
