import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { GoldTrendCard } from '../gold/GoldTrendCard'
import type { GoldStatsResponse, GoldLiveWindow } from '@/lib/goldStats'

/** UIFPE-544: the gold fleet's 30-day trend, under the Transform list.
 *
 *  The live tiles moved to the page's stat strip (GoldList.test.tsx). What
 *  matters here is that the trend is "as of last night", and when that file is
 *  missing or a night was missed the card says so rather than drawing a flat
 *  line at zero. */

let query: { data?: GoldStatsResponse; error?: unknown; isLoading: boolean } = { isLoading: true }

vi.mock('@/hooks/useGoldStats', () => ({ useGoldStats: () => query }))

const gateway404 = Object.assign(new Error('Request failed with status code 404'), {
  isAxiosError: true, response: { status: 404, data: { message: 'Not Found' } },
})

function live(over: Partial<GoldLiveWindow> = {}): GoldLiveWindow {
  return {
    as_of: '2026-09-24T12:00:00+00:00', window_hours: 24,
    entities: 5, enabled: 4, runs: 7, succeeded: 5, failed: 1, skipped: 1,
    rows_written: 1200, fresh: 5, stale: 0, unknown: 0, stale_entities: [],
    ...over,
  }
}

function response(over: Partial<GoldStatsResponse> = {}): GoldStatsResponse {
  return {
    live: live(),
    trend: {
      schema_version: 1,
      generated_at: '2026-09-24T04:30:00+00:00',
      days: 2,
      fleet: [
        { day: '2026-09-23', entities: 5, runs: 6, failed: 0, rows_written: 900, fresh: 5 },
        { day: '2026-09-24', entities: 5, runs: 7, failed: 2, rows_written: 1100, fresh: 4 },
      ],
      entities: {},
    },
    trend_available: true,
    trend_unavailable_reason: null,
    trend_generated_at: '2026-09-24T04:30:00+00:00',
    trend_age_minutes: 90,
    generated_at: '2026-09-24T06:00:00+00:00',
    ...over,
  }
}

function renderCard() {
  const qc = new QueryClient()
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter><GoldTrendCard /></MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('GoldTrendCard (UIFPE-544)', () => {
  beforeEach(() => { query = { isLoading: true } })

  it('shows placeholders while the one call is in flight', () => {
    renderCard()
    expect(screen.getByTestId('gold-fleet-loading')).toBeInTheDocument()
  })

  it('says the route is not deployed yet rather than showing zeros', () => {
    query = { error: gateway404, isLoading: false }
    renderCard()
    expect(screen.getByTestId('gold-fleet-unavailable'))
      .toHaveTextContent(/not available in this environment yet/i)
  })

  it('distinguishes a real failure from an undeployed route', () => {
    query = { error: new Error('boom'), isLoading: false }
    renderCard()
    expect(screen.getByTestId('gold-fleet-unavailable'))
      .toHaveTextContent(/could not load/i)
  })

  it('labels the trend as computed nightly, with when', () => {
    query = { data: response(), isLoading: false }
    renderCard()
    expect(screen.getByTestId('gold-trend-source'))
      .toHaveTextContent('Computed nightly · 24 Sep, 04:30 UTC')
  })

  it('totals the trend from the days it is drawing', () => {
    query = { data: response(), isLoading: false }
    renderCard()
    const totals = screen.getByTestId('gold-trend-totals')
    expect(totals).toHaveTextContent('13')      // 6 + 7 runs
    expect(totals).toHaveTextContent('2')       // failures
    expect(totals).toHaveTextContent('90%')     // 9 of 10 entity-days met freshness
  })

  it('says a night was missed instead of quietly showing old numbers', () => {
    query = { data: response({ trend_age_minutes: 40 * 60 }), isLoading: false }
    renderCard()
    expect(screen.getByTestId('gold-trend-source')).toHaveTextContent(/a night was missed/i)
  })

  it('says the summary has not been written yet rather than drawing a flat line', () => {
    query = { data: response({ trend: null, trend_available: false,
                               trend_unavailable_reason: 'NoSuchKey',
                               trend_generated_at: null, trend_age_minutes: null }),
              isLoading: false }
    renderCard()
    expect(screen.getByTestId('gold-trend-missing'))
      .toHaveTextContent(/has not written a summary yet/i)
    expect(screen.queryByTestId('gold-trend-runs')).toBeNull()
  })

  it('draws a sparkline per series, each labelled for a screen reader', () => {
    query = { data: response(), isLoading: false }
    renderCard()
    expect(screen.getByTestId('gold-trend-runs')).toBeInTheDocument()
    expect(screen.getByTestId('gold-trend-failed')).toBeInTheDocument()
    // Anchored: an unanchored /Runs …/i also matches "Failed runs …".
    expect(screen.getByLabelText('Runs over the last 2 days')).toBeInTheDocument()
    expect(screen.getByLabelText('Failed runs over the last 2 days')).toBeInTheDocument()
    expect(screen.getByLabelText('Rows written over the last 2 days')).toBeInTheDocument()
  })

  it('strokes the sparklines with tokens, never a hand-coded hex', () => {
    query = { data: response(), isLoading: false }
    const { container } = renderCard()
    const lines = [...container.querySelectorAll('polyline')]
    expect(lines.map((l) => l.getAttribute('data-series'))).toEqual(['runs', 'failed', 'rows'])
    for (const l of lines) expect(l.getAttribute('stroke')).not.toMatch(/^#/)
  })
})
