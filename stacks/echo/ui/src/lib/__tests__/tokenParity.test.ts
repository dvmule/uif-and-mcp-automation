import { describe, it, expect } from 'vitest'
import { existsSync, readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { SIGNAL, SURFACE } from '../tokens'

// Beacon's theme.css is in ANOTHER stack, so this file is listed in
// stacks/echo/ci-watch -- that is what makes a Beacon-only change run echo's
// CI at all. Without it the breaking diff selects a matrix this test is not
// in, which is how UIFPE-716's token change reached develop (UIFPE-728).
// Keeps four copies of the RELAY tokens in step: index.css (@theme),
// lib/tokens.ts (for code that cannot use a CSS class), Beacon's theme.css and transform-ui's tokens.css.

const echoCss = readFileSync(resolve(__dirname, '../../index.css'), 'utf8')
const beaconPath = resolve(__dirname, '../../../../../beacon/ui/src/theme.css')
const beaconCss = existsSync(beaconPath) ? readFileSync(beaconPath, 'utf8') : null

/** Value of `--name: value;` in a stylesheet, or undefined. Last declaration wins. */
function tokenValue(css: string, name: string): string | undefined {
  const re = new RegExp(`(?:^|[\\s;{])${name}\\s*:\\s*([^;]+);`, 'g')
  let value: string | undefined
  for (const m of css.matchAll(re)) value = m[1].trim()
  return value
}

const hex = (v: string | undefined) => v?.toLowerCase()

// Owner decision 7: Echo's live teal is darker than Beacon's #0e7c86, which fails 4.5:1 on its tint.
const ECHO_SIGNAL_LIVE = '#0c7078'

describe('tokens.ts matches index.css', () => {
  const signalMap: Array<[keyof typeof SIGNAL, string]> = [
    ['ok', '--color-signal-ok'],
    ['warn', '--color-signal-warn'],
    ['fail', '--color-signal-fail'],
    ['live', '--color-signal-live'],
  ]
  it.each(signalMap)('SIGNAL.%s equals %s', (key, cssName) => {
    expect(hex(SIGNAL[key])).toBe(hex(tokenValue(echoCss, cssName)))
  })

  const surfaceMap: Array<[keyof typeof SURFACE, string]> = [
    ['paper', '--color-paper'],
    ['card', '--color-paper-card'],
    ['line', '--color-line'],
    ['head', '--color-paper-head'],
  ]
  it.each(surfaceMap)('SURFACE.%s equals %s', (key, cssName) => {
    expect(hex(SURFACE[key])).toBe(hex(tokenValue(echoCss, cssName)))
  })
})

describe('index.css type scale and radii', () => {
  const scale: Array<[string, string, string]> = [
    ['label', '11px', '1.4'],
    ['meta', '12px', '1.45'],
    ['body', '13px', '1.5'],
    ['page', '24px', '1.2'],
    ['kpi', '28px', '1'],
  ]
  it.each(scale)('--text-%s is %s with line-height %s', (name, size, lineHeight) => {
    expect(tokenValue(echoCss, `--text-${name}`)).toBe(size)
    expect(tokenValue(echoCss, `--text-${name}--line-height`)).toBe(lineHeight)
  })

  it('card radius is 16px and brand radius is 6px', () => {
    expect(tokenValue(echoCss, '--radius-card')).toBe('16px')
    expect(tokenValue(echoCss, '--radius-brand')).toBe('6px')
  })
})

const SHARED_TOKENS = [
  '--color-paper',
  '--color-ink',
  '--color-ink-soft',
  '--color-ink-mute',
  '--color-signal-ok',
  '--color-signal-ok-bg',
  '--color-signal-warn',
  '--color-signal-warn-bg',
  '--color-signal-warn-line',
  '--color-signal-fail',
  '--color-signal-fail-bg',
  '--color-signal-live-bg',
  '--color-dot-ok',
  '--color-dot-fail',
  '--color-dot-mute',
  '--color-fuchsia-brand',
  '--color-pearson-amber-tint',
  '--color-line',
  '--color-paper-head',
  '--radius-card',
  '--radius-brand',
  '--shadow-card',
  '--shadow-sm-brand',
  '--font-sans',
  '--font-mono',
  '--text-label',
  '--text-meta',
  '--text-body',
  '--text-page',
  '--text-kpi',
]

// Skipped (not failed) when the Beacon UI is not in the checkout.
describe.skipIf(beaconCss === null)('echo tokens match Beacon theme.css', () => {
  it.each(SHARED_TOKENS)('%s has the same value in both', (name) => {
    const echo = tokenValue(echoCss, name)
    expect(echo).toBeDefined()
    expect(hex(echo)).toBe(hex(tokenValue(beaconCss as string, name)))
  })

  it('--color-signal-live is the darker teal of decision 7, not Beacon value', () => {
    expect(hex(tokenValue(echoCss, '--color-signal-live'))).toBe(ECHO_SIGNAL_LIVE)
    expect(tokenValue(beaconCss as string, '--color-signal-live')).toBeDefined()
  })
})

// transform-ui (a sibling bundle) declares the same token names in its tokens.css.
const transformPath = resolve(__dirname, '../../../../transform-ui/src/styles/tokens.css')
const transformCss = existsSync(transformPath) ? readFileSync(transformPath, 'utf8') : null

// Brand-palette names transform-ui does not declare.
const NOT_IN_TRANSFORM = new Set(['--text-kpi', '--shadow-sm-brand', '--color-fuchsia-brand', '--color-pearson-amber-tint'])

const TRANSFORM_TOKENS = [
  ...SHARED_TOKENS.filter((name) => !NOT_IN_TRANSFORM.has(name)),
  '--color-signal-live',
  '--color-accent',
  '--color-accent-soft',
  '--color-paper-card',
  '--color-paper-line',
]

describe.skipIf(transformCss === null)('echo tokens match transform-ui tokens.css', () => {
  it.each(TRANSFORM_TOKENS)('%s has the same value in both', (name) => {
    const echo = tokenValue(echoCss, name)
    expect(echo).toBeDefined()
    expect(hex(tokenValue(transformCss as string, name))).toBe(hex(echo))
  })

  it('uses the same font stack', () => {
    expect(tokenValue(transformCss as string, '--font-sans')).toBe(tokenValue(echoCss, '--font-sans'))
  })
})
