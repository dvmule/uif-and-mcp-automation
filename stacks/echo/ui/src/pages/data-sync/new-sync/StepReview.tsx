// Step 6: the sync id, a summary of every answer, and the exact config Deploy will save.
import type { ReactNode } from 'react'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { echoBronzeBucket, echoSilverBucket } from '@/lib/echoPaths'
import { buildPipelineConfig } from '@/lib/newSyncConfig'
import { useEnvStore } from '@/store/envStore'
import { useWizardStore } from '@/store/wizardStore'
import { FieldHint } from './fields'
import { scheduleSummary, silverTarget } from './steps'

const truncate = (v: string, n = 56) => (v.length > n ? `${v.slice(0, n - 1)}…` : v)

function Kv({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="min-w-0 text-body">
      <span className="block text-label text-ink-mute">{label}</span>
      <div className="break-all font-semibold text-ink">{children}</div>
    </div>
  )
}

function Sub({ children }: { children: ReactNode }) {
  return <span className="block font-mono text-label font-normal text-ink-mute">{children}</span>
}

export function StepReview() {
  const env = useEnvStore(e => e.env)
  const s = useWizardStore()
  const isGcp = s.destinationCloud === 'gcp'
  const tableSlug = s.analyzeResult?.table.toLowerCase().replace('.', '/') ?? ''
  const bronzeS3 = s.connection ? `s3://${echoBronzeBucket(env)}/data/${tableSlug}/` : '—'
  const silverS3 = s.connection ? `s3://${echoSilverBucket(env)}/data/${tableSlug}/` : '—'

  let configJson: string
  try {
    configJson = JSON.stringify(buildPipelineConfig(s, env), null, 2)
  } catch (e) {
    configJson = e instanceof Error ? e.message : 'Config cannot be built yet'
  }

  const load = [
    s.extractMode,
    s.extractMode === 'incremental' ? s.watermarkCol ?? '—' : null,
    s.watermarkFallbackCol ? `fallback ${s.watermarkFallbackCol}` : null,
    s.partitionCol ? `${s.partitionCol} · ${s.jdbcPartitions} partitions` : 'single partition',
  ].filter(Boolean).join(' · ')

  return (
    <div className="space-y-5">
      <div className="space-y-2">
        <Label htmlFor="new-sync-id">Sync id</Label>
        <Input
          id="new-sync-id"
          className="font-mono"
          value={s.pipelineId}
          onChange={e => s.patch({ pipelineId: e.target.value })}
        />
        <FieldHint>Generated from the table name. Editable; must be unique.</FieldHint>
      </div>

      <div className="grid gap-x-6 gap-y-3.5 md:grid-cols-2" data-testid="review-summary">
        <Kv label="Source">
          {s.connection?.name ?? '—'} · {s.analyzeResult?.table ?? '—'} · {s.analyzeResult?.columns.length ?? 0} columns
          <Sub>{truncate(s.connection?.arn ?? '')}</Sub>
        </Kv>
        <Kv label="Destination">
          {isGcp ? 'GCP' : 'AWS'} · {s.silverEnabled ? 'bronze + silver' : 'bronze only'}
        </Kv>
        <Kv label="Load">{load}</Kv>
        <Kv label="Silver">
          {s.silverEnabled ? (
            <>
              {silverTarget(s, env)} · PK {s.pkCols.join(', ') || '—'}
              {isGcp && <Sub>BigQuery {s.bqProject || '—'} · {s.bqLocation || '—'}</Sub>}
            </>
          ) : 'off'}
        </Kv>
        <Kv label="Schedule">
          {s.frequency === 'daily' ? `${scheduleSummary(s)} (slot assigned at registration)` : s.frequency}
        </Kv>
        <Kv label="Quality">
          {s.notNullCols.length} not-null columns · {s.minRowsExpected !== null ? `≥ ${s.minRowsExpected.toLocaleString()} rows` : 'no row minimum'}
        </Kv>
        <Kv label="Storage">
          {isGcp ? (
            <>
              <Sub>{s.bronzeGcsPath || '—'}</Sub>
              {s.silverEnabled && <Sub>{s.silverGcsPath || '—'}</Sub>}
              <Sub>secret {truncate(s.gcpSecretArn || '—')}</Sub>
            </>
          ) : (
            <>
              <Sub>{bronzeS3}</Sub>
              {s.silverEnabled && <Sub>{silverS3}</Sub>}
              <Sub>GCS replication: {s.replicationEnabled ? `gs://${s.gcsBucket}/${s.gcsPrefix}` : 'No'}</Sub>
              {s.silverBqEnabled && (
                <Sub>
                  Silver→BQ: gs://{s.silverBqBucket}/{s.silverBqPrefix} · {s.silverBqProject || '—'}.{s.silverBqDataset || '—'} · {s.silverBqLocation || '—'}
                </Sub>
              )}
            </>
          )}
        </Kv>
        <Kv label="Runtime">{s.workerType} × {s.numWorkers} · 120 min timeout</Kv>
      </div>

      <details>
        <summary className="cursor-pointer text-meta font-semibold text-accent">Config JSON</summary>
        <pre className="mt-1.5 max-h-96 overflow-auto rounded-brand border border-line bg-paper-head px-3 py-2 font-mono text-label text-ink">
          {configJson}
        </pre>
      </details>
    </div>
  )
}
