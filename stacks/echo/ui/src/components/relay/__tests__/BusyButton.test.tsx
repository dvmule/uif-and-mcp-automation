import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { BusyButton } from '../BusyButton'

describe('BusyButton', () => {
  it('behaves as a normal button when not busy', async () => {
    const onClick = vi.fn()
    render(<BusyButton onClick={onClick}>Save</BusyButton>)
    const button = screen.getByRole('button', { name: 'Save' })
    expect(button).toBeEnabled()
    expect(button).not.toHaveAttribute('aria-busy')
    await userEvent.click(button)
    expect(onClick).toHaveBeenCalledTimes(1)
  })

  it('disables itself, sets aria-busy and shows a spinner while busy', async () => {
    const onClick = vi.fn()
    render(
      <BusyButton busy onClick={onClick}>
        Save
      </BusyButton>,
    )
    const button = screen.getByRole('button', { name: 'Save' })
    expect(button).toBeDisabled()
    expect(button).toHaveAttribute('aria-busy', 'true')
    expect(button.querySelector('svg.animate-spin')).not.toBeNull()
    await userEvent.click(button)
    expect(onClick).not.toHaveBeenCalled()
  })

  it('stays disabled when disabled is set and not busy', () => {
    render(<BusyButton disabled>Save</BusyButton>)
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled()
  })
})
