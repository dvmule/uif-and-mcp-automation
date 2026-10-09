import { useNavigate, useParams, Route, Routes, Navigate, Outlet } from 'react-router-dom'
import { useState } from 'react'
import { EditFrame } from '@/components/data-sync/detail/edit/EditFrame'
import { useEditDraft } from '@/components/data-sync/detail/edit/useEditDraft'
import { IdentitySection } from '@/components/data-sync/detail/edit/sections/IdentitySection'
import { ExtractionSection } from '@/components/data-sync/detail/edit/sections/ExtractionSection'
import { GlueRuntimeSection } from '@/components/data-sync/detail/edit/sections/GlueRuntimeSection'
import { SilverSection } from '@/components/data-sync/detail/edit/sections/SilverSection'
import { QualitySection } from '@/components/data-sync/detail/edit/sections/QualitySection'
import { ReconcileSection } from '@/components/data-sync/detail/edit/sections/ReconcileSection'
import { RawJsonSection } from '@/components/data-sync/detail/edit/sections/RawJsonSection'
import { PredicateEditor } from '@/components/data-sync/detail/edit/PredicateEditor'
import { useUpsertPipeline } from '@/hooks/usePipelines'
import type { PipelineConfig } from '@/lib/types'

export function SyncDetailEdit() {
  const { id = '' } = useParams()
  const navigate = useNavigate()
  const { server, working, update, discard } = useEditDraft(id)
  const upsert = useUpsertPipeline()
  const [lastSavedRel, setLastSavedRel] = useState<string | null>(null)

  const changedSections = computeChangedSections(server, working)
  const pendingCount = Object.values(changedSections).reduce((a, b) => a + b, 0)

  const handleSaveDraft = () => {
    setLastSavedRel('just now')
  }
  const handleSaveAndClose = async () => {
    if (!working) return
    await upsert.mutateAsync({ id, config: working })
    discard()
    navigate(`/m/data-sync/syncs/${id}`)
  }
  const handleDiscard = () => {
    discard()
    navigate(`/m/data-sync/syncs/${id}`)
  }
  const handleBack = () => navigate(`/m/data-sync/syncs/${id}`)

  return (
    <div className="mx-auto max-w-[1180px]">
      <EditFrame
        pipelineId={id}
        pipeline={server}
        working={working}
        changedSections={changedSections}
        pendingCount={pendingCount}
        lastSavedRel={lastSavedRel}
        onDiscard={handleDiscard}
        onSaveDraft={handleSaveDraft}
        onSaveAndClose={handleSaveAndClose}
        onBack={handleBack}
      >
        <Routes>
          <Route element={<EditContextOutlet working={working} update={update} />}>
            <Route index element={<Navigate to="extraction" replace />} />
            <Route path="identity"     element={<IdentitySection />} />
            <Route path="extraction"   element={<ExtractionSection />} />
            <Route path="glue-runtime" element={<GlueRuntimeSection />} />
            <Route path="silver"       element={<SilverSection />} />
            <Route path="quality"      element={<QualitySection />} />
            <Route path="reconcile"    element={<ReconcileSection />} />
            <Route path="raw-json"     element={<RawJsonSection />} />
            <Route path="predicates"   element={<PredicateEditor />} />
          </Route>
        </Routes>
      </EditFrame>
    </div>
  )
}

function EditContextOutlet({
  working,
  update,
}: {
  working: PipelineConfig | null
  update: (next: PipelineConfig) => void
}) {
  return <Outlet context={{ working, update }} />
}

function computeChangedSections(
  server: PipelineConfig | undefined,
  working: PipelineConfig | null,
): Record<string, number> {
  if (!server || !working) return {}
  const out: Record<string, number> = {}
  const sections: [string, string[]][] = [
    ['identity',     ['pipeline_id', 'metadata.owner', 'metadata.tags']],
    ['extraction',   ['extract']],
    ['glue-runtime', ['glue']],
    ['silver',       ['silver']],
    ['quality',      ['quality']],
    ['reconcile',    ['reconcile']],
  ]
  for (const [section, paths] of sections) {
    let count = 0
    for (const p of paths) {
      if (JSON.stringify(get(server, p)) !== JSON.stringify(get(working, p))) count++
    }
    if (count) out[section] = count
  }
  return out
}

function get(obj: unknown, path: string): unknown {
  return path.split('.').reduce<unknown>(
    (o, k) => (o && typeof o === 'object' ? (o as Record<string, unknown>)[k] : undefined),
    obj,
  )
}
