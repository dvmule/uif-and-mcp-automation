import { Link } from 'react-router-dom'
import { useDownstreamSyncs } from '@/hooks/useDownstreamSyncs'
import { DetailCard } from '../DetailCard'

interface DqLike { status: 'PASSED' | 'FAILING' | 'UNKNOWN' }
interface Props {
  silverFqn: string
  dqChecks: DqLike[]
  exportsCount: number
  /** gold_ids whose sources read this silver table. */
  goldReaders?: string[]
}

export function DependenciesCard({ silverFqn, dqChecks, exportsCount, goldReaders = [] }: Props) {
  const passing = dqChecks.filter(c => c.status === 'PASSED').length
  const failing = dqChecks.filter(c => c.status === 'FAILING').length
  const { data: downstream = [] } = useDownstreamSyncs(silverFqn)
  const n = goldReaders.length
  return (
    <DetailCard title="Dependencies">
      {dqChecks.length === 0 && (
        <div className="mb-2 rounded-md bg-paper-head p-3 text-center text-meta text-ink-mute">
          No quality checks linked yet.<br />
          <Link to="/m/data-quality/checks/new" className="mt-1.5 inline-block font-semibold text-accent hover:underline">+ Add a quality check</Link>
        </div>
      )}
      {dqChecks.length > 0 && (
        <Row label={`${dqChecks.length} quality checks`} sub={`${passing} passing · ${failing} failing`} />
      )}
      <Row label={`${exportsCount} partner exports`} sub={exportsCount === 0 ? 'No exports' : 'linked'} />
      <Row
        label={`${n} gold ${n === 1 ? 'table reads' : 'tables read'} this silver table`}
        sub={goldReaders.join(', ')}
      />
      <Row label={downstream.length === 0 ? 'No downstream syncs' : `${downstream.length} downstream syncs`}
           sub={downstream.length === 0 ? 'This is a terminal silver table' : 'consumers'} />
    </DetailCard>
  )
}

function Row({ label, sub }: { label: string; sub: string }) {
  return (
    <div className="grid grid-cols-[1fr_auto] items-center gap-3 border-t border-line/60 py-2 text-body first:border-t-0 first:pt-0">
      <span className="font-semibold text-ink">{label}</span>
      <span className="text-right text-label text-ink-mute">{sub}</span>
    </div>
  )
}
