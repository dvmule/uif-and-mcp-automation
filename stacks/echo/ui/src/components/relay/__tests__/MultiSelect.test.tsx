import { useState } from 'react'
import { describe, it, expect, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MultiSelect } from '../MultiSelect'
import type { MultiSelectItem } from '@/lib/multiSelect'

const ITEMS: MultiSelectItem[] = [
  { key: 'healthy', label: 'Healthy', meta: 11 },
  { key: 'failing', label: 'Failing', meta: 1 },
  { key: 'running', label: 'Running' },
  { key: 'behind', label: 'Behind' },
  { key: 'disabled', label: 'Disabled' },
]

function Harness({ initial = [], onChange }: { initial?: string[]; onChange?: (next: string[]) => void }) {
  const [selected, setSelected] = useState<string[]>(initial)
  return (
    <MultiSelect
      label="Filter by status"
      noun="statuses"
      placeholder="No status"
      items={ITEMS}
      selected={selected}
      onChange={(next) => {
        onChange?.(next)
        setSelected(next)
      }}
    />
  )
}

const closed = () => screen.getByRole('button', { name: 'Filter by status' })

describe('MultiSelect', () => {
  it('reads the placeholder, one name, a count of N, or All in the closed box', () => {
    const { unmount } = render(<Harness />)
    expect(closed()).toHaveTextContent('No status')
    unmount()

    const one = render(<Harness initial={['failing']} />)
    expect(closed()).toHaveTextContent(/^Failing$/)
    one.unmount()

    const some = render(<Harness initial={['healthy', 'failing']} />)
    expect(closed()).toHaveTextContent(/^2 statuses selected\s*of 5$/)
    some.unmount()

    render(<Harness initial={ITEMS.map((i) => i.key)} />)
    expect(closed()).toHaveTextContent('All statuses (5)')
  })

  it('opens on click with the search box focused, and search narrows the options', async () => {
    const user = userEvent.setup()
    render(<Harness />)
    expect(screen.queryByRole('listbox')).toBeNull()
    await user.click(closed())
    expect(closed()).toHaveAttribute('aria-expanded', 'true')
    const search = screen.getByRole('textbox', { name: 'Search statuses' })
    await waitFor(() => expect(search).toHaveFocus())
    expect(screen.getAllByRole('option')).toHaveLength(5)
    await user.type(search, 'in')
    expect(screen.getAllByRole('option').map((o) => o.textContent)).toEqual(['Failing1', 'Running', 'Behind'])
    await user.clear(search)
    await user.type(search, 'zzz')
    expect(screen.queryAllByRole('option')).toHaveLength(0)
    expect(screen.getByText('No match for “zzz”.')).toBeInTheDocument()
  })

  it('Select all picks every option, then Clear all empties it, with the n / N count', async () => {
    const user = userEvent.setup()
    render(<Harness initial={['failing']} />)
    await user.click(closed())
    expect(screen.getByRole('button', { name: /Select all/ })).toHaveTextContent('1 / 5')
    await user.click(screen.getByRole('button', { name: /Select all/ }))
    expect(screen.getByRole('button', { name: /Clear all/ })).toHaveTextContent('5 / 5')
    expect(closed()).toHaveTextContent('All statuses (5)')
    await user.click(screen.getByRole('button', { name: /Clear all/ }))
    expect(screen.getByRole('button', { name: /Select all/ })).toHaveTextContent('0 / 5')
    expect(closed()).toHaveTextContent('No status')
  })

  it('toggling an option calls onChange and marks aria-selected', async () => {
    const user = userEvent.setup()
    const onChange = vi.fn()
    render(<Harness initial={['healthy']} onChange={onChange} />)
    await user.click(closed())
    const running = screen.getByRole('option', { name: 'Running' })
    expect(running).toHaveAttribute('aria-selected', 'false')
    await user.click(running)
    expect(onChange).toHaveBeenLastCalledWith(['healthy', 'running'])
    expect(screen.getByRole('option', { name: 'Running' })).toHaveAttribute('aria-selected', 'true')
    await user.click(screen.getByRole('option', { name: /Healthy/ }))
    expect(onChange).toHaveBeenLastCalledWith(['running'])
  })

  it('closes on Escape and on a click outside', async () => {
    const user = userEvent.setup()
    render(
      <div>
        <Harness />
        <p>outside</p>
      </div>,
    )
    await user.click(closed())
    expect(screen.getByRole('listbox')).toBeInTheDocument()
    await user.keyboard('{Escape}')
    expect(screen.queryByRole('listbox')).toBeNull()
    expect(closed()).toHaveAttribute('aria-expanded', 'false')

    await user.click(closed())
    expect(screen.getByRole('listbox')).toBeInTheDocument()
    await user.click(screen.getByText('outside'))
    expect(screen.queryByRole('listbox')).toBeNull()
  })

  it('an empty list reads "No {noun} yet" and shows the empty text', async () => {
    const user = userEvent.setup()
    render(
      <MultiSelect label="Filter by tag" noun="tags" items={[]} selected={[]} onChange={() => {}} emptyText="No tags." />,
    )
    const button = screen.getByRole('button', { name: 'Filter by tag' })
    expect(button).toHaveTextContent('No tags yet')
    await user.click(button)
    expect(screen.getByText('No tags.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Select all/ })).toBeDisabled()
  })

  it('outlines the closed box in accent only for a proper subset', () => {
    const cases: [string[], boolean][] = [
      [[], false],
      [['failing'], true],
      [['healthy', 'failing'], true],
      [ITEMS.map((i) => i.key), false],
    ]
    for (const [initial, narrowed] of cases) {
      const { unmount } = render(<Harness initial={initial} />)
      const cls = closed().className.split(/\s+/)
      expect(cls.includes('border-accent')).toBe(narrowed)
      expect(cls.includes('border-line')).toBe(!narrowed)
      unmount()
    }
  })

  it('uses the 38px toolbar size by default', () => {
    render(<Harness />)
    expect(closed().className).toContain('h-[38px]')
  })
})
