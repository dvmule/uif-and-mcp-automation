import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, within, waitFor } from '@testing-library/react'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Syncs } from '../Syncs'
import type { PipelineConfig, RunMetrics } from '@/lib/types'

const SYNC: PipelineConfig = {
  pipeline_id: 'sync_one',
  version: 'latest',
  enabled: true,
  source: { secret_arn: 'arn:secret' },
  extract: { table: 'INV.ONE', mode: 'full' },
  target: { s3_path: 's3://x/' },
  glue: { worker_type: 'G.1X', num_workers: 2, timeout_minutes: 60 },
  schedule: { frequency: 'daily' },
  silver: { enabled: true, primary_key_cols: ['id'] },
  metadata: { domain: 'Product' },
} as PipelineConfig

// Each test decides what the pipelines call returns.
let pipelinesResult: { data?: PipelineConfig[]; isLoading: boolean; isError?: boolean; refetch?: () => void } = {
  data: [SYNC], isLoading: false,
}
const refetch = vi.fn()
const deleteMutate = vi.fn()
const triggerMutate = vi.fn()

vi.mock('@/hooks/usePipelines', () => ({
  usePipelines: () => pipelinesResult,
  useTogglePipeline: () => ({ mutate: vi.fn(), isPending: false }),
  useTriggerRun:    () => ({ mutate: triggerMutate, isPending: false }),
  useDeletePipeline:() => ({ mutate: deleteMutate, isPending: false }),
}))

vi.mock('@/hooks/useRuns', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/hooks/useRuns')>()
  return {
    ...actual,
    useRuns: () => ({ data: { items: [] } }),
    useActiveRunId: () => undefined,
    useSyncsActiveRunPolls: () => {},
  }
})

// The rows' runs come from useFilteredPipelines -> api.getRuns, one call per pipeline.
const getRuns = vi.fn()
vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { ...actual.api, getRuns: (...a: unknown[]) => getRuns(...a) } }
})

// Pin the env so the {env} placeholder resolves to a known value.
vi.mock('@/lib/envConfig', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/envConfig')>()),
  getEnv: () => 'dev',
}))

const HOURS_AGO = (h: number) => new Date(Date.now() - h * 3600_000).toISOString()

function run(over: Partial<RunMetrics> = {}): RunMetrics {
  return {
    job_name: 'bronze-ingest', run_id: 'r1', pipeline_id: 'sync_one',
    status: 'SUCCEEDED', started_at: HOURS_AGO(2), completed_at: HOURS_AGO(2),
    rows_read: 1284, rows_written: 1200,
    ...over,
  }
}

function LocationProbe() {
  const loc = useLocation()
  return <div data-testid="location">{loc.pathname}{loc.search}</div>
}

function renderWithProviders(node: React.ReactNode, initialEntries?: string[]) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={initialEntries}>
        {node}
        <LocationProbe />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  pipelinesResult = { data: [SYNC], isLoading: false, refetch }
  getRuns.mockReset().mockResolvedValue({ items: [] })
  refetch.mockClear()
  deleteMutate.mockClear()
  triggerMutate.mockClear()
  if (!window.matchMedia) {
    window.matchMedia = vi.fn().mockReturnValue({
      matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn(),
    }) as unknown as typeof window.matchMedia
  }
})

describe('Syncs list', () => {
  it('renders the "Replicate" page heading, not the old hero or "Pipelines"', () => {
    renderWithProviders(<Syncs />)
    expect(screen.getByRole('heading', { level: 1, name: 'Replicate' })).toBeInTheDocument()
    expect(screen.queryByText(/Data\s*Sync\s*Jobs/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/^Pipelines$/)).not.toBeInTheDocument()
    // The description sits behind the ⓘ; the count moved to the count line.
    expect(screen.getByRole('button', { name: 'About Replicate' })).toBeInTheDocument()
    expect(screen.getByRole('tooltip')).toHaveTextContent('Oracle tables replicated into bronze and silver.')
    expect(screen.getByTestId('stat-total')).toHaveTextContent('1 sync')
  })

  it('offers New replication as a link to the wizard, and no New Sync button', () => {
    renderWithProviders(<Syncs />)
    const link = screen.getByRole('link', { name: 'New replication' })
    expect(link.getAttribute('href')).toBe('/m/data-sync/syncs/new')
    expect(screen.queryByRole('link', { name: /New Sync/i })).not.toBeInTheDocument()
  })

  it('shows Cloud + Layers columns in list view', () => {
    renderWithProviders(<Syncs />)
    expect(screen.getByRole('columnheader', { name: /Cloud/i })).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: /Layers/i })).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: /Rows read → written/i })).toBeInTheDocument()
  })

  it('orders the columns Table, Cloud, Layers, Schedule, Last run, Status, Freshness, Rows, Actions', () => {
    renderWithProviders(<Syncs />)
    expect(screen.getAllByRole('columnheader').map(th => th.textContent)).toEqual([
      'Table', 'Cloud', 'Layers', 'Schedule', 'Last run', 'Status', 'Freshness', 'Rows read → written', 'Actions',
    ])
  })

  it('draws the layers as accent pills', () => {
    renderWithProviders(<Syncs />)
    for (const layer of ['Bronze', 'Silver']) {
      const cls = screen.getByText(layer).className.split(/\s+/)
      expect(cls).toEqual(expect.arrayContaining(['bg-pearson-mist', 'text-accent']))
    }
  })

  it('renders Cloud badge and Bronze/Silver chips for a sync row, and no Gold chip', () => {
    renderWithProviders(<Syncs />)
    expect(screen.getAllByText('AWS').length).toBeGreaterThanOrEqual(1)
    expect(screen.getByText('Bronze')).toBeInTheDocument()
    expect(screen.getByText('Silver')).toBeInTheDocument()
    // UIFPE-511: a sync feeding a gold entity does not get a Gold chip.
    expect(screen.queryByText('Gold')).not.toBeInTheDocument()
  })

  it('links the sync name to /m/data-sync/syncs/:syncId, with id and domain beneath', () => {
    renderWithProviders(<Syncs />)
    const link = screen.getByRole('link', { name: 'INV.ONE' })
    expect(link.getAttribute('href')).toBe('/m/data-sync/syncs/sync_one')
    expect(screen.getByText('sync_one · Product')).toBeInTheDocument()
    // The open icon at the row's right edge goes to the same page.
    expect(screen.getByRole('link', { name: 'Open INV.ONE' }).getAttribute('href')).toBe('/m/data-sync/syncs/sync_one')
  })

  it('shows no gold rows and never asks for gold data (they moved to Transform)', () => {
    renderWithProviders(<Syncs />)
    expect(screen.queryByRole('button', { name: 'Run gold transform' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Change run time/ })).not.toBeInTheDocument()
  })
})

describe('Syncs list - run-derived cells and count line', () => {
  it('fills Status, Last run, Rows and Freshness from the pipeline runs', async () => {
    getRuns.mockResolvedValue({ items: [run()] })
    renderWithProviders(<Syncs />)
    const row = screen.getByText('INV.ONE').closest('tr') as HTMLElement
    expect(await within(row).findByText('Succeeded')).toBeInTheDocument()
    expect(within(row).getByText('1,284 → 1,200')).toBeInTheDocument()
    expect(within(row).getByText(/about 2 hours ago/)).toBeInTheDocument()
    expect(within(row).getByText('Fresh')).toBeInTheDocument()
    expect(within(row).getByText(/^daily · \d\d:\d\d UTC/)).toBeInTheDocument()
  })

  it('shows a dash when the last run carries no row counts', async () => {
    getRuns.mockResolvedValue({ items: [run({ rows_read: null, rows_written: null })] })
    renderWithProviders(<Syncs />)
    const row = screen.getByText('INV.ONE').closest('tr') as HTMLElement
    await within(row).findByText('Succeeded')
    expect(within(row).queryByText(/→/)).not.toBeInTheDocument()
  })

  it('shimmers only the run-derived cells while a pipeline\'s runs are still loading', () => {
    getRuns.mockReturnValue(new Promise(() => {}))          // never answers
    renderWithProviders(<Syncs />)
    const row = screen.getByText('INV.ONE').closest('tr') as HTMLElement
    expect(within(row).getByRole('link', { name: 'INV.ONE' })).toBeInTheDocument()
    expect(within(row).getByText('Bronze')).toBeInTheDocument()
    expect(within(row).queryByText('Skipped')).not.toBeInTheDocument()
    expect(row.querySelectorAll('[data-slot="skeleton"]').length).toBe(4)
  })

  it('counts the count-line segments from the loaded runs', async () => {
    getRuns.mockResolvedValue({ items: [run({ status: 'FAILED' })] })
    renderWithProviders(<Syncs />)
    expect(screen.getByTestId('stat-total')).toHaveTextContent('1 sync')
    expect(screen.getByTestId('stat-active')).toHaveTextContent('1 active')
    expect(screen.getByTestId('stat-disabled')).toHaveTextContent('0 disabled')
    await waitFor(() => expect(screen.getByTestId('stat-failing')).toHaveTextContent('1 failing'))
    expect(screen.getByTestId('stat-behind')).toHaveTextContent('0 behind schedule')
    expect(screen.getByTestId('stat-running')).toHaveTextContent('0 running now')
    expect(screen.getByTestId('stat-total').parentElement).toHaveTextContent(
      '1 sync · 1 active · 1 failing · 0 behind schedule · 0 running now · 0 disabled',
    )
  })

  it('shimmers the count-line figures while syncs load', () => {
    pipelinesResult = { data: undefined, isLoading: true }
    renderWithProviders(<Syncs />)
    expect(screen.getByTestId('stat-total').querySelector('[data-slot="skeleton"]')).not.toBeNull()
    expect(screen.getByTestId('stat-active').querySelector('[data-slot="skeleton"]')).not.toBeNull()
  })
})

describe('Syncs list - filters, search, sort, view, columns', () => {
  it('reads the URL facets into the MultiSelects', () => {
    renderWithProviders(<Syncs />, ['/?status=FAILED&freshness=stale'])
    expect(screen.getByRole('button', { name: 'Filter by status' })).toHaveTextContent('Failed')
    expect(screen.getByRole('button', { name: 'Filter by freshness' })).toHaveTextContent('Stale')
    expect(screen.getByRole('button', { name: 'Filter by domain' })).toHaveTextContent('Product')
    expect(screen.getByRole('button', { name: 'Filter by source' })).toHaveTextContent('No sources yet')
  })

  it('shows every status ticked with no status in the URL, and a pick writes status=FAILED', () => {
    renderWithProviders(<Syncs />)
    const status = screen.getByRole('button', { name: 'Filter by status' })
    expect(status).toHaveTextContent('All statuses (6)')
    fireEvent.click(status)
    expect(screen.getAllByRole('option').every(o => o.getAttribute('aria-selected') === 'true')).toBe(true)
    fireEvent.click(screen.getByRole('option', { name: /^Failed/ }))
    expect(screen.getByTestId('location').textContent).toBe('/?status=FAILED')
    expect(status).toHaveTextContent('Failed')
    fireEvent.click(screen.getByRole('option', { name: /^Running/ }))
    expect(screen.getByTestId('location').textContent).toBe('/?status=FAILED%2CRUNNING')
    expect(status).toHaveTextContent('2 statuses selected of 6')
  })

  it('Select all restores every value and drops the key, leaving the other facets', () => {
    renderWithProviders(<Syncs />, ['/?domain=Product&status=FAILED'])
    fireEvent.click(screen.getByRole('button', { name: 'Filter by status' }))
    fireEvent.click(screen.getByRole('button', { name: /Select all/ }))
    expect(screen.getByTestId('location').textContent).toBe('/?domain=Product')
    expect(screen.getByRole('button', { name: 'Filter by status' })).toHaveTextContent('All statuses (6)')
  })

  it('unticking the last picked value drops the key (there is no show-nothing state)', () => {
    renderWithProviders(<Syncs />, ['/?domain=Product&status=FAILED'])
    fireEvent.click(screen.getByRole('button', { name: 'Filter by status' }))
    fireEvent.click(screen.getByRole('option', { name: /^Failed/ }))
    expect(screen.getByTestId('location').textContent).toBe('/?domain=Product')
  })

  it('the filtered-empty Clear all empties every filter param', () => {
    renderWithProviders(<Syncs />, ['/?domain=Other&status=FAILED&tag=x'])
    fireEvent.click(screen.getByRole('button', { name: 'Clear all' }))
    expect(screen.getByTestId('location').textContent).toBe('/')
  })

  it('still filters rows by the URL facets (domain)', () => {
    renderWithProviders(<Syncs />, ['/?domain=Product'])
    expect(screen.getByText('INV.ONE')).toBeInTheDocument()
    renderWithProviders(<Syncs />, ['/?domain=Other'])
    expect(screen.getAllByText('No syncs match these filters').length).toBeGreaterThanOrEqual(1)
  })

  it('search narrows the rows, and a miss gives the filtered-empty state that clears it', () => {
    renderWithProviders(<Syncs />)
    const box = screen.getByRole('searchbox', { name: 'Search table or sync' })
    expect(box).toHaveAttribute('placeholder', 'Table or sync')
    fireEvent.change(box, { target: { value: 'nomatch' } })
    expect(screen.queryByText('INV.ONE')).not.toBeInTheDocument()
    expect(screen.getByText('No syncs match these filters')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Clear all' }))
    expect(screen.getByText('INV.ONE')).toBeInTheDocument()
    expect(box).toHaveValue('')
  })

  it('clicking a sortable header flips its direction (aria-sort)', () => {
    renderWithProviders(<Syncs />)
    const th = screen.getByRole('columnheader', { name: /Table/i })
    expect(th).toHaveAttribute('aria-sort', 'ascending')
    fireEvent.click(within(th).getByRole('button'))
    expect(th).toHaveAttribute('aria-sort', 'descending')
  })

  it('switches to the grid view and back', () => {
    renderWithProviders(<Syncs />)
    fireEvent.click(screen.getByRole('button', { name: 'Grid view' }))
    expect(screen.queryByRole('columnheader', { name: /Cloud/i })).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'INV.ONE' }).getAttribute('href')).toBe('/m/data-sync/syncs/sync_one')
    fireEvent.click(screen.getByRole('button', { name: 'List view' }))
    expect(screen.getByRole('columnheader', { name: /Cloud/i })).toBeInTheDocument()
  })

  it('the column chooser hides a column', () => {
    renderWithProviders(<Syncs />)
    fireEvent.click(screen.getByRole('button', { name: 'Toggle columns' }))
    fireEvent.click(screen.getByRole('checkbox', { name: 'Cloud' }))
    expect(screen.queryByRole('columnheader', { name: /Cloud/i })).not.toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: /Layers/i })).toBeInTheDocument()
  })

  it('pages at 25 rows from the pager at the toolbar end', () => {
    const many = Array.from({ length: 30 }, (_, i) => ({
      ...SYNC, pipeline_id: `p${String(i).padStart(2, '0')}`,
      extract: { table: `T.${String(i).padStart(2, '0')}`, mode: 'full' },
    })) as PipelineConfig[]
    pipelinesResult = { data: many, isLoading: false, refetch }
    renderWithProviders(<Syncs />)
    expect(screen.getByText('1–25 of 30')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Next page' })).toHaveAttribute('title', 'Page 1 of 2')
    expect(screen.queryByText('T.29')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Next page' }))
    expect(screen.getByText('T.29')).toBeInTheDocument()
    expect(screen.getByText('26–30 of 30')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Next page' })).toHaveAttribute('title', 'Page 2 of 2')
  })
})

describe('Syncs list - actions and states', () => {
  it('keeps Trigger run, Disable and Remove sync as hover actions before the open icon', () => {
    renderWithProviders(<Syncs />)
    const row = screen.getByText('INV.ONE').closest('tr') as HTMLElement
    expect(row.className.split(/\s+/)).toContain('group')
    const trigger = within(row).getByRole('button', { name: 'Trigger run' })
    expect(within(row).getByRole('button', { name: 'Disable' })).toBeInTheDocument()
    expect(within(row).getByRole('button', { name: 'Remove sync' })).toBeInTheDocument()
    const hover = trigger.closest('.opacity-0') as HTMLElement
    expect(hover.className.split(/\s+/)).toEqual(
      expect.arrayContaining(['group-hover:opacity-100', 'group-focus-within:opacity-100']),
    )
    expect(within(row).getAllByRole('link').at(-1)).toHaveAccessibleName('Open INV.ONE')
  })

  it('Run triggers the sync by pipeline id', () => {
    renderWithProviders(<Syncs />)
    fireEvent.click(screen.getByRole('button', { name: 'Trigger run' }))
    expect(triggerMutate).toHaveBeenCalledWith('sync_one')
  })

  it('Remove asks for confirmation, then deletes by pipeline id', () => {
    renderWithProviders(<Syncs />)
    fireEvent.click(screen.getByRole('button', { name: 'Remove sync' }))
    expect(screen.getByRole('dialog')).toHaveTextContent('Are you sure to remove the data sync?')
    expect(deleteMutate).not.toHaveBeenCalled()
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Remove sync' }))
    expect(deleteMutate).toHaveBeenCalledWith('sync_one', expect.anything())
  })

  it('says "No syncs yet" with the New replication button when there are none', () => {
    pipelinesResult = { data: [], isLoading: false, refetch }
    renderWithProviders(<Syncs />)
    expect(screen.getByText('No syncs yet')).toBeInTheDocument()
    expect(screen.getByText('Create the first replication.')).toBeInTheDocument()
    expect(screen.getAllByRole('link', { name: 'New replication' }).length).toBe(2)
  })

  it('says it could not load, and Retry refetches', () => {
    pipelinesResult = { data: undefined, isLoading: false, isError: true, refetch }
    renderWithProviders(<Syncs />)
    expect(screen.getByText(/Couldn.t load syncs\./)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
    expect(refetch).toHaveBeenCalledTimes(1)
  })
})
