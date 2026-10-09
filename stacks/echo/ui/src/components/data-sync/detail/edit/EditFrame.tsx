import type { ReactNode } from 'react'
import { useMatch } from 'react-router-dom'
import { Button } from '@/components/ui/button'
import { PageHeader } from '@/components/relay'
import { DetailBreadcrumb, DetailCard } from '../DetailCard'
import { EditNav } from './EditNav'
import { EDIT_SECTIONS, PREDICATES_SECTION } from './editSections'
import { ValidationPanel } from './ValidationPanel'
import { SaveFooter } from './SaveFooter'
import { validateDraft } from './validation'
import type { PipelineConfig } from '@/lib/types'

interface Props {
  pipelineId: string
  pipeline: PipelineConfig | undefined
  working: PipelineConfig | null
  changedSections: Record<string, number>
  pendingCount: number
  lastSavedRel: string | null
  onDiscard: () => void
  onSaveDraft: () => void
  onSaveAndClose: () => void
  onBack: () => void
  children: ReactNode
}

/** Edit page shell: breadcrumb header, section list on the left, the section's card with a save footer on the right. */
export function EditFrame(props: Props) {
  const {
    pipelineId,
    pipeline,
    working,
    changedSections,
    pendingCount,
    lastSavedRel,
    onDiscard,
    onSaveDraft,
    onSaveAndClose,
    onBack,
    children,
  } = props
  const match = useMatch('/m/data-sync/syncs/:id/edit/:section')

  if (!working || !pipeline) return null

  const { errors, warnings } = validateDraft(working)
  const blocked = errors.length > 0
  const table = pipeline.extract?.table || pipeline.pipeline_id
  const key = match?.params.section
  const section =
    EDIT_SECTIONS.find(s => s.key === key) ?? (key === PREDICATES_SECTION.key ? PREDICATES_SECTION : undefined)

  return (
    <>
      <PageHeader
        back={
          <DetailBreadcrumb
            to="/m/data-sync/syncs"
            label="Replicate"
            via={{ to: `/m/data-sync/syncs/${pipelineId}`, label: table }}
            current="Edit"
          />
        }
        title={<span className="break-all">Edit {table}</span>}
        sub="Changes save to the pipeline config and apply from the next run."
        actions={
          <Button variant="ghost" className="text-accent" onClick={onBack}>
            Back to sync
          </Button>
        }
      />

      <div className="grid grid-cols-[200px_1fr] items-start gap-4">
        <DetailCard bodyClassName="p-2">
          <EditNav pipelineId={pipelineId} changedSections={changedSections} violations={[...errors, ...warnings]} />
        </DetailCard>
        <DetailCard
          title={section?.label}
          aside={section?.aside}
          footer={
            <SaveFooter
              pendingCount={pendingCount}
              lastSavedRel={lastSavedRel}
              blocked={blocked}
              onDiscard={onDiscard}
              onSaveDraft={onSaveDraft}
              onSaveAndClose={onSaveAndClose}
            />
          }
        >
          <ValidationPanel violations={[...errors, ...warnings]} />
          {children}
        </DetailCard>
      </div>
    </>
  )
}
