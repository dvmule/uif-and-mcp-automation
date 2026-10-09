import { describe, it, expect } from 'vitest'
import { PEARSON, CHART_STATUS_COLOR, SURFACE } from '@/lib/tokens'

describe('Pearson tokens', () => {
  it('exposes the brand palette as hex strings', () => {
    expect(PEARSON.purple).toBe('#0D004D')
    expect(PEARSON.amethyst).toBe('#512EAB')
    expect(PEARSON.lightPurple).toBe('#C1BFFF')
    expect(PEARSON.mist).toBe('#EDECF6')
    expect(PEARSON.turquoise).toBe('#56E2E1')
    expect(PEARSON.turquoiseTint).toBe('#B3F2F1')
    expect(PEARSON.amber).toBe('#FFCE00')
    expect(PEARSON.amberTint).toBe('#FFE67F')
    expect(PEARSON.fuchsia).toBe('#DF41CF')
    expect(PEARSON.fuchsiaTint).toBe('#EFA0E7')
    expect(PEARSON.positive).toBe('#00C800')
    expect(PEARSON.negative).toBe('#FF0000')
    expect(PEARSON.white).toBe('#FFFFFF')
    expect(PEARSON.uxBlack).toBe('#333333')
  })

  it('maps run status to Beacon run-history colours (UIFPE-734)', () => {
    expect(CHART_STATUS_COLOR.SUCCEEDED).toBe('#56E2E1')
    expect(CHART_STATUS_COLOR.FAILED).toBe('#DF41CF')
    expect(CHART_STATUS_COLOR.WARNING).toBe('#FFCE00')
    expect(CHART_STATUS_COLOR.SKIPPED).toBe('#FFCE00')
    expect(CHART_STATUS_COLOR.RUNNING).toBe('#512EAB')
  })

  it('chart chrome uses the surface hairline and muted ink', () => {
    expect(PEARSON.line).toBe(SURFACE.line)
    expect(PEARSON.inkMute).toBe('#6e6989')
  })
})
