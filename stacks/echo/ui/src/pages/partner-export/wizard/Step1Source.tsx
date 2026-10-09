import { usePartnerExportWizardStore } from '@/store/partnerExportWizardStore'
import { useCatalogDatasets } from '@/hooks/useCatalogDatasets'

const INPUT = 'mt-1 w-full px-3 py-1.5 text-sm border rounded-md'

export function Step1Source() {
  const exportName = usePartnerExportWizardStore(s => s.exportName)
  const source = usePartnerExportWizardStore(s => s.source)
  const patch = usePartnerExportWizardStore(s => s.patch)
  const setSource = usePartnerExportWizardStore(s => s.setSource)
  const layer = source?.layer ?? 'silver'
  const datasetsQ = useCatalogDatasets(layer)
  const filtered = datasetsQ.data ?? []

  return (
    <div data-testid="wizard-step-0" className="space-y-4">
      <label className="block">
        <span className="text-sm text-slate-700">Export name</span>
        <input aria-label="Export name" value={exportName} onChange={e => patch({ exportName: e.target.value })} className={INPUT} placeholder="Partner A daily CSV" />
      </label>

      <fieldset>
        <legend className="text-sm text-slate-700 mb-1">Layer</legend>
        {(['silver', 'gold'] as const).map(L => (
          <label key={L} className="inline-flex items-center gap-2 mr-4 capitalize">
            <input type="radio" name="layer" checked={layer === L} onChange={() => setSource({ layer: L, dataset: source?.dataset ?? '' })} />{L}
          </label>
        ))}
      </fieldset>

      <label className="block">
        <span className="text-sm text-slate-700">Dataset</span>
        <select aria-label="Dataset" value={source?.dataset ?? ''} onChange={e => setSource({ layer, dataset: e.target.value })} className={INPUT}>
          <option value="">— pick a dataset —</option>
          {filtered.map(d => <option key={d.dataset} value={d.dataset}>{d.dataset}</option>)}
        </select>
      </label>
    </div>
  )
}
