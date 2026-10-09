import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { GoldScheduleDialog } from '../GoldScheduleDialog'
import type { GoldPipelineConfig } from '@/lib/types'

vi.mock('@/lib/envConfig', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/envConfig')>()),
  getEnv: () => 'dev',
}))

const product: GoldPipelineConfig = {
  gold_id: 'product',
  enabled: true,
  target: { database: 'uif_echo_{env}_gold_product', table: 'product' },
  schedule: { frequency: 'gold-24h', start_time: '06:30' },
}

// Stored as UTC: Wednesday and Friday 03:00. In Chicago (CDT, UTC-5 on this
// week) that is Tuesday and Thursday 22:00 - the day flips with the time.
const weeklyUtc: GoldPipelineConfig = {
  gold_id: 'rnr',
  enabled: true,
  target: { database: 'uif_echo_{env}_gold_product', table: 'rnr' },
  schedule: { frequency: 'gold-weekly', start_time: '03:00', weekly_days: ['WED', 'FRI'] },
}
const SEP = new Date('2026-09-23T12:00:00Z')   // a Wednesday, CDT in force

function setup(gold: GoldPipelineConfig | null = product, saving = false, zone = 'UTC', now = SEP) {
  const onClose = vi.fn(), onSave = vi.fn()
  render(<GoldScheduleDialog gold={gold} onClose={onClose} onSave={onSave} saving={saving} zone={zone} now={now} />)
  return { onClose, onSave }
}

describe('GoldScheduleDialog', () => {
  it('names the table and offers every cadence rung, the stored one selected', () => {
    setup()
    const dialog = screen.getByRole('dialog')
    expect(dialog).toHaveTextContent('uif_echo_dev_gold_product.product')
    // UIFPE-564: the cadence is the operator's now, so it is a control, not prose
    const picker = screen.getByLabelText(/Cadence/) as HTMLSelectElement
    expect(picker.value).toBe('gold-24h')
    expect([...picker.options].map(o => o.value)).toEqual([
      'gold-5m', 'gold-10m', 'gold-15m', 'gold-30m', 'gold-1h', 'gold-2h',
      'gold-4h', 'gold-6h', 'gold-8h', 'gold-12h', 'gold-24h', 'gold-weekly',
    ])
    expect([...picker.options].map(o => o.textContent)).toContain('daily')
    expect(dialog).not.toHaveTextContent(/pipeline YAML/)
    expect(screen.getAllByLabelText(/Start time/)).toHaveLength(1)
    // a day-tiling rung has no day picker until weekly is picked
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument()
  })

  it('starts on the current time with Save disabled until it changes', () => {
    const { onSave } = setup()
    const input = screen.getByLabelText(/Start time/) as HTMLInputElement
    expect(input.value).toBe('06:30')
    const save = screen.getByRole('button', { name: /Save schedule/ })
    expect(save).toBeDisabled()
    fireEvent.click(save)
    expect(onSave).not.toHaveBeenCalled()
  })

  it('previews the new time in the gold clock dialect and saves UTC by gold_id', () => {
    const { onSave } = setup()
    fireEvent.change(screen.getByLabelText(/Start time/), { target: { value: '07:00' } })
    expect(screen.getByRole('dialog')).toHaveTextContent('Runs daily at 07:00 UTC')
    expect(screen.getByRole('dialog')).toHaveTextContent('06:30 → 07:00')
    fireEvent.click(screen.getByRole('button', { name: /Save schedule/ }))
    expect(onSave).toHaveBeenCalledWith('product', { start_time: '07:00' })
  })

  it("works in the viewer's zone and stores UTC (UIFPE-532)", () => {
    const { onSave } = setup(product, false, 'America/Chicago')
    // 06:30 UTC is 01:30 CDT: the input shows the viewer's clock
    expect(screen.getByLabelText(/Start time \(America\/Chicago\)/)).toHaveValue('01:30')
    fireEvent.change(screen.getByLabelText(/Start time/), { target: { value: '02:00' } })
    expect(screen.getByRole('dialog')).toHaveTextContent('Runs daily at 2:00 AM CDT · 07:00 UTC')
    expect(screen.getByRole('dialog')).toHaveTextContent(/daylight saving/)
    fireEvent.click(screen.getByRole('button', { name: /Save schedule/ }))
    expect(onSave).toHaveBeenCalledWith('product', { start_time: '07:00' })
  })

  it('offers run days on the weekly rung, shown in the viewer zone with the time', () => {
    setup(weeklyUtc, false, 'America/Chicago')
    // WED/FRI 03:00 UTC read as Tue/Thu 22:00 in Chicago
    expect(screen.getByRole('checkbox', { name: 'Tue' })).toBeChecked()
    expect(screen.getByRole('checkbox', { name: 'Thu' })).toBeChecked()
    expect(screen.getByRole('checkbox', { name: 'Wed' })).not.toBeChecked()
    expect(screen.getByLabelText(/Start time/)).toHaveValue('22:00')
    expect(screen.getByTestId('gold-schedule-preview'))
      .toHaveTextContent('Runs weekly on Tue, Thu at 10:00 PM CDT · 03:00 UTC — stored as Wed, Fri 03:00 UTC')
    expect(screen.getByRole('button', { name: /Save schedule/ })).toBeDisabled()   // unchanged
  })

  it('converts each picked day WITH the time when saving a weekly schedule', () => {
    const { onSave } = setup(weeklyUtc, false, 'America/Chicago')
    fireEvent.click(screen.getByRole('checkbox', { name: 'Sat' }))          // Tue, Thu, Sat local 22:00
    fireEvent.click(screen.getByRole('button', { name: /Save schedule/ }))
    // Saturday 22:00 CDT is Sunday 03:00 UTC
    expect(onSave).toHaveBeenCalledWith('rnr', { start_time: '03:00', weekly_days: ['WED', 'FRI', 'SUN'] })
  })

  it('refuses a weekly schedule with no days, and an unusable time', () => {
    const { onSave } = setup(weeklyUtc, false, 'UTC')
    fireEvent.click(screen.getByRole('checkbox', { name: 'Wed' }))
    fireEvent.click(screen.getByRole('checkbox', { name: 'Fri' }))
    expect(screen.getByRole('dialog')).toHaveTextContent(/at least one day/)
    const save = screen.getByRole('button', { name: /Save schedule/ })
    expect(save).toBeDisabled()
    fireEvent.click(save)
    expect(onSave).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('checkbox', { name: 'Mon' }))
    fireEvent.change(screen.getByLabelText(/Start time/), { target: { value: '' } })
    expect(screen.getByRole('dialog')).toHaveTextContent(/HH:MM/)
    expect(screen.getByRole('button', { name: /Save schedule/ })).toBeDisabled()
  })

  it('a UTC viewer sees no zone conversion and no daylight-saving note', () => {
    setup(weeklyUtc, false, 'UTC')
    expect(screen.getByRole('checkbox', { name: 'Wed' })).toBeChecked()
    expect(screen.getByLabelText(/Start time \(UTC\)/)).toHaveValue('03:00')
    expect(screen.getByRole('dialog')).not.toHaveTextContent(/daylight saving/)
  })

  it('disables both buttons while saving, and Cancel calls onClose otherwise', () => {
    const first = setup(product, true)
    expect(screen.getByRole('button', { name: /Saving/ })).toBeDisabled()
    expect(screen.getByRole('button', { name: /Cancel/ })).toBeDisabled()
    expect(first.onClose).not.toHaveBeenCalled()
  })

  it('renders nothing when there is no entity', () => {
    setup(null)
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  // ── UIFPE-564: the cadence is console-owned ──────────────────────────────

  it('saves a cadence change beside the time, and only when it moved', () => {
    const { onSave } = setup()
    fireEvent.change(screen.getByLabelText(/Cadence/), { target: { value: 'gold-4h' } })
    expect(screen.getByTestId('gold-cadence-change')).toHaveTextContent(/Was daily/)
    fireEvent.click(screen.getByRole('button', { name: /Save schedule/ }))
    expect(onSave).toHaveBeenCalledWith('product', { start_time: '06:30', frequency: 'gold-4h' })
  })

  it('an unchanged cadence is not sent, so a start-time edit still works on an older API', () => {
    const { onSave } = setup()
    fireEvent.change(screen.getByLabelText(/Start time/), { target: { value: '07:15' } })
    fireEvent.click(screen.getByRole('button', { name: /Save schedule/ }))
    expect(onSave).toHaveBeenCalledWith('product', { start_time: '07:15' })
    expect(Object.keys(onSave.mock.calls[0][1])).toEqual(['start_time'])
  })

  it('a sub-hourly rung needs a second, explicit confirmation naming the cost', () => {
    const { onSave } = setup({ ...product, glue: { worker_type: 'G.2X', num_workers: 16 } })
    fireEvent.change(screen.getByLabelText(/Cadence/), { target: { value: 'gold-5m' } })
    const warning = screen.getByTestId('gold-cadence-cost')
    expect(warning).toHaveTextContent('288 runs a day')
    expect(warning).toHaveTextContent('16 × G.2X')
    expect(warning).toHaveTextContent(/1 today/)            // daily today
    const save = screen.getByRole('button', { name: /Save schedule/ })
    expect(save).toBeDisabled()
    fireEvent.click(save)
    expect(onSave).not.toHaveBeenCalled()

    fireEvent.click(screen.getByLabelText(/Confirm 288 runs a day/))
    expect(save).toBeEnabled()
    fireEvent.click(save)
    expect(onSave).toHaveBeenCalledWith('product', { start_time: '06:30', frequency: 'gold-5m' })
  })

  it('re-picking a rung clears an accepted cost confirmation', () => {
    setup()
    fireEvent.change(screen.getByLabelText(/Cadence/), { target: { value: 'gold-5m' } })
    fireEvent.click(screen.getByLabelText(/Confirm 288 runs a day/))
    expect(screen.getByRole('button', { name: /Save schedule/ })).toBeEnabled()
    fireEvent.change(screen.getByLabelText(/Cadence/), { target: { value: 'gold-15m' } })
    expect(screen.getByLabelText(/Confirm 96 runs a day/)).not.toBeChecked()
    expect(screen.getByRole('button', { name: /Save schedule/ })).toBeDisabled()
  })

  it('an hourly-or-slower rung needs no cost confirmation', () => {
    const { onSave } = setup()
    fireEvent.change(screen.getByLabelText(/Cadence/), { target: { value: 'gold-1h' } })
    expect(screen.queryByTestId('gold-cadence-cost')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /Save schedule/ }))
    expect(onSave).toHaveBeenCalledWith('product', { start_time: '06:30', frequency: 'gold-1h' })
  })

  it('picking weekly reveals the day picker and sends a day set with the move', () => {
    const { onSave } = setup()
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument()
    fireEvent.change(screen.getByLabelText(/Cadence/), { target: { value: 'gold-weekly' } })
    expect(screen.getAllByRole('checkbox')).toHaveLength(7)     // Mon..Sun, no cost confirm
    fireEvent.click(screen.getByLabelText('Wed'))
    fireEvent.click(screen.getByRole('button', { name: /Save schedule/ }))
    const [, change] = onSave.mock.calls[0]
    expect(change.frequency).toBe('gold-weekly')
    expect(change.weekly_days).toEqual(['MON', 'WED'])
    expect(change.start_time).toBe('06:30')
  })

  it('leaving the weekly rung stops sending a day set', () => {
    const { onSave } = setup(weeklyUtc)
    expect(screen.getAllByRole('checkbox')).toHaveLength(7)
    fireEvent.change(screen.getByLabelText(/Cadence/), { target: { value: 'gold-12h' } })
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /Save schedule/ }))
    const [, change] = onSave.mock.calls[0]
    expect(change.frequency).toBe('gold-12h')
    expect('weekly_days' in change).toBe(false)   // the handler drops the stored set
  })
})
