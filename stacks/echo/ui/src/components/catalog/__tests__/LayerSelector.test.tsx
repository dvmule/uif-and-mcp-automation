import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { LayerSelector } from '../LayerSelector'

describe('LayerSelector', () => {
  it('renders three layer buttons with current layer pressed', () => {
    render(<LayerSelector value="silver" onChange={() => {}} />)
    const silver = screen.getByRole('button', { name: /silver/i })
    const bronze = screen.getByRole('button', { name: /bronze/i })
    const gold = screen.getByRole('button', { name: /gold/i })
    expect(silver).toHaveAttribute('aria-pressed', 'true')
    expect(bronze).toHaveAttribute('aria-pressed', 'false')
    expect(gold).toHaveAttribute('aria-pressed', 'false')
  })

  it('calls onChange when a different layer is clicked', async () => {
    const user = userEvent.setup()
    const onChange = vi.fn()
    render(<LayerSelector value="bronze" onChange={onChange} />)
    await user.click(screen.getByRole('button', { name: /silver/i }))
    expect(onChange).toHaveBeenCalledWith('silver')
  })
})
