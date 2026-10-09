import { usePartnerExportWizardStore } from '@/store/partnerExportWizardStore'
import { FormatChip } from '@/components/partner-export/FormatChip'
import { MappingTable } from '@/components/partner-export/MappingTable'
import { OutputPreview } from '@/components/partner-export/OutputPreview'
import type { ExportFormat } from '@/lib/types'

const FORMATS: ExportFormat[] = ['csv', 'json', 'parquet', 'fixed_width', 'xml']

const FALLBACK_ROWS = [
  { ID: 1, NAME: 'Alpha', AMOUNT: '10.50' },
  { ID: 2, NAME: 'Beta', AMOUNT: '7.00' },
  { ID: 3, NAME: 'Gamma', AMOUNT: '0' },
]

export function Step2FormatMapping() {
  const format = usePartnerExportWizardStore(s => s.format)
  const mapping = usePartnerExportWizardStore(s => s.mapping)
  const advancedOpen = usePartnerExportWizardStore(s => s.advancedOpen)
  const setFormatKind = usePartnerExportWizardStore(s => s.setFormatKind)
  const patchFormat = usePartnerExportWizardStore(s => s.patchFormat)
  const addMappingRow = usePartnerExportWizardStore(s => s.addMappingRow)
  const updateMappingRow = usePartnerExportWizardStore(s => s.updateMappingRow)
  const removeMappingRow = usePartnerExportWizardStore(s => s.removeMappingRow)
  const toggleAdvanced = usePartnerExportWizardStore(s => s.toggleAdvanced)

  const TA = 'mt-1 w-full px-3 py-2 text-xs font-mono border rounded-md'
  return (
    <div data-testid="wizard-step-1" className="space-y-4">
      <div>
        <div className="text-sm text-slate-700 mb-2">Format</div>
        <div className="flex flex-wrap gap-2">
          {FORMATS.map(f => (
            <FormatChip key={f} format={f} selected={format.kind === f} onSelect={setFormatKind} />
          ))}
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div>
          <div className="text-sm text-slate-700 mb-2">Field mapping</div>
          <MappingTable rows={mapping} onAdd={addMappingRow} onUpdate={updateMappingRow} onRemove={removeMappingRow} />
        </div>
        <div>
          <div className="text-sm text-slate-700 mb-2">Output preview</div>
          <OutputPreview format={format} mapping={mapping} sampleRows={FALLBACK_ROWS} />
        </div>
      </div>

      <div>
        <button type="button" onClick={toggleAdvanced} aria-expanded={advancedOpen} className="text-sm text-indigo-700 hover:underline">{advancedOpen ? '▾' : '▸'} Advanced (optional)</button>
        {advancedOpen && (
          <div data-testid="advanced-block" className="mt-3 space-y-4 border rounded-md p-4 bg-slate-50">
            <label className="block"><span className="text-sm text-slate-700">SQL transform (pre-mapping)</span>
              <textarea aria-label="SQL transform" value={format.sql_transform ?? ''} onChange={e => patchFormat({ sql_transform: e.target.value || null })} placeholder="SELECT id, name FROM source WHERE active = true" className={TA} rows={4} />
            </label>
            <div className="border-l-4 border-amber-400 bg-amber-50 px-3 py-2 text-xs text-amber-800">Python / Jinja template — stub for v1.1. The backend rejects non-empty values.</div>
            <label className="block opacity-60"><span className="text-sm text-slate-700">Python / Jinja template</span>
              <textarea aria-label="Python template" value={format.python_template ?? ''} onChange={e => patchFormat({ python_template: e.target.value || null })} disabled placeholder="# coming v1.1" className={TA} rows={4} />
            </label>
          </div>
        )}
      </div>
    </div>
  )
}
