import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { RouterProvider, createMemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { partnerExportRoutes } from '../router'
import { api } from '@/lib/api'
import { openBeaconPanel } from '@/lib/echoPaths'

// UIFPE-484: the entry routes leave the SPA for the Beacon panel; jsdom
// cannot navigate, so the page-leave helper is mocked and asserted.
vi.mock('@/lib/echoPaths', async (importOriginal) => {
  const mod = await importOriginal<typeof import('@/lib/echoPaths')>()
  return { ...mod, openBeaconPanel: vi.fn() }
})

vi.mock('@/lib/api', () => ({
  api: {
    partnerExport: {
      listExports: vi.fn(), listPartners: vi.fn(), listRuns: vi.fn(),
      listFormats: vi.fn(), getExport: vi.fn(), getPartner: vi.fn(),
      listExportRuns: vi.fn(),
    },
  },
}))

vi.mock('@/hooks/useCatalogDatasets', () => ({
  useCatalogDatasets: () => ({ data: [], isLoading: false }),
}))
vi.mock('@/hooks/useConnections', () => ({ useConnections: () => ({ data: [], isLoading: false }) }))

beforeEach(() => {
  vi.mocked(openBeaconPanel).mockClear()
  Object.values(api.partnerExport).forEach(fn => (fn as ReturnType<typeof vi.fn>).mockReset())
  vi.mocked(api.partnerExport.listExports).mockResolvedValue([])
  vi.mocked(api.partnerExport.listPartners).mockResolvedValue([])
  vi.mocked(api.partnerExport.listRuns).mockResolvedValue({ items: [], next_token: null })
  vi.mocked(api.partnerExport.listFormats).mockResolvedValue([])
})

function renderAt(path: string) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const router = createMemoryRouter(partnerExportRoutes, { initialEntries: [path] })
  return render(
    <QueryClientProvider client={qc}><RouterProvider router={router} /></QueryClientProvider>,
  )
}

describe('partner-export routes', () => {
  it('/m/partner-export leaves for the Beacon panel (UIFPE-484)', async () => {
    renderAt('/m/partner-export')
    await waitFor(() => expect(vi.mocked(openBeaconPanel)).toHaveBeenCalled())
  })

  it('/m/partner-export/exports leaves for the Beacon panel (UIFPE-484)', async () => {
    renderAt('/m/partner-export/exports')
    await waitFor(() => expect(vi.mocked(openBeaconPanel)).toHaveBeenCalled())
  })

  it('/m/partner-export/overview renders the KPI tiles', async () => {
    renderAt('/m/partner-export/overview')
    await waitFor(() => expect(screen.getByText(/partner export overview/i)).toBeInTheDocument())
  })

  it('/m/partner-export/exports/new renders the wizard', async () => {
    renderAt('/m/partner-export/exports/new')
    await waitFor(() => expect(screen.getByText(/new export/i)).toBeInTheDocument())
  })
})
