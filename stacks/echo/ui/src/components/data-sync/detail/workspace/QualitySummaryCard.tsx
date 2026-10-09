import { TONE_CLASS } from '@/lib/statusVocabulary'
import type { QualityRule } from '@/lib/qualityRules'
import { cn } from '@/lib/utils'
import { DetailCard } from '../DetailCard'

interface Props {
  rules: QualityRule[]
}

/** The sync's configured quality rules, each scored by the last run. */
export function QualitySummaryCard({ rules }: Props) {
  return (
    <DetailCard
      title="Quality"
      aside={rules.length > 0 ? 'rules on this sync · scored by the last run that evaluated them' : undefined}
      testid="quality-card"
    >
      {rules.length === 0 ? (
        <p className="text-body text-ink-mute">No quality rules configured on this sync.</p>
      ) : (
        <ul>
          {rules.map((r) => (
            <li
              key={`${r.name}:${r.context}`}
              className="grid grid-cols-[1fr_auto] items-center gap-4 border-t border-line/60 py-2 text-body first:border-t-0 first:pt-0"
            >
              <span className="min-w-0">
                <b className="font-semibold text-ink">{r.name}</b>{' '}
                <span className="text-meta text-ink-mute">· {r.context}</span>
              </span>
              {r.total === 0 ? (
                <span className="text-meta text-ink-mute">not evaluated yet</span>
              ) : (
                <span
                  className={cn(
                    'tnum rounded-full px-2 py-0.5 text-label font-bold',
                    TONE_CLASS[r.passed === r.total ? 'ok' : 'fail'],
                  )}
                >
                  {r.passed} / {r.total}
                </span>
              )}
            </li>
          ))}
        </ul>
      )}
    </DetailCard>
  )
}
