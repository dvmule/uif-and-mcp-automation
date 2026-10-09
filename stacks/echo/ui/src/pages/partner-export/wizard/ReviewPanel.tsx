import { useMemo, type ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'
import { slugifyExportId, usePartnerExportWizardStore } from '@/store/partnerExportWizardStore'
import { useUpsertPartnerExport } from '@/hooks/usePartnerExports'

export function ReviewPanel() {
  const state = usePartnerExportWizardStore()
  const mut = useUpsertPartnerExport()
  const navigate = useNavigate()

  const exportId = useMemo(() => slugifyExportId(state.exportName), [state.exportName])
  const canSave = !!(state.exportName.trim() && state.source?.dataset && state.delivery.partner_id && state.delivery.connection_arn && state.delivery.path_template && exportId)

  async function onSave() {
    await mut.mutateAsync({ id: exportId, body: state.toBody(exportId) })
    state.reset()
    navigate(`/m/partner-export/exports/${exportId}`)
  }

  const miss = (s?: string | null): ReactNode => s || <span className="text-red-600">missing</span>
  const rows: Array<[string, ReactNode]> = [
    ['Name', miss(state.exportName)],
    ['Export ID', <code className="text-xs">{miss(exportId)}</code>],
    ['Source', miss(state.source?.dataset)],
    ['Format', state.format.kind],
    ['Mapping fields', state.mapping.length],
    ['Partner', miss(state.delivery.partner_id)],
    ['Method', state.delivery.method.toUpperCase()],
    ['Connection', <code className="text-xs break-all">{miss(state.delivery.connection_arn)}</code>],
    ['Path template', <code className="text-xs">{state.delivery.path_template}</code>],
    ['Schedule', `${state.schedule.frequency} ${state.schedule.run_at ?? state.schedule.cron_expression ?? ''}`],
  ]

  return (
    <div data-testid="wizard-step-4" className="space-y-4">
      <h2 className="text-base font-semibold text-slate-900">Review</h2>
      <dl className="grid grid-cols-2 gap-x-8 gap-y-1 text-sm">
        {rows.map(([k, v]) => (
          <div key={k} className="contents">
            <dt className="text-slate-500">{k}</dt>
            <dd>{v}</dd>
          </div>
        ))}
      </dl>

      <div className="flex items-center justify-end gap-2 pt-2">
        {mut.isError && <span className="text-xs text-red-600">Save failed: {(mut.error as Error).message}</span>}
        <button type="button" disabled={!canSave || mut.isPending} onClick={onSave} className="px-4 py-1.5 text-sm font-medium bg-indigo-600 text-white rounded-md hover:bg-indigo-700 disabled:opacity-50">
          {mut.isPending ? 'Saving…' : 'Save'}
        </button>
      </div>
    </div>
  )
}
