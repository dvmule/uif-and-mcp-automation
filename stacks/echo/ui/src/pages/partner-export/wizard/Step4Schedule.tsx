import { useState, type ReactNode } from 'react'
import { usePartnerExportWizardStore } from '@/store/partnerExportWizardStore'
import type { ExportFrequency } from '@/lib/types'

const FREQUENCIES: ExportFrequency[] = ['hourly', 'daily', 'weekly', 'cron']
const INPUT_CLASS = 'mt-1 w-full px-3 py-1.5 text-sm border rounded-md'

const Field = ({ label, children }: { label: string; children: ReactNode }) =>
  <label className="block"><span className="text-sm text-slate-700">{label}</span>{children}</label>
const Check = ({ label, checked, onChange }: { label: string; checked: boolean; onChange: (v: boolean) => void }) =>
  <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={checked} onChange={e => onChange(e.target.checked)} />{label}</label>

export function Step4Schedule() {
  const schedule = usePartnerExportWizardStore(s => s.schedule)
  const patchSchedule = usePartnerExportWizardStore(s => s.patchSchedule)
  const patchAlerts = usePartnerExportWizardStore(s => s.patchAlerts)
  const [recipientDraft, setRecipientDraft] = useState('')

  const addRecipient = () => {
    const v = recipientDraft.trim()
    if (!v || schedule.alerts.recipients.includes(v)) return
    patchAlerts({ recipients: [...schedule.alerts.recipients, v] })
    setRecipientDraft('')
  }
  const removeRecipient = (r: string) =>
    patchAlerts({ recipients: schedule.alerts.recipients.filter(x => x !== r) })

  return (
    <div data-testid="wizard-step-3" className="space-y-4">
      <div className="grid grid-cols-2 gap-4">
        <Field label="Frequency">
          <select aria-label="Frequency" value={schedule.frequency} onChange={e => patchSchedule({ frequency: e.target.value as ExportFrequency })} className={INPUT_CLASS}>
            {FREQUENCIES.map(f => <option key={f} value={f}>{f}</option>)}
          </select>
        </Field>

        {schedule.frequency === 'cron' ? (
          <Field label="Cron expression">
            <input aria-label="Cron expression" value={schedule.cron_expression ?? ''} onChange={e => patchSchedule({ cron_expression: e.target.value || null })} placeholder="0 2 * * *" className={`${INPUT_CLASS} font-mono`} />
          </Field>
        ) : (
          <Field label="Run at (UTC)">
            <input aria-label="Run at" type="time" value={schedule.run_at ?? '02:00'} onChange={e => patchSchedule({ run_at: e.target.value })} className={INPUT_CLASS} />
          </Field>
        )}

        <Field label="Watermark window">
          <input aria-label="Watermark window" value={schedule.watermark_window ?? ''} onChange={e => patchSchedule({ watermark_window: e.target.value || null })} placeholder="PT24H" className={`${INPUT_CLASS} font-mono`} />
        </Field>

        <Field label="Empty file behavior">
          <select aria-label="Empty file behavior" value={schedule.empty_file_behavior} onChange={e => patchSchedule({ empty_file_behavior: e.target.value as 'skip' | 'empty' | 'fail' })} className={INPUT_CLASS}>
            <option value="skip">Skip — don&apos;t deliver</option>
            <option value="empty">Empty — header only</option>
            <option value="fail">Fail — alert as error</option>
          </select>
        </Field>

        <Field label="Retention (days)">
          <input aria-label="Retention" type="number" min={0} value={schedule.retention_days} onChange={e => patchSchedule({ retention_days: Number(e.target.value) })} className={INPUT_CLASS} />
        </Field>
      </div>

      <fieldset className="border rounded-md p-3">
        <legend className="text-sm font-medium text-slate-700 px-1">Alerts</legend>
        <div className="space-y-1">
          <Check label="On failure" checked={schedule.alerts.on_failure} onChange={v => patchAlerts({ on_failure: v })} />
          <Check label="On partner 4xx/5xx" checked={schedule.alerts.on_partner_http_error} onChange={v => patchAlerts({ on_partner_http_error: v })} />
          <Check label="On zero-row output" checked={schedule.alerts.on_zero_rows} onChange={v => patchAlerts({ on_zero_rows: v })} />
        </div>

        <div className="mt-3">
          <span className="text-xs text-slate-600">Recipients</span>
          <div className="flex flex-wrap gap-1 mt-1">
            {schedule.alerts.recipients.map(r => (
              <span key={r} className="inline-flex items-center gap-1 px-2 py-0.5 text-xs bg-slate-100 border rounded">{r}<button type="button" aria-label={`Remove ${r}`} onClick={() => removeRecipient(r)} className="text-slate-500 hover:text-red-600">×</button></span>
            ))}
          </div>
          <div className="flex gap-2 mt-2">
            <input aria-label="Add recipient" value={recipientDraft} onChange={e => setRecipientDraft(e.target.value)} onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); addRecipient() } }} placeholder="ops@partner.example" className="flex-1 px-3 py-1 text-sm border rounded-md" />
            <button type="button" onClick={addRecipient} className="px-3 py-1 text-sm border rounded-md hover:bg-slate-50">Add</button>
          </div>
        </div>
      </fieldset>
    </div>
  )
}
