import { useEffect, useState } from 'react'
import type { PipelineConfig } from '@/lib/types'
import { usePipeline } from '@/hooks/usePipelines'
import { usePipelineDraft } from '@/hooks/usePipelineDraft'

export function useEditDraft(id: string) {
  const { data: server } = usePipeline(id)
  const { draft, save, discard } = usePipelineDraft(id)
  const [working, setWorking] = useState<PipelineConfig | null>(null)

  useEffect(() => {
    if (draft) setWorking(draft)
    else if (server) setWorking(structuredClone(server))
  }, [server, draft])

  const update = (next: PipelineConfig) => {
    setWorking(next)
    save(next)
  }

  return { server, draft, working, update, discard }
}
