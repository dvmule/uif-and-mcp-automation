import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { DataTable, TablePager, type DataTableColumn } from '../DataTable'
import { EmptyState } from '../EmptyState'

interface Row {
  id: string
  name: string
  count: number
}

const rows: Row[] = [
  { id: 'a', name: 'Alpha', count: 1 },
  { id: 'b', name: 'Beta', count: 2 },
]

const columns: DataTableColumn<Row>[] = [
  { key: 'name', header: 'Name', cell: (r) => r.name, sortable: true },
  { key: 'count', header: 'Count', cell: (r) => r.count, align: 'right' },
]

describe('DataTable', () => {
  it('renders headers and rows, and right-aligns a right column', () => {
    render(<DataTable columns={columns} rows={rows} rowKey={(r) => r.id} />)
    expect(screen.getByRole('columnheader', { name: /Name/ })).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: 'Count' }).className).toContain('text-right')
    expect(screen.getByText('Alpha')).toBeInTheDocument()
    expect(screen.getByText('Beta')).toBeInTheDocument()
  })

  it('reports aria-sort and calls onSort with the column key', async () => {
    const onSort = vi.fn()
    render(
      <DataTable
        columns={columns}
        rows={rows}
        rowKey={(r) => r.id}
        sort={{ key: 'name', direction: 'desc' }}
        onSort={onSort}
      />,
    )
    const header = screen.getByRole('columnheader', { name: /Name/ })
    expect(header).toHaveAttribute('aria-sort', 'descending')
    expect(screen.getByRole('columnheader', { name: 'Count' })).not.toHaveAttribute('aria-sort')
    await userEvent.click(screen.getByRole('button', { name: /Name/ }))
    expect(onSort).toHaveBeenCalledWith('name')
  })

  it('names the table with ariaLabel', () => {
    render(<DataTable ariaLabel="Syncs" columns={columns} rows={rows} rowKey={(r) => r.id} />)
    expect(screen.getByRole('table', { name: 'Syncs' })).toBeInTheDocument()
  })

  it('uses an EmptyState element as the empty row without nesting it', () => {
    const { container } = render(
      <DataTable
        columns={columns}
        rows={[]}
        rowKey={(r) => r.id}
        empty={<EmptyState title="No rows match" action={<button>Clear all</button>} />}
      />,
    )
    expect(screen.getByText('No rows match').closest('td')).toHaveAttribute('colspan', '2')
    expect(container.querySelectorAll('td')).toHaveLength(1)
    expect(container.querySelector('td div')).toBeNull()
    expect(screen.getByRole('button', { name: 'Clear all' })).toBeInTheDocument()
  })

  it('shows the empty message as a full-width row', () => {
    render(<DataTable columns={columns} rows={[]} rowKey={(r) => r.id} empty="Nothing here" />)
    const cell = screen.getByText('Nothing here').closest('td')
    expect(cell).toHaveAttribute('colspan', '2')
  })

  it('shows skeleton rows while loading and hides the data', () => {
    const { container } = render(<DataTable columns={columns} rows={rows} rowKey={(r) => r.id} loading />)
    expect(container.querySelectorAll('tbody tr[aria-hidden="true"]').length).toBe(4)
    expect(screen.queryByText('Alpha')).toBeNull()
  })

  it('marks every body row as a hover group', () => {
    const { container } = render(<DataTable columns={columns} rows={rows} rowKey={(r) => r.id} />)
    const bodyRows = container.querySelectorAll('tbody tr')
    expect(bodyRows).toHaveLength(2)
    bodyRows.forEach((tr) => expect(tr.className.split(/\s+/)).toContain('group'))
  })

  it('drops the card frame when framed is false', () => {
    const { container } = render(<DataTable columns={columns} rows={rows} rowKey={(r) => r.id} framed={false} />)
    expect(container.firstElementChild?.className ?? '').not.toContain('rounded-card')
  })
})

describe('TablePager', () => {
  it('shows the range, with the page count in the buttons titles', () => {
    render(<TablePager pageIndex={0} pageSize={25} total={93} onPageChange={() => {}} />)
    expect(screen.getByText('1–25 of 93')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Previous page' })).toHaveAttribute('title', 'Page 1 of 4')
    expect(screen.getByRole('button', { name: 'Next page' })).toHaveAttribute('title', 'Page 1 of 4')
  })

  it('shows a short last page', () => {
    render(<TablePager pageIndex={3} pageSize={25} total={93} onPageChange={() => {}} />)
    expect(screen.getByText('76–93 of 93')).toBeInTheDocument()
  })

  it('handles no rows', () => {
    render(<TablePager pageIndex={0} pageSize={25} total={0} onPageChange={() => {}} />)
    expect(screen.getByText('0')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Next page' })).toHaveAttribute('title', 'Page 1 of 1')
  })

  it('drops the footer border when inline', () => {
    const { container, rerender } = render(
      <TablePager pageIndex={0} pageSize={25} total={93} onPageChange={() => {}} />,
    )
    expect(container.firstElementChild?.className).toContain('border-t')
    rerender(<TablePager pageIndex={0} pageSize={25} total={93} onPageChange={() => {}} inline />)
    expect(container.firstElementChild?.className).toContain('ml-auto')
    expect(container.firstElementChild?.className).not.toContain('border-t')
  })

  it('disables previous on the first page and next on the last', () => {
    const { rerender } = render(<TablePager pageIndex={0} pageSize={25} total={93} onPageChange={() => {}} />)
    expect(screen.getByRole('button', { name: 'Previous page' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Next page' })).toBeEnabled()
    rerender(<TablePager pageIndex={3} pageSize={25} total={93} onPageChange={() => {}} />)
    expect(screen.getByRole('button', { name: 'Next page' })).toBeDisabled()
  })

  it('asks for the neighbouring page', async () => {
    const onPageChange = vi.fn()
    render(<TablePager pageIndex={1} pageSize={25} total={93} onPageChange={onPageChange} />)
    await userEvent.click(screen.getByRole('button', { name: 'Next page' }))
    await userEvent.click(screen.getByRole('button', { name: 'Previous page' }))
    expect(onPageChange).toHaveBeenNthCalledWith(1, 2)
    expect(onPageChange).toHaveBeenNthCalledWith(2, 0)
  })
})
