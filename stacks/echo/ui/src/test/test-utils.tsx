import type { ReactElement, ReactNode } from 'react'
import { render, type RenderOptions, type RenderResult } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

export interface RenderWithProvidersOptions extends Omit<RenderOptions, 'wrapper'> {
  /** Initial MemoryRouter path. Default `/`. */
  route?: string
  /** Reuse a QueryClient (e.g. to assert cache state after the render). */
  queryClient?: QueryClient
}

export function makeQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  })
}

export function renderWithProviders(
  ui: ReactElement,
  { route = '/', queryClient, ...rtl }: RenderWithProvidersOptions = {},
): RenderResult & { queryClient: QueryClient } {
  const qc = queryClient ?? makeQueryClient()
  const Wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[route]}>{children}</MemoryRouter>
    </QueryClientProvider>
  )
  const result = render(ui, { wrapper: Wrapper, ...rtl })
  return { ...result, queryClient: qc }
}
