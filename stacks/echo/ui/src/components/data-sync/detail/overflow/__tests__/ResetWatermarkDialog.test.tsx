import { describe, it, expect, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ResetWatermarkDialog } from '@/components/data-sync/detail/overflow/ResetWatermarkDialog'

describe('ResetWatermarkDialog', () => {
  it('shows the current value and resets through a destructive button', async () => {
    const onConfirm = vi.fn()
    render(<ResetWatermarkDialog open currentValue="2026-09-28" onCancel={vi.fn()} onConfirm={onConfirm} />)
    expect(screen.getByRole('dialog', { name: 'Reset watermark to NULL?' })).toBeInTheDocument()
    expect(screen.getByText('2026-09-28')).toBeInTheDocument()
    expect(screen.getByText('NULL')).toBeInTheDocument()
    const button = screen.getByRole('button', { name: 'Reset watermark' })
    expect(button).toHaveAttribute('data-variant', 'destructive')
    await userEvent.click(button)
    expect(onConfirm).toHaveBeenCalledTimes(1)
  })

  it('focuses the confirm button on open and cancels on Escape', async () => {
    const onCancel = vi.fn()
    render(<ResetWatermarkDialog open currentValue="x" onCancel={onCancel} onConfirm={vi.fn()} />)
    await waitFor(() => expect(screen.getByRole('button', { name: 'Reset watermark' })).toHaveFocus())
    await userEvent.keyboard('{Escape}')
    expect(onCancel).toHaveBeenCalledTimes(1)
  })
})
