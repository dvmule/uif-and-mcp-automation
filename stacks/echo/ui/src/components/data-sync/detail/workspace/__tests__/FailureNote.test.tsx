import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { FailureNote } from '@/components/data-sync/detail/workspace/FailureNote'
import { pipelineRunRow } from '@/lib/runsTable'
import type { PipelineConfig, RunMetrics } from '@/lib/types'

const pipeline = { pipeline_id: 'p-1', extract: { table: 'EDS.PRICE_LIST' } } as unknown as PipelineConfig
const NOW = new Date('2026-09-29T17:50:00Z')

function row(over: Partial<RunMetrics>) {
  return pipelineRunRow(pipeline, {
    job_name: 'uif-echo-dev-bronze-ingest-eds', pipeline_id: 'p-1', run_id: 'r-1', status: 'SUCCEEDED',
    started_at: '2026-09-29T15:50:00Z', rows_read: 0, rows_written: 0, ...over,
  })
}

describe('FailureNote', () => {
  it('is hidden when the latest run succeeded, and when there is no run', () => {
    const { container, rerender } = render(<FailureNote row={row({})} onOpenRun={vi.fn()} now={NOW} />)
    expect(container).toBeEmptyDOMElement()
    rerender(<FailureNote row={null} onOpenRun={vi.fn()} now={NOW} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('explains a credentials failure in plain words, with the pattern, the time, an open-run link and the raw error', () => {
    const onOpenRun = vi.fn()
    render(
      <FailureNote
        row={row({ status: 'FAILED', error_message: 'ORA-01017: invalid username/password; logon denied' })}
        onOpenRun={onOpenRun}
        now={NOW}
      />,
    )
    const box = screen.getByTestId('latest-failure-explain')
    expect(screen.getByText("The source database refused the sync's login.").tagName).toBe('B')
    expect(box).toHaveTextContent(/Oracle rejected the credentials/)
    expect(box).toHaveTextContent('pattern: ORA-01017 · 15:50 UTC · 2 h ago · open run')
    const raw = screen.getByText('ORA-01017: invalid username/password; logon denied')
    expect(raw.closest('details')).not.toHaveAttribute('open')
    expect(screen.getByText('Technical detail')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'open run' }))
    expect(onOpenRun).toHaveBeenCalledWith('r-1')
  })

  it('is a fail-tone note for a failure and a warn-tone note for a warning', () => {
    const { rerender } = render(<FailureNote row={row({ status: 'FAILED', error_message: 'boom' })} onOpenRun={vi.fn()} now={NOW} />)
    expect(screen.getByTestId('latest-failure').firstElementChild).toHaveClass('bg-signal-fail-bg', 'mb-5')
    rerender(<FailureNote row={row({ status: 'WARNING', error_message: null })} onOpenRun={vi.fn()} now={NOW} />)
    expect(screen.getByTestId('latest-failure').firstElementChild).toHaveClass('bg-signal-warn-bg')
  })

  it('shows for a warning too, and says when no message was captured', () => {
    render(<FailureNote row={row({ status: 'WARNING', error_message: null })} onOpenRun={vi.fn()} now={NOW} />)
    expect(screen.getByTestId('latest-failure-explain')).toHaveTextContent('Finished with a warning.')
    expect(screen.getByTestId('latest-failure-explain')).toHaveTextContent('15:50 UTC · 2 h ago · open run')
    expect(screen.getByText('No error message captured.')).toBeInTheDocument()
  })
})
