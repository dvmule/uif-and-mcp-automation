import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Inbox } from 'lucide-react'
import { EmptyState, Note } from '../EmptyState'

describe('EmptyState', () => {
  it('renders icon, title and text', () => {
    const { container } = render(
      <EmptyState icon={Inbox} title="No syncs yet">
        Create one to get started
      </EmptyState>,
    )
    expect(screen.getByText('No syncs yet')).toBeInTheDocument()
    expect(screen.getByText('Create one to get started')).toBeInTheDocument()
    expect(container.querySelector('svg')).not.toBeNull()
  })

  it('renders an action slot under the text', async () => {
    const onClear = vi.fn()
    render(
      <EmptyState title="No syncs match these filters" action={<button onClick={onClear}>Clear all</button>}>
        Try a wider window
      </EmptyState>,
    )
    expect(screen.getByText('No syncs match these filters')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Clear all' }))
    expect(onClear).toHaveBeenCalledTimes(1)
  })

  it('renders the title as a heading when headingLevel is set', () => {
    render(<EmptyState title="Page not found" headingLevel={1} />)
    expect(screen.getByRole('heading', { level: 1, name: 'Page not found' })).toBeInTheDocument()
  })

  it('renders as a full-width table row with colSpan', () => {
    render(
      <table>
        <tbody>
          <EmptyState colSpan={5}>Nothing</EmptyState>
        </tbody>
      </table>,
    )
    expect(screen.getByText('Nothing').closest('td')).toHaveAttribute('colspan', '5')
  })
})

describe('Note', () => {
  it('renders children with a tone class and an icon', () => {
    const { container } = render(<Note tone="fail">Something broke</Note>)
    expect(screen.getByText('Something broke')).toBeInTheDocument()
    expect(container.firstElementChild?.className).toContain('text-signal-fail')
    expect(container.querySelector('svg')).not.toBeNull()
  })

  it('defaults to the mute tone', () => {
    const { container } = render(<Note>Just so you know</Note>)
    expect(container.firstElementChild?.className).toContain('bg-paper-head')
  })
})
