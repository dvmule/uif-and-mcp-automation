import { XCircle } from 'lucide-react'
import { StatusBadge } from '@/components/relay'
import { DetailCard } from '@/components/data-sync/detail/DetailCard'
import type { StatusView } from '@/lib/statusVocabulary'
import type { GoldSource } from '@/lib/types'

const MISSING: StatusView = { label: 'missing', tone: 'fail', icon: XCircle, raw: 'missing source' }

interface Props {
  sources: GoldSource[] | undefined
  /** silver_table values the latest failure names as missing. */
  missing: string[]
  /** Resolves the `{env}` placeholder in the names shown. */
  env?: string
}

/** The silver tables a gold entity reads. */
export function SourcesCard({ sources, missing, env }: Props) {
  const list = sources ?? []
  return (
    <DetailCard title="Sources" testid="sources-card">
      {list.length === 0 ? (
        <p className="text-meta text-ink-mute">No silver sources on this entity&apos;s config.</p>
      ) : (
        <ul className="flex flex-col gap-1.5">
          {list.map((s) => (
            <li key={s.silver_table} className="flex flex-wrap items-center gap-1.5">
              <span className="break-all font-mono text-meta font-semibold text-ink">
                {env ? s.silver_table.replace('{env}', env) : s.silver_table}
              </span>
              {missing.includes(s.silver_table) && <StatusBadge view={MISSING} size="sm" showIcon={false} />}
            </li>
          ))}
        </ul>
      )}
    </DetailCard>
  )
}
