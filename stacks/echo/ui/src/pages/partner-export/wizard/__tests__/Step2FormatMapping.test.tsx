import { describe, it, expect, beforeEach } from 'vitest'
import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { renderWithProviders } from '@/test/test-utils'
import { Step2FormatMapping } from '../Step2FormatMapping'
import { usePartnerExportWizardStore } from '@/store/partnerExportWizardStore'

beforeEach(() => {
  usePartnerExportWizardStore.getState().reset()
})

describe('Step2FormatMapping — layout', () => {
  it('renders five format chips (csv/json/parquet live, fixed_width/xml soon)', () => {
    renderWithProviders(<Step2FormatMapping />)
    for (const k of ['csv', 'json', 'parquet']) expect(screen.getByRole('button', { name: new RegExp(k, 'i') })).toBeEnabled()
    for (const k of ['fixed_width', 'xml']) expect(screen.getByRole('button', { name: new RegExp(k, 'i') })).toBeDisabled()
  })

  it('add row writes into the store', async () => {
    renderWithProviders(<Step2FormatMapping />)
    await userEvent.click(screen.getByRole('button', { name: /add row/i }))
    expect(usePartnerExportWizardStore.getState().mapping).toHaveLength(1)
  })
})

describe('Step2FormatMapping — advanced disclosure', () => {
  it('Advanced is collapsed by default', () => {
    renderWithProviders(<Step2FormatMapping />)
    expect(screen.queryByTestId('advanced-block')).not.toBeInTheDocument()
  })

  it('clicking the disclosure reveals the SQL transform + python stub', async () => {
    renderWithProviders(<Step2FormatMapping />)
    await userEvent.click(screen.getByRole('button', { name: /advanced/i }))
    expect(screen.getByTestId('advanced-block')).toBeInTheDocument()
    expect(screen.getByLabelText(/sql transform/i)).toBeInTheDocument()
    expect(screen.getByLabelText(/python template/i)).toBeDisabled()
  })

  it('SQL transform writes into format.sql_transform', async () => {
    renderWithProviders(<Step2FormatMapping />)
    await userEvent.click(screen.getByRole('button', { name: /advanced/i }))
    await userEvent.type(screen.getByLabelText(/sql transform/i), 'SELECT 1')
    expect(usePartnerExportWizardStore.getState().format.sql_transform).toBe('SELECT 1')
  })
})

describe('Step2FormatMapping — chip changes preview', () => {
  beforeEach(() => {
    const st = usePartnerExportWizardStore.getState()
    st.addMappingRow()
    st.updateMappingRow(0, { source_col: 'ID', output_field: 'id', type: 'int' })
  })

  it('CSV preview appears by default', () => {
    renderWithProviders(<Step2FormatMapping />)
    expect(screen.getByTestId('output-preview-pre').textContent).toContain('id')
  })

  it('clicking JSON chip switches the preview to NDJSON', async () => {
    renderWithProviders(<Step2FormatMapping />)
    await userEvent.click(screen.getByRole('button', { name: /json/i }))
    expect(screen.getByTestId('output-preview-pre').textContent).toContain('"id"')
  })

  it('clicking Parquet swaps the preview to schema dump', async () => {
    renderWithProviders(<Step2FormatMapping />)
    await userEvent.click(screen.getByRole('button', { name: /parquet/i }))
    expect(screen.getByText(/parquet schema/i)).toBeInTheDocument()
  })
})
