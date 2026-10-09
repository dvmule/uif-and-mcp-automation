import { describe, it, expect } from 'vitest'
import { canAdvance, stepAnswer, STEP_LABELS } from '../steps'
import { AWS_INCREMENTAL_SILVER, GCP_FULL_NO_SILVER } from '@/lib/__tests__/__fixtures__/newSyncStates'
import type { NewSyncState } from '@/lib/newSyncConfig'

const aws = AWS_INCREMENTAL_SILVER
const with_ = (patch: Partial<NewSyncState>): NewSyncState => ({ ...aws, ...patch })

describe('STEP_LABELS', () => {
  it('has the six steps in order', () => {
    expect(STEP_LABELS).toEqual(['Source', 'Destination', 'Schema & load', 'Silver', 'Schedule & storage', 'Review & deploy'])
  })
})

describe('canAdvance', () => {
  it('Source needs both a connection (old Connection) and an analysis (old Table Name)', () => {
    expect(canAdvance(0, aws)).toBe(true)
    expect(canAdvance(0, with_({ connection: null }))).toBe(false)
    expect(canAdvance(0, with_({ analyzeResult: null }))).toBe(false)
  })

  it('Destination needs aws or gcp', () => {
    expect(canAdvance(1, aws)).toBe(true)
    expect(canAdvance(1, GCP_FULL_NO_SILVER)).toBe(true)
  })

  it('Schema & load needs an analysis (old Schema Review) and a watermark when incremental (old Extraction)', () => {
    expect(canAdvance(2, aws)).toBe(true)
    expect(canAdvance(2, with_({ analyzeResult: null }))).toBe(false)
    expect(canAdvance(2, with_({ watermarkCol: null }))).toBe(false)
    expect(canAdvance(2, with_({ watermarkCol: null, extractMode: 'full' }))).toBe(true)
  })

  it('Silver needs PK columns when on, and a valid BigQuery target on GCP', () => {
    expect(canAdvance(3, aws)).toBe(true)
    expect(canAdvance(3, with_({ pkCols: [] }))).toBe(false)
    expect(canAdvance(3, with_({ pkCols: [], silverEnabled: false }))).toBe(true)
    const gcpSilver = { ...GCP_FULL_NO_SILVER, silverEnabled: true, pkCols: ['A'] }
    expect(canAdvance(3, gcpSilver)).toBe(false)
    expect(canAdvance(3, { ...gcpSilver, bqProject: 'informatica-etl-nonprd', bqDataset: 'ds_1' })).toBe(true)
  })

  it('Schedule & storage needs valid storage (old Storage & Replication) and a frequency (old Schedule & Quality)', () => {
    expect(canAdvance(4, aws)).toBe(true)
    expect(canAdvance(4, with_({ gcsSecretArn: 'not-an-arn' }))).toBe(false)
    expect(canAdvance(4, with_({ silverBqBucket: ' ' }))).toBe(false)
    expect(canAdvance(4, with_({ frequency: '' as NewSyncState['frequency'] }))).toBe(false)
    expect(canAdvance(4, GCP_FULL_NO_SILVER)).toBe(true)
    expect(canAdvance(4, { ...GCP_FULL_NO_SILVER, bronzeGcsPath: 's3://x/' })).toBe(false)
  })

  it('Review always allows (Deploy has its own checks)', () => {
    expect(canAdvance(5, aws)).toBe(true)
  })
})

describe('stepAnswer', () => {
  it('summarises each done step', () => {
    expect(stepAnswer(aws, 0, 'dev')).toBe('FIPM.X · fipm')
    expect(stepAnswer(aws, 1, 'dev')).toBe('AWS')
    expect(stepAnswer(GCP_FULL_NO_SILVER, 1, 'dev')).toBe('GCP')
    expect(stepAnswer(aws, 2, 'dev')).toBe('incremental · LAST_UPDATE_DATE')
    expect(stepAnswer(GCP_FULL_NO_SILVER, 2, 'dev')).toBe('full')
    expect(stepAnswer(aws, 3, 'dev')).toBe('on · echo_dev_silver_fipm.X')
    expect(stepAnswer(GCP_FULL_NO_SILVER, 3, 'dev')).toBe('off')
    expect(stepAnswer(aws, 4, 'dev')).toBe('daily · 05:30–11:00 UTC')
    expect(stepAnswer(GCP_FULL_NO_SILVER, 4, 'dev')).toBe('weekly')
    expect(stepAnswer(aws, 5, 'dev')).toBeUndefined()
  })

  it('has no Source answer before a connection is picked', () => {
    expect(stepAnswer(with_({ connection: null }), 0, 'dev')).toBeUndefined()
  })
})
