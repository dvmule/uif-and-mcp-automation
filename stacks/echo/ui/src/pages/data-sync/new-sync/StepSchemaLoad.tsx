// Step 3: what Analyze found (recommendations, columns) and how each run reads the table.
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Note } from '@/components/relay'
import { AnalysisRecommendations } from '@/components/pipeline/AnalysisRecommendations'
import { TONE_CLASS } from '@/lib/statusVocabulary'
import { cn, suggestWatermarkCol, suggestWatermarkFallbackCol, suggestPartitionCol } from '@/lib/utils'
import { useWizardStore } from '@/store/wizardStore'
import { ConfidenceBadge, Hint, Section } from './fields'

const TILE = 'rounded-brand border border-line bg-paper-head p-2.5'

export function StepSchemaLoad() {
  const { analyzeResult, extractMode, watermarkCol, watermarkFallbackCol, partitionCol,
    jdbcLowerBound, jdbcUpperBound, jdbcPartitions, patch } = useWizardStore()
  if (!analyzeResult) return null

  const r = analyzeResult
  const hasRecs = !!(r.partition_strategy || r.worker_type || r.num_workers || r.total_gb)
  const cols = r.columns
  const suggestedWm = suggestWatermarkCol(cols)
  const suggestedPart = suggestPartitionCol(cols)
  const suggestedWmFb = suggestWatermarkFallbackCol(cols, watermarkCol)

  function applyRecommendations() {
    patch({
      partitionCol: r.jdbc_partition_col ?? r.partition_id_col ?? r.partition_hash_col ?? null,
      jdbcLowerBound: r.jdbc_lower_bound ?? null,
      jdbcUpperBound: r.jdbc_upper_bound ?? null,
      jdbcPartitions: r.jdbc_partitions ?? 8,
      workerType: r.worker_type ?? 'G.1X',
      numWorkers: r.num_workers ?? 2,
    })
  }

  return (
    <div className="space-y-5">
      <div className="flex items-center gap-4 text-meta text-ink-mute">
        <span>{r.columns.length} columns</span>
        <span>{r.row_count.toLocaleString()} rows</span>
        {r.total_gb !== null && r.total_gb !== undefined && <span>{r.total_gb.toFixed(2)} GB</span>}
      </div>

      {hasRecs && (
        <Section title="Recommended from table analysis">
          <Note>These are heuristics over the column names and types, not AI.</Note>
          <div className="space-y-3">
            <div className="flex justify-end">
              <Button type="button" size="sm" variant="outline" onClick={applyRecommendations}>
                Apply recommendations
              </Button>
            </div>
            <div className="grid grid-cols-2 gap-3 text-meta">
              {(r.total_gb !== null && r.total_gb !== undefined) && (
                <div className={TILE}>
                  <div className="font-mono text-ink-soft">Table size</div>
                  <div className="mt-0.5 font-semibold text-ink">
                    {r.total_gb.toFixed(2)} GB / {r.row_count.toLocaleString()} rows
                  </div>
                </div>
              )}
              {r.partition_strategy && (
                <div className={TILE}>
                  <div className="font-mono text-ink-soft">Partition strategy</div>
                  <span className={cn('mt-1 inline-block rounded-full px-2 py-0.5 text-label font-bold', TONE_CLASS.live)}>
                    {r.partition_strategy.replace(/_/g, ' ')}
                  </span>
                </div>
              )}
              {(r.worker_type || r.num_workers) && (
                <div className={TILE}>
                  <div className="font-mono text-ink-soft">Glue sizing</div>
                  <div className="mt-0.5 font-semibold text-ink">
                    {r.worker_type ?? '—'} × {r.num_workers ?? '—'} workers
                  </div>
                </div>
              )}
              {r.jdbc_partition_col && (
                <div className={TILE}>
                  <div className="font-mono text-ink-soft">JDBC partition col</div>
                  <div className="mt-0.5 font-mono text-accent">{r.jdbc_partition_col}</div>
                  {(r.jdbc_lower_bound !== null && r.jdbc_lower_bound !== undefined &&
                    r.jdbc_upper_bound !== null && r.jdbc_upper_bound !== undefined) && (
                    <div className="mt-0.5 text-ink-mute">
                      {r.jdbc_lower_bound.toLocaleString()} – {r.jdbc_upper_bound.toLocaleString()}
                    </div>
                  )}
                </div>
              )}
              {(r.jdbc_predicates_count !== null && r.jdbc_predicates_count !== undefined && r.jdbc_predicates_count > 0) && (
                <div className={TILE}>
                  <div className="font-mono text-ink-soft">Predicates</div>
                  <div className="mt-0.5 font-semibold text-ink">{r.jdbc_predicates_count} predicates</div>
                </div>
              )}
            </div>
          </div>
          <AnalysisRecommendations result={r} />
        </Section>
      )}

      <Section title="Extraction" sub="Configure how data will be extracted from Oracle.">
        <div className="grid gap-x-6 gap-y-4 md:grid-cols-2">
          <div className="space-y-2">
            <Label>
              Extract Mode
              <Hint text="Incremental uses a watermark column to only pull new/changed rows. Full re-extracts everything." />
            </Label>
            <Select value={extractMode} onValueChange={v => patch({ extractMode: v as 'full' | 'incremental' })}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="incremental">Incremental (recommended)</SelectItem>
                <SelectItem value="full">Full Extract</SelectItem>
              </SelectContent>
            </Select>
          </div>

          {extractMode === 'incremental' && (
            <div className="space-y-2">
              <Label>
                Watermark Column
                {suggestedWm === watermarkCol && <ConfidenceBadge level="high" />}
              </Label>
              <Select value={watermarkCol ?? ''} onValueChange={v => patch({ watermarkCol: v || null })}>
                <SelectTrigger>
                  <SelectValue placeholder="Select column..." />
                </SelectTrigger>
                <SelectContent>
                  {cols.map(c => (
                    <SelectItem key={c.name} value={c.name}>
                      {c.name}
                      <span className="ml-2 text-meta text-ink-mute">{c.data_type}</span>
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          )}

          {extractMode === 'incremental' && (
            <div className="space-y-2">
              <Label>
                Watermark Fallback Column
                <Hint text="Used when the primary watermark is NULL. Rows are included if COALESCE(watermark, fallback) is after the watermark value." />
                {suggestedWmFb === watermarkFallbackCol && watermarkFallbackCol && (
                  <ConfidenceBadge level="medium" />
                )}
              </Label>
              <Select
                value={watermarkFallbackCol ?? 'none'}
                onValueChange={v => patch({ watermarkFallbackCol: v === 'none' ? null : v })}
              >
                <SelectTrigger>
                  <SelectValue placeholder="None (optional)" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="none">None</SelectItem>
                  {cols
                    .filter(c => c.name.toUpperCase() !== (watermarkCol ?? '').toUpperCase())
                    .map(c => (
                      <SelectItem key={c.name} value={c.name}>
                        {c.name}
                        <span className="ml-2 text-meta text-ink-mute">{c.data_type}</span>
                      </SelectItem>
                    ))}
                </SelectContent>
              </Select>
            </div>
          )}

          <div className="space-y-2">
            <Label>
              JDBC Partition Column
              <Hint text="Used to parallelize the Oracle read. Pick a numeric ID column with even distribution." />
              {suggestedPart === partitionCol && partitionCol && <ConfidenceBadge level="medium" />}
            </Label>
            <Select value={partitionCol ?? 'none'} onValueChange={v => patch({ partitionCol: v === 'none' ? null : v })}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="none">None (single partition)</SelectItem>
                {cols.filter(c => ['NUMBER', 'INTEGER', 'INT'].some(t => c.data_type.toUpperCase().includes(t))).map(c => (
                  <SelectItem key={c.name} value={c.name}>{c.name}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>

        {partitionCol && partitionCol !== 'none' && (
          <div className="grid grid-cols-3 gap-3">
            <div className="space-y-1">
              <Label className="text-meta">Lower Bound</Label>
              <Input
                type="number"
                value={jdbcLowerBound ?? ''}
                onChange={e => patch({ jdbcLowerBound: e.target.value ? Number(e.target.value) : null })}
              />
            </div>
            <div className="space-y-1">
              <Label className="text-meta">Upper Bound</Label>
              <Input
                type="number"
                value={jdbcUpperBound ?? ''}
                onChange={e => patch({ jdbcUpperBound: e.target.value ? Number(e.target.value) : null })}
              />
            </div>
            <div className="space-y-1">
              <Label className="text-meta">Partitions</Label>
              <Input
                type="number"
                min={1}
                max={32}
                value={jdbcPartitions}
                onChange={e => patch({ jdbcPartitions: Number(e.target.value) })}
              />
            </div>
          </div>
        )}
      </Section>

      <Section title="Columns">
        <div className="max-h-96 overflow-y-auto rounded-brand border border-line">
          <table className="w-full text-body">
            <thead className="sticky top-0 border-b border-line bg-paper-head">
              <tr>
                <th className="px-3 py-2 text-left text-label font-semibold uppercase tracking-wider text-ink-mute">Column</th>
                <th className="px-3 py-2 text-left text-label font-semibold uppercase tracking-wider text-ink-mute">Type</th>
                <th className="px-3 py-2 text-left text-label font-semibold uppercase tracking-wider text-ink-mute">Nullable</th>
                <th className="px-3 py-2 text-left text-label font-semibold uppercase tracking-wider text-ink-mute">Sample</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {r.columns.map(col => (
                <tr key={col.name} className="hover:bg-paper-head">
                  <td className="px-3 py-2 font-mono text-meta text-accent">{col.name}</td>
                  <td className="px-3 py-2 text-ink-soft">{col.data_type}</td>
                  <td className="px-3 py-2">
                    <span className={cn('text-meta', col.nullable ? 'text-ink-mute' : 'font-medium text-signal-ok')}>
                      {col.nullable ? 'NULL' : 'NOT NULL'}
                    </span>
                  </td>
                  <td className="max-w-32 truncate px-3 py-2 text-meta text-ink-mute">
                    {col.sample_values?.slice(0, 2).join(', ') ?? '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Section>
    </div>
  )
}
