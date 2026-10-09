import { describe, it, expect } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import { MemoryRouter, useRoutes } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { routes } from '@/router'

// A component router, as in chrome.integration.test: the data router's fetch Request fails under jsdom here.
function AppRoutes() {
  return useRoutes(routes)
}

function renderAt(path: string) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[path]}>
        <AppRoutes />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('Page not found', () => {
  it('an unknown path renders the not-found card inside the shell, with the path and three links', () => {
    renderAt('/m/data-sync/gold/nope/edit')
    const main = screen.getByRole('main')
    expect(within(main).getByRole('heading', { level: 1, name: 'Page not found' })).toBeInTheDocument()
    expect(within(main).getByText('/m/data-sync/gold/nope/edit')).toBeInTheDocument()
    expect(main).toHaveTextContent('It may have moved during the redesign.')
    expect(within(main).getByRole('link', { name: 'Home' })).toHaveAttribute('href', '/')
    expect(within(main).getByRole('link', { name: 'Replicate' })).toHaveAttribute('href', '/m/data-sync/syncs')
    expect(within(main).getByRole('link', { name: 'Transform' })).toHaveAttribute('href', '/m/data-sync/gold')
    // the shell is still there around it
    expect(screen.getByRole('navigation', { name: 'Echo menu' })).toBeInTheDocument()
  })

  it('is the last route, after every other child of the shell', () => {
    const children = routes.find((r) => r.path === '/')?.children ?? []
    expect(children.at(-1)?.path).toBe('*')
    expect(children.filter((r) => r.path === '*')).toHaveLength(1)
  })
})
