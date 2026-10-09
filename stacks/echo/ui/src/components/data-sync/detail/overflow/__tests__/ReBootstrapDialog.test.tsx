import { describe, it, expect, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ReBootstrapDialog } from '@/components/data-sync/detail/overflow/ReBootstrapDialog'

describe('ReBootstrapDialog', () => {
  it('holds Re-bootstrap until the sync name is typed', async () => {
    const onConfirm = vi.fn()
    render(<ReBootstrapDialog open syncName="sync_a" onCancel={vi.fn()} onConfirm={onConfirm} />)
    expect(screen.getByRole('dialog', { name: 'Re-bootstrap this sync?' })).toBeInTheDocument()
    const button = screen.getByRole('button', { name: 'Re-bootstrap' })
    expect(button).toHaveAttribute('data-variant', 'destructive')
    expect(button).toBeDisabled()
    await userEvent.type(screen.getByRole('textbox', { name: /Type the sync name/ }), 'sync_')
    expect(button).toBeDisabled()
    await userEvent.type(screen.getByRole('textbox'), 'a')
    expect(button).toBeEnabled()
    await userEvent.click(button)
    expect(onConfirm).toHaveBeenCalledTimes(1)
  })

  it('focuses the name field while confirm is held, and cancels on Escape', async () => {
    const onCancel = vi.fn()
    render(<ReBootstrapDialog open syncName="sync_a" onCancel={onCancel} onConfirm={vi.fn()} />)
    await waitFor(() => expect(screen.getByRole('textbox')).toHaveFocus())
    await userEvent.keyboard('{Escape}')
    expect(onCancel).toHaveBeenCalledTimes(1)
  })
})
