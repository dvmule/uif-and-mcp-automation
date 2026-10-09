import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { TONE_CLASS, type Tone } from '@/lib/statusVocabulary'

// WCAG contrast of each status pill's text over its tint, as rendered on a white card.

type Rgb = [number, number, number]

const css = readFileSync(resolve(__dirname, '../../index.css'), 'utf8')

function token(name: string): Rgb {
  const m = css.match(new RegExp(`--color-${name}\\s*:\\s*#([0-9a-fA-F]{6})`))
  if (!m) throw new Error(`no --color-${name} in index.css`)
  const hex = m[1]
  return [0, 2, 4].map((i) => parseInt(hex.slice(i, i + 2), 16)) as Rgb
}

function luminance([r, g, b]: Rgb): number {
  const lin = (c: number) => {
    const s = c / 255
    return s <= 0.04045 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4
  }
  return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)
}

function contrast(a: Rgb, b: Rgb): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x)
  return (hi + 0.05) / (lo + 0.05)
}

/** A `/NN` tint over white, as the browser composites it. */
function overWhite(color: Rgb, alpha: number): Rgb {
  return color.map((c) => Math.round(c * alpha + 255 * (1 - alpha))) as Rgb
}

/** Reads `bg-<token>[/NN] text-<token>` from TONE_CLASS. */
function pill(tone: Tone): { text: Rgb; ground: Rgb } {
  const cls = TONE_CLASS[tone]
  const bg = cls.match(/\bbg-([a-z-]+)(?:\/(\d+))?/)
  const text = cls.match(/\btext-([a-z-]+)/)
  if (!bg || !text) throw new Error(`unexpected TONE_CLASS for ${tone}: ${cls}`)
  const alpha = bg[2] ? Number(bg[2]) / 100 : 1
  return { text: token(text[1]), ground: overWhite(token(bg[1]), alpha) }
}

describe('status tint contrast', () => {
  it('the helper matches known WCAG values', () => {
    expect(contrast([0, 0, 0], [255, 255, 255])).toBeCloseTo(21, 5)
    expect(contrast([118, 118, 118], [255, 255, 255])).toBeCloseTo(4.54, 2)
  })

  it.each(['ok', 'warn', 'fail', 'live', 'mute'] as Tone[])('%s text is at least 4.5:1 on its tint', (tone) => {
    const { text, ground } = pill(tone)
    expect(contrast(text, ground)).toBeGreaterThanOrEqual(4.5)
  })

  // The ratios the approved palette gives; a change to any tint or text token moves one.
  it.each([
    ['ok', 5.84],
    ['warn', 4.6],
    ['fail', 5.75],
    ['live', 5.15],
    ['mute', 5.77],
  ] as Array<[Tone, number]>)('%s pill is %s:1', (tone, ratio) => {
    const { text, ground } = pill(tone)
    expect(contrast(text, ground)).toBeCloseTo(ratio, 1)
  })

  it('resolves the solid tint tokens (control)', () => {
    expect(TONE_CLASS.ok).toMatch(/\bbg-signal-ok-bg\b/)
    expect(TONE_CLASS.mute).toMatch(/\bbg-pearson-mist\b/)
    expect(pill('ok').ground).toEqual(token('signal-ok-bg'))
  })
})
