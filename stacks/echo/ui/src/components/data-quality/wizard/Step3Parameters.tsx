// Step 3 of New check: the kind's parameters (reconcile or null-rate).
import { Input } from '@/components/ui/input'
import { Note } from '@/components/relay'
import { LIVE_KINDS } from './parameterSchemas'
import { CHECK_LABEL, FIELD_LABEL } from './fieldStyles'
import type { DqCheckKind } from '@/lib/types'

export function Step3Parameters({ kind, value, onChange }: {
  kind: DqCheckKind
  value: Record<string, unknown>
  onChange: (p: Record<string, unknown>) => void
}) {
  if (!LIVE_KINDS.includes(kind as typeof LIVE_KINDS[number])) {
    return (
      <Note tone="warn">
        The <strong>{kind}</strong> kind is shipping later. Pick a live kind in Step 1 to continue.
      </Note>
    )
  }

  if (kind === 'null-rate') {
    return (
      <div className="space-y-4">
        <label className={FIELD_LABEL}>
          Column
          <Input
            value={(value.column as string) ?? ''}
            onChange={e => onChange({ ...value, column: e.target.value })}
            className="mt-1 font-mono"
            placeholder="UNIT_PRICE"
          />
        </label>
        <label className={FIELD_LABEL}>
          Threshold (0–1, e.g. 0.05 means fail above 5% nulls)
          <Input
            type="number" step="0.01" min="0" max="1"
            value={(value.threshold_pct as number) ?? 0.05}
            onChange={e => onChange({ ...value, threshold_pct: parseFloat(e.target.value) })}
            className="mt-1"
          />
        </label>
      </div>
    )
  }

  // reconcile
  return (
    <div className="space-y-4">
      <label className={FIELD_LABEL}>
        Primary key columns (comma-separated)
        <Input
          value={Array.isArray(value.pk_cols) ? (value.pk_cols as string[]).join(',') : ''}
          onChange={e => onChange({ ...value, pk_cols: e.target.value.split(',').map(s => s.trim()).filter(Boolean) })}
          className="mt-1 font-mono"
          placeholder="ROY_SKU_ID"
        />
      </label>
      <label className={FIELD_LABEL}>
        PK partition column (optional — for JDBC parallel read)
        <Input
          value={(value.pk_partition_col as string) ?? ''}
          onChange={e => onChange({ ...value, pk_partition_col: e.target.value || undefined })}
          className="mt-1 font-mono"
        />
      </label>
      <label className={FIELD_LABEL}>
        Max deletes per run (safety cap)
        <Input
          type="number" min="0"
          value={(value.max_deletes_per_run as number) ?? 5000}
          onChange={e => onChange({ ...value, max_deletes_per_run: parseInt(e.target.value || '0', 10) })}
          className="mt-1"
        />
      </label>
      <label className={CHECK_LABEL}>
        <input type="checkbox"
               className="accent-accent"
               checked={!!value.dry_run}
               onChange={e => onChange({ ...value, dry_run: e.target.checked })} />
        Dry-run (count orphans, do not delete)
      </label>
      <label className={FIELD_LABEL}>
        Oracle Secrets Manager ARN
        <Input
          value={(value.oracle_secret_arn as string) ?? ''}
          onChange={e => onChange({ ...value, oracle_secret_arn: e.target.value })}
          className="mt-1 font-mono"
          placeholder="arn:aws:secretsmanager:eu-west-1:..."
        />
      </label>
      <label className={FIELD_LABEL}>
        Oracle JDBC URL
        <Input
          value={(value.jdbc_url as string) ?? ''}
          onChange={e => onChange({ ...value, jdbc_url: e.target.value })}
          className="mt-1 font-mono"
          placeholder="jdbc:oracle:thin:@//host:1521/SVC"
        />
      </label>
    </div>
  )
}
