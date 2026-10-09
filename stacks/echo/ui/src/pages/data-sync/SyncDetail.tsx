import { useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import {
  usePipeline,
  useTogglePipeline,
  useTriggerRun,
  useDeletePipeline,
} from '@/hooks/usePipelines'
import { useRunsInfinite, useActiveRunPoll, useActiveRunId } from '@/hooks/useRuns'
import { useSyncState } from '@/hooks/useSyncState'
import { useResetWatermark } from '@/hooks/useResetWatermark'
import { useReBootstrap } from '@/hooks/useReBootstrap'
import { useDqChecks } from '@/hooks/useDqChecks'
import { usePartnerExports } from '@/hooks/usePartnerExports'
import { useGoldPipelines } from '@/hooks/useGoldPipelines'
import { Button } from '@/components/ui/button'
import { BusyButton, KpiCard, KpiRow, ObjectHeader, Pill, StatusBadge, SuccessRing, TabCard } from '@/components/relay'
import { CardLink } from '@/components/data-sync/detail/DetailCard'
import { FailureNote } from '@/components/data-sync/detail/workspace/FailureNote'
import { RunHistoryCard } from '@/components/data-sync/detail/workspace/RunHistoryCard'
import { DetailRunsPanel } from '@/components/data-sync/detail/workspace/DetailRunsPanel'
import { SyncDetailsGroups } from '@/components/data-sync/detail/workspace/DetailsGroups'
import { QualitySummaryCard } from '@/components/data-sync/detail/workspace/QualitySummaryCard'
import { DependenciesCard } from '@/components/data-sync/detail/rail/DependenciesCard'
// import { SyncMoreMenu } from '@/components/data-sync/detail/overflow/SyncMoreMenu'
import { silverTableFqn } from '@/lib/cloud'
import { getEnv } from '@/lib/envConfig'
import { DEFAULT_RUN_WINDOW, RUN_WINDOW_LABEL, runWindowCaption, runsInWindow, type RunWindow } from '@/lib/runWindow'
import { RunDrawer } from '@/components/data-sync/detail/drawer/RunDrawer'
import { useRunDrawerParam } from '@/components/data-sync/detail/drawer/useRunDrawerParam'
import { ResetWatermarkDialog } from '@/components/data-sync/detail/overflow/ResetWatermarkDialog'
import { ReBootstrapDialog } from '@/components/data-sync/detail/overflow/ReBootstrapDialog'
import { DisableSyncDialog } from '@/components/data-sync/detail/overflow/DisableSyncDialog'
import { DeleteSyncDialog } from '@/components/data-sync/detail/overflow/DeleteSyncDialog'
import { syncHeaderFacts, syncPrimaryAction } from '@/lib/detailHeader'
import { goldReadersOfSilver } from '@/lib/goldDetail'
import { qualityRules } from '@/lib/qualityRules'
import { isPipelineRunInFlight } from '@/lib/runLifecycle'
import { pipelineRunRow, sortRunRows, statusBucket } from '@/lib/runsTable'
import { BEHIND_SCHEDULE_VIEW, dqRunStatusView, syncStatusView } from '@/lib/statusVocabulary'
import {
  failedKpiHint,
  latestReconcileCheck,
  reconcileHint,
  rowsReadWrittenFact,
  runsKpiHint,
  windowRunCounts,
} from '@/lib/trust'
import type { RunMetrics } from '@/lib/types'

type Dialog = 'reset' | 'rebootstrap' | 'disable' | 'delete' | null

// Robust flattener: handles both the production shape `{ items, next_token }`
// and the simpler array-of-runs page shape used in some test fixtures.
function flattenRuns(pages: unknown[] | undefined): RunMetrics[] {
  if (!pages) return []
  return pages.flatMap((p) => {
    if (Array.isArray(p)) return p as RunMetrics[]
    const items = (p as { items?: RunMetrics[] })?.items
    return items ?? []
  })
}

export function SyncDetail() {
  const { id = '' } = useParams()
  const navigate = useNavigate()
  const { data: pipeline, isLoading } = usePipeline(id)
  const { data: runPages } = useRunsInfinite(id)
  const runs = flattenRuns(runPages?.pages)
  useActiveRunPoll(id, runs[0])
  const state = useSyncState(pipeline, runs)
  const toggle = useTogglePipeline()
  const trigger = useTriggerRun()
  const resetWm = useResetWatermark()
  const rebootstrap = useReBootstrap()
  const deletePipeline = useDeletePipeline()
  const activeRunId = useActiveRunId(id)
  const runInFlight = isPipelineRunInFlight(runs[0], activeRunId)
  const { runId: drawerRunId, open: openDrawer } = useRunDrawerParam()

  const silverFqn = pipeline?.silver?.enabled ? (silverTableFqn(pipeline) ?? '') : ''
  const dqSubject = silverFqn ? `silver.${silverFqn}` : ''

  // Linked DQ checks + partner exports — real hooks, scoped to silver FQN.
  const { data: dqChecks = [] } = useDqChecks(
    dqSubject ? { subject: dqSubject } : undefined,
  )
  const { data: linkedExports = [] } = usePartnerExports(
    dqSubject ? { source: dqSubject } : {},
  )
  const { data: golds } = useGoldPipelines()

  const [dialog, setDialog] = useState<Dialog>(null)
  // Shared by the run-history chart and the Runs / Failed figures.
  const [win, setWin] = useState<RunWindow>(DEFAULT_RUN_WINDOW)

  if (isLoading) {
    return (
      <div className="mx-auto max-w-[1180px]">
        <ObjectHeader back={{ to: '/m/data-sync/syncs', label: 'Replicate' }} loading />
      </div>
    )
  }
  if (!pipeline) {
    return <div className="p-6 text-ink-soft">Sync not found.</div>
  }

  const watermarkValue = runs[0]?.new_watermark ?? '—'
  const tableName = pipeline.extract?.table || pipeline.pipeline_id

  // Map DqRunStatus ('SUCCEEDED' | 'FAILED' | 'SKIPPED' | ...) to the
  // DependenciesCard's status vocabulary.
  const dqChecksForDeps = dqChecks.map((c) => ({
    status:
      c.last_run?.status === 'SUCCEEDED'
        ? ('PASSED' as const)
        : c.last_run?.status === 'FAILED'
          ? ('FAILING' as const)
          : ('UNKNOWN' as const),
  }))

  const rows = runs.map((r) => pipelineRunRow(pipeline, r))
  const lastRow = state.lastRun ? pipelineRunRow(pipeline, state.lastRun) : null
  const readWritten = rowsReadWrittenFact(lastRow)
  const windowRows = runsInWindow(runs, win).map((r) => pipelineRunRow(pipeline, r))
  const counts = windowRunCounts(windowRows)
  // Without a silver table the checks query is unscoped, so nothing on it verifies this sync.
  const reconcile = dqSubject ? latestReconcileCheck(dqChecks) : null
  const [pipelineIdFact, cloudFact, ...metaFacts] = syncHeaderFacts(pipeline, state.nextRunInMin)

  const primary = syncPrimaryAction(state.status, runInFlight)
  const handlePrimary = () => {
    if (state.status === 'disabled') {
      toggle.mutate({ id, enabled: true })
    } else if (state.status === 'running') {
      // Open the drawer on the most recent (running) run.
      const running = runs[0]
      if (running) openDrawer(running.run_id)
    } else if (!runInFlight) {
      trigger.mutate(id)
    }
  }

  const canRetry = !runInFlight && pipeline.enabled
  const handleRetry = () => {
    if (canRetry) trigger.mutate(id)
  }
  const newestFailed = sortRunRows(rows, 'desc').find((r) => statusBucket(r.status) === 'FAILED')

  const drawerRun = drawerRunId ? runs.find((r) => r.run_id === drawerRunId) : undefined

  return (
    <div className="mx-auto max-w-[1180px]">
      <ObjectHeader
        back={{ to: '/m/data-sync/syncs', label: 'Replicate' }}
        title={tableName}
        pills={
          <>
            <Pill tone="mute">{pipelineIdFact}</Pill>
            <Pill tone="accent">{cloudFact}</Pill>
            <Pill tone="accent">{pipeline.silver?.enabled ? 'bronze · silver' : 'bronze'}</Pill>
            <StatusBadge view={syncStatusView(state.status)} />
            {state.stale && <StatusBadge view={BEHIND_SCHEDULE_VIEW} size="sm" />}
          </>
        }
        meta={metaFacts.join(' · ')}
        caption={runWindowCaption(win, counts.total)}
        actions={
          <>
            <BusyButton
              onClick={handlePrimary}
              disabled={primary.disabled}
              busy={primary.kind === 'enable' ? toggle.isPending : primary.kind === 'run' && trigger.isPending}
            >
              {primary.label}
            </BusyButton>
            {/* Hidden per product request — keep for easy restore.
            <Button variant="outline" onClick={() => navigate(`/m/data-sync/syncs/${id}/edit/extraction`)}>Edit config</Button>
            <SyncMoreMenu
              onClone={() => navigate(`/m/data-sync/syncs/new?clone=${id}`)}
              onRawJson={() => navigate(`/m/data-sync/syncs/${id}/edit/raw-json`)}
              onResetWatermark={() => setDialog('reset')}
              onReBootstrap={() => setDialog('rebootstrap')}
              onDisable={() => setDialog('disable')}
              onDelete={() => setDialog('delete')}
              runInFlight={runInFlight}
            />
            */}
          </>
        }
      />

      <FailureNote row={lastRow} onOpenRun={openDrawer} />

      <KpiRow>
        <KpiCard
          testid="kpi-runs"
          label={`Runs · ${RUN_WINDOW_LABEL[win]}`}
          value={counts.total}
          ring={counts.ok + counts.bad > 0 ? <SuccessRing ok={counts.ok} bad={counts.bad} /> : undefined}
          hint={runsKpiHint(counts, rows)}
        />
        <KpiCard
          testid="kpi-rows"
          label="Rows read → written"
          dot="turq"
          edge="turq"
          value={readWritten.value}
          hint={readWritten.hint}
        />
        <KpiCard
          testid="kpi-failed"
          label={`Failed · ${RUN_WINDOW_LABEL[win]}`}
          dot="fuchsia"
          edge="fuchsia"
          value={<span className={counts.bad > 0 ? 'text-signal-fail' : undefined}>{counts.bad}</span>}
          hint={failedKpiHint(windowRows)}
        />
        <KpiCard
          testid="kpi-verified"
          label="Last verified"
          dot="ok"
          edge="ok"
          value={
            reconcile ? (
              <StatusBadge view={dqRunStatusView(reconcile.last_run?.status ?? undefined)} />
            ) : (
              <span className="text-body font-medium text-ink-mute">Not verified</span>
            )
          }
          hint={
            reconcile ? (
              <>
                {reconcileHint(reconcile)} ·{' '}
                <CardLink to={`/m/data-quality/checks/${encodeURIComponent(reconcile.check_id)}`}>open check</CardLink>
              </>
            ) : (
              <CardLink
                to={
                  dqSubject
                    ? `/m/data-quality/checks/new?subject=${encodeURIComponent(dqSubject)}`
                    : '/m/data-quality/checks/new'
                }
              >
                Add reconcile check
              </CardLink>
            )
          }
        />
      </KpiRow>

      <div className="grid gap-4 xl:grid-cols-[2fr_1fr]">
        <div className="min-w-0">
          <RunHistoryCard runs={runs} onBarClick={openDrawer} window={win} onWindowChange={setWin} />
          <TabCard
            label="Sync sections"
            tabs={[
              {
                value: 'details',
                label: 'Details',
                content: (
                  <SyncDetailsGroups
                    pipeline={pipeline}
                    watermarkValue={watermarkValue}
                    nextRunInMin={state.nextRunInMin}
                    onReset={() => setDialog('reset')}
                  />
                ),
              },
              {
                value: 'runs',
                label: 'Runs',
                count: rows.length,
                content: (
                  <DetailRunsPanel
                    rows={rows}
                    allRunsHref={`/m/data-sync/runs?object=${encodeURIComponent(id)}&window=all`}
                    allRunsLabel="All runs for this sync →"
                    onOpen={openDrawer}
                    selectedRunId={drawerRunId}
                    hideColumns={['object']}
                    rowAction={(row) =>
                      canRetry && row.runId === newestFailed?.runId ? (
                        <Button
                          type="button"
                          variant="ghost"
                          size="xs"
                          className="text-accent"
                          onClick={(e) => {
                            e.stopPropagation()
                            handleRetry()
                          }}
                        >
                          Retry
                        </Button>
                      ) : null
                    }
                  />
                ),
              },
            ]}
          />
        </div>

        <aside className="min-w-0 xl:sticky xl:top-4 xl:max-h-[calc(100vh-2rem)] xl:self-start xl:overflow-y-auto">
          <QualitySummaryCard rules={qualityRules(pipeline.quality, runs.find((r) => r.quality_result)?.quality_result)} />
          <DependenciesCard
            silverFqn={silverFqn}
            dqChecks={dqChecksForDeps}
            exportsCount={linkedExports.length}
            goldReaders={goldReadersOfSilver(golds, silverFqn, getEnv())}
          />
        </aside>
      </div>

      <RunDrawer
        row={drawerRun ? pipelineRunRow(pipeline, drawerRun) : null}
        onRetry={handleRetry}
        retryDisabled={runInFlight || !pipeline.enabled}
      />

      <ResetWatermarkDialog
        open={dialog === 'reset'}
        currentValue={watermarkValue}
        onCancel={() => setDialog(null)}
        onConfirm={() => {
          resetWm.mutate(id)
          setDialog(null)
        }}
      />
      <ReBootstrapDialog
        open={dialog === 'rebootstrap'}
        syncName={pipeline.pipeline_id}
        onCancel={() => setDialog(null)}
        onConfirm={() => {
          rebootstrap.mutate(id)
          setDialog(null)
        }}
      />
      <DisableSyncDialog
        open={dialog === 'disable'}
        onCancel={() => setDialog(null)}
        onConfirm={() => {
          toggle.mutate({ id, enabled: false })
          setDialog(null)
        }}
      />
      <DeleteSyncDialog
        open={dialog === 'delete'}
        confirming={deletePipeline.isPending}
        onCancel={() => setDialog(null)}
        onConfirm={() => {
          deletePipeline.mutate(id, {
            onSuccess: () => {
              setDialog(null)
              navigate('/m/data-sync/syncs')
            },
          })
        }}
      />
    </div>
  )
}

export default SyncDetail
