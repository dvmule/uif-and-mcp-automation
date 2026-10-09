import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { DeleteSyncDialog } from '@/components/data-sync/detail/overflow/DeleteSyncDialog'

describe('DeleteSyncDialog', () => {
  it('shows remove confirmation copy', () => {
    render(<DeleteSyncDialog open onCancel={vi.fn()} onConfirm={vi.fn()} />)
    expect(screen.getByRole('dialog', { name: 'Remove sync' })).toBeInTheDocument()
    expect(screen.getByText(/Are you sure to remove the data sync\?/)).toBeInTheDocument()
    expect(screen.getByText(/Data Sync Administrator/)).toBeInTheDocument()
  })

  it('calls onConfirm when Remove sync is clicked, with a destructive button', () => {
    const onConfirm = vi.fn()
    render(<DeleteSyncDialog open onCancel={vi.fn()} onConfirm={onConfirm} />)
    const button = screen.getByRole('button', { name: /Remove sync/i })
    expect(button).toHaveAttribute('data-variant', 'destructive')
    fireEvent.click(button)
    expect(onConfirm).toHaveBeenCalled()
  })

  it('focuses the confirm button on open and cancels on Escape', async () => {
    const onCancel = vi.fn()
    render(<DeleteSyncDialog open onCancel={onCancel} onConfirm={vi.fn()} />)
    await waitFor(() => expect(screen.getByRole('button', { name: 'Remove sync' })).toHaveFocus())
    await userEvent.keyboard('{Escape}')
    expect(onCancel).toHaveBeenCalledTimes(1)
  })

  it('locks both buttons while the delete is running', () => {
    render(<DeleteSyncDialog open confirming onCancel={vi.fn()} onConfirm={vi.fn()} />)
    expect(screen.getByRole('button', { name: /Removing/ })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeDisabled()
  })
})
