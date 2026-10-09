import { describe, it, expect } from 'vitest'

describe('vitest smoke', () => {
  it('runs basic arithmetic', () => {
    expect(1 + 1).toBe(2)
  })

  it('has jest-dom matchers available', () => {
    const el = document.createElement('div')
    el.textContent = 'hello'
    document.body.appendChild(el)
    expect(el).toBeInTheDocument()
    expect(el).toHaveTextContent('hello')
    document.body.removeChild(el)
  })
})
