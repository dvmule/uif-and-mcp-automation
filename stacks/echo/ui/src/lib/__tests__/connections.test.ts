import { describe, it, expect } from 'vitest'
import { arnTail, connectionUsage, groupSourcesBySecret, secretName } from '../connections'
import type { PipelineConfig } from '../types'

const ARN = 'arn:aws:secretsmanager:eu-west-1:0:secret:oracle-fipm-AbC123'

const pipe = (id: string, over: Partial<PipelineConfig>) =>
  ({ pipeline_id: id, extract: { table: id.toUpperCase(), mode: 'full' }, ...over }) as PipelineConfig

describe('secret ARNs', () => {
  it('names a secret without the Secrets Manager suffix', () => {
    expect(secretName(ARN)).toBe('oracle-fipm')
    expect(secretName('arn:aws:secretsmanager:eu-west-1:0:secret:echo/dev/oracle')).toBe('echo/dev/oracle')
    expect(secretName('arn:secret:demo')).toBe('demo')
  })

  it('shows the tail from :secret: on', () => {
    expect(arnTail(ARN)).toBe('…secret:oracle-fipm-AbC123')
    expect(arnTail('not-an-arn')).toBe('not-an-arn')
  })
})

describe('groupSourcesBySecret', () => {
  it('groups by secret, sorted by name, tables sorted, missing ARN as (unknown)', () => {
    const groups = groupSourcesBySecret([
      pipe('b', { source: { secret_arn: ARN } }),
      pipe('a', { source: { secret_arn: ARN } }),
      pipe('c', { source: { secret_arn: 'arn:x:secret:alpha' } }),
      pipe('d', { source: undefined as unknown as PipelineConfig['source'] }),
    ])
    expect(groups.map((g) => [g.name, g.syncs.map((s) => s.id)])).toEqual([
      ['(unknown)', ['d']],
      ['alpha', ['c']],
      ['oracle-fipm', ['a', 'b']],
    ])
  })
})

describe('connectionUsage', () => {
  const pipelines = [
    pipe('a', { source: { secret_arn: ARN } }),
    pipe('b', { source: { secret_arn: ARN }, replication: { gcs: { enabled: true, bucket: 'b', prefix: 'p/', credentials_secret_arn: 'gcs-1' } } }),
    pipe('c', { source: { secret_arn: 'other' } }),
  ]

  it('counts Oracle syncs by source.secret_arn, zero included', () => {
    expect(connectionUsage(pipelines, 'oracle', ARN)).toBe(2)
    expect(connectionUsage(pipelines, 'oracle', 'unused')).toBe(0)
  })

  it('counts GCS syncs by their replication block', () => {
    expect(connectionUsage(pipelines, 'gcs', 'gcs-1')).toBe(1)
    expect(connectionUsage(pipelines, 'gcs', 'gcs-2')).toBe(0)
  })

  it('is null when no sync carries that replication block', () => {
    expect(connectionUsage(pipelines, 'silver-bq', 'bq-1')).toBeNull()
  })
})
