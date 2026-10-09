import { describe, expect, it } from 'vitest'
import {
  DAILY_SLOT_COUNT,
  formatClockLabel,
  formatDailyScheduleLabel,
  formatReconcileScheduleLabel,
  formatReconcileSlotUtc,
  formatSlotEastern,
  formatSlotUtc,
  minutesUntilNextDailySlot,
  resolveDailySlot,
  slotUtcDate,
  slotUtcMinutes,
} from '@/lib/dailySchedule'

describe('dailySchedule', () => {
  it('maps slots to UTC window endpoints', () => {
    expect(formatSlotUtc(0)).toBe('05:30 UTC')
    expect(formatSlotUtc(11)).toBe('11:00 UTC')
  })

  it('defaults missing daily_slot to slot 0', () => {
    expect(resolveDailySlot({ frequency: 'daily' })).toBe(0)
    expect(resolveDailySlot({ frequency: 'daily', daily_slot: 7 })).toBe(7)
    expect(resolveDailySlot({ frequency: 'hourly' })).toBe(0)
  })

  it('formats daily schedule label with UTC time', () => {
    const label = formatDailyScheduleLabel({ frequency: 'daily', daily_slot: 3 })
    expect(label).toContain('daily · 07:00 UTC')
  })

  it('formats reconcile schedule label when enabled', () => {
    const label = formatReconcileScheduleLabel({ frequency: 'daily', daily_slot: 3 }, true)
    expect(label).toBe('07:30 UTC (2:30 AM ET)')
  })

  it('maps reconcile slots to UTC window endpoints', () => {
    expect(formatReconcileSlotUtc(0)).toBe('06:00 UTC')
    expect(formatReconcileSlotUtc(11)).toBe('11:30 UTC')
  })

  it('computes minutes until next slot tick', () => {
    const now = slotUtcDate(2, new Date('2026-07-16T06:00:00.000Z'))
    now.setUTCMinutes(now.getUTCMinutes() - 5)
    const mins = minutesUntilNextDailySlot(2, now)
    expect(mins).toBe(5)
  })

  it('wraps to next day after slot passes', () => {
    const after = new Date('2026-07-16T12:00:00.000Z')
    const mins = minutesUntilNextDailySlot(0, after)
    expect(mins).toBeGreaterThan(60 * 16)
    expect(mins).toBeLessThan(60 * 18)
  })

  // The shared clock label is what gold rows reuse; pin it independently of slots.
  it('formats a clock label for any minute-of-day, matching the slot helpers', () => {
    expect(formatClockLabel(6 * 60 + 30)).toBe('06:30 UTC (1:30 AM ET)')
    expect(formatClockLabel(slotUtcMinutes(3))).toBe(`${formatSlotUtc(3)} (${formatSlotEastern(3)} ET)`)
  })

  it('has twelve slots', () => {
    expect(DAILY_SLOT_COUNT).toBe(12)
  })
})
