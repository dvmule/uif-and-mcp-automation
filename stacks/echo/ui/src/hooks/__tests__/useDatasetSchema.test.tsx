import { describe, it, expect, vi } from 'vitest'
import { renderHook, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { useDatasetSchema } from '../useDatasetSchema'
import { api } from '@/lib/api'
import type { PipelineConfig } from '@/lib/types'

vi.mock('@/lib/api', () => ({
  api: { getPipelines: vi.fn(), getPipeline: vi.fn(), analyzeTable: vi.fn() },
}))

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>
}

const PIPELINE: PipelineConfig = {
  pipeline_id: 'smoke_author',
  version: 'latest',
  enabled: true,
  source: { secret_arn: 'arn:aws:secretsmanager:eu-west-1:0:secret:src' },
  extract: { table: 'AUTHOR.AUTHOR_DATA', mode: 'incremental' },
  target: { s3_path: 's3://b/' },
  glue: { worker_type: 'G.1X', num_workers: 2, timeout_minutes: 60 },
  schedule: { frequency: 'daily' },
  silver: {
    enabled: true,
    database: 'smoke_silver',
    table: 'author_data',
    s3_path: 's3://s/',
    primary_key_cols: ['AUTHOR_ID'],
  },
}

describe('useDatasetSchema', () => {
  it('fetches schema for the silver dataset via the upstream sync', async () => {
    vi.mocked(api.getPipelines).mockResolvedValue([PIPELINE])
    vi.mocked(api.analyzeTable).mockResolvedValue({
      table: 'AUTHOR.AUTHOR_DATA',
      row_count: 0,
      columns: [
        { name: 'AUTHOR_ID', data_type: 'NUMBER', nullable: false },
        { name: 'NAME', data_type: 'VARCHAR2', nullable: true },
      ],
    })

    const { result } = renderHook(
      () => useDatasetSchema('silver', 'smoke_silver', 'author_data'),
      { wrapper },
    )

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data?.columns).toHaveLength(2)
  })
})
