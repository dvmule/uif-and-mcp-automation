import { Clock, Play, PlayCircle, Power } from 'lucide-react'
import { IconBtn } from '@/components/data-sync/SyncRowActions'
import { useGoldRunLock, useToggleGoldPipeline } from '@/hooks/useGoldPipelineMutations'
import { getEnv } from '@/lib/envConfig'
import { goldEnabled, goldTableFqn } from '@/lib/syncRow'
import { cn } from '@/lib/utils'
import type { GoldPipelineConfig } from '@/lib/types'

interface Props {
  gold: GoldPipelineConfig
  /** Opens the confirm step; the parent owns the run mutation, as it does for delete. */
  onRun: (g: GoldPipelineConfig) => void
  /** Opens the run-time editor; the Schedule button shows only when given. */
  onEditSchedule?: (g: GoldPipelineConfig) => void
  runPending?: boolean
  className?: string
}

/** Play / Schedule / Power for a gold entity. No delete: a gold entity is retired
 *  through its domain's pipeline config, not from the console. */
export function GoldRowActions({ gold, onRun, onEditSchedule, runPending, className }: Props) {
  const toggle = useToggleGoldPipeline()
  const lockedRunId = useGoldRunLock(gold.gold_id)
  const enabled = goldEnabled(gold)

  // The server will run a DISABLED gold entity (silver refuses; gold does not
  // check), so this button holds the line the API does not.
  const runBlocked = !enabled || !!runPending || !!lockedRunId
  const runTitle = lockedRunId
    ? 'Run started this session'
    : !enabled
      ? 'Enable before running'
      : 'Run gold transform'

  return (
    <div className={cn('inline-flex items-center gap-0.5', className)}>
      <IconBtn title={runTitle} disabled={runBlocked} onClick={() => onRun(gold)}>
        <Play size={14} />
      </IconBtn>
      {onEditSchedule && (
        <IconBtn
          title={`Change run time for ${goldTableFqn(gold, getEnv()) ?? gold.gold_id}`}
          onClick={() => onEditSchedule(gold)}
        >
          <Clock size={14} />
        </IconBtn>
      )}
      <IconBtn
        title={enabled ? 'Disable' : 'Enable'}
        disabled={toggle.isPending}
        onClick={() => toggle.mutate({ id: gold.gold_id, enabled: !enabled })}
      >
        {enabled ? (
          <Power size={14} className="text-signal-ok" />
        ) : (
          <PlayCircle size={14} className="text-ink-mute" />
        )}
      </IconBtn>
    </div>
  )
}
