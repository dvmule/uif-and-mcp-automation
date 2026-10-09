// returnPath.test.ts (UIFPE-487) — the login page's ?return= validator: only
// a same-origin module path comes back, everything else (other origins,
// protocol-relative, backslash tricks, shell-internal routes) is refused.

import { describe, expect, it } from 'vitest'
import { sameOriginReturnPath } from '../returnPath'

function search(value: string) {
  return `?return=${encodeURIComponent(value)}`
}

describe('sameOriginReturnPath', () => {
  it('accepts a module path with query and hash', () => {
    const path = '/m/beacon/index.html#/m/beacon/exports/100005'
    expect(sameOriginReturnPath(search(path))).toBe(path)
  })

  it('returns null when the parameter is absent', () => {
    expect(sameOriginReturnPath('')).toBeNull()
    expect(sameOriginReturnPath('?other=1')).toBeNull()
  })

  it('refuses other origins and protocol-relative URLs', () => {
    expect(sameOriginReturnPath(search('https://evil.example/m/beacon'))).toBeNull()
    expect(sameOriginReturnPath(search('//evil.example/m/beacon'))).toBeNull()
  })

  it('refuses backslash variants', () => {
    expect(sameOriginReturnPath(search('/m/beacon\\..\\x'))).toBeNull()
    expect(sameOriginReturnPath(search('\\/m/beacon'))).toBeNull()
  })

  it('refuses shell-internal and root paths (router state owns those)', () => {
    expect(sameOriginReturnPath(search('/'))).toBeNull()
    expect(sameOriginReturnPath(search('/settings'))).toBeNull()
    expect(sameOriginReturnPath(search('/m/'))).toBeNull()
  })
})
