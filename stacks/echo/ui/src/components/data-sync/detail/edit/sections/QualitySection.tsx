import { useState, type ReactNode } from 'react'
import { Link, useOutletContext } from 'react-router-dom'
import { X } from 'lucide-react'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Badge } from '@/components/ui/badge'
import type { PipelineConfig, PipelineQuality } from '@/lib/types'

interface OutletCtx {
  working: PipelineConfig
  update: (next: PipelineConfig) => void
}

function EditRow({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid grid-cols-[180px_1fr] gap-x-4 items-center py-1.5">
      <Label className="text-xs text-ink-mute uppercase tracking-wide font-medium">{label}</Label>
      <div>{children}</div>
    </div>
  )
}

function ChipEditor({
  values, onChange, placeholder,
}: { values: string[]; onChange: (next: string[]) => void; placeholder?: string }) {
  const [text, setText] = useState('')
  const commit = (raw: string) => {
    const next = raw
      .split(',')
      .map(s => s.trim())
      .filter(s => s.length > 0 && !values.includes(s))
    if (next.length > 0) onChange([...values, ...next])
    setText('')
  }
  const remove = (v: string) => onChange(values.filter(x => x !== v))
  return (
    <div className="flex flex-wrap gap-1.5 rounded-lg border border-paper-line p-2 min-h-9">
      {values.map(v => (
        <Badge key={v} variant="secondary" className="text-xs font-mono gap-1 pr-1">
          {v}
          <button
            onClick={() => remove(v)}
            className="rounded hover:bg-ink/10 p-0.5 text-ink-mute hover:text-ink"
            title="Remove"
          >
            <X size={10} />
          </button>
        </Badge>
      ))}
      <input
        value={text}
        onChange={e => setText(e.target.value)}
        onKeyDown={e => {
          if (e.key === 'Enter' || e.key === ',') {
            e.preventDefault()
            commit(text)
          } else if (e.key === 'Backspace' && text === '' && values.length > 0) {
            remove(values[values.length - 1])
          }
        }}
        onBlur={() => commit(text)}
        placeholder={placeholder ?? 'add column…'}
        className="flex-1 min-w-[120px] bg-transparent text-sm outline-none px-1"
      />
    </div>
  )
}

export function QualitySection() {
  const { working, update } = useOutletContext<OutletCtx>()
  const q: PipelineQuality = working.quality ?? {}

  const setQ = (patch: Partial<PipelineQuality>) => {
    const next = structuredClone(working)
    next.quality = { ...q, ...patch }
    update(next)
  }

  return (
    <div className="space-y-4">
      <EditRow label="Not-Null Cols">
        <ChipEditor
          values={q.not_null_cols ?? []}
          onChange={next => setQ({ not_null_cols: next })}
        />
      </EditRow>

      <EditRow label="Min Rows Expected">
        <Input
          type="number"
          value={q.min_rows_expected ?? ''}
          onChange={e => setQ({
            min_rows_expected: e.target.value === '' ? undefined : Number(e.target.value),
          })}
        />
      </EditRow>

      <EditRow label="Custom DQ Checks">
        <Link
          to={`/m/data-sync/syncs/${working.pipeline_id}#dq-checks`}
          className="text-[13px] text-accent font-semibold hover:underline"
        >
          Manage linked checks →
        </Link>
      </EditRow>
    </div>
  )
}
