import type { Violation } from './validation'

interface Props { violations: Violation[] }

export function ValidationPanel({ violations }: Props) {
  if (violations.length === 0) return null
  const errs = violations.filter(v => v.severity === 'err')
  const warns = violations.filter(v => v.severity === 'warn')
  return (
    <div className="mb-4 space-y-1.5 rounded-brand border-l-[3px] border-amber bg-amber/10 p-3 text-meta text-ink">
      {errs.map((v, i) => <ValRow key={`e${i}`} severity="err" v={v} />)}
      {warns.map((v, i) => <ValRow key={`w${i}`} severity="warn" v={v} />)}
    </div>
  )
}

function ValRow({ severity, v }: { severity: 'warn' | 'err'; v: Violation }) {
  const label = severity === 'err' ? 'ERR' : 'WARN'
  const cls = severity === 'err' ? 'bg-signal-fail-bg text-signal-fail' : 'bg-signal-warn-bg text-signal-warn'
  return (
    <div>
      <span className={`mr-1.5 inline-block rounded-md px-1.5 py-px text-[10px] font-bold ${cls}`}>{label}</span>
      {v.message}
    </div>
  )
}
