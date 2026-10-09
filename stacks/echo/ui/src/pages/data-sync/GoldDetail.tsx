import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { useGoldPipelines } from '@/hooks/useGoldPipelines'
import { useGoldRuns } from '@/hooks/useGoldRuns'
import {
  useGoldRunLock,
  useSetGoldSchedule,
  useToggleGoldPipeline,
  useTriggerGoldRun,
} from '@/hooks/useGoldPipelineMutations'
import { Button } from '@/components/ui/button'
import {
  KpiCard,
  KpiRow,
  Note,
  ObjectHeader,
  Pill,
  StatusBadge,
  SuccessRing,
  TabCard,
  type DataTableColumn,
} from '@/components/relay'
import { GoldNotifyCard } from '@/components/data-sync/gold/GoldNotifyCard'
import { SourcesCard } from '@/components/data-sync/gold/SourcesCard'
import { GoldRunConfirmDialog } from '@/components/data-sync/gold/GoldRunConfirmDialog'
import { GoldScheduleDialog } from '@/components/data-sync/GoldScheduleDialog'
import { RunHistoryCard } from '@/components/data-sync/detail/workspace/RunHistoryCard'
import { DetailRunsPanel } from '@/components/data-sync/detail/workspace/DetailRunsPanel'
import { GoldDetailsGroups } from '@/components/data-sync/detail/workspace/DetailsGroups'
import { FailureNote } from '@/components/data-sync/detail/workspace/FailureNote'
import { RunDrawer } from '@/components/data-sync/detail/drawer/RunDrawer'
import { useRunDrawerParam } from '@/components/data-sync/detail/drawer/useRunDrawerParam'
import { isUndeployedRouteError } from '@/lib/apiError'
import { goldHeaderFacts, goldPrimaryAction } from '@/lib/detailHeader'
import { getEnv } from '@/lib/envConfig'
import { goldDetailState, missingSourceTables } from '@/lib/goldDetail'
import { goldRunRow, type RunRow } from '@/lib/runsTable'
import { DEFAULT_RUN_WINDOW, RUN_WINDOW_LABEL, runWindowCaption, runsInWindow, type RunWindow } from '@/lib/runWindow'
import { freshnessView, goldStatusView } from '@/lib/statusVocabulary'
import { goldCatalogPath, goldEnabled, goldFreshness, goldTableFqn } from '@/lib/syncRow'
import {
  failedKpiHint,
  goldBookmarkWord,
  keysSelectedFact,
  rowsWrittenFact,
  runsKpiHint,
  windowRunCounts,
} from '@/lib/trust'
import { formatRows } from '@/lib/utils'

const UNAVAILABLE = 'Run history is not available in this environment yet.'
const UNAVAILABLE_HINT = 'not available in this environment yet'

// Recent runs in gold's terms: the keys CDC selected, and the rows whose hash actually moved.
const GOLD_RUN_COLUMNS: DataTableColumn<RunRow>[] = [
  {
    key: 'keys',
    header: 'Keys selected',
    align: 'right',
    className: 'tnum',
    cell: (row) => formatRows('affected_keys' in row.run ? row.run.affected_keys : null),
  },
  {
    key: 'written',
    header: 'Rows written',
    align: 'right',
    className: 'tnum',
    cell: (row) => formatRows(row.rowsWritten),
  },
]

/** A gold entity's own page (UIFPE-517): the sync detail layout, in gold's
 *  terms. Everything but runs comes from GET /gold-pipelines; runs come from
 *  GET /gold-runs (deployed in dev and UAT since 2026-09-29). The "not available"
 *  placeholder is a generic fallback for any environment that answers a bare 404. */
export function GoldDetail() {
  const { goldId = '' } = useParams()
  const { data: golds, isLoading } = useGoldPipelines()
  const gold = golds?.find(g => g.gold_id === goldId)

  const runsQuery = useGoldRuns(goldId)
  const runs = runsQuery.data?.items ?? []
  const runsUnavailable = isUndeployedRouteError(runsQuery.error)
  const state = goldDetailState(gold, runs)

  const toggle = useToggleGoldPipeline()
  const trigger = useTriggerGoldRun()
  const setSchedule = useSetGoldSchedule()
  const lockedRunId = useGoldRunLock(goldId)
  const { runId: drawerRunId, open: openDrawer } = useRunDrawerParam()

  const [confirmRun, setConfirmRun] = useState(false)
  const [editSchedule, setEditSchedule] = useState(false)
  // Shared by the run-history chart and the Runs / Failed figures.
  const [win, setWin] = useState<RunWindow>(DEFAULT_RUN_WINDOW)

  if (isLoading) {
    return (
      <div className="mx-auto max-w-[1180px]">
        <ObjectHeader back={{ to: '/m/data-sync/gold', label: 'Transform' }} loading />
      </div>
    )
  }
  if (!gold) {
    return <div className="p-6 text-ink-soft">Gold entity not found.</div>
  }

  const env = getEnv()
  const enabled = goldEnabled(gold)
  const fqn = goldTableFqn(gold, env) ?? gold.gold_id
  const catalog = goldCatalogPath(gold, env)
  const domain = gold.domain ?? gold.metadata?.domain
  const primary = goldPrimaryAction({
    enabled,
    running: state.status === 'running',
    runLocked: !!lockedRunId,
    runPending: trigger.isPending,
  })

  const rows = runs.map(r => goldRunRow(r, gold))
  const lastRow = state.lastRun ? goldRunRow(state.lastRun, gold) : null
  const drawerRun = drawerRunId ? runs.find(r => r.run_id === drawerRunId) : undefined

  const windowRows = runsInWindow(runs, win).map(r => goldRunRow(r, gold))
  const counts = windowRunCounts(windowRows)
  const written = rowsWrittenFact(lastRow)
  const keys = keysSelectedFact(lastRow)
  const bookmarkWord = goldBookmarkWord(rows)
  // goldHeaderFacts leads with the domain when there is one; here it is a pill.
  const metaFacts = goldHeaderFacts(gold, { showNextRun: state.nextRunInMin != null }).slice(domain ? 1 : 0)

  return (
    <div className="mx-auto max-w-[1180px]">
      <ObjectHeader
        back={{ to: '/m/data-sync/gold', label: 'Transform' }}
        title={gold.gold_id}
        pills={
          <>
            <Pill tone="mute">
              {catalog ? (
                <Link to={catalog} className="text-accent hover:underline">{fqn}</Link>
              ) : (
                fqn
              )}
            </Pill>
            {domain && <Pill tone="accent">{domain}</Pill>}
            <StatusBadge view={goldStatusView(state.status)} />
            {enabled && (
              <StatusBadge
                view={freshnessView(goldFreshness(gold), { neverRan: !gold.metadata?.last_run_at })}
                size="sm"
              />
            )}
          </>
        }
        meta={metaFacts.join(' · ')}
        caption={runsUnavailable ? undefined : runWindowCaption(win, counts.total)}
        actions={
          <>
            <Button variant="outline" onClick={() => setEditSchedule(true)}>Change run time</Button>
            {enabled && (
              <Button variant="outline" onClick={() => toggle.mutate({ id: gold.gold_id, enabled: false })} disabled={toggle.isPending}>
                Disable
              </Button>
            )}
            <Button
              onClick={() => (enabled ? setConfirmRun(true) : toggle.mutate({ id: gold.gold_id, enabled: true }))}
              disabled={primary.disabled}
            >
              {primary.label}
            </Button>
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
          hint={runsUnavailable ? UNAVAILABLE_HINT : runsKpiHint(counts, rows)}
          unavailable={runsUnavailable}
        />
        <KpiCard
          testid="kpi-rows"
          label="Rows written"
          dot="turq"
          edge="turq"
          value={written.value}
          hint={runsUnavailable ? UNAVAILABLE_HINT : written.hint}
          unavailable={runsUnavailable}
        />
        <KpiCard
          testid="kpi-failed"
          label={`Failed · ${RUN_WINDOW_LABEL[win]}`}
          dot="fuchsia"
          edge="fuchsia"
          value={<span className={counts.bad > 0 ? 'text-signal-fail' : undefined}>{counts.bad}</span>}
          hint={runsUnavailable ? UNAVAILABLE_HINT : failedKpiHint(windowRows)}
          unavailable={runsUnavailable}
        />
        <KpiCard
          testid="kpi-keys"
          label="Keys selected by CDC"
          value={keys.value}
          hint={runsUnavailable ? UNAVAILABLE_HINT : bookmarkWord ? `${keys.hint} · ${bookmarkWord}` : keys.hint}
          unavailable={runsUnavailable}
        />
      </KpiRow>

      <div className="grid gap-4 xl:grid-cols-[2fr_1fr]">
        <div className="min-w-0">
          <RunHistoryCard
            runs={runs}
            onBarClick={openDrawer}
            placeholder={runsUnavailable ? <Note>{UNAVAILABLE}</Note> : undefined}
            window={win}
            onWindowChange={setWin}
          />
          <TabCard
            label="Gold sections"
            tabs={[
              {
                value: 'details',
                label: 'Details',
                content: <GoldDetailsGroups gold={gold} nextRunInMin={state.nextRunInMin} />,
              },
              {
                value: 'runs',
                label: 'Runs',
                count: runsUnavailable ? undefined : rows.length,
                content: (
                  <DetailRunsPanel
                    rows={rows}
                    allRunsHref={`/m/data-sync/runs?object=${encodeURIComponent(gold.gold_id)}&window=all`}
                    allRunsLabel="All runs for this table →"
                    onOpen={openDrawer}
                    selectedRunId={drawerRunId}
                    hideColumns={['object', 'layer', 'kind', 'rows']}
                    extraColumns={GOLD_RUN_COLUMNS}
                    placeholder={runsUnavailable ? <Note>{UNAVAILABLE}</Note> : undefined}
                  />
                ),
              },
            ]}
          />
        </div>

        <aside className="min-w-0 xl:sticky xl:top-4 xl:max-h-[calc(100vh-2rem)] xl:self-start xl:overflow-y-auto">
          <SourcesCard sources={gold.sources} missing={missingSourceTables(gold.sources, lastRow, env)} env={env} />
          <GoldNotifyCard goldId={gold.gold_id} />
        </aside>
      </div>

      <RunDrawer
        row={drawerRun ? goldRunRow(drawerRun, gold) : null}
        onRunNow={enabled ? () => setConfirmRun(true) : undefined}
        runNowDisabled={primary.disabled}
      />

      <GoldRunConfirmDialog
        gold={confirmRun ? gold : null}
        pending={trigger.isPending}
        onCancel={() => setConfirmRun(false)}
        onConfirm={() => trigger.mutate(gold.gold_id, { onSuccess: () => setConfirmRun(false) })}
      />
      <GoldScheduleDialog
        gold={editSchedule ? gold : null}
        onClose={() => setEditSchedule(false)}
        saving={setSchedule.isPending}
        onSave={(id, change) =>
          setSchedule.mutate({ id, change }, { onSuccess: () => setEditSchedule(false) })
        }
      />
    </div>
  )
}

export default GoldDetail
