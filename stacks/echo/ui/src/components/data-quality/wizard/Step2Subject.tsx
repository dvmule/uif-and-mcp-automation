// Step 2 of New check: the silver or gold table the check evaluates.
import { Input } from '@/components/ui/input'
import type { DqCheckSubject } from '@/lib/types'
import { FIELD_LABEL, SELECT_CLASS } from './fieldStyles'

export function Step2Subject({ value, onChange }: {
  value: DqCheckSubject
  onChange: (s: DqCheckSubject) => void
}) {
  return (
    <div className="space-y-4">
      <p className="text-meta text-ink-mute">Which dataset does this check evaluate?</p>
      <label className={FIELD_LABEL}>
        Layer
        <select
          value={value.layer}
          onChange={e => onChange({ ...value, layer: e.target.value as 'silver' | 'gold' })}
          className={SELECT_CLASS}
        >
          <option value="silver">Silver</option>
          <option value="gold">Gold</option>
        </select>
      </label>
      <label className={FIELD_LABEL}>
        Database
        <Input
          value={value.database}
          onChange={e => onChange({ ...value, database: e.target.value })}
          className="mt-1 font-mono"
          placeholder="silver_foo"
        />
      </label>
      <label className={FIELD_LABEL}>
        Table
        <Input
          value={value.table}
          onChange={e => onChange({ ...value, table: e.target.value })}
          className="mt-1 font-mono"
          placeholder="bar"
        />
      </label>
    </div>
  )
}
