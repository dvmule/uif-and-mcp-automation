import { Link, useOutletContext, useParams } from 'react-router-dom'
import { type ReactNode } from 'react'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import { Button } from '@/components/ui/button'
import {
  Tooltip as UITooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip'
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import type { PipelineConfig, ExtractMode } from '@/lib/types'
import {
  HEAVY_INCR_PREDICATE_THRESHOLD,
  readExtractProfile,
  profileViolations,
  type Phase,
  type FlexExtract,
  type ExtractProfile,
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

export function ExtractionSection() {
  const { id } = useParams<{ id: string }>()
  const { working, update } = useOutletContext<OutletCtx>()
  const dExt = working.extract as unknown as FlexExtract
  const initProf = readExtractProfile(dExt, 'initial')
  const incrProf = readExtractProfile(dExt, 'incremental')
  const violations = profileViolations(working as unknown as FlexDraft)

  const setExtract = (patch: Partial<PipelineConfig['extract']>) => {
    const next = structuredClone(working)
    next.extract = { ...next.extract, ...patch }
    update(next)
  }

  // Migrate-on-save: when patching a profile, drop legacy top-level JDBC keys so
  // the persisted shape matches the resolver's contract.
  const setProfile = (phase: Phase, patch: Partial<ExtractProfile>) => {
    const current = phase === 'initial' ? initProf : incrProf
    const merged = { ...current, ...patch }
    const next = structuredClone(working)
    const nextExt: FlexExtract = { ...(next.extract as unknown as FlexExtract), [phase]: merged }
    for (const k of ['jdbc_partition_col', 'jdbc_lower_bound', 'jdbc_upper_bound', 'jdbc_partitions', 'jdbc_predicates']) {
      delete nextExt[k]
    }
    ;(next as { extract: unknown }).extract = nextExt
    update(next)
  }

  return (
    <div className="space-y-4">
      <EditRow label="Table">
        <Input
          value={working.extract.table}
          onChange={e => setExtract({ table: e.target.value })}
        />
      </EditRow>

      <EditRow label="Mode">
        <Select
          value={working.extract.mode}
          onValueChange={v => setExtract({ mode: v as ExtractMode })}
        >
          <SelectTrigger className="w-full"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="full">full</SelectItem>
            <SelectItem value="incremental">incremental</SelectItem>
          </SelectContent>
        </Select>
      </EditRow>

      {working.extract.mode === 'incremental' && (
        <>
          <EditRow label="Watermark Column">
            <Input
              value={working.extract.watermark_col ?? ''}
              onChange={e => setExtract({ watermark_col: e.target.value || null })}
            />
          </EditRow>
          <EditRow label="Watermark Fallback">
            <Input
              value={working.extract.watermark_fallback_col ?? ''}
              onChange={e => setExtract({ watermark_fallback_col: e.target.value || null })}
              placeholder="e.g. CREATION_DATE"
            />
          </EditRow>
        </>
      )}

      {violations.length > 0 && !incrProf.allow_heavy && (
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
          <EditRow label="JDBC Partition Col">
            <Input
              value={initProf.jdbc_partition_col ?? ''}
              onChange={e => setProfile('initial', { jdbc_partition_col: e.target.value || null })}
            />
          </EditRow>
          <EditRow label="JDBC Lower Bound">
            <Input
              type="number"
              value={initProf.jdbc_lower_bound ?? ''}
              onChange={e => setProfile('initial', {
                jdbc_lower_bound: e.target.value === '' ? null : Number(e.target.value),
              })}
            />
          </EditRow>
          <EditRow label="JDBC Upper Bound">
            <Input
              type="number"
              value={initProf.jdbc_upper_bound ?? ''}
              onChange={e => setProfile('initial', {
                jdbc_upper_bound: e.target.value === '' ? null : Number(e.target.value),
              })}
            />
          </EditRow>
          <EditRow label="JDBC Partitions">
            <Input
              type="number"
              value={initProf.jdbc_partitions ?? ''}
              onChange={e => setProfile('initial', {
                jdbc_partitions: e.target.value === '' ? null : Number(e.target.value),
              })}
            />
          </EditRow>
          <EditRow label="JDBC Predicates">
            <div className="flex items-center gap-2">
              <span className="text-xs text-ink-mute">
                {initProf.jdbc_predicates?.length ?? 0} configured
              </span>
              <Link to={`/m/data-sync/syncs/${id}/edit/predicates?phase=initial`}>
                <Button size="sm" variant="outline" className="h-7 text-xs">
                  Open editor →
                </Button>
              </Link>
            </div>
          </EditRow>
          <EditRow label="Fetch Size">
            <Input
              type="number"
              value={(working.extract as unknown as { jdbc_fetchsize?: number }).jdbc_fetchsize ?? ''}
              onChange={e => setExtract({
                jdbc_fetchsize: e.target.value === '' ? undefined : Number(e.target.value),
              } as Partial<PipelineConfig['extract']>)}
            />
          </EditRow>
        </TabsContent>

        <TabsContent value="incremental" className="space-y-2 pt-3">
          <EditRow label="JDBC Partition Col">
            <Input
              value={incrProf.jdbc_partition_col ?? ''}
              onChange={e => setProfile('incremental', { jdbc_partition_col: e.target.value || null })}
            />
          </EditRow>
          <EditRow label="JDBC Lower Bound">
            <Input
              type="number"
              value={incrProf.jdbc_lower_bound ?? ''}
              onChange={e => setProfile('incremental', {
                jdbc_lower_bound: e.target.value === '' ? null : Number(e.target.value),
              })}
            />
          </EditRow>
          <EditRow label="JDBC Upper Bound">
            <Input
              type="number"
              value={incrProf.jdbc_upper_bound ?? ''}
              onChange={e => setProfile('incremental', {
                jdbc_upper_bound: e.target.value === '' ? null : Number(e.target.value),
              })}
            />
          </EditRow>
          <EditRow label="JDBC Partitions">
            <Input
              type="number"
              value={incrProf.jdbc_partitions ?? ''}
              onChange={e => setProfile('incremental', {
                jdbc_partitions: e.target.value === '' ? null : Number(e.target.value),
              })}
            />
          </EditRow>
          <EditRow label="JDBC Predicates">
            <div className="flex items-center gap-2">
              <span className="text-xs text-ink-mute">
                {incrProf.jdbc_predicates?.length ?? 0} configured
              </span>
              <Link to={`/m/data-sync/syncs/${id}/edit/predicates?phase=incremental`}>
                <Button size="sm" variant="outline" className="h-7 text-xs">
                  Open editor →
                </Button>
              </Link>
            </div>
          </EditRow>
          <EditRow label="Allow Heavy">
            <div className="flex items-center gap-2">
              <Switch
                checked={incrProf.allow_heavy ?? false}
                onCheckedChange={v => setProfile('incremental', { allow_heavy: v })}
              />
              <TooltipProvider>
                <UITooltip>
                  <TooltipTrigger asChild>
                    <span className="text-xs text-ink-mute cursor-help underline decoration-dotted">why?</span>
                  </TooltipTrigger>
                  <TooltipContent>
                    Bypass heavy-incremental safety rules. Required when you have
                    more than {HEAVY_INCR_PREDICATE_THRESHOLD} incremental predicates
                    or G.4X+ workers on hourly cadence.
                  </TooltipContent>
                </UITooltip>
              </TooltipProvider>
            </div>
          </EditRow>
        </TabsContent>
      </Tabs>
    </div>
  )
}
