import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { CopyLink } from '@/components/data-sync/detail/rail/CopyLink'

vi.mock('sonner', () => ({ toast: { success: vi.fn() } }))

describe('CopyLink', () => {
  it('writes the value to the clipboard and triggers a toast', async () => {
    const writeText = vi.fn(() => Promise.resolve())
    Object.assign(navigator, { clipboard: { writeText } })
    render(<CopyLink value="gs://bucket/path" label="Bronze" />)
    fireEvent.click(screen.getByRole('button', { name: /Copy/i }))
    expect(writeText).toHaveBeenCalledWith('gs://bucket/path')
    const { toast } = await import('sonner')
    expect((toast.success as any)).toHaveBeenCalledWith('Copied Bronze path to clipboard')
  })
})
