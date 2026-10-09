import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { __setRuntimeConfigForTests } from '@/lib/envConfig'
import { Environments } from '../Environments'

describe('settings/Environments', () => {
  it('renders the page heading and read-only sub line', () => {
    render(<Environments />)
    expect(screen.getByRole('heading', { level: 1, name: /environment/i })).toBeInTheDocument()
    expect(screen.getByRole('tooltip')).toHaveTextContent('Where this console points. Read-only.')
    expect(screen.getByText('current')).toBeInTheDocument()
  })

  it('counts the one environment under the table', () => {
    render(<Environments />)
    expect(screen.getByTestId('environments-total')).toHaveTextContent('1 environment')
  })

  it('shows a single environment row', () => {
    render(<Environments />)
    const rows = screen.getAllByRole('row')
    expect(rows).toHaveLength(2)
  })

  it('explains there is no in-app environment switcher', () => {
    render(<Environments />)
    expect(screen.getByText(/no in-app environment switcher/i)).toBeInTheDocument()
  })

  // The endpoint now comes from /config.json at runtime (UIFPE-515), so the
  // page reports whatever this deployment was published with. src/test/setup.ts
  // seeds it.
  it('shows the API endpoint and reports it live', () => {
    render(<Environments />)
    expect(screen.getByText('https://api.test.invalid')).toBeInTheDocument()
    expect(screen.getByText('Live')).toBeInTheDocument()
  })

  it('shows the environment label from the loaded config', () => {
    __setRuntimeConfigForTests({ apiUrl: 'https://api.uat.invalid', env: 'uat' })
    render(<Environments />)
    expect(screen.getByText('UAT')).toBeInTheDocument()
  })

  // Unreachable from a real config.json -- parseConfig refuses a blank apiUrl
  // at load time -- so this asserts the page degrades honestly rather than
  // rendering an empty cell, for a seeded config or a future loader change.
  it('falls back to a placeholder when the loaded apiUrl is empty', () => {
    __setRuntimeConfigForTests({ apiUrl: '', env: 'dev' })
    render(<Environments />)
    expect(screen.getByText(/apiUrl is empty in config\.json/)).toBeInTheDocument()
    expect(screen.getByText('Not configured')).toBeInTheDocument()
  })
})
