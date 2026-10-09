import { Play, PlayCircle, Power, Trash2 } from 'lucide-react'
import { useTogglePipeline, useTriggerRun } from '@/hooks/usePipelines'
import { useActiveRunId } from '@/hooks/useRuns'
import { isPipelineRunInFlight } from '@/lib/runLifecycle'
import { cn } from '@/lib/utils'
import type { PipelineConfig, RunMetrics } from '@/lib/types'

interface Props {
  pipeline: PipelineConfig
  lastRun?: RunMetrics
  onDelete: (p: PipelineConfig) => void
  className?: string
}

/** Play / PlayCircle|Power / Trash icons — extracted so List + Grid views share behavior. */
export function SyncRowActions({ pipeline, lastRun, onDelete, className }: Props) {
  const toggle = useTogglePipeline()
  const trigger = useTriggerRun()
  const activeRunId = useActiveRunId(pipeline.pipeline_id)
  const runInFlight = isPipelineRunInFlight(lastRun, activeRunId)

  return (
    <div className={cn('inline-flex items-center gap-0.5', className)}>
      <IconBtn
        title={runInFlight ? 'Run in progress' : 'Trigger run'}
        disabled={trigger.isPending || runInFlight || !pipeline.enabled}
        onClick={() => trigger.mutate(pipeline.pipeline_id)}
      >
        <Play size={14} />
      </IconBtn>
      <IconBtn
        title={
          runInFlight
            ? 'Run in progress'
            : pipeline.enabled
              ? 'Disable'
              : 'Enable'
        }
        disabled={toggle.isPending || runInFlight}
        onClick={() =>
          toggle.mutate({ id: pipeline.pipeline_id, enabled: !pipeline.enabled })
        }
      >
        {pipeline.enabled ? (
          <Power size={14} className="text-signal-ok" />
        ) : (
          <PlayCircle size={14} className="text-ink-mute" />
        )}
      </IconBtn>
      <IconBtn
        title={runInFlight ? 'Run in progress' : 'Remove sync'}
        disabled={runInFlight}
        onClick={() => onDelete(pipeline)}
        destructive
      >
        <Trash2 size={14} />
      </IconBtn>
    </div>
  )
}

interface IconBtnProps {
  title: string
  onClick: () => void
  disabled?: boolean
  destructive?: boolean
  children: React.ReactNode
}

export function IconBtn({ title, onClick, disabled, destructive, children }: IconBtnProps) {
  return (
    <button
      type="button"
      title={title}
      aria-label={title}
      disabled={disabled}
      onClick={onClick}
      className={cn(
        'inline-flex h-7 w-7 items-center justify-center rounded-full transition-colors',
        'text-ink-soft hover:bg-accent/10 hover:text-accent',
        destructive && 'hover:bg-signal-fail/10 hover:text-signal-fail',
        'disabled:opacity-40 disabled:hover:bg-transparent disabled:hover:text-ink-soft',
      )}
    >
      {children}
    </button>
  )
}
