import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { SyncMoreMenu } from '@/components/data-sync/detail/overflow/SyncMoreMenu'

describe('SyncMoreMenu', () => {
  it('exposes all 6 actions', () => {
    render(
      <SyncMoreMenu
        onClone={vi.fn()}
        onRawJson={vi.fn()}
        onResetWatermark={vi.fn()}
        onReBootstrap={vi.fn()}
        onDisable={vi.fn()}
        onDelete={vi.fn()}
      />
    )
    fireEvent.click(screen.getByRole('button', { name: /More/i }))
    expect(screen.getByText(/Clone sync/i)).toBeInTheDocument()
    expect(screen.getByText(/View raw JSON/i)).toBeInTheDocument()
    expect(screen.getByText(/Reset watermark/i)).toBeInTheDocument()
    expect(screen.getByText(/Re-bootstrap/i)).toBeInTheDocument()
    expect(screen.getByText(/Disable sync/i)).toBeInTheDocument()
    expect(screen.getByText(/Delete sync/i)).toBeInTheDocument()
  })
})
