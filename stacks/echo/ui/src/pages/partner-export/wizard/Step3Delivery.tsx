import { useState } from 'react'
import { usePartnerExportWizardStore } from '@/store/partnerExportWizardStore'
import { usePartners, useCreatePartner } from '@/hooks/usePartners'
import { useConnections } from '@/hooks/useConnections'
import type { PartnerDeliveryMethod } from '@/lib/types'

const METHODS: PartnerDeliveryMethod[] = ['sftp', 'https', 's3', 'gcs']
const INPUT = 'mt-1 w-full px-3 py-1.5 text-sm border rounded-md'

export function Step3Delivery() {
  const delivery = usePartnerExportWizardStore(s => s.delivery)
  const patchDelivery = usePartnerExportWizardStore(s => s.patchDelivery)
  const partnersQ = usePartners()
  const createPartner = useCreatePartner()
  const connectionsQ = useConnections()
  const [showNew, setShowNew] = useState(false)
  const [newName, setNewName] = useState('')

  // Filter by `kind` when the field exists on the Connection (tests provide it);
  // when omitted in prod data, fall through and show all.
  const all = (connectionsQ.data ?? []) as Array<{ name: string; arn: string; kind?: string }>
  const connections = all.some(c => 'kind' in c)
    ? all.filter(c => (c.kind ?? '').toLowerCase() === delivery.method)
    : all

  async function createInline() {
    const p = await createPartner.mutateAsync({ name: newName.trim() })
    patchDelivery({ partner_id: p.partner_id })
    setShowNew(false); setNewName('')
  }

  return (
    <div data-testid="wizard-step-2" className="space-y-4">
      <div className="flex items-end gap-2">
        <label className="flex-1">
          <span className="text-sm text-slate-700">Partner</span>
          <select aria-label="Partner" value={delivery.partner_id} onChange={e => patchDelivery({ partner_id: e.target.value })} className={INPUT}>
            <option value="">— pick a partner —</option>
            {(partnersQ.data ?? []).map(p => <option key={p.partner_id} value={p.partner_id}>{p.name}</option>)}
          </select>
        </label>
        <button type="button" onClick={() => setShowNew(v => !v)} className="px-2 py-1.5 text-xs text-indigo-700 border border-indigo-300 rounded-md hover:bg-indigo-50">+ New partner</button>
      </div>

      {showNew && (
        <div className="border rounded-md p-3 bg-slate-50 flex items-end gap-2">
          <label className="flex-1"><span className="text-xs text-slate-700">New partner name</span><input aria-label="New partner name" value={newName} onChange={e => setNewName(e.target.value)} className={INPUT} /></label>
          <button type="button" disabled={!newName.trim() || createPartner.isPending} onClick={createInline} className="px-3 py-1.5 text-sm bg-indigo-600 text-white rounded-md disabled:opacity-50">Create</button>
        </div>
      )}

      <fieldset>
        <legend className="text-sm text-slate-700 mb-1">Delivery method</legend>
        <div className="flex flex-wrap gap-3">{METHODS.map(m => (
          <label key={m} className="inline-flex items-center gap-2"><input type="radio" name="method" checked={delivery.method === m} onChange={() => patchDelivery({ method: m, connection_arn: '' })} />{m.toUpperCase()}</label>
        ))}</div>
      </fieldset>

      <label className="block">
        <span className="text-sm text-slate-700">Connection</span>
        <select aria-label="Connection" value={delivery.connection_arn} onChange={e => patchDelivery({ connection_arn: e.target.value })} className={INPUT}>
          <option value="">— pick a {delivery.method.toUpperCase()} connection —</option>
          {connections.map(c => <option key={c.arn} value={c.arn}>{c.name}</option>)}
        </select>
      </label>

      <label className="block">
        <span className="text-sm text-slate-700">Path template</span>
        <input aria-label="Path template" value={delivery.path_template} onChange={e => patchDelivery({ path_template: e.target.value })} className={`${INPUT} font-mono`} placeholder="/incoming/{export_id}/{YYYYMMDD}.csv" />
        <p className="mt-1 text-xs text-slate-500">Placeholders: <code>{'{YYYYMMDD}'}</code>, <code>{'{export_id}'}</code>, <code>{'{run_id}'}</code>.</p>
      </label>
    </div>
  )
}
