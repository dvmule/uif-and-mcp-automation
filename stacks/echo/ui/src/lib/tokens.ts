export const PEARSON = {
  purple:       '#0D004D',
  amethyst:     '#512EAB',
  lightPurple:  '#C1BFFF',
  mist:         '#EDECF6',
  white:        '#FFFFFF',
  uxBlack:      '#333333',
  turquoise:    '#56E2E1',
  turquoiseTint:'#B3F2F1',
  amber:        '#FFCE00',
  amberTint:    '#FFE67F',
  fuchsia:      '#DF41CF',
  fuchsiaTint:  '#EFA0E7',
  positive:     '#00C800',
  negative:     '#FF0000',
  // Chart chrome: the hairline and muted ink of index.css (--color-line, --color-ink-mute).
  line:         '#e3e1ef',
  inkMute:      '#6e6989',
} as const

// Beacon's run-history colours (UIFPE-734): turquoise succeeded, fuchsia failed.
export const CHART_STATUS_COLOR = {
  SUCCEEDED: PEARSON.turquoise,
  FAILED:    PEARSON.fuchsia,
  WARNING:   PEARSON.amber,
  SKIPPED:   PEARSON.amber,
  RUNNING:   PEARSON.amethyst,
} as const

export type RunStatus = keyof typeof CHART_STATUS_COLOR

// RELAY status + surface colours (UIFPE-677). Must match the @theme block in index.css; tokenParity.test.ts enforces it.
export const SIGNAL = { ok: '#0b6b4f', warn: '#8a6d00', fail: '#b3261e', live: '#0c7078' } as const

export const SURFACE = { paper: '#efeef7', card: '#ffffff', line: '#e3e1ef', head: '#faf9ff' } as const
