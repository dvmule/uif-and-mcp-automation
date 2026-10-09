import { isValidElement, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { Cloud, Cpu } from 'lucide-react'
import { CloudBadge } from '@/components/pipeline/CloudBadge'
import { LayersChips } from '@/components/data-sync/LayersChips'
import { SyncRowActions } from '@/components/data-sync/SyncRowActions'
import { EmptyState, StatusBadge } from '@/components/relay'
import { pipelineCloud } from '@/lib/cloud'
import { formatDailyScheduleLabel } from '@/lib/dailySchedule'
import { pipelineStatus } from '@/lib/filterMatchers'
import { freshnessView, pipelineStatusView } from '@/lib/statusVocabulary'
import { cn, freshness, relativeTime } from '@/lib/utils'
import type { PipelineConfig, RunMetrics } from '@/lib/types'

interface Props {
  pipelines: PipelineConfig[]
  lastRunByPipeline: Map<string, RunMetrics | undefined>
  runsByPipeline: Map<string, RunMetrics[]>
  onDelete: (p: PipelineConfig) => void
  empty?: ReactNode
}

export function SyncsGrid({
  pipelines,
  lastRunByPipeline,
  runsByPipeline,
  onDelete,
  empty,
}: Props) {
  if (pipelines.length === 0) {
    return (
      <section className="rounded-card border border-line bg-paper-card shadow-card">
        {isValidElement(empty) && empty.type === EmptyState ? (
          empty
        ) : (
          <EmptyState>{empty ?? 'No syncs to show'}</EmptyState>
        )}
      </section>
    )
  }
  return (
    <section
      className="grid gap-3.5"
      style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(320px, 1fr))' }}
    >
      {pipelines.map(p => (
        <SyncCard
          key={p.pipeline_id}
          pipeline={p}
          lastRun={lastRunByPipeline.get(p.pipeline_id)}
          allRuns={runsByPipeline.get(p.pipeline_id)}
          onDelete={onDelete}
        />
      ))}
    </section>
  )
}

function SyncCard({
  pipeline, lastRun, allRuns, onDelete,
}: {
  pipeline: PipelineConfig
  lastRun: RunMetrics | undefined
  allRuns: RunMetrics[] | undefined
  onDelete: (p: PipelineConfig) => void
}) {
  const cloud = pipelineCloud(pipeline)
  const status = pipelineStatus(pipeline, lastRun, allRuns)
  const freshnessLevel = freshness(pipeline, lastRun?.completed_at)
  const isGcp = cloud === 'gcp'

  return (
    <article className="group flex flex-col gap-3 rounded-card border border-line bg-paper-card p-4 shadow-card transition-colors hover:border-paper-line">
      <header className="flex items-start gap-3">
        <div
          className={cn(
            'flex h-10 w-10 shrink-0 items-center justify-center rounded-[10px]',
            isGcp ? 'bg-accent/15 text-accent' : 'bg-turq/25 text-ink',
          )}
        >
          {isGcp ? <Cloud size={18} strokeWidth={1.75} /> : <Cpu size={18} strokeWidth={1.75} />}
        </div>
        <div className="min-w-0 flex-1">
          <Link
            to={`/m/data-sync/syncs/${pipeline.pipeline_id}`}
            className="block truncate text-[14px] font-semibold text-ink hover:text-accent"
          >
            {pipeline.extract.table}
          </Link>
          <p className="mt-0.5 truncate text-meta text-ink-mute">{pipeline.pipeline_id}</p>
        </div>
        <CloudBadge cloud={cloud} />
      </header>

      <div className="flex flex-wrap items-center gap-1.5">
        <StatusBadge view={pipelineStatusView(status)} size="sm" />
        <StatusBadge view={freshnessView(freshnessLevel, { neverRan: !lastRun })} size="sm" />
        <span className="ml-auto text-meta text-ink-mute tnum">
          {relativeTime(lastRun?.started_at)}
        </span>
      </div>

      <footer className="mt-auto flex items-center gap-2 border-t border-line pt-3">
        <LayersChips pipeline={pipeline} />
        <span className="text-meta text-ink-mute tnum">
          {formatDailyScheduleLabel(pipeline.schedule)}
        </span>
        <SyncRowActions
          pipeline={pipeline}
          lastRun={lastRun}
          onDelete={onDelete}
          className="ml-auto opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100"
        />
      </footer>
    </article>
  )
}
