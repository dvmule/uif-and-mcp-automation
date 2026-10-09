// TypeScript mirror of src/libs/schedule_vocabulary.py — same convention as
// dailySchedule.ts mirroring daily_schedule.py.
//
// The mirror is checked, not trusted: qe/libs/test_schedule_vocabulary.py
// parses this file and asserts both lists equal the Python ones, which in turn
// are asserted equal to the EventBridge rules in scheduler.tf. So all three
// layers move together or CI fails.
//
// Matching is EXACT and every canonical value is lowercase. scheduler_dispatcher
// selects pipelines with `schedule.frequency !== payload` and does not normalise,
// so 'Hourly' and 'hourly ' are values that would look valid here and then never
// dispatch. Lowercase the input before comparing; do not loosen the comparison.

/** Bronze/Silver schedule-driven cadences. `daily` also carries daily_slot. */
export const BRONZE_SILVER_FREQUENCIES = [
  '30min',
  'hourly',
  '4hr',
  'daily',
  'weekly',
] as const

/** Silver reconcile — reuses schedule.daily_slot, fires +30 min after Bronze. */
export const RECONCILE_FREQUENCIES = ['reconcile'] as const

/** Gold cadences belong to the gold dispatcher (gold_frequencies), not here. */
export const DISPATCHABLE_FREQUENCIES: readonly string[] = [
  ...BRONZE_SILVER_FREQUENCIES,
  ...RECONCILE_FREQUENCIES,
]

/**
 * Cadences frequent enough that a fat incremental Glue profile is a cost bug.
 * This list previously omitted '4hr' here while pipeline_controller included
 * it, so the API warned about configurations the UI called fine.
 */
export const HIGH_CADENCE_FREQUENCIES: readonly string[] = [
  '30min',
  'hourly',
  '4hr',
]

/** True when some EventBridge rule sends exactly this string. */
export function isDispatchable(frequency: unknown): boolean {
  return typeof frequency === 'string' && DISPATCHABLE_FREQUENCIES.includes(frequency)
}

/** An absent or blank frequency means adhoc, which is legitimate. */
export function isScheduled(frequency: unknown): boolean {
  return typeof frequency === 'string' && frequency.trim() !== ''
}
