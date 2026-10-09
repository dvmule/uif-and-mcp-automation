import { describe, it, expect } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join, relative, resolve } from 'node:path'

// Grep guard: no Tailwind palette classes, legacy pearson-* classes or inline status hex outside the token files.

const SRC = resolve(__dirname, '../..')

// Frozen module (partner-export) and the files that define the tokens themselves.
const EXCLUDED_PREFIXES = ['pages/partner-export/', 'components/partner-export/']
const EXCLUDED_FILES = new Set([
  'store/partnerExportWizardStore.ts',
  'index.css',
  'lib/tokens.ts',
  'lib/statusVocabulary.ts',
])
// Allow-list: file -> reason. Empty: the run-history chart uses SIGNAL tokens for its recharts fills.
const ALLOWED: Record<string, string> = {}

const PATTERNS: Array<[string, RegExp]> = [
  ['palette class', /\b(bg|text|border|ring|from|to|via|fill|stroke)-(slate|violet|indigo|emerald|amber|rose|zinc|gray|red|green|blue|yellow|purple)-[0-9]{2,3}\b/],
  // Brand surfaces (purple, light-purple, mist, amethyst) are allowed; the status-like brand colours are not.
  ['legacy pearson class', /\b(bg|text|border|ring)-pearson-(?!(?:purple|light-purple|mist|amethyst)(?![a-z-]))[a-z-]+\b/],
  ['inline status hex', /#(16a34a|dc2626|f59e0b|22c55e|ef4444|10b981|6366f1|8b5cf6|0ea5e9)\b/i],
]

function isTestFile(rel: string): boolean {
  return /(^|\/)__tests__\//.test(rel) || /\.test\.tsx?$/.test(rel) || rel.startsWith('test/')
}

function walk(dir: string, out: string[]): string[] {
  for (const name of readdirSync(dir)) {
    const full = join(dir, name)
    if (statSync(full).isDirectory()) walk(full, out)
    else if (/\.(ts|tsx)$/.test(name)) out.push(full)
  }
  return out
}

function scan(): string[] {
  const hits: string[] = []
  for (const full of walk(SRC, [])) {
    const rel = relative(SRC, full).split('\\').join('/')
    if (EXCLUDED_FILES.has(rel) || rel in ALLOWED || isTestFile(rel)) continue
    if (EXCLUDED_PREFIXES.some((p) => rel.startsWith(p))) continue
    readFileSync(full, 'utf8').split(/\r?\n/).forEach((line, i) => {
      for (const [label, re] of PATTERNS) {
        const m = line.match(re)
        if (m) hits.push(`${rel}:${i + 1} ${label} ${m[0]}`)
      }
    })
  }
  return hits
}

describe('legacy style guard', () => {
  it('finds no palette classes, pearson-* classes or inline status hex in ui/src', () => {
    expect(scan()).toEqual([])
  })

  it('actually scans files (control)', () => {
    expect(walk(SRC, []).length).toBeGreaterThan(100)
    expect(PATTERNS[0][1].test('bg-slate-100')).toBe(true)
    expect(PATTERNS[1][1].test('text-pearson-ink')).toBe(true)
    expect(PATTERNS[2][1].test("fill: '#16a34a'")).toBe(true)
    expect(PATTERNS[0][1].test('bg-paper-card')).toBe(false)
  })

  it('allows the brand surfaces but still bans the status-like pearson colours (control)', () => {
    const re = PATTERNS[1][1]
    for (const ok of ['bg-pearson-purple', 'bg-pearson-purple/45', 'bg-pearson-mist', 'text-pearson-amethyst', 'border-pearson-light-purple']) {
      expect(re.test(ok)).toBe(false)
    }
    for (const bad of ['bg-pearson-positive', 'text-pearson-negative', 'bg-pearson-turquoise', 'bg-pearson-amber', 'bg-pearson-amber-tint', 'text-pearson-fuchsia', 'bg-pearson-purple-tint']) {
      expect(re.test(bad)).toBe(true)
    }
  })
})
