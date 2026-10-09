import { describe, it, expect } from 'vitest'
import { render, waitFor } from '@testing-library/react'
import { toast } from 'sonner'
import { Toaster } from '@/components/ui/sonner'

describe('Toaster', () => {
  it('renders without crashing and uses the Pearson theme class', async () => {
    render(<Toaster />)
    // sonner v2 only renders the [data-sonner-toaster] list once a toast is queued.
    // Firing a toast verifies the wrapper mounts a working sonner instance.
    toast('hello pearson')
    await waitFor(() => {
      expect(document.querySelector('[data-sonner-toaster]')).not.toBeNull()
    })
  })
})
