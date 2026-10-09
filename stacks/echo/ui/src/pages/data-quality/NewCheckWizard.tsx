// New check: four steps on the shared Stepper; ?subject=layer.db.table prefills step 2.
import { useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { Button } from '@/components/ui/button'
import { BusyButton, Note, PageHeader, Stepper } from '@/components/relay'
import { DetailBreadcrumb } from '@/components/data-sync/detail/DetailCard'
import { WizardCard, WizardFooter } from '@/components/wizard/WizardCard'
import { Step1Kind } from '@/components/data-quality/wizard/Step1Kind'
import { Step2Subject } from '@/components/data-quality/wizard/Step2Subject'
import { Step3Parameters } from '@/components/data-quality/wizard/Step3Parameters'
import { Step4Schedule } from '@/components/data-quality/wizard/Step4Schedule'
import { SCHEMAS, LIVE_KINDS } from '@/components/data-quality/wizard/parameterSchemas'
import { kindLabel, paramsSummary } from '@/components/data-quality/wizard/kindCopy'
import { useDqKinds, useUpsertDqCheck } from '@/hooks/useDqChecks'
import { apiErrorMessage } from '@/lib/apiError'
import { parseDqSubjectParam } from '@/lib/dqSubjectParam'
import type { DqCheckConfig, DqCheckKind, DqCheckSubject, DqCheckSchedule } from '@/lib/types'

const LABELS = ['Kind', 'Subject', 'Parameters', 'Schedule'] as const
const CHECKS = '/m/data-quality/checks'

export function NewCheckWizard() {
  const navigate = useNavigate()
  const upsert = useUpsertDqCheck()
  const { data: kinds } = useDqKinds()
  const [searchParams] = useSearchParams()
  const [prefill] = useState(() => parseDqSubjectParam(searchParams.get('subject')))

  const [step, setStep] = useState<1 | 2 | 3 | 4>(1)
  const [kind, setKind] = useState<DqCheckKind | null>(null)
  const [subject, setSubject] = useState<DqCheckSubject>(prefill ?? { layer: 'silver', database: '', table: '' })
  const [params, setParams] = useState<Record<string, unknown>>({ dry_run: true })
  const [schedule, setSchedule] = useState<DqCheckSchedule>({ frequency: 'daily' })
  const [error, setError] = useState<string | null>(null)

  function next() {
    setError(null)
    if (step === 1) {
      if (!kind) return setError('Pick a kind to continue')
      if (!LIVE_KINDS.includes(kind as typeof LIVE_KINDS[number])) {
        return setError(`${kind} is shipping later — pick a live kind`)
      }
    }
    if (step === 2) {
      if (!subject.database || !subject.table) return setError('Database and table are required')
    }
    if (step === 3 && kind) {
      const schema = SCHEMAS[kind]
      if (schema) {
        const parsed = schema.safeParse(params)
        if (!parsed.success) return setError(parsed.error.issues.map(i => i.message).join('; '))
      }
    }
    setStep(s => (s < 4 ? ((s + 1) as 1 | 2 | 3 | 4) : s))
  }

  function back() {
    setError(null)
    setStep(s => (s > 1 ? ((s - 1) as 1 | 2 | 3 | 4) : s))
  }

  function jumpTo(index: number) {
    setError(null)
    setStep((index + 1) as 1 | 2 | 3 | 4)
  }

  async function submit() {
    if (!kind) { setError('Pick a kind'); return }
    const checkId = `${kind}-${subject.database}-${subject.table}`
    const body: DqCheckConfig = {
      check_id: checkId,
      version: 'latest',
      kind,
      enabled: true,
      subject,
      schedule,
      parameters: params,
      metadata: { created_at: new Date().toISOString(), owner: '' },
      last_run: null,
    }
    try {
      await upsert.mutateAsync({ checkId, body })
      navigate(`/m/data-quality/checks/${encodeURIComponent(checkId)}`)
    } catch (e) {
      setError(apiErrorMessage(e, 'Submit failed'))
    }
  }

  const subjectText = subject.database && subject.table ? `${subject.database}.${subject.table}` : undefined
  const answers = [
    kind ? kindLabel(kind) : undefined,
    subjectText,
    paramsSummary(kind, params),
    schedule.frequency === 'cron' ? `cron ${schedule.cron ?? ''}`.trim() : schedule.frequency,
  ]
  const liveCount = (kinds ?? []).filter(k => k.status === 'live').length

  const titles = ['What kind of check?', 'Subject', 'Parameters', 'Schedule']
  const asides = [
    kinds ? `${liveCount} run today; the rest are planned` : undefined,
    'the silver or gold table this check evaluates',
    kind ? `for ${kindLabel(kind)}` : undefined,
    'how often it runs',
  ]
  const i = step - 1

  return (
    <div className="mx-auto max-w-[960px]">
      <PageHeader
        back={<DetailBreadcrumb to={CHECKS} label="Quality" current="New check" />}
        title="New check"
        sub="Verify a silver or gold table on a schedule."
        actions={
          <Button type="button" variant="ghost" className="text-accent" onClick={() => navigate(CHECKS)}>
            Cancel
          </Button>
        }
      />
      <div className="mb-5 rounded-card border border-line bg-paper-card px-4 py-3 shadow-card">
        <Stepper
          steps={LABELS.map((label, n) => ({
            label,
            answer: answers[n],
            hint: n === 1 && prefill ? `prefilled: ${prefill.database}.${prefill.table}` : undefined,
          }))}
          current={i}
          onStepClick={jumpTo}
        />
      </div>
      <WizardCard
        title={titles[i]}
        aside={asides[i]}
        notice={error ? <Note tone="fail">{error}</Note> : undefined}
        footer={
          <WizardFooter
            index={i}
            total={LABELS.length}
            onBack={back}
            action={
              step < 4 ? (
                <Button type="button" onClick={next}>
                  Next: {LABELS[i + 1]} →
                </Button>
              ) : (
                <BusyButton type="button" onClick={submit} busy={upsert.isPending}>
                  {upsert.isPending ? 'Creating…' : 'Create check'}
                </BusyButton>
              )
            }
          />
        }
      >
        {step === 1 && <Step1Kind value={kind} onChange={setKind} />}
        {step === 2 && <Step2Subject value={subject} onChange={setSubject} />}
        {step === 3 && kind && <Step3Parameters kind={kind} value={params} onChange={setParams} />}
        {step === 4 && <Step4Schedule value={schedule} onChange={setSchedule} />}
      </WizardCard>
    </div>
  )
}
