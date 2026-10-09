import { useState } from 'react'
import { ChevronDown, Database, Zap } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Note } from '@/components/relay'
import { TONE_CLASS } from '@/lib/statusVocabulary'
import { cn } from '@/lib/utils'
import type { AnalyzeResult } from '@/lib/types'

interface AnalysisRecommendationsProps {
  result: AnalyzeResult | null | undefined
}

export function AnalysisRecommendations({ result }: AnalysisRecommendationsProps) {
  const [predicatesExpanded, setPredicatesExpanded] = useState(false)

  if (!result) return null

  const {
    partition_strategy,
    partition_date_col,
    partition_id_col,
    partition_hash_col,
    date_min,
    date_max,
    id_min,
    id_max,
    jdbc_predicates,
    jdbc_predicates_count,
    worker_type,
    num_workers,
    total_gb,
    warning,
  } = result

  if (!partition_strategy) return null

  // The heading ("Recommended from table analysis") belongs to the step that renders this block.
  return (
    <div className="space-y-4 rounded-brand border border-line bg-paper-head p-4">
      {warning && <Note tone="warn">{warning}</Note>}

      <div className="flex items-center gap-3">
        <div className={cn('flex items-center gap-2 rounded-full px-3 py-1 text-meta font-semibold', TONE_CLASS.live)}>
          <Database size={14} aria-hidden="true" />
          <span className="capitalize">{partition_strategy.replace(/_/g, ' ')}</span>
        </div>
      </div>

      {/* Partition Bounds */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        {partition_date_col && (
          <div className="p-3 rounded-brand border border-line bg-paper-card text-sm">
            <div className="font-mono text-xs text-ink-soft mb-1">Date Column</div>
            <div className="font-medium text-ink">{partition_date_col}</div>
            {date_min && date_max && (
              <div className="text-xs text-ink-mute mt-1">
                {date_min} to {date_max}
              </div>
            )}
          </div>
        )}

        {partition_id_col && (
          <div className="p-3 rounded-brand border border-line bg-paper-card text-sm">
            <div className="font-mono text-xs text-ink-soft mb-1">ID Column</div>
            <div className="font-medium text-ink">{partition_id_col}</div>
            {id_min !== null && id_min !== undefined && id_max !== null && id_max !== undefined && (
              <div className="text-xs text-ink-mute mt-1">
                {id_min.toLocaleString()} to {id_max.toLocaleString()}
              </div>
            )}
          </div>
        )}

        {partition_hash_col && (
          <div className="p-3 rounded-brand border border-line bg-paper-card text-sm">
            <div className="font-mono text-xs text-ink-soft mb-1">Hash Column</div>
            <div className="font-medium text-ink">{partition_hash_col}</div>
          </div>
        )}
      </div>

      {/* Predicates Count */}
      {jdbc_predicates_count !== null && jdbc_predicates_count !== undefined && jdbc_predicates_count > 0 && (
        <div>
          <button
            onClick={() => setPredicatesExpanded(!predicatesExpanded)}
            className="w-full flex items-center justify-between p-3 rounded-brand border border-line bg-paper-card hover:border-accent transition-colors text-sm"
          >
            <span className="font-medium flex items-center gap-2">
              <Zap size={14} className="text-accent" />
              {jdbc_predicates_count} Partition Predicates
            </span>
            <ChevronDown size={16} className={cn('transition-transform', predicatesExpanded && 'rotate-180')} />
          </button>

          {predicatesExpanded && jdbc_predicates && (
            <div className="mt-2 p-3 rounded-brand border border-line bg-paper-card max-h-64 overflow-y-auto">
              <div className="space-y-1">
                {jdbc_predicates.slice(0, 10).map((pred, i) => (
                  <div key={i} className="font-mono text-xs text-ink-soft p-2 rounded-brand border border-line bg-paper-head">
                    <div className="break-all">{pred}</div>
                  </div>
                ))}
                {jdbc_predicates.length > 10 && (
                  <div className="text-xs text-ink-mute text-center py-2">
                    ... and {jdbc_predicates.length - 10} more predicates
                  </div>
                )}
              </div>
            </div>
          )}
        </div>
      )}

      {/* Sizing Recommendations */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-3 pt-2 border-t border-line">
        {total_gb !== null && total_gb !== undefined && (
          <div className="p-3 rounded-brand border border-line bg-paper-card text-sm">
            <div className="text-xs text-ink-soft font-mono mb-1">Estimated Size</div>
            <div className="font-semibold text-lg text-accent">{total_gb.toFixed(2)} GB</div>
          </div>
        )}

        {worker_type && (
          <div className="p-3 rounded-brand border border-line bg-paper-card text-sm">
            <div className="text-xs text-ink-soft font-mono mb-1">Worker Type</div>
            <Badge variant="outline" className="text-sm font-mono">
              {worker_type}
            </Badge>
          </div>
        )}

        {num_workers !== null && (
          <div className="p-3 rounded-brand border border-line bg-paper-card text-sm">
            <div className="text-xs text-ink-soft font-mono mb-1">Recommended Workers</div>
            <div className="font-semibold text-lg text-accent">{num_workers}</div>
          </div>
        )}
      </div>
    </div>
  )
}
