// Step 5: where files land (and optional GCS/BigQuery mirrors), then schedule, quality checks and Glue sizing.
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
import { cn } from '@/lib/utils'
import { echoBronzeBucket, echoSilverBucket, echoGlueDatabase, echoGcsBronzeBucket, echoGcsSilverBucket } from '@/lib/echoPaths'
import { useEnvStore } from '@/store/envStore'
import { useWizardStore } from '@/store/wizardStore'
import { useGcsConnections } from '@/hooks/useGcsConnections'
import { useSilverBqConnections } from '@/hooks/useSilverBqConnections'
import { BQ_LOCATIONS, validateGcsPath, validateSecretArn } from '@/lib/validation'
import { ConfidenceBadge, FieldError, FieldHint, Hint, Section } from './fields'
import { DAILY_SLOT_WINDOW } from './steps'

export function StepScheduleStorage() {
  return (
    <div className="space-y-5">
      <StorageFields />
      <ScheduleFields />
    </div>
  )
}

const PATH_BOX = 'break-all rounded-brand border border-line bg-paper-head px-3 py-2 font-mono text-meta text-ink-soft'

function StorageFields() {
  const env = useEnvStore(s => s.env)
  const {
    analyzeResult, connection, destinationCloud,
    replicationEnabled, gcsBucket, gcsPrefix, gcsSecretArn,
    bronzeGcsPath, silverGcsPath, gcpSecretArn, silverEnabled,
    silverBqEnabled, silverBqBucket, silverBqPrefix, silverBqSecretArn,
    silverBqProject, silverBqDataset, silverBqLocation,
    patch,
  } = useWizardStore()
  const { data: gcsConns, isLoading } = useGcsConnections()
  const { data: silverBqConns, isLoading: silverBqLoading } = useSilverBqConnections()
  const tableLower = analyzeResult?.table.toLowerCase().replace('.', '/') ?? ''

  if (destinationCloud === 'gcp') {
    const bronzeErr = bronzeGcsPath ? validateGcsPath(bronzeGcsPath, 'Bronze GCS path') : null
    const silverErr = silverEnabled && silverGcsPath ? validateGcsPath(silverGcsPath, 'Silver GCS path') : null
    const secretErr = gcpSecretArn ? validateSecretArn(gcpSecretArn) : null

    return (
      <Section
        title="Storage Paths"
        sub="GCP-native sync. Bronze Parquet and Silver Iceberg both write to GCS; BigLake reads Silver from the same bucket."
      >
        <div className="space-y-2">
          <Label>
            Bronze GCS Path
            <Hint text="gs:// URI ending in /. The Glue Bronze job writes Parquet here." />
          </Label>
          <Input
            placeholder={`gs://${echoGcsBronzeBucket(env)}/${tableLower || '<domain>/<table>'}/`}
            value={bronzeGcsPath}
            onChange={e => patch({ bronzeGcsPath: e.target.value.trim() })}
            className="font-mono"
          />
          {bronzeErr && <FieldError>{bronzeErr}</FieldError>}
        </div>

        {silverEnabled && (
          <div className="space-y-2">
            <Label>
              Silver GCS Path
              <Hint text="gs:// URI ending in /. Holds the Iceberg table data + metadata that BigLake reads." />
            </Label>
            <Input
              placeholder={`gs://${echoGcsSilverBucket(env)}/<dataset>/<table>/`}
              value={silverGcsPath}
              onChange={e => patch({ silverGcsPath: e.target.value.trim() })}
              className="font-mono"
            />
            {silverErr && <FieldError>{silverErr}</FieldError>}
          </div>
        )}

        <div className="space-y-2">
          <Label>
            GCP Service Account Secret
            <Hint text="AWS Secrets Manager entry holding the GCP service-account JSON. Same SA powers Bronze writes, Silver writes, and BigQuery jobs." />
          </Label>
          {isLoading ? (
            <FieldHint>Loading secrets…</FieldHint>
          ) : gcsConns && gcsConns.length > 0 ? (
            <Select value={gcpSecretArn || 'none'} onValueChange={v => patch({ gcpSecretArn: v === 'none' ? '' : v })}>
              <SelectTrigger>
                <SelectValue placeholder="Select a GCP service account secret" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="none">— select —</SelectItem>
                {gcsConns.map(c => (
                  <SelectItem key={c.arn} value={c.arn}>{c.name}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          ) : (
            <>
              <FieldHint>
                No secrets tagged <code>purpose=gcs-replication</code> found. Paste an ARN below or provision one with
                <code className="mx-1">scripts/upload_gcp_secret.py</code>.
              </FieldHint>
              <Input
                placeholder="arn:aws:secretsmanager:eu-west-1:…:secret:gcp-…"
                value={gcpSecretArn}
                onChange={e => patch({ gcpSecretArn: e.target.value.trim() })}
                className="font-mono"
              />
            </>
          )}
          {secretErr && <FieldError>{secretErr}</FieldError>}
        </div>
      </Section>
    )
  }

  // AWS: bronze/silver paths are computed and shown read-only, plus the optional mirrors.
  const bronzeS3Preview = connection && analyzeResult
    ? `s3://${echoBronzeBucket(env)}/data/${tableLower}/`
    : `s3://${echoBronzeBucket(env)}/data/<schema>/<table>/`
  const silverS3Preview = connection && analyzeResult
    ? `s3://${echoSilverBucket(env)}/data/${tableLower}/`
    : `s3://${echoSilverBucket(env)}/data/<schema>/<table>/`
  const suggestedPrefix = tableLower ? `bronze/${tableLower}/` : ''

  function onToggle(v: boolean) {
    patch({
      replicationEnabled: v,
      gcsPrefix: v && !gcsPrefix ? suggestedPrefix : gcsPrefix,
    })
  }

  return (
    <Section
      title="Storage & Replication"
      sub="AWS-native sync. Bronze + Silver paths are derived from the connection name and table. Optionally mirror Bronze to GCS."
    >
      <div className="space-y-2">
        <Label>Bronze S3 Path (computed)</Label>
        <div className={PATH_BOX}>{bronzeS3Preview}</div>
      </div>

      {silverEnabled && (
        <div className="space-y-2">
          <Label>Silver S3 Path (computed)</Label>
          <div className={PATH_BOX}>{silverS3Preview}</div>
        </div>
      )}

      <div className="flex items-center justify-between rounded-xl border border-line p-4">
        <div>
          <p className="text-body font-medium text-ink">Replicate Bronze to GCS</p>
          <FieldHint>Mirror Parquet to a GCS bucket so GCP consumers can read it. S3 stays source-of-truth.</FieldHint>
        </div>
        <Switch checked={replicationEnabled} onCheckedChange={onToggle} aria-label="Replicate Bronze to GCS" />
      </div>

      {replicationEnabled && (
        <>
          <div className="space-y-2">
            <Label>
              GCS Bucket
              <Hint text="Target bucket name. Must already exist and the service account below must have objectAdmin on it." />
            </Label>
            <Input
              placeholder={echoGcsBronzeBucket(env)}
              value={gcsBucket}
              onChange={e => patch({ gcsBucket: e.target.value.trim() })}
            />
          </div>

          <div className="space-y-2">
            <Label>
              Object Prefix
              <Hint text="Object key prefix under the bucket. Must end with '/'. Defaults to bronze/{schema}/{table}/." />
            </Label>
            <Input
              placeholder={suggestedPrefix || 'bronze/schema/table/'}
              value={gcsPrefix}
              onChange={e => patch({ gcsPrefix: e.target.value })}
            />
          </div>

          <div className="space-y-2">
            <Label>
              Service Account Secret
              <Hint text="AWS Secrets Manager entry holding the GCP service-account JSON. Provision with scripts/upload_gcp_secret.py." />
            </Label>
            {isLoading ? (
              <FieldHint>Loading secrets…</FieldHint>
            ) : gcsConns && gcsConns.length > 0 ? (
              <Select value={gcsSecretArn || 'none'} onValueChange={v => patch({ gcsSecretArn: v === 'none' ? '' : v })}>
                <SelectTrigger>
                  <SelectValue placeholder="Select a GCP service account secret" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="none">— select —</SelectItem>
                  {gcsConns.map(c => (
                    <SelectItem key={c.arn} value={c.arn}>{c.name}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            ) : (
              <>
                <FieldHint>
                  No secrets tagged <code>purpose=gcs-replication</code> found. Paste an ARN below or provision one with
                  <code className="mx-1">scripts/upload_gcp_secret.py</code>.
                </FieldHint>
                <Input
                  placeholder="arn:aws:secretsmanager:eu-west-1:…:secret:gcp-…"
                  value={gcsSecretArn}
                  onChange={e => patch({ gcsSecretArn: e.target.value.trim() })}
                />
              </>
            )}
          </div>
        </>
      )}

      {silverEnabled && (
        <div className="rounded-xl border border-line">
          <div className="flex items-center justify-between p-4">
            <div>
              <p className="text-body font-medium text-ink">Silver → BigQuery (BigLake mirror)</p>
              <FieldHint>Mirror Silver Iceberg to GCS + register a BigLake external table.</FieldHint>
            </div>
            <Switch
              checked={silverBqEnabled}
              onCheckedChange={v => patch({ silverBqEnabled: v })}
              aria-label="Silver to BigQuery"
            />
          </div>

          {silverBqEnabled && (
            <div className="space-y-4 border-t border-line p-4">
              <div className="space-y-2">
                <Label>
                  GCS Bucket
                  <Hint text="Target bucket for the Silver Iceberg mirror. Must already exist." />
                </Label>
                <Input
                  placeholder={echoGcsSilverBucket(env)}
                  value={silverBqBucket}
                  onChange={e => patch({ silverBqBucket: e.target.value.trim() })}
                />
              </div>

              <div className="space-y-2">
                <Label>
                  Object Prefix
                  <Hint text="Object key prefix under the bucket. Must end with '/'." />
                </Label>
                <Input
                  placeholder="silver/<dataset>/<table>/"
                  value={silverBqPrefix}
                  onChange={e => patch({ silverBqPrefix: e.target.value })}
                />
              </div>

              <div className="space-y-2">
                <Label>
                  GCP Service Account Secret
                  <Hint text="AWS Secrets Manager entry holding the GCP service-account JSON (purpose=silver-bq)." />
                </Label>
                {silverBqLoading ? (
                  <FieldHint>Loading secrets…</FieldHint>
                ) : silverBqConns && silverBqConns.length > 0 ? (
                  <Select
                    value={silverBqSecretArn || 'none'}
                    onValueChange={v => patch({ silverBqSecretArn: v === 'none' ? '' : v })}
                  >
                    <SelectTrigger>
                      <SelectValue placeholder="Select a Silver→BQ secret" />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="none">— select —</SelectItem>
                      {silverBqConns.map(c => (
                        <SelectItem key={c.arn} value={c.arn}>{c.name}</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                ) : (
                  <>
                    <FieldHint>
                      No secrets tagged <code>purpose=silver-bq</code> found. Paste an ARN below.
                    </FieldHint>
                    <Input
                      placeholder="arn:aws:secretsmanager:eu-west-1:…:secret:gcp-…"
                      value={silverBqSecretArn}
                      onChange={e => patch({ silverBqSecretArn: e.target.value.trim() })}
                      className="font-mono"
                    />
                  </>
                )}
              </div>

              <div className="space-y-2">
                <Label>BigQuery Project</Label>
                <Input
                  placeholder="informatica-etl-nonprd"
                  value={silverBqProject}
                  onChange={e => patch({ silverBqProject: e.target.value.trim() })}
                />
              </div>

              <div className="space-y-2">
                <Label>BigQuery Dataset</Label>
                <Input
                  placeholder={connection ? echoGlueDatabase(env, 'silver', connection.name) : 'echo_<env>_silver_<domain>'}
                  value={silverBqDataset}
                  onChange={e => patch({ silverBqDataset: e.target.value.trim() })}
                />
              </div>

              <div className="space-y-2">
                <Label>BigQuery Location</Label>
                <Select
                  value={silverBqLocation || 'EU'}
                  onValueChange={v => patch({ silverBqLocation: v })}
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
              </div>
            </div>
          )}
        </div>
      )}
    </Section>
  )
}

function ScheduleFields() {
  const { analyzeResult, frequency, notNullCols, minRowsExpected,
    workerType, numWorkers, patch } = useWizardStore()
  const cols = analyzeResult?.columns ?? []
  const notNullSuggested = cols.filter(c => !c.nullable).map(c => c.name)

  function toggleNullCol(name: string) {
    patch({
      notNullCols: notNullCols.includes(name)
        ? notNullCols.filter(c => c !== name)
        : [...notNullCols, name],
    })
  }

  return (
    <Section title="Schedule & Quality">
      <div className="space-y-2">
        <Label>Schedule Frequency</Label>
        <Select value={frequency} onValueChange={v => patch({ frequency: v as 'hourly' | 'daily' | 'weekly' })}>
          <SelectTrigger>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="hourly">Hourly</SelectItem>
            <SelectItem value="daily">Daily</SelectItem>
            <SelectItem value="weekly">Weekly</SelectItem>
          </SelectContent>
        </Select>
        {frequency === 'daily' && (
          <p className="text-meta text-accent">
            Daily syncs run in staggered 30-minute slots from {DAILY_SLOT_WINDOW}. A slot is assigned automatically when the pipeline is registered.
          </p>
        )}
      </div>

      <div className="space-y-2">
        <Label>
          Not-Null Checks
          <Hint text="Pre-selected from Oracle NOT NULL constraints. Uncheck to remove." />
          {notNullSuggested.length > 0 && <ConfidenceBadge level="high" />}
        </Label>
        <div className="flex max-h-40 flex-wrap gap-2 overflow-y-auto rounded-brand border border-line p-3">
          {cols.map(c => (
            <button
              type="button"
              key={c.name}
              onClick={() => toggleNullCol(c.name)}
              aria-pressed={notNullCols.includes(c.name)}
              className={cn(
                'rounded-md border px-2 py-0.5 text-meta transition-colors',
                notNullCols.includes(c.name)
                  ? 'border-accent bg-accent-soft text-accent'
                  : 'border-line text-ink-mute hover:border-accent',
              )}
            >
              {c.name}
            </button>
          ))}
        </div>
      </div>

      <div className="space-y-2">
        <Label>Minimum Rows Expected</Label>
        <Input
          type="number"
          value={minRowsExpected ?? ''}
          onChange={e => patch({ minRowsExpected: e.target.value ? Number(e.target.value) : null })}
          placeholder="e.g. 1000"
        />
        <FieldHint>Pre-filled at 90% of current row count ({analyzeResult?.row_count.toLocaleString() ?? '?'})</FieldHint>
      </div>

      <div className="grid grid-cols-2 gap-4">
        <div className="space-y-2">
          <Label>Glue Worker Type</Label>
          <Select value={workerType} onValueChange={v => patch({ workerType: v as 'G.1X' | 'G.2X' | 'G.4X' })}>
            <SelectTrigger><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="G.1X">G.1X (4 vCPU, 16 GB)</SelectItem>
              <SelectItem value="G.2X">G.2X (8 vCPU, 32 GB)</SelectItem>
              <SelectItem value="G.4X">G.4X (16 vCPU, 64 GB)</SelectItem>
            </SelectContent>
          </Select>
        </div>
        <div className="space-y-2">
          <Label>Workers</Label>
          <Input
            type="number"
            min={2}
            max={20}
            value={numWorkers}
            onChange={e => patch({ numWorkers: Number(e.target.value) })}
          />
        </div>
      </div>
    </Section>
  )
}
