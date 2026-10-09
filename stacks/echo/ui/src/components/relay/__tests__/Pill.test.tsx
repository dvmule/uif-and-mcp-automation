import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { Pill } from '../Pill'

describe('Pill', () => {
  it('defaults to the mute tone', () => {
    render(<Pill>p1</Pill>)
    expect(screen.getByText('p1')).toHaveClass('rounded-full', 'text-meta', 'font-semibold', 'bg-paper-head', 'text-ink-soft')
  })

  it.each([
    ['accent', 'bg-pearson-mist', 'text-accent'],
    ['ok', 'bg-signal-ok-bg', 'text-signal-ok'],
    ['warn', 'bg-signal-warn-bg', 'text-signal-warn'],
    ['fail', 'bg-signal-fail-bg', 'text-signal-fail'],
  ] as const)('%s tone uses %s and %s', (tone, bg, text) => {
    render(<Pill tone={tone}>x</Pill>)
    expect(screen.getByText('x')).toHaveClass(bg, text)
  })

  it('merges a className', () => {
    render(<Pill className="font-medium">x</Pill>)
    expect(screen.getByText('x')).toHaveClass('font-medium')
    expect(screen.getByText('x')).not.toHaveClass('font-semibold')
  })
})
