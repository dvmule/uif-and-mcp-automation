import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { TooltipProvider } from '@/components/ui/tooltip'
import { NewSyncWizard } from '../NewSyncWizard'
import { useWizardStore } from '@/store/wizardStore'
import { buildPipelineConfig } from '@/lib/newSyncConfig'
import { AWS_INCREMENTAL_SILVER } from '@/lib/__tests__/__fixtures__/newSyncStates'

const mutateAsync = vi.fn()
const triggerMutate = vi.fn()

vi.mock('@/hooks/useConnections', () => ({
  useConnections: () => ({
    data: [{ name: 'demo', arn: 'arn:demo', description: 'Demo connection' }],
    isLoading: false,
  }),
}))

vi.mock('@/hooks/useGcsConnections',     () => ({ useGcsConnections:     () => ({ data: [], isLoading: false }) }))
vi.mock('@/hooks/useSilverBqConnections',() => ({ useSilverBqConnections:() => ({ data: [], isLoading: false }) }))
vi.mock('@/hooks/usePipelines', () => ({
  useUpsertPipeline: () => ({ mutateAsync, isPending: false }),
  useTriggerRun: () => ({ mutate: triggerMutate, isPending: false }),
}))

function renderWiz() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}><TooltipProvider>
      <MemoryRouter initialEntries={['/m/data-sync/syncs/new']}>
        <Routes>
          <Route path="/m/data-sync/syncs/new" element={<NewSyncWizard />} />
          <Route path="/m/data-sync/syncs/:id" element={<p>sync page</p>} />
          <Route path="/m/data-sync/syncs" element={<p>syncs list</p>} />
        </Routes>
      </MemoryRouter>
    </TooltipProvider></QueryClientProvider>,
  )
}

function atReview() {
  useWizardStore.setState({ ...AWS_INCREMENTAL_SILVER, step: 5 })
}

describe('NewSyncWizard', () => {
  beforeEach(() => {
    mutateAsync.mockReset()
    triggerMutate.mockReset()
    useWizardStore.getState().reset()
    if (!window.matchMedia) {
      window.matchMedia = vi.fn().mockReturnValue({
        matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn(),
      }) as unknown as typeof window.matchMedia
    }
  })

  it('renders the "New replication" header with a breadcrumb back to Replicate', () => {
    renderWiz()
    expect(screen.getByRole('heading', { level: 1, name: /^New replication$/ })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Replicate' })).toHaveAttribute('href', '/m/data-sync/syncs')
  })

  it('step 1 is Source: the connection picker and the table input, with Back disabled', () => {
    renderWiz()
    expect(screen.getByText('Oracle connection')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /demo/ })).toBeInTheDocument()
    expect(screen.getByLabelText('Table name')).toBeInTheDocument()
    expect(screen.getByText('Step 1 of 6')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Back/ })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Next: Destination →' })).toBeDisabled()
    expect(screen.queryByRole('button', { name: /Deploy/ })).toBeNull()
  })

  it('done steps show their answer in the stepper and jump back when clicked', async () => {
    useWizardStore.setState({
      ...AWS_INCREMENTAL_SILVER,
      analyzeResult: { ...AWS_INCREMENTAL_SILVER.analyzeResult!, total_gb: 1.5, partition_strategy: 'numeric_range' },
      step: 2,
    })
    renderWiz()
    expect(screen.getByText('FIPM.X · fipm')).toBeInTheDocument()
    expect(screen.getByText('AWS')).toBeInTheDocument()
    expect(screen.getByText('Recommended from table analysis')).toBeInTheDocument()
    expect(screen.getByText('These are heuristics over the column names and types, not AI.')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: /Destination/ }))
    expect(useWizardStore.getState().step).toBe(1)
  })

  it('Cancel resets the wizard and goes back to the syncs list', async () => {
    useWizardStore.setState({ ...AWS_INCREMENTAL_SILVER, step: 3 })
    renderWiz()
    await userEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(screen.getByText('syncs list')).toBeInTheDocument()
    expect(useWizardStore.getState().step).toBe(0)
    expect(useWizardStore.getState().pipelineId).toBe('')
  })

  it('Deploy saves the built config and shows the success panel', async () => {
    mutateAsync.mockResolvedValue({})
    atReview()
    renderWiz()
    expect(screen.getByText('Step 6 of 6')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Deploy' }))
    expect(mutateAsync).toHaveBeenCalledWith({
      id: 'fipm-x-daily',
      config: buildPipelineConfig(AWS_INCREMENTAL_SILVER, 'dev'),
    })
    expect(await screen.findByText('Sync deployed')).toBeInTheDocument()
    expect(
      screen.getByText('fipm-x-daily is saved and enabled. Its first run starts at the next daily slot, or now if you run it.'),
    ).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'View sync' })).toHaveAttribute('href', '/m/data-sync/syncs/fipm-x-daily')
    expect(useWizardStore.getState().pipelineId).toBe('')
  })

  it('Run now triggers a run and opens the sync', async () => {
    mutateAsync.mockResolvedValue({})
    atReview()
    renderWiz()
    await userEvent.click(screen.getByRole('button', { name: 'Deploy' }))
    await userEvent.click(await screen.findByRole('button', { name: 'Run now' }))
    expect(triggerMutate).toHaveBeenCalledWith('fipm-x-daily')
    expect(screen.getByText('sync page')).toBeInTheDocument()
  })

  it('Set up another starts again on step 1', async () => {
    mutateAsync.mockResolvedValue({})
    atReview()
    renderWiz()
    await userEvent.click(screen.getByRole('button', { name: 'Deploy' }))
    await userEvent.click(await screen.findByRole('button', { name: 'Set up another' }))
    expect(screen.getByText('Step 1 of 6')).toBeInTheDocument()
  })

  it('a failed deploy stays on review, shows the server message and re-enables Deploy', async () => {
    mutateAsync.mockRejectedValue(new Error('pipeline fipm-x-daily already exists'))
    atReview()
    renderWiz()
    await userEvent.click(screen.getByRole('button', { name: 'Deploy' }))
    expect(await screen.findByTestId('wizard-notice')).toHaveTextContent('pipeline fipm-x-daily already exists')
    expect(screen.getByText('Step 6 of 6')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Deploy' })).toBeEnabled()
    expect(screen.queryByText('Sync deployed')).toBeNull()
    expect(useWizardStore.getState().pipelineId).toBe('fipm-x-daily')
  })

  it('a state failing validation is refused before anything is sent', async () => {
    useWizardStore.setState({ ...AWS_INCREMENTAL_SILVER, silverBqProject: '', step: 5 })
    renderWiz()
    await userEvent.click(screen.getByRole('button', { name: 'Deploy' }))
    expect(await screen.findByTestId('wizard-notice')).toHaveTextContent('BigQuery project is required')
    expect(mutateAsync).not.toHaveBeenCalled()
  })
})
