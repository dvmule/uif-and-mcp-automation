import { useOutletContext } from 'react-router-dom'
import { CopyLink } from '@/components/data-sync/detail/rail/CopyLink'
import type { PipelineConfig } from '@/lib/types'

interface OutletCtx {
  working: PipelineConfig
  update: (next: PipelineConfig) => void
}

export function RawJsonSection() {
  const { working } = useOutletContext<OutletCtx>()
  const json = JSON.stringify(working, null, 2)

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between gap-3">
        <p className="text-[12px] text-ink-mute">
          Use Save draft / Save changes to persist edits made elsewhere.
        </p>
        <CopyLink value={json} label="config JSON" />
      </div>
      <pre className="text-[12px] bg-paper-head rounded-md border border-line p-3 overflow-x-auto font-mono text-ink-soft leading-relaxed max-h-[60vh]">
        {json}
      </pre>
    </div>
  )
}
