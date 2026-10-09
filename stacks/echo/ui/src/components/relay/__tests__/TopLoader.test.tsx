import { afterEach, beforeEach, describe, it, expect, vi } from 'vitest'
import { act, render, screen } from '@testing-library/react'
import { TopLoader } from '../TopLoader'

describe('TopLoader', () => {
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => vi.useRealTimers())

  it('stays hidden during the 200 ms grace, then appears', () => {
    render(<TopLoader active />)
    expect(screen.queryByRole('progressbar')).toBeNull()
    act(() => {
      vi.advanceTimersByTime(199)
    })
    expect(screen.queryByRole('progressbar')).toBeNull()
    act(() => {
      vi.advanceTimersByTime(1)
    })
    expect(screen.getByRole('progressbar', { name: 'Loading' })).toBeInTheDocument()
  })

  it('never shows for a call that finishes inside the grace', () => {
    const { rerender } = render(<TopLoader active />)
    act(() => {
      vi.advanceTimersByTime(100)
    })
    rerender(<TopLoader active={false} />)
    act(() => {
      vi.advanceTimersByTime(500)
    })
    expect(screen.queryByRole('progressbar')).toBeNull()
  })

  it('disappears as soon as it goes inactive, and waits the grace again next time', () => {
    const { rerender } = render(<TopLoader active />)
    act(() => {
      vi.advanceTimersByTime(300)
    })
    expect(screen.getByRole('progressbar')).toBeInTheDocument()
    rerender(<TopLoader active={false} />)
    expect(screen.queryByRole('progressbar')).toBeNull()
    rerender(<TopLoader active />)
    expect(screen.queryByRole('progressbar')).toBeNull()
  })
})
