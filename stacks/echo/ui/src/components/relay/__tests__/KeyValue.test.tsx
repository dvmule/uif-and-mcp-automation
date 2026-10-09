import { describe, it, expect } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import { Kv, KvGroup } from '../KeyValue'

describe('KvGroup and Kv', () => {
  it('renders a titled list of term / definition rows', () => {
    render(
      <KvGroup title="Schedule and runtime" testid="g">
        <Kv label="Schedule">daily</Kv>
        <Kv label="Glue">G.1X × 5 · 60 min timeout</Kv>
      </KvGroup>,
    )
    const group = screen.getByTestId('g')
    expect(within(group).getByRole('heading', { name: 'Schedule and runtime' })).toBeInTheDocument()
    expect(within(group).getAllByRole('term').map((t) => t.textContent)).toEqual(['Schedule', 'Glue'])
    expect(within(group).getAllByRole('definition').map((d) => d.textContent)).toEqual([
      'daily',
      'G.1X × 5 · 60 min timeout',
    ])
  })

  it('reads as "label value" in text', () => {
    render(
      <dl>
        <Kv label="Snapshot gate">on</Kv>
      </dl>,
    )
    expect(screen.getByRole('term').parentElement).toHaveTextContent('Snapshot gate on')
  })
})
