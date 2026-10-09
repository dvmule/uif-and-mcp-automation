import { Link } from 'react-router-dom'
import type { LineageUpstream, LineageDownstreamExport, LineageDownstreamCheck } from '@/lib/types'

export interface LineageCardProps {
  upstream: LineageUpstream | null
  downstream_exports: LineageDownstreamExport[]
  downstream_checks: LineageDownstreamCheck[]
}

const HEADING = 'mb-2 text-label font-semibold uppercase tracking-wider text-ink-mute'
const LINK = 'text-body font-semibold text-accent hover:underline'
const NONE = 'text-body italic text-ink-mute'

// Upstream sync and downstream consumers of one dataset; the page supplies the card around it.
export function LineageCard({ upstream, downstream_exports, downstream_checks }: LineageCardProps) {
  const hasDownstream = downstream_exports.length > 0 || downstream_checks.length > 0

  return (
    <div className="flex flex-col gap-4">
      <section>
        <h3 className={HEADING}>Upstream Sync</h3>
        {upstream ? (
          <Link to={`/m/data-sync/syncs/${upstream.sync_id}`} className={LINK}>
            {upstream.sync_name}
          </Link>
        ) : (
          <p className={NONE}>No upstream sync recorded</p>
        )}
      </section>

      <section>
        <h3 className={HEADING}>Downstream Consumers</h3>
        {!hasDownstream ? (
          <p className={NONE}>No downstream consumers yet</p>
        ) : (
          <div className="flex flex-col gap-3">
            {downstream_exports.length > 0 && (
              <div>
                <p className="mb-1 text-meta font-semibold text-ink-soft">Exports</p>
                <ul className="flex flex-col gap-1">
                  {downstream_exports.map(exp => (
                    <li key={exp.export_id}>
                      <Link to={`/m/partner-export/exports/${exp.export_id}`} className={LINK}>
                        {exp.name}
                      </Link>
                    </li>
                  ))}
                </ul>
              </div>
            )}
            {downstream_checks.length > 0 && (
              <div>
                <p className="mb-1 text-meta font-semibold text-ink-soft">Quality Checks</p>
                <ul className="flex flex-col gap-1">
                  {downstream_checks.map(chk => (
                    <li key={chk.check_id}>
                      <Link to={`/m/data-quality/checks/${chk.check_id}`} className={LINK}>
                        {chk.name}
                      </Link>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        )}
      </section>
    </div>
  )
}
