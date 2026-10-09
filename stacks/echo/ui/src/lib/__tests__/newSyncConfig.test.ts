import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { buildPipelineConfig, NewSyncConfigError } from '../newSyncConfig'
import { AWS_INCREMENTAL_SILVER, GCP_FULL_NO_SILVER, GCP_INVALID } from './__fixtures__/newSyncStates'

const expected = JSON.parse(
  readFileSync(resolve(__dirname, '__fixtures__/newSyncConfig.expected.json'), 'utf8'),
)

// expected.json was captured by clicking Deploy on the pre-split wizard with these states;
// undefined values were written as "__undefined__" so key presence and order are compared too.
const keepUndefined = (_k: string, v: unknown) => (v === undefined ? '__undefined__' : v)
const serialise = (v: unknown) => JSON.stringify(v, keepUndefined, 2)

describe('buildPipelineConfig', () => {
  it('AWS incremental with silver is byte-for-byte the pre-split config', () => {
    const config = buildPipelineConfig(AWS_INCREMENTAL_SILVER, 'dev')
    expect(serialise(config)).toBe(serialise(expected.AWS_INCREMENTAL_SILVER.config))
    expect(config).toMatchSnapshot()
  })

  it('GCP full without silver is byte-for-byte the pre-split config', () => {
    const config = buildPipelineConfig(GCP_FULL_NO_SILVER, 'dev')
    expect(serialise(config)).toBe(serialise(expected.GCP_FULL_NO_SILVER.config))
    expect(config).toMatchSnapshot()
  })

  it('a state failing validation throws the same message the old review step showed', () => {
    expect(() => buildPipelineConfig(GCP_INVALID, 'dev')).toThrow(NewSyncConfigError)
    expect(() => buildPipelineConfig(GCP_INVALID, 'dev')).toThrow(expected.GCP_INVALID.error)
  })

  it('refuses a state with no connection or analysis', () => {
    expect(() => buildPipelineConfig({ ...GCP_FULL_NO_SILVER, analyzeResult: null }, 'dev')).toThrow(
      NewSyncConfigError,
    )
  })
})
