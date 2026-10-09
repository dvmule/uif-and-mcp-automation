// New replication: six steps on the shared Stepper, Deploy on the last, then a success panel.
import { useState, type ComponentType } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { Check } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { BusyButton, Note, PageHeader, Stepper } from '@/components/relay'
import { DetailBreadcrumb } from '@/components/data-sync/detail/DetailCard'
import { WizardCard, WizardFooter } from '@/components/wizard/WizardCard'
import { useUpsertPipeline, useTriggerRun } from '@/hooks/usePipelines'
import { apiErrorMessage } from '@/lib/apiError'
import { buildPipelineConfig, NewSyncConfigError } from '@/lib/newSyncConfig'
import type { PipelineConfig } from '@/lib/types'
import { useEnvStore } from '@/store/envStore'
import { useWizardStore } from '@/store/wizardStore'
import { REVIEW_STEP, STEP_LABELS, canAdvance, stepAnswer } from './steps'
import { StepSource } from './StepSource'
import { StepDestination } from './StepDestination'
import { StepSchemaLoad } from './StepSchemaLoad'
import { StepSilver } from './StepSilver'
import { StepScheduleStorage } from './StepScheduleStorage'
import { StepReview } from './StepReview'

const STEP_COMPONENTS: ComponentType[] = [
  StepSource,
  StepDestination,
  StepSchemaLoad,
  StepSilver,
  StepScheduleStorage,
  StepReview,
]

const SYNCS = '/m/data-sync/syncs'

interface Deployed {
  id: string
  frequency: string
}

export function NewSyncWizard() {
  const s = useWizardStore()
  const env = useEnvStore(e => e.env)
  const navigate = useNavigate()
  const upsert = useUpsertPipeline()
  const trigger = useTriggerRun()
  const [deployError, setDeployError] = useState<string | null>(null)
  const [deployed, setDeployed] = useState<Deployed | null>(null)

  const step = Math.min(Math.max(s.step, 0), REVIEW_STEP)
  const StepComp = STEP_COMPONENTS[step]
  const ok = canAdvance(step, s)

  function cancel() {
    s.reset()
    navigate(SYNCS)
  }

  function goTo(index: number) {
    setDeployError(null)
    s.setStep(index)
  }

  async function deploy() {
    if (!s.connection || !s.analyzeResult) return
    setDeployError(null)
    let config: PipelineConfig
    try {
      config = buildPipelineConfig(s, env)
    } catch (e: unknown) {
      setDeployError(e instanceof NewSyncConfigError ? e.message : apiErrorMessage(e, 'Deploy failed'))
      return
    }
    try {
      await upsert.mutateAsync({ id: s.pipelineId, config })
      const done = { id: s.pipelineId, frequency: s.frequency }
      s.reset()
      setDeployed(done)
    } catch (e: unknown) {
      setDeployError(apiErrorMessage(e, 'Deploy failed'))
    }
  }

  function runNow(id: string) {
    trigger.mutate(id)
    navigate(`${SYNCS}/${encodeURIComponent(id)}`)
  }

  function setUpAnother() {
    s.reset()
    setDeployed(null)
    setDeployError(null)
  }

  const header = (
    <PageHeader
      back={<DetailBreadcrumb to={SYNCS} label="Replicate" current="New replication" />}
      title="New replication"
      sub="Bring one Oracle table into bronze, and optionally silver. Six steps, deploy at the end."
      actions={
        <Button type="button" variant="ghost" className="text-accent" onClick={cancel}>
          Cancel
        </Button>
      }
    />
  )

  if (deployed) {
    return (
      <div className="mx-auto max-w-[960px]">
        {header}
        <section
          data-testid="deploy-success"
          className="rounded-card border border-line bg-paper-card px-4 py-7 text-center shadow-card"
        >
          <span className="mx-auto mb-3 grid h-12 w-12 place-items-center rounded-full bg-signal-ok/10 text-signal-ok">
            <Check size={22} strokeWidth={3} aria-hidden="true" />
          </span>
          <h2 className="text-[18px] font-semibold text-ink">Sync deployed</h2>
          <p className="mx-auto mt-1 mb-4 max-w-xl text-body text-ink-mute">
            {deployed.id} is saved and enabled. Its first run starts at the next {deployed.frequency} slot, or now if you run it.
          </p>
          <div className="flex justify-center gap-2">
            <Button asChild>
              <Link to={`${SYNCS}/${encodeURIComponent(deployed.id)}`}>View sync</Link>
            </Button>
            <Button type="button" variant="outline" onClick={() => runNow(deployed.id)}>
              Run now
            </Button>
            <Button type="button" variant="ghost" className="text-accent" onClick={setUpAnother}>
              Set up another
            </Button>
          </div>
        </section>
      </div>
    )
  }

  const isReview = step === REVIEW_STEP
  const action = isReview ? (
    <BusyButton type="button" onClick={deploy} busy={upsert.isPending} disabled={!s.pipelineId}>
      Deploy
    </BusyButton>
  ) : (
    <Button type="button" onClick={() => goTo(step + 1)} disabled={!ok}>
      Next: {STEP_LABELS[step + 1]} →
    </Button>
  )

  return (
    <div className="mx-auto max-w-[960px]">
      {header}
      <div className="mb-5 rounded-card border border-line bg-paper-card px-4 py-3 shadow-card">
        <Stepper
          steps={STEP_LABELS.map((label, i) => ({ label, answer: stepAnswer(s, i, env) }))}
          current={step}
          onStepClick={goTo}
        />
      </div>
      <WizardCard
        title={STEP_LABELS[step]}
        aside={stepAside(step, s.analyzeResult?.columns.length, s.analyzeResult?.table)}
        notice={isReview && deployError ? <Note tone="fail">{deployError}</Note> : undefined}
        footer={
          <WizardFooter index={step} total={STEP_LABELS.length} onBack={() => goTo(step - 1)} action={action} />
        }
      >
        <StepComp />
      </WizardCard>
    </div>
  )
}

function stepAside(step: number, columns?: number, table?: string): string {
  switch (step) {
    case 0: return 'the Oracle connection and the table to replicate'
    case 1: return 'where bronze and silver are written'
    case 2: return columns !== undefined ? `${columns} columns read from ${table} · how each run reads the table` : 'how each run reads the table'
    case 3: return 'an optional deduplicated Iceberg table'
    case 4: return 'where files land, how often it runs, what it checks'
    default: return 'what will be saved · click a step to change it'
  }
}
