import { describe, it, expect, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { GoldRunConfirmDialog } from '@/components/data-sync/gold/GoldRunConfirmDialog'
import type { GoldPipelineConfig } from '@/lib/types'

const GOLD: GoldPipelineConfig = {
  gold_id: 'product',
  enabled: true,
  domain: 'Product',
  target: { database: 'uif_echo_{env}_gold_product', table: 'product' },
}

describe('GoldRunConfirmDialog', () => {
  it('names the table and runs through the primary (not destructive) button', async () => {
    const onConfirm = vi.fn()
    render(<GoldRunConfirmDialog gold={GOLD} onCancel={vi.fn()} onConfirm={onConfirm} />)
    const dialog = screen.getByRole('dialog', { name: 'Run gold transform' })
    expect(dialog).toHaveTextContent('gold_product.product')
    expect(dialog).toHaveTextContent('outside its schedule')
    const button = screen.getByRole('button', { name: 'Run now' })
    expect(button).toHaveAttribute('data-variant', 'default')
    await userEvent.click(button)
    expect(onConfirm).toHaveBeenCalledTimes(1)
  })

  it('is closed when there is no entity', () => {
    render(<GoldRunConfirmDialog gold={null} onCancel={vi.fn()} onConfirm={vi.fn()} />)
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('focuses the confirm button on open and cancels on Escape', async () => {
    const onCancel = vi.fn()
    render(<GoldRunConfirmDialog gold={GOLD} onCancel={onCancel} onConfirm={vi.fn()} />)
    await waitFor(() => expect(screen.getByRole('button', { name: 'Run now' })).toHaveFocus())
    await userEvent.keyboard('{Escape}')
    expect(onCancel).toHaveBeenCalledTimes(1)
  })

  it('locks while the run is starting', () => {
    render(<GoldRunConfirmDialog gold={GOLD} pending onCancel={vi.fn()} onConfirm={vi.fn()} />)
    expect(screen.getByRole('button', { name: /Starting/ })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeDisabled()
  })
})
