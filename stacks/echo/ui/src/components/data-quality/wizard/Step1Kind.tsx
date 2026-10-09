// Step 1 of New check: every kind the API reports; kinds not live yet are shown but cannot be picked.
import { ChoiceCard } from '@/components/wizard/ChoiceCard'
import type { DqCheckKind } from '@/lib/types'
import { useDqKinds } from '@/hooks/useDqChecks'
import { KIND_COPY } from './kindCopy'

export function Step1Kind({ value, onChange }: { value: DqCheckKind | null; onChange: (k: DqCheckKind) => void }) {
  const { data: kinds, isLoading } = useDqKinds()
  if (isLoading) return <p className="text-body text-ink-mute">Loading kinds…</p>
  return (
    <div className="grid gap-3 md:grid-cols-3">
      {(kinds ?? []).map(k => {
        const live = k.status === 'live'
        const copy = KIND_COPY[k.kind as DqCheckKind]
        return (
          <ChoiceCard
            key={k.kind}
            selected={value === k.kind}
            onSelect={() => onChange(k.kind as DqCheckKind)}
            disabled={!live}
            chip={live ? undefined : 'Coming soon'}
            title={copy?.label ?? k.kind}
          >
            <span className="mt-0.5 block text-meta text-ink-mute">
              {copy?.description ?? `Parameters: ${k.parameters.join(', ') || '—'}`}
            </span>
          </ChoiceCard>
        )
      })}
    </div>
  )
}
