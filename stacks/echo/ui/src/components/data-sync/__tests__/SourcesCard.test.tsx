import { describe, it, expect } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import { SourcesCard } from '@/components/data-sync/gold/SourcesCard'

const sources = [
  { alias: 'p1', silver_table: 'uif_echo_{env}_silver_price.price_1' },
  { alias: 'hdr', silver_table: 'uif_echo_{env}_silver_price.price_list_header' },
]

describe('SourcesCard', () => {
  it('lists every silver source with {env} resolved, and flags only the missing one', () => {
    render(<SourcesCard sources={sources} missing={['uif_echo_{env}_silver_price.price_1']} env="dev" />)
    const items = within(screen.getByTestId('sources-card')).getAllByRole('listitem')
    expect(items).toHaveLength(2)
    expect(items[0]).toHaveTextContent('uif_echo_dev_silver_price.price_1')
    expect(within(items[0]).getByText('missing')).toBeInTheDocument()
    expect(items[1]).toHaveTextContent('uif_echo_dev_silver_price.price_list_header')
    expect(within(items[1]).queryByText('missing')).not.toBeInTheDocument()
  })

  it('flags nothing when no source is missing', () => {
    render(<SourcesCard sources={sources} missing={[]} />)
    expect(screen.queryByText('missing')).not.toBeInTheDocument()
    expect(screen.getByText('uif_echo_{env}_silver_price.price_1')).toBeInTheDocument()
  })

  it('says so when the config lists no sources', () => {
    render(<SourcesCard sources={undefined} missing={[]} />)
    expect(screen.getByText(/No silver sources/)).toBeInTheDocument()
  })
})
