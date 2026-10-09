import { describe, it, expect, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ConfirmDialog } from '../ConfirmDialog'

function setup(props: Partial<React.ComponentProps<typeof ConfirmDialog>> = {}) {
  const onConfirm = vi.fn()
  const onCancel = vi.fn()
  render(
    <ConfirmDialog
      open
      title="Delete sync?"
      description="This cannot be undone."
      onConfirm={onConfirm}
      onCancel={onCancel}
      {...props}
    />,
  )
  return { onConfirm, onCancel }
}

describe('ConfirmDialog', () => {
  it('shows title, description and children', () => {
    setup({ children: <p>extra detail</p> })
    expect(screen.getByRole('dialog', { name: 'Delete sync?' })).toBeInTheDocument()
    expect(screen.getByText('This cannot be undone.')).toBeInTheDocument()
    expect(screen.getByText('extra detail')).toBeInTheDocument()
  })

  it('renders nothing when closed', () => {
    setup({ open: false })
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('confirms and cancels', async () => {
    const { onConfirm, onCancel } = setup({ confirmLabel: 'Delete' })
    await userEvent.click(screen.getByRole('button', { name: 'Delete' }))
    expect(onConfirm).toHaveBeenCalledTimes(1)
    await userEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(onCancel).toHaveBeenCalledTimes(1)
  })

  it('cancels on Escape', async () => {
    const { onCancel } = setup()
    await userEvent.keyboard('{Escape}')
    expect(onCancel).toHaveBeenCalledTimes(1)
  })

  it('uses the destructive variant when danger is set', () => {
    setup({ danger: true })
    expect(screen.getByRole('button', { name: 'Confirm' })).toHaveAttribute('data-variant', 'destructive')
  })

  it('focuses the confirm button when opened', async () => {
    setup({ confirmLabel: 'Delete' })
    await waitFor(() => expect(screen.getByRole('button', { name: 'Delete' })).toHaveFocus())
  })

  it('holds the confirm button while confirmDisabled is set', async () => {
    const { onConfirm } = setup({ confirmDisabled: true, children: <input aria-label="Name" /> })
    expect(screen.getByRole('button', { name: 'Confirm' })).toBeDisabled()
    await waitFor(() => expect(screen.getByRole('textbox', { name: 'Name' })).toHaveFocus())
    await userEvent.click(screen.getByRole('button', { name: 'Confirm' }))
    expect(onConfirm).not.toHaveBeenCalled()
  })

  it('locks both buttons and ignores Escape while busy', async () => {
    const { onCancel } = setup({ busy: true })
    expect(screen.getByRole('button', { name: 'Confirm' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Confirm' })).toHaveAttribute('aria-busy', 'true')
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeDisabled()
    await userEvent.keyboard('{Escape}')
    expect(onCancel).not.toHaveBeenCalled()
  })
})
