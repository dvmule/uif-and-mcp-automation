import { useState } from 'react'
import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Toolbar, ToolbarCheckbox, ToolbarField, ToolbarSearch } from '../Toolbar'

describe('Toolbar', () => {
  it('lays out a wrapping row with the border under it', () => {
    const { container } = render(
      <Toolbar>
        <span>child</span>
      </Toolbar>,
    )
    expect(container.firstElementChild?.className).toContain('flex-wrap')
    expect(container.firstElementChild?.className).toContain('items-end')
    expect(container.firstElementChild?.className).toContain('border-b')
  })

  it('renders the caption as a label when htmlFor is given, else as a span', () => {
    render(
      <Toolbar>
        <ToolbarField label="Find" htmlFor="find-box">
          <input id="find-box" />
        </ToolbarField>
        <ToolbarField label="Domain">
          <button type="button">All domains</button>
        </ToolbarField>
      </Toolbar>,
    )
    expect(screen.getByText('Find').tagName).toBe('LABEL')
    expect(screen.getByLabelText('Find')).toHaveAttribute('id', 'find-box')
    expect(screen.getByText('Domain').tagName).toBe('SPAN')
    expect(screen.getByText('Domain').className).toContain('font-bold')
  })

  it('ToolbarSearch carries the aria-label and calls onChange with the value', async () => {
    const onChange = vi.fn()
    function Harness() {
      const [q, setQ] = useState('')
      return (
        <ToolbarSearch
          id="q"
          value={q}
          placeholder="Table or sync"
          onChange={(v) => {
            onChange(v)
            setQ(v)
          }}
        />
      )
    }
    render(<Harness />)
    const box = screen.getByRole('searchbox', { name: 'Table or sync' })
    await userEvent.type(box, 'ab')
    expect(onChange).toHaveBeenLastCalledWith('ab')
    expect(box).toHaveValue('ab')
  })

  it('ToolbarSearch prefers an explicit aria-label', () => {
    render(<ToolbarSearch id="q" value="" onChange={() => {}} placeholder="Search…" ariaLabel="Find a check" />)
    expect(screen.getByRole('searchbox', { name: 'Find a check' })).toBeInTheDocument()
  })

  it('ToolbarCheckbox reports the checked state', async () => {
    const onChange = vi.fn()
    render(
      <ToolbarCheckbox id="show" checked={false} onChange={onChange}>
        Show disabled
      </ToolbarCheckbox>,
    )
    await userEvent.click(screen.getByRole('checkbox', { name: 'Show disabled' }))
    expect(onChange).toHaveBeenCalledWith(true)
  })
})
