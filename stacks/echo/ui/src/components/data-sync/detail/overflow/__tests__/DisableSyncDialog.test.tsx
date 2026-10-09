import { describe, it, expect, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { DisableSyncDialog } from '@/components/data-sync/detail/overflow/DisableSyncDialog'

describe('DisableSyncDialog', () => {
  it('keeps its wording and confirms through a destructive button', async () => {
    const onConfirm = vi.fn()
    render(<DisableSyncDialog open onCancel={vi.fn()} onConfirm={onConfirm} />)
    expect(screen.getByRole('dialog', { name: 'Disable this sync?' })).toBeInTheDocument()
    expect(screen.getByText(/Scheduled runs will be paused/)).toBeInTheDocument()
    const button = screen.getByRole('button', { name: 'Disable sync' })
    expect(button).toHaveAttribute('data-variant', 'destructive')
    await userEvent.click(button)
    expect(onConfirm).toHaveBeenCalledTimes(1)
  })

  it('focuses the confirm button on open and cancels on Escape', async () => {
    const onCancel = vi.fn()
    render(<DisableSyncDialog open onCancel={onCancel} onConfirm={vi.fn()} />)
    await waitFor(() => expect(screen.getByRole('button', { name: 'Disable sync' })).toHaveFocus())
    await userEvent.keyboard('{Escape}')
    expect(onCancel).toHaveBeenCalledTimes(1)
  })
})
