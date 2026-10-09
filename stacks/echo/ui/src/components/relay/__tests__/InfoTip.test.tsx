import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { InfoTip } from '../InfoTip'

describe('InfoTip', () => {
  it('keeps the text in the DOM, hidden until hover, focus or a pin', () => {
    render(<InfoTip text="Oracle tables replicated into bronze and silver." />)
    const tip = screen.getByRole('tooltip')
    expect(tip).toHaveTextContent('Oracle tables replicated into bronze and silver.')
    expect(tip.className).toContain('opacity-0')
    expect(screen.getByRole('button', { name: 'About this section' })).toHaveAttribute('aria-expanded', 'false')
  })

  it('uses the given label', () => {
    render(<InfoTip text="x" label="About Replicate" />)
    expect(screen.getByRole('button', { name: 'About Replicate' })).toBeInTheDocument()
  })

  it('pins on click and unpins on Escape', async () => {
    const user = userEvent.setup()
    render(<InfoTip text="Details" label="About Syncs" />)
    const button = screen.getByRole('button', { name: 'About Syncs' })
    await user.click(button)
    expect(button).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByRole('tooltip').className).toContain('opacity-100')
    await user.keyboard('{Escape}')
    expect(button).toHaveAttribute('aria-expanded', 'false')
  })

  it('click again and blur both unpin', async () => {
    const user = userEvent.setup()
    render(
      <div>
        <InfoTip text="Details" />
        <button type="button">elsewhere</button>
      </div>,
    )
    const button = screen.getByRole('button', { name: 'About this section' })
    await user.click(button)
    await user.click(button)
    expect(button).toHaveAttribute('aria-expanded', 'false')
    await user.click(button)
    await user.tab()
    expect(button).toHaveAttribute('aria-expanded', 'false')
  })

  it('renders nothing without text', () => {
    const { container } = render(<InfoTip text="" />)
    expect(container).toBeEmptyDOMElement()
  })
})
