import { useState } from 'react'
import { useOutletContext, useSearchParams, useNavigate, useParams } from 'react-router-dom'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import type { PipelineConfig } from '@/lib/types'
import {
  readExtractProfile,
  HEAVY_INCR_PREDICATE_THRESHOLD,
  type Phase,
  type FlexExtract,
} from './sections/profileHelpers'

interface OutletCtx {
  working: PipelineConfig
  update: (next: PipelineConfig) => void
}

export function PredicateEditor() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const [params] = useSearchParams()
  const phase: Phase = params.get('phase') === 'incremental' ? 'incremental' : 'initial'
  const { working, update } = useOutletContext<OutletCtx>()
  const dExt = working.extract as unknown as FlexExtract
  const profile = readExtractProfile(dExt, phase)
  const initial = (profile.jdbc_predicates ?? []).join('\n')
  const [text, setText] = useState(initial)
  const [shownPhase, setShownPhase] = useState(phase)

  // Resync when phase changes (adjusted during render, not in an effect)
  if (shownPhase !== phase) {
    setShownPhase(phase)
    setText(initial)
  }

  const lines = text.split('\n').map(l => l.trim()).filter(Boolean)
  const overThreshold = phase === 'incremental' && lines.length > HEAVY_INCR_PREDICATE_THRESHOLD

  const apply = () => {
    const next = structuredClone(working)
    const ext = (next.extract as unknown as FlexExtract) ?? {}
    const currentProfile = readExtractProfile(ext, phase)
    const merged = { ...currentProfile, jdbc_predicates: lines }
    const nextExt: FlexExtract = { ...ext, [phase]: merged }
    for (const k of ['jdbc_partition_col', 'jdbc_lower_bound', 'jdbc_upper_bound', 'jdbc_partitions', 'jdbc_predicates']) {
      delete nextExt[k]
    }
    ;(next as { extract: unknown }).extract = nextExt
    update(next)
    navigate(`/m/data-sync/syncs/${id}/edit/extraction`)
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <p className="text-[13px] font-semibold text-ink">
          Phase · <span className="text-accent">{phase}</span>
        </p>
        <span className={`text-[12px] ${overThreshold ? 'text-signal-fail font-semibold' : 'text-ink-mute'}`}>
          {lines.length} / 20 predicates
        </span>
      </div>
      <p className="text-[12px] text-ink-mute">
        One predicate per line. Spark will run one partition per predicate. Empty lines are ignored.
      </p>
      <Textarea
        value={text}
        onChange={e => setText(e.target.value)}
        rows={20}
        className="font-mono text-[12px]"
        placeholder="ORA_HASH(ROWID, 3, 0) = 0"
      />
      {overThreshold && (
        <div className="rounded-md border border-amber bg-amber/15 px-3 py-2 text-xs text-ink">
          More than {HEAVY_INCR_PREDICATE_THRESHOLD} incremental predicates — enable
          <code className="mx-1 font-mono">allow_heavy</code>
          in Extraction → Incremental, or reduce the predicate count.
        </div>
      )}
      <div className="flex justify-end gap-2 mt-3">
        <Button
          variant="outline"
          onClick={() => navigate(`/m/data-sync/syncs/${id}/edit/extraction`)}
        >
          Cancel
        </Button>
        <Button onClick={apply}>
          Apply
        </Button>
      </div>
    </div>
  )
}
