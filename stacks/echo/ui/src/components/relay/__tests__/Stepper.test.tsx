import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Stepper } from '../Stepper'

const steps = [
  { label: 'Source', answer: 'Source: FIPM.X' },
  { label: 'Destination', answer: 'Lake' },
  { label: 'Schedule' },
]

describe('Stepper', () => {
  it('marks the active step and shows answers only for done steps', () => {
    render(<Stepper steps={steps} current={1} />)
    const items = screen.getAllByRole('listitem')
    expect(items[1]).toHaveAttribute('aria-current', 'step')
    expect(items[0]).not.toHaveAttribute('aria-current')
    expect(screen.getByText('Source: FIPM.X')).toBeInTheDocument()
    expect(screen.queryByText('Lake')).toBeNull()
  })

  it('shows a hint only while the step is not done', () => {
    const hinted = [{ label: 'Kind' }, { label: 'Subject', hint: 'prefilled: db.t' }]
    const { rerender } = render(<Stepper steps={hinted} current={0} />)
    expect(screen.getByText('prefilled: db.t')).toBeInTheDocument()
    rerender(<Stepper steps={[...hinted, { label: 'Schedule' }]} current={2} />)
    expect(screen.queryByText('prefilled: db.t')).toBeNull()
  })

  it('has no buttons without onStepClick', () => {
    render(<Stepper steps={steps} current={2} />)
    expect(screen.queryByRole('button')).toBeNull()
  })

  it('makes only earlier steps clickable', async () => {
    const onStepClick = vi.fn()
    render(<Stepper steps={steps} current={2} onStepClick={onStepClick} />)
    expect(screen.getAllByRole('button')).toHaveLength(2)
    await userEvent.click(screen.getByRole('button', { name: /Source/ }))
    expect(onStepClick).toHaveBeenCalledWith(0)
  })
})
