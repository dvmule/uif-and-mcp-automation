import { useState, type ReactNode } from 'react'
import { useOutletContext } from 'react-router-dom'
import { X } from 'lucide-react'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Badge } from '@/components/ui/badge'
import type { PipelineConfig } from '@/lib/types'

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

function TagEditor({
  values,
  onChange,
}: { values: string[]; onChange: (next: string[]) => void }) {
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
        placeholder="add tag…"
        className="flex-1 min-w-[120px] bg-transparent text-sm outline-none px-1"
      />
    </div>
  )
}

export function IdentitySection() {
  const { working, update } = useOutletContext<OutletCtx>()
  const meta = working.metadata ?? {}

  const setMeta = (patch: Partial<typeof meta>) => {
    const next = structuredClone(working)
    next.metadata = { ...meta, ...patch }
    update(next)
  }

  return (
    <div className="space-y-4">
      <EditRow label="Pipeline ID">
        <code className="font-mono text-xs text-ink-soft">{working.pipeline_id}</code>
      </EditRow>
      <EditRow label="Owner">
        <Input
          value={meta.owner ?? ''}
          placeholder="team@example.com"
          onChange={e => setMeta({ owner: e.target.value })}
        />
      </EditRow>
      <EditRow label="Domain">
        <Input
          value={meta.domain ?? ''}
          placeholder="Inventory, Catalog, …"
          onChange={e => setMeta({ domain: e.target.value })}
        />
      </EditRow>
      <EditRow label="Source System">
        <Input
          value={meta.source_system ?? ''}
          placeholder="Oracle EBS"
          onChange={e => setMeta({ source_system: e.target.value })}
        />
      </EditRow>
      <EditRow label="Tags">
        <TagEditor
          values={meta.tags ?? []}
          onChange={next => setMeta({ tags: next })}
        />
      </EditRow>
    </div>
  )
}
