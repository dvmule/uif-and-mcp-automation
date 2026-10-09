import { describe, it, expect } from 'vitest'
import { RAIL_KEY, readRailCollapsed, writeRailCollapsed } from '@/lib/railPreference'

function memoryStorage(initial: Record<string, string> = {}) {
  const data = { ...initial }
  return {
    data,
    getItem: (k: string) => (k in data ? data[k] : null),
    setItem: (k: string, v: string) => {
      data[k] = v
    },
  }
}

const throwing = {
  getItem: () => {
    throw new Error('blocked')
  },
  setItem: () => {
    throw new Error('blocked')
  },
}

describe('railPreference', () => {
  it('uses the relay.echo key', () => {
    expect(RAIL_KEY).toBe('relay.echo.rail.collapsed')
  })

  it('defaults to expanded', () => {
    expect(readRailCollapsed(memoryStorage())).toBe(false)
    expect(readRailCollapsed(undefined)).toBe(false)
  })

  it('round-trips the choice', () => {
    const s = memoryStorage()
    writeRailCollapsed(s, true)
    expect(s.data[RAIL_KEY]).toBe('1')
    expect(readRailCollapsed(s)).toBe(true)
    writeRailCollapsed(s, false)
    expect(s.data[RAIL_KEY]).toBe('0')
    expect(readRailCollapsed(s)).toBe(false)
  })

  it('treats an unexpected stored value as expanded', () => {
    expect(readRailCollapsed(memoryStorage({ [RAIL_KEY]: 'yes' }))).toBe(false)
  })

  it('never throws when storage is blocked', () => {
    expect(readRailCollapsed(throwing)).toBe(false)
    expect(() => writeRailCollapsed(throwing, true)).not.toThrow()
    expect(() => writeRailCollapsed(undefined, true)).not.toThrow()
  })
})
