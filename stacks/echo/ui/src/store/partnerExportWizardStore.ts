import { create } from 'zustand'
import type {
  ExportDeliveryConfig,
  ExportFormat,
  ExportFormatConfig,
  ExportScheduleConfig,
  ExportSourceRef,
  MappingField,
  PartnerExport,
} from '@/lib/types'

const STEP_MIN = 0
const STEP_MAX = 4 // 0=Source, 1=Format/Mapping, 2=Delivery, 3=Schedule, 4=Review

const emptyMappingRow: MappingField = {
  source_col: '',
  output_field: '',
  type: 'string',
  format: null,
}

export interface PartnerExportWizardState {
  step: number
  exportName: string
  source: ExportSourceRef | null
  format: ExportFormatConfig
  mapping: MappingField[]
  advancedOpen: boolean
  delivery: ExportDeliveryConfig
  schedule: ExportScheduleConfig

  setStep: (s: number) => void
  patch: (
    p: Partial<
      Pick<PartnerExportWizardState, 'exportName' | 'source' | 'advancedOpen'>
    >,
  ) => void
  setSource: (src: ExportSourceRef) => void
  setFormatKind: (kind: ExportFormat) => void
  patchFormat: (p: Partial<ExportFormatConfig>) => void
  addMappingRow: () => void
  updateMappingRow: (idx: number, p: Partial<MappingField>) => void
  removeMappingRow: (idx: number) => void
  setMapping: (rows: MappingField[]) => void
  toggleAdvanced: () => void
  patchDelivery: (p: Partial<ExportDeliveryConfig>) => void
  patchSchedule: (p: Partial<ExportScheduleConfig>) => void
  patchAlerts: (p: Partial<ExportScheduleConfig['alerts']>) => void
  reset: () => void
  toBody: (exportId: string) => PartnerExport
}

const defaults = {
  step: 0,
  exportName: '',
  source: null,
  format: { kind: 'csv' as const, csv: { delimiter: ',', quote: '"', header_row: true } },
  mapping: [] as MappingField[],
  advancedOpen: false,
  delivery: {
    method: 'sftp',
    partner_id: '',
    connection_arn: '',
    path_template: '/{YYYYMMDD}.csv',
  } as ExportDeliveryConfig,
  schedule: {
    frequency: 'daily',
    run_at: '02:00',
    cron_expression: null,
    watermark_window: 'PT24H',
    empty_file_behavior: 'skip',
    retention_days: 30,
    alerts: {
      on_failure: true,
      on_partner_http_error: true,
      on_zero_rows: false,
      recipients: [] as string[],
    },
  } as ExportScheduleConfig,
}

const clampStep = (s: number) => Math.max(STEP_MIN, Math.min(STEP_MAX, s))

export const usePartnerExportWizardStore = create<PartnerExportWizardState>()((set, get) => ({
  ...defaults,
  setStep: (s) => set({ step: clampStep(s) }),
  patch: (p) => set(p),
  setSource: (src) => set({ source: src }),
  setFormatKind: (kind) =>
    set((s) => ({ format: { ...s.format, kind } })),
  patchFormat: (p) => set((s) => ({ format: { ...s.format, ...p } })),
  addMappingRow: () =>
    set((s) => ({ mapping: [...s.mapping, { ...emptyMappingRow }] })),
  updateMappingRow: (idx, p) =>
    set((s) => ({
      mapping: s.mapping.map((row, i) => (i === idx ? { ...row, ...p } : row)),
    })),
  removeMappingRow: (idx) =>
    set((s) => ({ mapping: s.mapping.filter((_, i) => i !== idx) })),
  setMapping: (rows) => set({ mapping: rows }),
  toggleAdvanced: () => set((s) => ({ advancedOpen: !s.advancedOpen })),
  patchDelivery: (p) => set((s) => ({ delivery: { ...s.delivery, ...p } })),
  patchSchedule: (p) => set((s) => ({ schedule: { ...s.schedule, ...p } })),
  patchAlerts: (p) =>
    set((s) => ({ schedule: { ...s.schedule, alerts: { ...s.schedule.alerts, ...p } } })),
  reset: () => set({ ...defaults, mapping: [] }),
  toBody: (exportId) => {
    const s = get()
    return {
      export_id: exportId,
      name: s.exportName,
      enabled: true,
      source: s.source!,
      format: s.format,
      mapping: s.mapping,
      delivery: s.delivery,
      schedule: s.schedule,
    }
  },
}))

/** Slugify an export name for use as an export_id. */
export function slugifyExportId(name: string): string {
  return name
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '')
    .slice(0, 64)
}
