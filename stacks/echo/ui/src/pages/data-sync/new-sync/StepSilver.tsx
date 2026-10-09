// Step 4: optional silver layer (dedup + MERGE into Iceberg), and its BigQuery target on GCP.
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
import { cn, suggestPkCols } from '@/lib/utils'
import { echoGlueDatabase } from '@/lib/echoPaths'
import { useEnvStore } from '@/store/envStore'
import { useWizardStore } from '@/store/wizardStore'
import { BQ_LOCATIONS, validateBqDataset, validateBqLocation, validateBqProject } from '@/lib/validation'
import { ConfidenceBadge, FieldError, FieldHint, Hint } from './fields'

export function StepSilver() {
  const env = useEnvStore(s => s.env)
  const {
    analyzeResult, silverEnabled, pkCols, softDeleteCol, destinationCloud, connection,
    bqProject, bqDataset, bqLocation, patch,
  } = useWizardStore()
  const cols = analyzeResult?.columns ?? []
  const suggested = suggestPkCols(cols)

  function togglePk(colName: string) {
    patch({
      pkCols: pkCols.includes(colName)
        ? pkCols.filter(c => c !== colName)
        : [...pkCols, colName],
    })
  }

  const isGcp = destinationCloud === 'gcp'
  const bqProjectErr = isGcp && silverEnabled && bqProject ? validateBqProject(bqProject) : null
  const bqDatasetErr = isGcp && silverEnabled && bqDataset ? validateBqDataset(bqDataset) : null
  const bqLocationErr = isGcp && silverEnabled ? validateBqLocation(bqLocation) : null

  return (
    <div className="space-y-5">
      <FieldHint>Optional. Enables deduplication and MERGE into an Iceberg table.</FieldHint>

      <div className="flex items-center justify-between rounded-xl border border-line p-4">
        <div>
          <p className="text-body font-medium text-ink">Enable Silver Transform</p>
          <FieldHint>Creates a deduplicated Iceberg table from Bronze Parquet</FieldHint>
        </div>
        <Switch checked={silverEnabled} onCheckedChange={v => patch({ silverEnabled: v })} aria-label="Enable Silver Transform" />
      </div>

      {silverEnabled && (
        <>
          <div className="space-y-2">
            <Label>
              Primary Key Columns
              {suggested.every(s => pkCols.includes(s)) && suggested.length > 0 && (
                <ConfidenceBadge level="high" />
              )}
            </Label>
            <div className="flex flex-wrap gap-2 rounded-brand border border-line p-3">
              {cols.map(c => (
                <button
                  type="button"
                  key={c.name}
                  onClick={() => togglePk(c.name)}
                  aria-pressed={pkCols.includes(c.name)}
                  className={cn(
                    'rounded-md border px-2.5 py-1 text-meta font-medium transition-colors',
                    pkCols.includes(c.name)
                      ? 'border-accent bg-accent-soft text-accent'
                      : 'border-line text-ink-soft hover:border-accent',
                  )}
                >
                  {c.name}
                </button>
              ))}
            </div>
          </div>

          <div className="space-y-2">
            <Label>
              Soft Delete Flag Column
              <Hint text="If a row has this column set to the inactive value, it will be deleted from Silver." />
            </Label>
            <Select
              value={softDeleteCol ?? 'none'}
              onValueChange={v => patch({ softDeleteCol: v === 'none' ? null : v })}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="none">None</SelectItem>
                {cols.map(c => (
                  <SelectItem key={c.name} value={c.name}>{c.name}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          {isGcp && (
            <div className="space-y-4 rounded-xl border border-line p-4">
              <div>
                <p className="text-body font-medium text-ink">BigQuery Target</p>
                <FieldHint>BigLake reads Iceberg Silver via this BigQuery dataset.</FieldHint>
              </div>

              <div className="space-y-2">
                <Label>BigQuery Project</Label>
                <Input
                  placeholder="informatica-etl-nonprd"
                  value={bqProject}
                  onChange={e => patch({ bqProject: e.target.value.trim() })}
                />
                {bqProjectErr && <FieldError>{bqProjectErr}</FieldError>}
              </div>

              <div className="space-y-2">
                <Label>BigQuery Dataset</Label>
                <Input
                  placeholder={connection ? echoGlueDatabase(env, 'silver', connection.name) : 'echo_<env>_silver_<domain>'}
                  value={bqDataset}
                  onChange={e => patch({ bqDataset: e.target.value.trim() })}
                />
                {bqDatasetErr && <FieldError>{bqDatasetErr}</FieldError>}
              </div>

              <div className="space-y-2">
                <Label>BigQuery Location</Label>
                <Select
                  value={bqLocation || 'EU'}
                  onValueChange={v => patch({ bqLocation: v })}
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {BQ_LOCATIONS.map(loc => (
                      <SelectItem key={loc} value={loc}>{loc}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                {bqLocationErr && <FieldError>{bqLocationErr}</FieldError>}
              </div>
            </div>
          )}
        </>
      )}
    </div>
  )
}
