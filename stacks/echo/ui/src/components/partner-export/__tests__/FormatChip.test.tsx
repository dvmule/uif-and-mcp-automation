import { describe, it, expect, vi } from 'vitest'
import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { renderWithProviders } from '@/test/test-utils'
import { FormatChip } from '../FormatChip'

describe('FormatChip', () => {
  it('renders the format label and is clickable when live', async () => {
    const onSelect = vi.fn()
    renderWithProviders(<FormatChip format="csv" onSelect={onSelect} />)
    const chip = screen.getByRole('button', { name: /csv/i })
    await userEvent.click(chip)
    expect(onSelect).toHaveBeenCalledWith('csv')
  })

  it('marks the active chip with aria-pressed', () => {
    renderWithProviders(<FormatChip format="json" selected onSelect={() => {}} />)
    expect(screen.getByRole('button', { name: /json/i })).toHaveAttribute('aria-pressed', 'true')
  })

  it('renders fixed_width / xml as disabled "soon"', () => {
    renderWithProviders(<><FormatChip format="fixed_width" onSelect={() => {}} /><FormatChip format="xml" onSelect={() => {}} /></>)
    expect(screen.getByRole('button', { name: /fixed_width/i })).toBeDisabled()
    expect(screen.getByRole('button', { name: /xml/i })).toBeDisabled()
    expect(screen.getAllByText(/soon/i)).toHaveLength(2)
  })

  it('does not fire onSelect when disabled is clicked', async () => {
    const onSelect = vi.fn()
    renderWithProviders(<FormatChip format="xml" onSelect={onSelect} />)
    await userEvent.click(screen.getByRole('button', { name: /xml/i }))
    expect(onSelect).not.toHaveBeenCalled()
  })
})
