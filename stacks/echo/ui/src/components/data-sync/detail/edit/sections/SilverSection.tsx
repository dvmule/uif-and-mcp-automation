import { useState, type ReactNode } from 'react'
import { useOutletContext } from 'react-router-dom'
import { X } from 'lucide-react'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import { Badge } from '@/components/ui/badge'
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select'
import type { PipelineConfig, PipelineSilver } from '@/lib/types'
import { pipelineCloud } from '@/lib/cloud'
import { BQ_LOCATIONS } from '@/lib/validation'

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

export function SilverSection() {
  const { working, update } = useOutletContext<OutletCtx>()
  const cloud = pipelineCloud(working)
  const s: PipelineSilver = working.silver ?? { primary_key_cols: [], enabled: true }
  // Partition cols live on `target` in the real PipelineConfig shape.
  const partitionCols =
    (working.target as unknown as { partition_cols?: string[] }).partition_cols ?? []

  const setSilver = (patch: Partial<PipelineSilver>) => {
    const next = structuredClone(working)
    next.silver = { ...s, ...patch }
    update(next)
  }

  const setPartitionCols = (cols: string[]) => {
    const next = structuredClone(working)
    ;(next.target as unknown as { partition_cols?: string[] }).partition_cols = cols
    update(next)
  }

  const setSoftDelete = (patch: Partial<NonNullable<PipelineSilver['soft_delete']>> | null) => {
    if (patch === null) {
      setSilver({ soft_delete: null })
      return
    }
    const current = s.soft_delete ?? { flag_col: '', inactive_val: '' }
    setSilver({ soft_delete: { ...current, ...patch } })
  }

  return (
    <div className="space-y-4">
      <EditRow label="Enabled">
        <Switch
          checked={s.enabled ?? true}
          onCheckedChange={v => setSilver({ enabled: v })}
        />
      </EditRow>

      <EditRow label="Primary Keys">
        <ChipEditor
          values={s.primary_key_cols ?? []}
          onChange={next => setSilver({ primary_key_cols: next })}
        />
      </EditRow>

      <EditRow label="Partition Cols">
        <ChipEditor
          values={partitionCols}
          onChange={setPartitionCols}
          placeholder="add partition col…"
        />
      </EditRow>

      {cloud === 'aws' && (
        <>
          <EditRow label="Database">
            <Input
              value={s.database ?? ''}
              onChange={e => setSilver({ database: e.target.value })}
            />
          </EditRow>
          <EditRow label="Table">
            <Input
              value={s.table ?? ''}
              onChange={e => setSilver({ table: e.target.value })}
            />
          </EditRow>
          <EditRow label="S3 Path">
            <Input
              value={s.s3_path ?? ''}
              placeholder="s3://bucket/silver/…/"
              onChange={e => setSilver({ s3_path: e.target.value })}
            />
          </EditRow>
        </>
      )}

      {cloud === 'gcp' && (
        <>
          <EditRow label="GCS Path">
            <Input
              value={s.gcs_path ?? ''}
              placeholder="gs://bucket/silver/…/"
              onChange={e => setSilver({ gcs_path: e.target.value })}
            />
          </EditRow>
          <EditRow label="BQ Project">
            <Input
              value={s.bq_project ?? ''}
              onChange={e => setSilver({ bq_project: e.target.value })}
            />
          </EditRow>
          <EditRow label="BQ Dataset">
            <Input
              value={s.bq_dataset ?? ''}
              onChange={e => setSilver({ bq_dataset: e.target.value })}
            />
          </EditRow>
          <EditRow label="BQ Location">
            <Select
              value={s.bq_location ?? ''}
              onValueChange={v => setSilver({ bq_location: v })}
            >
              <SelectTrigger className="w-full"><SelectValue placeholder="Select location…" /></SelectTrigger>
              <SelectContent>
                {BQ_LOCATIONS.map(loc => (
                  <SelectItem key={loc} value={loc}>{loc}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </EditRow>
        </>
      )}

      <EditRow label="Soft Delete">
        <div className="flex items-center gap-2">
          <Switch
            checked={!!s.soft_delete}
            onCheckedChange={v => setSoftDelete(v ? { flag_col: '', inactive_val: '' } : null)}
          />
          <span className="text-xs text-ink-mute">
            {s.soft_delete ? 'flag column drives logical delete' : 'disabled'}
          </span>
        </div>
      </EditRow>

      {s.soft_delete && (
        <>
          <EditRow label="Soft Delete · flag col">
            <Input
              value={s.soft_delete.flag_col}
              onChange={e => setSoftDelete({ flag_col: e.target.value })}
            />
          </EditRow>
          <EditRow label="Soft Delete · inactive val">
            <Input
              value={s.soft_delete.inactive_val}
              onChange={e => setSoftDelete({ inactive_val: e.target.value })}
            />
          </EditRow>
        </>
      )}
    </div>
  )
}
