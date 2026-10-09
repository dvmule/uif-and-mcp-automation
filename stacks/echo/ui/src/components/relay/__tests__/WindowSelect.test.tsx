import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { WindowSelect } from '../WindowSelect'

const options = [['24h', 'Last 24 hours'], ['7d', 'Last 7 days']] as const

describe('WindowSelect', () => {
  it('is a labelled native select showing the value', () => {
    render(<WindowSelect label="Jobs window" value="7d" onChange={() => {}} options={options} />)
    const select = screen.getByRole('combobox', { name: 'Jobs window' })
    expect(select).toHaveValue('7d')
    expect(screen.getAllByRole('option').map((o) => o.textContent)).toEqual(['Last 24 hours', 'Last 7 days'])
    expect(select).toHaveClass('h-[30px]', 'border-line', 'focus:border-accent')
  })

  it('takes an id for a toolbar caption, and the toolbar size matches the 38px controls', () => {
    render(
      <>
        <label htmlFor="w">Window</label>
        <WindowSelect id="w" size="toolbar" label="Window" value="24h" onChange={() => {}} options={options} />
      </>,
    )
    const select = screen.getByRole('combobox', { name: 'Window' })
    expect(select).toHaveAttribute('id', 'w')
    expect(select).toHaveClass('h-[38px]', 'text-body')
    expect(select).not.toHaveClass('h-[30px]')
  })

  it('calls onChange with the picked value', () => {
    const onChange = vi.fn()
    render(<WindowSelect label="Jobs window" value="24h" onChange={onChange} options={options} />)
    fireEvent.change(screen.getByRole('combobox', { name: 'Jobs window' }), { target: { value: '7d' } })
    expect(onChange).toHaveBeenCalledWith('7d')
  })
})
