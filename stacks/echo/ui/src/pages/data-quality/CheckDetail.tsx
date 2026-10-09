import { useParams } from 'react-router-dom'
import { useDqCheckDetail, useDqCheckRuns } from '@/hooks/useDqCheckDetail'
import { useTriggerDqCheck } from '@/hooks/useDqChecks'
import { usePipelines } from '@/hooks/usePipelines'
import { AlertCircle, History } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { BusyButton, EmptyState, Kv, Note, ObjectHeader, Pill, StatusBadge, TabCard } from '@/components/relay'
import { CardLink } from '@/components/data-sync/detail/DetailCard'
import { CheckStatusBadge } from '@/components/data-quality/CheckStatusBadge'
import { kindLabel } from '@/components/data-quality/wizard/kindCopy'
import { linkedSync, subjectFqn } from '@/lib/dqStats'
import { dqRunStatusView } from '@/lib/statusVocabulary'
import { relativeTime } from '@/lib/utils'
import type { DqRunStatus } from '@/lib/types'

const BACK = { to: '/m/data-quality/checks', label: 'Quality' }

export function CheckDetail() {
  const { checkId = '' } = useParams<{ checkId: string }>()
  const { data: check, isLoading } = useDqCheckDetail(checkId)
  const { data: runs, isError: runsError, refetch: refetchRuns } = useDqCheckRuns(checkId)
  const { data: pipelines } = usePipelines()
  const trigger = useTriggerDqCheck()

  if (isLoading) {
    return (
      <div className="mx-auto max-w-[1180px]">
        <ObjectHeader back={BACK} loading />
      </div>
    )
  }
  if (!check) {
    return <div className="p-6 text-ink-soft">Check not found.</div>
  }

  const fqn = subjectFqn(check)
  const sync = linkedSync(pipelines, check)
  const lastStatus = check.last_run?.status
  // Kind and layer are pills; the meta line keeps the rest.
  const meta = [
    fqn,
    check.schedule.frequency,
    check.last_run?.ts ? `last run ${relativeTime(check.last_run.ts)}` : 'never ran',
  ].join(' · ')

  const overview = (
    <dl>
      <Kv label="Subject"><span className="break-all font-mono">{check.subject.layer} · {fqn}</span></Kv>
      <Kv label="Kind">{check.kind}</Kv>
      <Kv label="Schedule">{check.schedule.frequency}</Kv>
      <Kv label="Enabled">{check.enabled ? 'Yes' : 'No'}</Kv>
      <Kv label="Owner">{check.metadata?.owner}</Kv>
      <Kv label="Created">{check.metadata?.created_at}</Kv>
      <Kv label="Last run summary">{check.last_run?.summary ?? '—'}</Kv>
    </dl>
  )

  const runList =
    runsError && !runs ? (
      <EmptyState
        icon={AlertCircle}
        title="Couldn't load runs"
        action={
          <Button type="button" variant="outline" size="sm" onClick={() => void refetchRuns()}>
            Retry
          </Button>
        }
      />
    ) : (runs ?? []).length === 0 ? (
      <EmptyState icon={History} title="No runs yet">
        Run now records the first one.
      </EmptyState>
    ) : (
      <ul className="divide-y divide-line text-body">
        {(runs ?? []).map((r, i) => (
          <li key={i} className="flex items-center justify-between gap-3 py-2">
            <span className="font-mono text-meta text-ink-soft">{String(r.run_id ?? '')}</span>
            <span className="flex-1 text-ink-soft">{String(r.summary ?? '')}</span>
            <CheckStatusBadge status={(r.status ?? null) as DqRunStatus | null} />
          </li>
        ))}
      </ul>
    )

  const parameters = (
    <pre className="overflow-x-auto rounded-brand border border-line bg-paper-head p-3 font-mono text-meta text-ink-soft">
      {JSON.stringify(check.parameters, null, 2)}
    </pre>
  )

  const linked = (
    <div className="space-y-2">
      <p className="text-meta text-ink-mute">the sync whose silver table this check verifies</p>
      {sync ? (
        <p className="text-body text-ink">
          <CardLink to={`/m/data-sync/syncs/${encodeURIComponent(sync.pipeline_id)}`}>
            {sync.extract?.table || sync.pipeline_id}
          </CardLink>{' '}
          <span className="text-ink-mute">writes <span className="font-mono">{fqn}</span></span>
        </p>
      ) : (
        <Note>No sync writes this table.</Note>
      )}
    </div>
  )

  return (
    <div className="mx-auto max-w-[1180px]">
      <ObjectHeader
        back={BACK}
        title={check.check_id}
        pills={
          <>
            <Pill tone="accent">{kindLabel(check.kind)}</Pill>
            <Pill tone="mute">{check.subject.layer}</Pill>
            {lastStatus && <StatusBadge view={dqRunStatusView(lastStatus)} />}
          </>
        }
        meta={meta}
        actions={
          <BusyButton onClick={() => trigger.mutate(checkId)} busy={trigger.isPending}>
            {trigger.isPending ? 'Starting…' : 'Run now'}
          </BusyButton>
        }
      />

      <TabCard
        label="Check sections"
        tabs={[
          { value: 'overview', label: 'Overview', content: overview },
          { value: 'runs', label: 'Runs', count: runs ? runs.length : undefined, content: runList },
          { value: 'parameters', label: 'Parameters', content: parameters },
          { value: 'linked-sync', label: 'Linked sync', content: linked },
        ]}
      />
    </div>
  )
}
