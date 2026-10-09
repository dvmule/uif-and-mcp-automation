import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, within, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { Runs } from '../Runs'

const hoursAgo = (h: number) => new Date(Date.now() - h * 3_600_000).toISOString()

const h = vi.hoisted(() => ({
  triggerMutate: vi.fn(),
  goldError: false,
  slowPipeline: false,
  pipelines: [] as unknown[],
  runsByPipeline: {} as Record<string, unknown>,
  goldRuns: [] as unknown[],
}))

vi.mock('@/hooks/usePipelines', () => ({
  usePipelines: () => ({ data: h.pipelines, isLoading: false }),
  useTriggerRun: () => ({ mutate: h.triggerMutate, isPending: false }),
}))
vi.mock('@/hooks/useRuns', () => ({
  useRuns: (id: string) =>
    id === 'slow' && h.slowPipeline
      ? { data: undefined, isLoading: true, isError: false }
      : { data: h.runsByPipeline[id], isLoading: false, isError: false },
}))
vi.mock('@/hooks/useGoldRuns', () => ({
  useGoldRuns: () =>
    h.goldError
      ? { data: undefined, isLoading: false, error: new Error('boom') }
      : { data: { items: h.goldRuns, truncated: false }, isLoading: false, error: null },
}))
vi.mock('@/hooks/useGoldPipelines', () => ({
  useGoldPipelines: () => ({
    data: [{ gold_id: 'price', enabled: true, target: { table: 'gold_price' } }],
    isLoading: false,
  }),
}))
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

const pipe = (id: string, table: string, enabled = true) => ({
  pipeline_id: id,
  enabled,
  extract: { table, mode: 'incremental' },
  schedule: { frequency: 'daily' },
})

const brun = (id: string, status: string, ago: number, extra: Record<string, unknown> = {}) => ({
  run_id: id,
  pipeline_id: 'a',
  job_name: 'uif-echo-dev-bronze-ingest',
  status,
  started_at: hoursAgo(ago),
  duration_sec: 30,
  rows_read: 100,
  rows_written: 90,
  ...extra,
})

function Probe() {
  const loc = useLocation()
  return <div data-testid="loc">{loc.pathname + loc.search}</div>
}

function renderAt(search = '') {
  return render(
    <MemoryRouter initialEntries={[`/m/data-sync/runs${search}`]}>
      <Runs />
      <Probe />
    </MemoryRouter>,
  )
}

const bodyRows = () => screen.getAllByRole('row').slice(1)
const search = () => screen.getByTestId('loc').textContent

describe('Insights › Jobs', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    h.goldError = false
    h.slowPipeline = false
    h.pipelines = [pipe('a', 'INV.A'), pipe('b', 'INV.B')]
    h.runsByPipeline = {
      a: {
        items: [
          brun('r-ok', 'SUCCEEDED', 3),
          brun('r-fail', 'FAILED', 5, { error_message: 'ORA-01017: invalid username/password', rows_read: 0, rows_written: 0 }),
          brun('r-3d', 'SUCCEEDED', 72),
        ],
      },
      b: { items: [] },
    }
    h.goldRuns = [
      {
        run_id: 'jr_gold', gold_id: 'price', job_name: 'uif-echo-dev-gold-transform', status: 'SUCCEEDED',
        started_at: hoursAgo(1), duration_sec: 198, rows_written: 0, affected_keys: 0,
      },
    ]
  })

  it('has the page title, the description behind the info tip, and no longer says Job Runs', () => {
    renderAt()
    expect(screen.getByRole('heading', { level: 1, name: 'Jobs' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'About Jobs' })).toBeInTheDocument()
    expect(screen.getByRole('tooltip')).toHaveTextContent(/Every run across bronze, silver and gold, newest first/)
    expect(screen.queryByText('Job Runs')).not.toBeInTheDocument()
  })

  it('says the range and how many runs and objects are in it, from the filtered rows', () => {
    renderAt()
    expect(screen.getByText('Last 24 hours · 3 runs across 2 objects')).toBeInTheDocument()
  })

  it('the range line follows the window and the filters', () => {
    renderAt('?window=all&layer=bronze')
    expect(screen.getByText('All loaded · 3 runs across 1 object')).toBeInTheDocument()
  })

  it('lists bronze and gold together, newest first, hiding what is older than 24 h by default', () => {
    renderAt()
    const rows = bodyRows().map((r) => r.textContent ?? '')
    expect(rows).toHaveLength(3)
    expect(rows[0]).toContain('price') // gold, 1 h ago
    expect(rows[1]).toContain('INV.A') // 3 h ago
    expect(rows[2]).toContain('INV.A') // 5 h ago
    expect(screen.getByRole('button', { name: '24 hours' })).toHaveAttribute('aria-pressed', 'true')
  })

  it('links each object to its own page, gold included', () => {
    renderAt()
    expect(screen.getByRole('link', { name: 'price' })).toHaveAttribute('href', '/m/data-sync/gold/price')
    expect(screen.getAllByRole('link', { name: 'INV.A' })[0]).toHaveAttribute('href', '/m/data-sync/syncs/a')
  })

  it('shows layer, kind, status, duration and rows read → written', () => {
    renderAt()
    const gold = bodyRows()[0]
    expect(within(gold).getByText('Gold')).toBeInTheDocument()
    expect(within(gold).getByText('Transform')).toBeInTheDocument()
    expect(within(gold).getByText('Succeeded · no changes')).toBeInTheDocument()
    expect(within(gold).getByText('3m 18s')).toBeInTheDocument()
    expect(within(gold).getByText('— → 0')).toBeInTheDocument()
    const bronze = bodyRows()[1]
    expect(within(bronze).getByText('Bronze')).toBeInTheDocument()
    expect(within(bronze).getByText('Incremental')).toBeInTheDocument()
    expect(within(bronze).getByText('100 → 90')).toBeInTheDocument()
  })

  it('?window=all brings the older run back', () => {
    renderAt('?window=all')
    expect(bodyRows()).toHaveLength(4)
    expect(screen.getByRole('button', { name: 'All loaded' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('button', { name: '24 hours' })).toHaveAttribute('aria-pressed', 'false')
  })

  it('a status in the URL fills the Status MultiSelect and filters, and unticking it lifts it', () => {
    renderAt('?status=FAILED')
    expect(bodyRows()).toHaveLength(1)
    const status = screen.getByRole('button', { name: 'Filter by status' })
    expect(status).toHaveTextContent('Failed')
    fireEvent.click(status)
    fireEvent.click(screen.getByRole('option', { name: /^Failed/ }))
    expect(search()).toBe('/m/data-sync/runs')
    expect(status).toHaveTextContent('All statuses (5)')
    expect(bodyRows()).toHaveLength(3)
  })

  it('the header range control writes the window, and the 24 h default leaves the URL plain', () => {
    renderAt()
    expect(screen.queryByRole('combobox', { name: 'Time' })).not.toBeInTheDocument()
    const range = screen.getByRole('group', { name: 'Time range' })
    const buttons = within(range).getAllByRole('button')
    expect(buttons.map((b) => b.textContent)).toEqual(['24 hours', '7 days', 'All loaded'])
    expect(buttons.map((b) => b.getAttribute('aria-pressed'))).toEqual(['true', 'false', 'false'])
    fireEvent.click(within(range).getByRole('button', { name: 'All loaded' }))
    expect(search()).toBe('/m/data-sync/runs?window=all')
    expect(bodyRows()).toHaveLength(4)
    fireEvent.click(within(range).getByRole('button', { name: '24 hours' }))
    expect(search()).toBe('/m/data-sync/runs')
    expect(bodyRows()).toHaveLength(3)
  })

  it('search narrows by object or run id and is kept in the URL', () => {
    renderAt()
    const box = screen.getByRole('searchbox', { name: 'Search object or run id' })
    fireEvent.change(box, { target: { value: 'jr_gold' } })
    expect(bodyRows()).toHaveLength(1)
    expect(search()).toBe('/m/data-sync/runs?q=jr_gold')
  })

  it('the old ?sync= link limits to that sync, over all its runs, as an Object pick', () => {
    renderAt('?sync=a')
    expect(screen.getByRole('button', { name: 'Filter by object' })).toHaveTextContent('INV.A')
    const rows = bodyRows()
    expect(rows).toHaveLength(3)
    expect(rows.every((r) => !(r.textContent ?? '').includes('price'))).toBe(true)
  })

  it('the toolbar offers status, layer, object and the view, and applies a pick', async () => {
    const user = userEvent.setup()
    renderAt()
    expect(screen.getByRole('group', { name: 'View' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Filter by status' })).toHaveTextContent('All statuses (5)')
    const layer = screen.getByRole('button', { name: 'Filter by layer' })
    expect(layer).toHaveTextContent('All layers (4)')
    await user.click(layer)
    await user.click(screen.getByRole('option', { name: /^Gold/ }))
    expect(search()).toBe('/m/data-sync/runs?layer=gold')
    expect(bodyRows()).toHaveLength(1)
    expect(layer).toHaveTextContent('Gold')
    // objects come from both the syncs and the gold tables, with their own search box
    await user.click(screen.getByRole('button', { name: 'Filter by object' }))
    expect(screen.getByRole('option', { name: /^INV\.B/ })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: /^price/ })).toBeInTheDocument()
    await user.type(screen.getByRole('textbox', { name: 'Search objects' }), 'pri')
    expect(within(screen.getByRole('listbox')).getAllByRole('option')).toHaveLength(1)
  })

  it('clicking a row opens the drawer via ?run= and keeps the other filters', () => {
    renderAt('?status=FAILED')
    fireEvent.click(bodyRows()[0])
    expect(search()).toBe('/m/data-sync/runs?status=FAILED&run=r-fail')
    const dialog = screen.getByRole('dialog')
    expect(within(dialog).getByTestId('run-explain')).toHaveTextContent(/refused the sync's login/)
  })

  it('?run= opens the drawer on load, and it survives the table being filtered past that run', () => {
    renderAt('?run=jr_gold&layer=bronze')
    const dialog = screen.getByRole('dialog')
    expect(within(dialog).getByText('Open gold table')).toBeInTheDocument()
    expect(within(dialog).queryByRole('button', { name: 'Retry run' })).not.toBeInTheDocument()
  })

  it('Retry run in the drawer triggers the sync, and is off when that sync is disabled', () => {
    renderAt('?run=r-fail')
    fireEvent.click(screen.getByRole('button', { name: 'Retry run' }))
    expect(h.triggerMutate).toHaveBeenCalledWith('a')
  })

  it('disables Retry for a disabled sync', () => {
    h.pipelines = [pipe('a', 'INV.A', false), pipe('b', 'INV.B')]
    renderAt('?run=r-fail')
    expect(screen.getByRole('button', { name: 'Retry run' })).toBeDisabled()
  })

  it('pages 25 at a time', () => {
    h.runsByPipeline = {
      a: { items: Array.from({ length: 30 }, (_, i) => brun(`r${i}`, 'SUCCEEDED', 2 + i / 100)) },
      b: { items: [] },
    }
    h.goldRuns = []
    renderAt()
    expect(bodyRows()).toHaveLength(25)
    expect(screen.getByText('1–25 of 30')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Next page' }))
    expect(bodyRows()).toHaveLength(5)
  })

  it('sorts by Started when the header is clicked', () => {
    renderAt()
    fireEvent.click(screen.getByRole('button', { name: /Started/ }))
    const rows = bodyRows().map((r) => r.textContent ?? '')
    expect(rows[0]).toContain('INV.A')
    expect(rows[2]).toContain('price')
  })

  describe('partial data is labelled', () => {
    it('says so while some syncs are still loading, and still shows the rest', () => {
      h.pipelines = [pipe('a', 'INV.A'), pipe('slow', 'INV.SLOW')]
      h.slowPipeline = true
      renderAt()
      expect(screen.getByText(/Some syncs' runs are still loading/)).toBeInTheDocument()
      expect(bodyRows().length).toBeGreaterThan(0)
    })

    it('says gold is unavailable but keeps the silver rows', () => {
      h.goldError = true
      renderAt()
      expect(screen.getByText(/Gold runs unavailable right now/)).toBeInTheDocument()
      expect(bodyRows()).toHaveLength(2)
      expect(screen.queryByRole('link', { name: 'price' })).not.toBeInTheDocument()
    })

    it('no note when everything loaded', () => {
      renderAt()
      expect(screen.queryByText(/still loading/)).not.toBeInTheDocument()
      expect(screen.queryByText(/unavailable/)).not.toBeInTheDocument()
    })
  })

  describe('empty states', () => {
    it('no runs anywhere: No runs yet', () => {
      h.runsByPipeline = { a: { items: [] }, b: { items: [] } }
      h.goldRuns = []
      renderAt()
      expect(screen.getByText('No runs yet')).toBeInTheDocument()
      expect(screen.getByRole('button', { name: /Export CSV/ })).toBeDisabled()
    })

    it('runs exist but none match: says so and offers Clear all', () => {
      renderAt('?layer=reconcile')
      expect(screen.getByText('No runs match these filters')).toBeInTheDocument()
      const buttons = screen.getAllByRole('button', { name: 'Clear all' })
      fireEvent.click(buttons[buttons.length - 1])
      expect(bodyRows()).toHaveLength(3)
    })
  })

  describe('Export CSV', () => {
    it('downloads exactly the filtered rows', async () => {
      const blobs: Blob[] = []
      const createObjectURL = vi.fn((b: Blob) => (blobs.push(b), 'blob:x'))
      const revokeObjectURL = vi.fn()
      Object.assign(URL, { createObjectURL, revokeObjectURL })
      const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})

      renderAt('?status=FAILED')
      fireEvent.click(screen.getByRole('button', { name: /Export CSV/ }))

      expect(click).toHaveBeenCalledTimes(1)
      expect(revokeObjectURL).toHaveBeenCalled()
      const text = await new Promise<string>((resolve) => {
        const reader = new FileReader()
        reader.onload = () => resolve(String(reader.result))
        reader.readAsText(blobs[0])
      })
      const lines = text.split('\r\n')
      expect(lines).toHaveLength(2) // header + the one failed run
      expect(lines[1]).toContain('r-fail')
      click.mockRestore()
    })
  })

  describe('object view', () => {
    const objectRows = () => within(screen.getByRole('table', { name: 'Objects' })).getAllByRole('row').slice(1)

    it('the View toggle writes ?view=objects and shows one row per object', () => {
      renderAt()
      const view = screen.getByRole('group', { name: 'View' })
      expect(within(view).getByRole('button', { name: 'Runs' })).toHaveAttribute('aria-pressed', 'true')
      fireEvent.click(within(view).getByRole('button', { name: 'Objects' }))
      expect(search()).toBe('/m/data-sync/runs?view=objects')
      expect(screen.queryByRole('table', { name: 'Runs' })).not.toBeInTheDocument()
      const rows = objectRows()
      expect(rows).toHaveLength(2)
      expect(rows[0]).toHaveTextContent('price') // gold, ran 1 h ago
      expect(rows[1]).toHaveTextContent('INV.A')
      fireEvent.click(within(view).getByRole('button', { name: 'Runs' }))
      expect(search()).toBe('/m/data-sync/runs')
      expect(bodyRows()).toHaveLength(3)
    })

    it('each object shows its last runs as dots, its success share and an open link', () => {
      renderAt('?view=objects')
      const [price, inv] = objectRows()
      expect(within(price).getByRole('img')).toHaveAttribute('aria-label', 'Last 1 run, oldest first: Succeeded · no changes')
      expect(within(inv).getByRole('img')).toHaveAttribute('aria-label', 'Last 2 runs, oldest first: Failed, Succeeded')
      expect(within(inv).getByTitle(/^Failed · .* · r-fail$/)).toBeInTheDocument()
      expect(within(inv).getByTitle(/^Succeeded · .* · r-ok$/)).toBeInTheDocument()
      expect(within(price).getByText('100%')).toHaveAttribute('title', '1 of 1 finished run')
      expect(within(inv).getByText('50%')).toHaveAttribute('title', '1 of 2 finished runs')
      expect(within(inv).getByText('bronze')).toBeInTheDocument()
      expect(within(inv).getByText(/^daily/)).toBeInTheDocument()
      expect(within(inv).getByRole('link', { name: 'Open INV.A' })).toHaveAttribute('href', '/m/data-sync/syncs/a')
      expect(within(price).getByRole('link', { name: 'Open price' })).toHaveAttribute('href', '/m/data-sync/gold/price')
    })

    it('the count line names objects, their health, and the loaded runs', () => {
      renderAt('?view=objects')
      expect(screen.getByTestId('objects-count')).toHaveTextContent(
        '2 objects · 2 healthy · 0 failing · 0 disabled · filtered from 4 loaded runs',
      )
    })

    it('filters apply before grouping: a status keeps only the objects with a matching run', () => {
      renderAt('?view=objects&status=FAILED')
      const rows = objectRows()
      expect(rows).toHaveLength(1)
      expect(rows[0]).toHaveTextContent('INV.A')
      expect(within(rows[0]).getByText('0%')).toBeInTheDocument()
      expect(screen.getByText('Last 24 hours · 1 run across 1 object')).toBeInTheDocument()
    })

    it('Clear all keeps the object view', () => {
      renderAt('?view=objects&layer=reconcile')
      expect(screen.getByText('No objects match these filters')).toBeInTheDocument()
      const buttons = screen.getAllByRole('button', { name: 'Clear all' })
      fireEvent.click(buttons[buttons.length - 1])
      expect(search()).toBe('/m/data-sync/runs?view=objects')
      expect(objectRows()).toHaveLength(2)
    })

    it('?run= still opens the drawer in the object view', () => {
      renderAt('?view=objects&run=r-fail')
      expect(within(screen.getByRole('dialog')).getByTestId('run-explain')).toHaveTextContent(/refused the sync's login/)
    })

    it('a disabled sync counts as disabled, not healthy', () => {
      h.pipelines = [pipe('a', 'INV.A', false), pipe('b', 'INV.B')]
      renderAt('?view=objects')
      expect(within(objectRows()[1]).getByText('disabled')).toBeInTheDocument()
      expect(screen.getByTestId('objects-count')).toHaveTextContent('2 objects · 1 healthy · 0 failing · 1 disabled')
    })

    it('Export CSV in the object view still exports the filtered runs', async () => {
      const blobs: Blob[] = []
      Object.assign(URL, { createObjectURL: vi.fn((b: Blob) => (blobs.push(b), 'blob:x')), revokeObjectURL: vi.fn() })
      const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
      renderAt('?view=objects')
      fireEvent.click(screen.getByRole('button', { name: /Export CSV/ }))
      const text = await new Promise<string>((resolve) => {
        const reader = new FileReader()
        reader.onload = () => resolve(String(reader.result))
        reader.readAsText(blobs[0])
      })
      expect(text.split('\r\n')).toHaveLength(4) // header + three runs
      click.mockRestore()
    })
  })

  it('opening the drawer does not lose the rows behind it', async () => {
    renderAt()
    fireEvent.click(bodyRows()[1])
    await waitFor(() => expect(screen.getByRole('dialog')).toBeInTheDocument())
    expect(screen.getAllByText('INV.A').length).toBeGreaterThan(0)
  })
})
