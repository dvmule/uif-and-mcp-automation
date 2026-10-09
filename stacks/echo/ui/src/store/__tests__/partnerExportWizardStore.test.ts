import { describe, it, expect, beforeEach } from 'vitest'
import { usePartnerExportWizardStore } from '../partnerExportWizardStore'

beforeEach(() => {
  usePartnerExportWizardStore.getState().reset()
})

describe('partnerExportWizardStore', () => {
  it('starts with sensible defaults', () => {
    const s = usePartnerExportWizardStore.getState()
    expect(s.step).toBe(0)
    expect(s.exportName).toBe('')
    expect(s.source).toBeNull()
    expect(s.format.kind).toBe('csv')
    expect(s.mapping).toEqual([])
    expect(s.advancedOpen).toBe(false)
    expect(s.schedule.frequency).toBe('daily')
    expect(s.schedule.empty_file_behavior).toBe('skip')
    expect(s.schedule.retention_days).toBe(30)
    expect(s.schedule.alerts.on_failure).toBe(true)
    expect(s.schedule.alerts.on_partner_http_error).toBe(true)
    expect(s.schedule.alerts.on_zero_rows).toBe(false)
  })

  it('step navigation clamps to [0..4]', () => {
    const { setStep } = usePartnerExportWizardStore.getState()
    setStep(-1)
    expect(usePartnerExportWizardStore.getState().step).toBe(0)
    setStep(99)
    expect(usePartnerExportWizardStore.getState().step).toBe(4)
    setStep(2)
    expect(usePartnerExportWizardStore.getState().step).toBe(2)
  })

  it('setSource updates layer + dataset together', () => {
    usePartnerExportWizardStore.getState().setSource({ layer: 'silver', dataset: 'silver.s.t' })
    const s = usePartnerExportWizardStore.getState()
    expect(s.source).toEqual({ layer: 'silver', dataset: 'silver.s.t' })
  })

  it('setFormat preserves advanced fields when only kind changes', () => {
    const st = usePartnerExportWizardStore.getState()
    st.patchFormat({ sql_transform: "SELECT * WHERE x > 0" })
    st.setFormatKind('json')
    const s = usePartnerExportWizardStore.getState()
    expect(s.format.kind).toBe('json')
    expect(s.format.sql_transform).toBe("SELECT * WHERE x > 0")
  })

  it('mapping CRUD: add, update, remove', () => {
    const st = usePartnerExportWizardStore.getState()
    st.addMappingRow()
    expect(usePartnerExportWizardStore.getState().mapping).toHaveLength(1)
    st.updateMappingRow(0, { source_col: 'ID', output_field: 'id', type: 'int' })
    expect(usePartnerExportWizardStore.getState().mapping[0].source_col).toBe('ID')
    st.removeMappingRow(0)
    expect(usePartnerExportWizardStore.getState().mapping).toHaveLength(0)
  })

  it('toggleAdvanced flips the disclosure flag', () => {
    usePartnerExportWizardStore.getState().toggleAdvanced()
    expect(usePartnerExportWizardStore.getState().advancedOpen).toBe(true)
    usePartnerExportWizardStore.getState().toggleAdvanced()
    expect(usePartnerExportWizardStore.getState().advancedOpen).toBe(false)
  })

  it('toBody assembles a PartnerExport from state', () => {
    const st = usePartnerExportWizardStore.getState()
    st.patch({
      exportName: 'Partner A daily CSV',
      source: { layer: 'silver', dataset: 'silver.s.t' },
    })
    st.patchDelivery({
      method: 'sftp',
      partner_id: 'partner_a',
      connection_arn: 'arn:x',
      path_template: '/{YYYYMMDD}.csv',
    })
    const body = usePartnerExportWizardStore.getState().toBody('partner_a_daily_csv')
    expect(body.export_id).toBe('partner_a_daily_csv')
    expect(body.name).toBe('Partner A daily CSV')
    expect(body.source.dataset).toBe('silver.s.t')
    expect(body.delivery.partner_id).toBe('partner_a')
  })

  it('reset clears everything back to defaults', () => {
    usePartnerExportWizardStore.getState().patch({ exportName: 'x' })
    usePartnerExportWizardStore.getState().reset()
    expect(usePartnerExportWizardStore.getState().exportName).toBe('')
  })
})
