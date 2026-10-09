import { describe, expect, it } from 'vitest'
import {
  formatRunKindLabel,
  isBronzeJob,
  isGcsReplicatorJob,
  isIngestJob,
  isReconcileJob,
  isReplicatorJob,
  isSilverBqReplicatorJob,
  isSilverJob,
  resolveRunLayer,
  isGoldJob,
  formatRunLayerLabel,
  resolveRunPhase,
} from '../runKindLabel'

describe('runKindLabel', () => {
  it('classifies GCP and AWS job names', () => {
    expect(isBronzeJob('eds-prod-bronze-ingest')).toBe(true)
    expect(isSilverJob('eds-prod-silver-bq-native')).toBe(true)
    expect(isSilverJob('echo-dev-silver-transform')).toBe(true)
    expect(isSilverJob('echo-dev-silver-bq-replicator')).toBe(false)
    expect(isGcsReplicatorJob('echo-dev-gcs-replicator')).toBe(true)
    expect(isSilverBqReplicatorJob('echo-dev-silver-bq-replicator')).toBe(true)
    expect(isReplicatorJob('echo-dev-gcs-replicator')).toBe(true)
    expect(isIngestJob('echo-dev-silver-bq-replicator')).toBe(false)
    expect(isIngestJob('echo-dev-bronze-ingest')).toBe(true)
    expect(isReconcileJob('eds-prod-silver-reconcile-gcp-bq')).toBe(true)
    expect(isReconcileJob('echo-dev-silver-reconcile')).toBe(true)
  })

  it('formats bronze and silver with phase', () => {
    expect(
      formatRunKindLabel({
        job_name: 'echo-dev-bronze-ingest',
        phase: null,
        load_phase: 'initial',
      }),
    ).toBe('Bronze · initial')

    expect(
      formatRunKindLabel({
        job_name: 'eds-prod-silver-bq-native',
        phase: 'incremental',
        load_phase: null,
      }),
    ).toBe('Silver · incremental')
  })

  it('formats reconcile without phase', () => {
    expect(
      formatRunKindLabel({
        job_name: 'eds-prod-silver-reconcile-gcp-bq',
        phase: null,
        load_phase: null,
      }),
    ).toBe('Reconcile')
  })

  it('formats replicator jobs', () => {
    expect(
      formatRunKindLabel({
        job_name: 'echo-dev-gcs-replicator',
        phase: null,
        load_phase: null,
      }),
    ).toBe('Bronze Replicator')

    expect(
      formatRunKindLabel({
        job_name: 'echo-dev-silver-bq-replicator',
        phase: null,
        load_phase: null,
      }),
    ).toBe('Silver Replicator')
  })

  it('defaults missing phase to incremental for ingest jobs', () => {
    expect(
      formatRunKindLabel({
        job_name: 'echo-dev-silver-transform',
        phase: null,
        load_phase: null,
      }),
    ).toBe('Silver · incremental')
  })

  it('resolveRunLayer and resolveRunPhase', () => {
    expect(resolveRunLayer('echo-dev-bronze-ingest')).toBe('bronze')
    expect(resolveRunPhase({ phase: 'initial', load_phase: null })).toBe('initial')
    expect(resolveRunPhase({ phase: null, load_phase: 'incremental' })).toBe('incremental')
  })
})

describe('gold runs (UIFPE-502)', () => {
  it('recognises both gold jobs', () => {
    expect(isGoldJob('uif-echo-dev-gold-transform')).toBe(true)
    expect(isGoldJob('uif-echo-dev-gold-reconcile')).toBe(true)
    expect(isGoldJob('uif-echo-dev-silver-transform')).toBe(false)
    expect(isGoldJob('uif-echo-dev-bronze-ingest')).toBe(false)
  })

  it('resolves gold-reconcile as gold, not reconcile', () => {
    // isReconcileJob is a substring match, so order in resolveRunLayer decides
    expect(resolveRunLayer('uif-echo-dev-gold-transform')).toBe('gold')
    expect(resolveRunLayer('uif-echo-dev-gold-reconcile')).toBe('gold')
    expect(resolveRunLayer('uif-echo-dev-silver-reconcile')).toBe('reconcile')
  })

  it('labels a gold run without inventing a phase', () => {
    // gold writes no phase/load_phase; resolveRunPhase would default every run
    // to "incremental", which would be a made-up fact on the screen
    expect(formatRunKindLabel({ job_name: 'uif-echo-dev-gold-transform' })).toBe('Gold')
  })

  it('gives the layer column a short label', () => {
    expect(formatRunLayerLabel('uif-echo-dev-gold-transform')).toBe('Gold')
    expect(formatRunLayerLabel('uif-echo-dev-bronze-ingest')).toBe('Bronze')
    expect(formatRunLayerLabel('')).toBe('—')
  })

  it('keeps gold out of the ingest filter', () => {
    // Gold is excluded by construction: isIngestJob ends in a bronze/silver
    // allowlist that gold-transform simply does not match, so no gold-specific
    // check is needed. Pinned because useSyncState and filterMatchers use this
    // to pick a pipeline's own runs — loosening the allowlist (say to
    // /transform/) would silently admit gold runs there.
    expect(isIngestJob('uif-echo-dev-gold-transform')).toBe(false)
    expect(isIngestJob('uif-echo-dev-gold-reconcile')).toBe(false)
    expect(isIngestJob('uif-echo-dev-bronze-ingest')).toBe(true)
  })
})
