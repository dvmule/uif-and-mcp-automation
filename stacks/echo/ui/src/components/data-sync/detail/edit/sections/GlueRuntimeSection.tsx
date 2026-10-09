import { useOutletContext } from 'react-router-dom'
import { type ReactNode } from 'react'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import type { PipelineConfig, WorkerType } from '@/lib/types'
import {
  readGlueProfile,
  profileViolations,
  type Phase,
  type FlexGlue,
  type FlexExtract,
  type GlueProfile,
  type FlexDraft,
} from './profileHelpers'

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

export function GlueRuntimeSection() {
  const { working, update } = useOutletContext<OutletCtx>()
  const dGlue = working.glue as unknown as FlexGlue
  const initG = readGlueProfile(dGlue, 'initial')
  const incrG = readGlueProfile(dGlue, 'incremental')
  const violations = profileViolations(working as unknown as FlexDraft)
  const allowHeavy =
    (working.extract as unknown as FlexExtract)?.incremental?.allow_heavy === true

  const setProfile = (phase: Phase, patch: Partial<GlueProfile>) => {
    const current = phase === 'initial' ? initG : incrG
    const merged = { ...current, ...patch }
    const next = structuredClone(working)
    const nextGlue: FlexGlue = { ...(next.glue as unknown as FlexGlue), [phase]: merged }
    for (const k of ['worker_type', 'num_workers', 'timeout_minutes']) {
      delete nextGlue[k]
    }
    ;(next as { glue: unknown }).glue = nextGlue
    update(next)
  }

  return (
    <div className="space-y-4">
      {violations.length > 0 && !allowHeavy && (
        <div className="rounded-md border border-amber bg-amber/15 px-3 py-2 text-xs text-ink space-y-1">
          <p className="font-medium">Heavy-incremental safety warnings:</p>
          {violations.map((v, i) => <p key={i}>• {v}</p>)}
        </div>
      )}

      <Tabs defaultValue="initial">
        <TabsList>
          <TabsTrigger value="initial">Initial backfill</TabsTrigger>
          <TabsTrigger value="incremental">Incremental</TabsTrigger>
        </TabsList>

        <TabsContent value="initial" className="space-y-2 pt-3">
          <EditRow label="Worker Type">
            <Select
              value={initG.worker_type}
              onValueChange={v => setProfile('initial', { worker_type: v as WorkerType })}
            >
              <SelectTrigger className="w-full"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="G.1X">G.1X</SelectItem>
                <SelectItem value="G.2X">G.2X</SelectItem>
                <SelectItem value="G.4X">G.4X</SelectItem>
              </SelectContent>
            </Select>
          </EditRow>
          <EditRow label="Num Workers">
            <Input
              type="number"
              value={initG.num_workers}
              onChange={e => setProfile('initial', { num_workers: Number(e.target.value) })}
            />
          </EditRow>
          <EditRow label="Timeout (min)">
            <Input
              type="number"
              value={initG.timeout_minutes}
              onChange={e => setProfile('initial', { timeout_minutes: Number(e.target.value) })}
            />
          </EditRow>
        </TabsContent>

        <TabsContent value="incremental" className="space-y-2 pt-3">
          <EditRow label="Worker Type">
            <Select
              value={incrG.worker_type}
              onValueChange={v => setProfile('incremental', { worker_type: v as WorkerType })}
            >
              <SelectTrigger className="w-full"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="G.1X">G.1X</SelectItem>
                <SelectItem value="G.2X">G.2X</SelectItem>
                <SelectItem value="G.4X">G.4X</SelectItem>
              </SelectContent>
            </Select>
          </EditRow>
          <EditRow label="Num Workers">
            <Input
              type="number"
              value={incrG.num_workers}
              onChange={e => setProfile('incremental', { num_workers: Number(e.target.value) })}
            />
          </EditRow>
          <EditRow label="Timeout (min)">
            <Input
              type="number"
              value={incrG.timeout_minutes}
              onChange={e => setProfile('incremental', { timeout_minutes: Number(e.target.value) })}
            />
          </EditRow>
        </TabsContent>
      </Tabs>
    </div>
  )
}
