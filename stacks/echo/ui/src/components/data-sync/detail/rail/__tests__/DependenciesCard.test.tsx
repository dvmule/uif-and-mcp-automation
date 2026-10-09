import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { DependenciesCard } from '@/components/data-sync/detail/rail/DependenciesCard'

vi.mock('@/hooks/useDownstreamSyncs', () => ({
  useDownstreamSyncs: (fqn: string) => ({ data: fqn ? [] : [], isLoading: false }),
}))

describe('DependenciesCard', () => {
  it('renders DQ verb counts', () => {
    render(
      <MemoryRouter>
        <DependenciesCard silverFqn="items.t" dqChecks={[{ status: 'PASSED' as const }, { status: 'PASSED' as const }, { status: 'FAILING' as const }]} exportsCount={2} />
      </MemoryRouter>,
    )
    expect(screen.getByText(/3 quality checks/i)).toBeInTheDocument()
    expect(screen.getByText(/2 passing/i)).toBeInTheDocument()
    expect(screen.getByText(/1 failing/i)).toBeInTheDocument()
    expect(screen.getByText(/2 partner exports/i)).toBeInTheDocument()
  })

  it('names the gold tables that read this silver table, and says 0 when none do', () => {
    const { unmount } = render(
      <MemoryRouter>
        <DependenciesCard silverFqn="items.t" dqChecks={[]} exportsCount={0} goldReaders={['price', 'product']} />
      </MemoryRouter>,
    )
    expect(screen.getByText('2 gold tables read this silver table')).toBeInTheDocument()
    expect(screen.getByText('price, product')).toBeInTheDocument()
    unmount()
    render(<MemoryRouter><DependenciesCard silverFqn="items.t" dqChecks={[]} exportsCount={0} /></MemoryRouter>)
    expect(screen.getByText('0 gold tables read this silver table')).toBeInTheDocument()
  })

  it('shows empty-state for no DQ', () => {
    render(<MemoryRouter><DependenciesCard silverFqn="" dqChecks={[]} exportsCount={0} /></MemoryRouter>)
    expect(screen.getByText(/Add a quality check/i)).toBeInTheDocument()
  })
})
