import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { GoldRowActions } from '../GoldRowActions'
import type { GoldPipelineConfig } from '@/lib/types'

const toggleMutate = vi.fn()
let lockedRunId: string | null = null

vi.mock('@/hooks/useGoldPipelineMutations', () => ({
  useToggleGoldPipeline: () => ({ mutate: toggleMutate, isPending: false }),
  useGoldRunLock: () => lockedRunId,
}))

const gold: GoldPipelineConfig = {
  gold_id: 'product',
  enabled: true,
  target: { database: 'uif_echo_{env}_gold_product', table: 'product' },
}

describe('GoldRowActions', () => {
  beforeEach(() => {
    toggleMutate.mockClear()
    lockedRunId = null
  })

  it('hands Run to the parent rather than firing the mutation itself', () => {
    const onRun = vi.fn()
    render(<GoldRowActions gold={gold} onRun={onRun} />)
    fireEvent.click(screen.getByRole('button', { name: 'Run gold transform' }))
    expect(onRun).toHaveBeenCalledWith(gold)
  })

  // The server does not check `enabled` before starting a gold run; the UI must.
  it('blocks Run on a disabled entity', () => {
    const onRun = vi.fn()
    render(<GoldRowActions gold={{ ...gold, enabled: false }} onRun={onRun} />)
    const run = screen.getByRole('button', { name: 'Enable before running' })
    expect(run).toBeDisabled()
    fireEvent.click(run)
    expect(onRun).not.toHaveBeenCalled()
  })

  it('blocks Run while pending and after this session already started one', () => {
    const { rerender } = render(<GoldRowActions gold={gold} onRun={vi.fn()} runPending />)
    expect(screen.getByRole('button', { name: 'Run gold transform' })).toBeDisabled()

    lockedRunId = 'jr_abc'
    rerender(<GoldRowActions gold={gold} onRun={vi.fn()} />)
    expect(screen.getByRole('button', { name: 'Run started this session' })).toBeDisabled()
  })

  // toggle takes an explicit boolean server-side and never flips, so the client
  // must send the target state, not a "toggle" verb.
  it('sends the explicit target enabled value', () => {
    render(<GoldRowActions gold={gold} onRun={vi.fn()} />)
    fireEvent.click(screen.getByRole('button', { name: 'Disable' }))
    expect(toggleMutate).toHaveBeenCalledWith({ id: 'product', enabled: false })
  })

  it('offers Enable on a disabled entity and sends true', () => {
    render(<GoldRowActions gold={{ ...gold, enabled: false }} onRun={vi.fn()} />)
    fireEvent.click(screen.getByRole('button', { name: 'Enable' }))
    expect(toggleMutate).toHaveBeenCalledWith({ id: 'product', enabled: true })
  })
})
