import { describe, it, expect } from 'vitest'
import { screen } from '@testing-library/react'
import { renderWithProviders } from '../test-utils'

describe('renderWithProviders', () => {
  it('renders children inside Query + Router providers', () => {
    renderWithProviders(<div data-testid="t">hi</div>)
    expect(screen.getByTestId('t')).toHaveTextContent('hi')
  })

  it('honors initialEntries', () => {
    renderWithProviders(<div>x</div>, { route: '/m/partner-export/exports/abc' })
    expect(window.location.pathname).toBe('/')
  })
})
