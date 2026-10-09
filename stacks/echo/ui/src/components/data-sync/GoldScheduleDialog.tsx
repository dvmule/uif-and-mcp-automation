import { useState } from 'react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogTitle,
} from '@/components/ui/dialog'
import { getEnv } from '@/lib/envConfig'
import {
  DEFAULT_WEEKLY_DAYS,
  WEEKDAYS,
  browserTimeZone,
  formatGoldClockLabel,
  formatMinutes,
  formatWeekdays,
  isGoldWeekly,
  isUtcZone,
  localWeeklySchedule,
  parseWeeklyDays,
  utcWeeklySchedule,
  zonedParts,
  zonedToUtc,
  type Weekday,
} from '@/lib/goldWeekly'
import {
  DEFAULT_GOLD_CADENCE,
  GOLD_CADENCE_RUNGS,
  goldCadenceLabel,
  goldRunsPerDay,
  goldTableFqn,
  isSubHourlyCadence,
  parseStartTime,
} from '@/lib/syncRow'
import type { GoldPipelineConfig, GoldScheduleChange } from '@/lib/types'

/** What the dialog saves: always UTC, always the runtime's shape. Declared in
 *  lib/types beside the client that sends it (UIFPE-564). */
export type { GoldScheduleChange } from '@/lib/types'

interface Props {
  /** The entity being edited; null closes the dialog. */
  gold: GoldPipelineConfig | null
  onClose: () => void
  onSave: (goldId: string, change: GoldScheduleChange) => void
  saving?: boolean
  /** IANA zone the picker works in; the viewer's browser zone by default. */
  zone?: string
  /** The week the conversion happens on (DST offset in force); now by default. */
  now?: Date
}

/** Pick a gold entity's run time - and, on the weekly rung, its run days - in
 *  the viewer's own timezone. Stored as UTC (UIFPE-532): day and time are
 *  converted together, so Tuesday 22:00 in Chicago is saved as Wednesday 03:00.
 *  The cadence is READ-ONLY here: start_time and weekly_days are console-owned,
 *  frequency stays in the domain's pipeline YAML (UIFPE-513). */
export function GoldScheduleDialog({ gold, onClose, onSave, saving, zone, now }: Props) {
  return (
    <Dialog open={!!gold} onOpenChange={open => !open && onClose()}>
      <DialogContent className="border border-paper-line bg-paper-card shadow-lg sm:max-w-md">
        {gold && (
          // Keyed on the entity so the input state resets by remount when the
          // operator opens a different row - no effect needed to sync it.
          <ScheduleForm
            key={gold.gold_id}
            gold={gold}
            onClose={onClose}
            onSave={onSave}
            saving={saving}
            zone={zone ?? browserTimeZone()}
            now={now ?? new Date()}
          />
        )}
      </DialogContent>
    </Dialog>
  )
}

function sameDays(a: readonly string[], b: readonly string[]): boolean {
  return a.length === b.length && a.every((d, i) => d === b[i])
}

function ScheduleForm({
  gold, onClose, onSave, saving, zone, now,
}: {
  gold: GoldPipelineConfig
  onClose: () => void
  onSave: (goldId: string, change: GoldScheduleChange) => void
  saving?: boolean
  zone: string
  now: Date
}) {
  // The cadence is now the operator's too (UIFPE-564). `storedWeekly` reads the
  // row as it IS, which is how its start_time and day set must be interpreted;
  // `weekly` reads the rung being PICKED, which is what the form shows.
  const storedFrequency = gold.schedule?.frequency ?? DEFAULT_GOLD_CADENCE
  const storedWeekly = isGoldWeekly(storedFrequency)
  const [frequency, setFrequency] = useState<string>(storedFrequency)
  const [costAccepted, setCostAccepted] = useState(false)
  const weekly = isGoldWeekly(frequency)
  const currentStart = gold.schedule?.start_time ?? ''
  const currentUtcMinutes = parseStartTime(currentStart)
  const currentUtcDays = parseWeeklyDays(gold.schedule?.weekly_days) ?? DEFAULT_WEEKLY_DAYS

  // The form works in the viewer's zone. Seed it from the stored UTC values,
  // converted on this week so the offset in force (DST included) is used.
  const seed = currentUtcMinutes == null
    ? { days: currentUtcDays, minutes: null as number | null }
    : storedWeekly
      ? localWeeklySchedule(currentUtcDays, currentUtcMinutes, zone, now)
      : { days: currentUtcDays, minutes: utcToLocalMinutes(currentUtcMinutes, zone, now) }
  const [value, setValue] = useState(seed.minutes == null ? currentStart : formatMinutes(seed.minutes))
  const [localDays, setLocalDays] = useState<Weekday[]>(seed.days)

  const localMinutes = parseStartTime(value)
  const timeValid = localMinutes != null
  const daysValid = !weekly || localDays.length > 0

  // What would be stored, always UTC.
  const utc = localMinutes == null
    ? null
    : weekly
      ? utcWeeklySchedule(localDays, localMinutes, zone, now)
      : { days: [] as Weekday[], minutes: zonedToUtc(zonedParts(now, zone).day, localMinutes, zone, now).minutes }
  const nextStart = utc ? formatMinutes(utc.minutes) : null
  const cadenceMoved = frequency !== storedFrequency
  // A sub-hourly rung multiplies this table's Glue spend, so picking one needs a
  // second, explicit act - not one careless click on a dropdown.
  const needsCostConfirm = cadenceMoved && isSubHourlyCadence(frequency)
  const changed = !!utc && timeValid && daysValid
    && (!needsCostConfirm || costAccepted)
    && (cadenceMoved || nextStart !== currentStart
        || (weekly && !sameDays(utc.days, currentUtcDays)))

  const cadence = goldCadenceLabel(frequency)
  const runsPerDay = goldRunsPerDay(frequency)
  const workers = gold.glue?.num_workers
  const workerType = gold.glue?.worker_type
  const name = goldTableFqn(gold, getEnv()) ?? gold.gold_id
  const zoneLabel = isUtcZone(zone) ? 'UTC' : zone

  const toggleDay = (d: Weekday) =>
    setLocalDays(prev => WEEKDAYS.filter(w => (w === d ? !prev.includes(w) : prev.includes(w))))

  return (
    <>
      <DialogTitle className="text-[15px] font-semibold text-ink">
        Change schedule
      </DialogTitle>
      <DialogDescription asChild>
        <div className="space-y-4 text-[13px] leading-relaxed text-ink">
          <p className="font-mono text-[12.5px] text-ink-soft">{name}</p>

          <div className="space-y-1">
            <Label htmlFor="gold-cadence" className="text-ink-mute">Cadence</Label>
            <select
              id="gold-cadence"
              className="w-full rounded-md border border-paper-line bg-paper px-2 py-1.5 text-[13px] text-ink"
              value={frequency}
              onChange={e => { setFrequency(e.target.value); setCostAccepted(false) }}
            >
              {GOLD_CADENCE_RUNGS.map(rung => (
                <option key={rung} value={rung}>{goldCadenceLabel(rung)}</option>
              ))}
            </select>
            {cadenceMoved && (
              <p className="text-[11.5px] text-ink-soft" data-testid="gold-cadence-change">
                Was {goldCadenceLabel(storedFrequency)}. The next run follows the new
                cadence; nothing runs now.
              </p>
            )}
          </div>

          {needsCostConfirm && (
            <div
              className="space-y-2 rounded-md border border-signal-warn bg-paper px-3 py-2"
              data-testid="gold-cadence-cost"
            >
              <p className="text-[12px] text-ink">
                {cadence} is <strong>{runsPerDay} runs a day</strong>
                {workers && workerType ? ` on ${workers} × ${workerType} workers` : ''}, against{' '}
                {goldRunsPerDay(storedFrequency) ?? 'fewer than 1'} today. Glue is billed per run,
                so this multiplies what this table costs.
              </p>
              <label className="inline-flex items-start gap-2 text-[12px] text-ink">
                <input
                  type="checkbox"
                  checked={costAccepted}
                  onChange={e => setCostAccepted(e.target.checked)}
                  aria-label={`Confirm ${runsPerDay} runs a day`}
                />
                <span>I understand this table will run {runsPerDay} times a day.</span>
              </label>
            </div>
          )}

          {weekly && (
            <fieldset className="space-y-1">
              <legend className="text-[13px] text-ink-mute">Run days ({zoneLabel})</legend>
              <div className="flex flex-wrap gap-2 pt-1">
                {WEEKDAYS.map(d => (
                  <label key={d} className="inline-flex items-center gap-1 text-[12.5px]">
                    <input
                      type="checkbox"
                      checked={localDays.includes(d)}
                      onChange={() => toggleDay(d)}
                      aria-label={d.charAt(0) + d.slice(1).toLowerCase()}
                    />
                    {d.charAt(0) + d.slice(1).toLowerCase()}
                  </label>
                ))}
              </div>
              {!daysValid && (
                <p className="text-[11.5px] text-signal-fail">Pick at least one day.</p>
              )}
            </fieldset>
          )}

          <div className="space-y-1">
            <Label htmlFor="gold-start-time">Start time ({zoneLabel})</Label>
            <Input
              id="gold-start-time"
              type="time"
              step={300}
              value={value}
              onChange={e => setValue(e.target.value)}
              className="w-40"
            />
            {timeValid && utc ? (
              <p className="text-[11.5px] text-ink-soft" data-testid="gold-schedule-preview">
                {weekly
                  ? `Runs weekly on ${formatWeekdays(localDays)} at ${formatGoldClockLabel(utc.minutes, zone, now)}`
                  : `Runs ${cadence} at ${formatGoldClockLabel(utc.minutes, zone, now)}`}
                {weekly && !isUtcZone(zone) && (
                  <> — stored as {formatWeekdays(utc.days)} {nextStart} UTC</>
                )}
              </p>
            ) : (
              <p className="text-[11.5px] text-signal-fail">
                Start time must be a time of day, HH:MM.
              </p>
            )}
          </div>

          {changed && currentStart && (
            <p className="text-[11.5px] text-ink-soft">
              Changes the registered schedule {weekly && `${formatWeekdays(currentUtcDays)} `}{currentStart} →{' '}
              {weekly && `${formatWeekdays(utc!.days)} `}{nextStart} UTC. The next scheduled run
              moves with it; nothing runs now.
            </p>
          )}
          {!isUtcZone(zone) && (
            <p className="text-[11px] text-ink-mute">
              Stored in UTC, so your local time shifts by an hour when daylight saving changes.
            </p>
          )}
        </div>
      </DialogDescription>
      <DialogFooter className="border-t border-paper-line bg-paper-card sm:flex-row sm:justify-end gap-2">
        <Button variant="outline" onClick={onClose} disabled={saving}>
          Cancel
        </Button>
        <Button
          onClick={() => utc && onSave(gold.gold_id, {
            start_time: nextStart!,
            // Sent only when it moved: an unchanged cadence must not be part of
            // the request, so a start-time-only edit still works on a
            // deployment that predates UIFPE-564.
            ...(cadenceMoved ? { frequency } : {}),
            ...(weekly ? { weekly_days: utc.days } : {}),
          } satisfies GoldScheduleChange)}
          disabled={!changed || !!saving}
        >
          {saving ? 'Saving…' : 'Save schedule'}
        </Button>
      </DialogFooter>
    </>
  )
}

/** A UTC minute-of-day as the viewer's clock shows it today. */
function utcToLocalMinutes(utcMinutes: number, zone: string, now: Date): number {
  const instant = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate()) + utcMinutes * 60_000)
  return zonedParts(instant, zone).minutes
}
