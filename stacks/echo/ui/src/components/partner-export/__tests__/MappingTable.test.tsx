import { describe, it, expect, vi } from 'vitest'
import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { renderWithProviders } from '@/test/test-utils'
import { MappingTable } from '../MappingTable'
import type { MappingField } from '@/lib/types'

const rows: MappingField[] = [
  { source_col: 'ID', output_field: 'id', type: 'int' },
  { source_col: 'NAME', output_field: 'name', type: 'string' },
]

const noop = () => {}

describe('MappingTable', () => {
  it('renders one row per mapping field', () => {
    renderWithProviders(<MappingTable rows={rows} onAdd={noop} onUpdate={noop} onRemove={noop} />)
    expect(screen.getByDisplayValue('ID')).toBeInTheDocument()
    expect(screen.getByDisplayValue('name')).toBeInTheDocument()
  })

  it('calls onAdd when + Add row is clicked', async () => {
    const onAdd = vi.fn()
    renderWithProviders(<MappingTable rows={rows} onAdd={onAdd} onUpdate={noop} onRemove={noop} />)
    await userEvent.click(screen.getByRole('button', { name: /add row/i }))
    expect(onAdd).toHaveBeenCalledOnce()
  })

  it('calls onUpdate when a source_col is edited', async () => {
    const onUpdate = vi.fn()
    renderWithProviders(<MappingTable rows={rows} onAdd={noop} onUpdate={onUpdate} onRemove={noop} />)
    const input = screen.getByDisplayValue('ID')
    await userEvent.clear(input)
    await userEvent.type(input, 'ID2')
    expect(onUpdate).toHaveBeenLastCalledWith(0, { source_col: 'ID2' })
  })

  it('calls onRemove with the row index', async () => {
    const onRemove = vi.fn()
    renderWithProviders(<MappingTable rows={rows} onAdd={noop} onUpdate={noop} onRemove={onRemove} />)
    await userEvent.click(screen.getAllByRole('button', { name: /remove/i })[1])
    expect(onRemove).toHaveBeenCalledWith(1)
  })
})
