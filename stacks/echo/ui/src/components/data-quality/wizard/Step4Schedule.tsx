// Step 4 of New check: how often it runs.
import { Input } from '@/components/ui/input'
import type { DqCheckSchedule } from '@/lib/types'
import { FIELD_LABEL, SELECT_CLASS } from './fieldStyles'

export function Step4Schedule({ value, onChange }: {
  value: DqCheckSchedule
  onChange: (s: DqCheckSchedule) => void
}) {
  return (
    <div className="space-y-4">
      <label className={FIELD_LABEL}>
        Frequency
        <select
          value={value.frequency}
          onChange={e => onChange({ ...value, frequency: e.target.value as DqCheckSchedule['frequency'] })}
          className={SELECT_CLASS}
        >
          <option value="hourly">Hourly</option>
          <option value="daily">Daily</option>
          <option value="weekly">Weekly</option>
          <option value="cron">Custom (cron)</option>
        </select>
      </label>
      {value.frequency === 'cron' && (
        <label className={FIELD_LABEL}>
          Cron expression
          <Input
            value={value.cron ?? ''}
            onChange={e => onChange({ ...value, cron: e.target.value })}
            className="mt-1 font-mono"
            placeholder="0 3 * * ? *"
          />
        </label>
      )}
    </div>
  )
}
