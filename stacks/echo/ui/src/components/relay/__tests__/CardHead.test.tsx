import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { CardHead } from '../CardHead'

describe('CardHead', () => {
  it('renders an h2 title and the right slot', () => {
    render(<CardHead title="Activity" right={<span>right side</span>} />)
    expect(screen.getByRole('heading', { level: 2, name: 'Activity' })).toHaveClass('text-[18px]', 'font-extrabold')
    expect(screen.getByText('right side')).toBeInTheDocument()
  })
})
