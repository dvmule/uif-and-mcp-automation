import { useCallback, useEffect, useState } from 'react'
import { api } from '@/lib/api'
import { FEATURE_FLAGS } from '@/lib/feature-flags'
import type { PipelineConfig } from '@/lib/types'

const key = (id: string) => `pipeline-draft-${id}`

export function usePipelineDraft(id: string) {
  const [draft, setDraft] = useState<PipelineConfig | null>(null)

  useEffect(() => {
    let cancelled = false
    async function load() {
      if (FEATURE_FLAGS.draftPersistenceEnabled) {
        const remote = await api.getDraft(id)
        if (!cancelled) setDraft(remote)
      } else {
        const raw = localStorage.getItem(key(id))
        if (!cancelled) setDraft(raw ? JSON.parse(raw) : null)
      }
    }
    load()
    return () => { cancelled = true }
  }, [id])

  const save = useCallback(async (next: PipelineConfig) => {
    setDraft(next)
    if (FEATURE_FLAGS.draftPersistenceEnabled) await api.putDraft(id, next)
    else localStorage.setItem(key(id), JSON.stringify(next))
  }, [id])

  const discard = useCallback(async () => {
    setDraft(null)
    if (FEATURE_FLAGS.draftPersistenceEnabled) await api.deleteDraft(id)
    else localStorage.removeItem(key(id))
  }, [id])

  return { draft, save, discard }
}
