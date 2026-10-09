import { describe, it, expect } from 'vitest'
import {
  BEHIND_SCHEDULE_VIEW,
  TONE_CLASS,
  alertRuleStatusView,
  goldStatusView,
  dqRunStatusView,
  freshnessView,
  pipelineStatusView,
  runStatusView,
  syncStatusView,
  type Tone,
} from '../statusVocabulary'
import type { DqRunStatus } from '../types'

const shape = (v: { label: string; tone: Tone }) => [v.label, v.tone]

describe('runStatusView', () => {
  const cases: Array<[string | null | undefined, string, Tone]> = [
    ['SUCCEEDED', 'Succeeded', 'ok'],
    ['FAILED', 'Failed', 'fail'],
    ['ERROR', 'Failed', 'fail'],
    ['TIMEOUT', 'Failed', 'fail'],
    ['WARNING', 'Warning', 'warn'],
    ['RUNNING', 'Running', 'live'],
    ['STARTING', 'Running', 'live'],
    ['STOPPING', 'Running', 'live'],
    ['WAITING', 'Running', 'live'],
    ['SKIPPED', 'Skipped', 'mute'],
    ['STOPPED', 'Stopped', 'mute'],
    ['succeeded', 'Succeeded', 'ok'],
    [null, 'Unknown', 'mute'],
    [undefined, 'Unknown', 'mute'],
  ]
  it.each(cases)('%s -> %s (%s)', (input, label, tone) => {
    expect(shape(runStatusView(input))).toEqual([label, tone])
  })

  it('treats an unrecognised value as Failed, like normalizeRunStatus', () => {
    const v = runStatusView('SOMETHING_NEW')
    expect(shape(v)).toEqual(['Failed', 'fail'])
    expect(v.raw).toBe('SOMETHING_NEW')
  })

  it('notes a gold run that changed nothing', () => {
    expect(runStatusView('SUCCEEDED', { goldNoChanges: true }).label).toBe('Succeeded · no changes')
    expect(runStatusView('FAILED', { goldNoChanges: true }).label).toBe('Failed')
  })

  it('always supplies an icon', () => {
    for (const s of ['SUCCEEDED', 'FAILED', 'WARNING', 'RUNNING', 'SKIPPED', 'STOPPED', null]) {
      expect(runStatusView(s).icon).toBeTruthy()
    }
  })
})

describe('pipelineStatusView', () => {
  it('adds Disabled', () => {
    expect(shape(pipelineStatusView('DISABLED'))).toEqual(['Disabled', 'mute'])
  })
  it('defers to runStatusView for the rest', () => {
    expect(shape(pipelineStatusView('SUCCEEDED'))).toEqual(['Succeeded', 'ok'])
    expect(shape(pipelineStatusView(undefined))).toEqual(['Unknown', 'mute'])
  })
})

describe('syncStatusView', () => {
  const cases: Array<['healthy' | 'degraded' | 'failing' | 'running' | 'disabled', string, Tone]> = [
    ['healthy', 'Healthy', 'ok'],
    ['degraded', 'Degraded', 'warn'],
    ['failing', 'Failing', 'fail'],
    ['running', 'Running', 'live'],
    ['disabled', 'Disabled', 'mute'],
  ]
  it.each(cases)('%s -> %s (%s)', (input, label, tone) => {
    expect(shape(syncStatusView(input))).toEqual([label, tone])
  })
})

describe('freshnessView', () => {
  it.each([
    ['fresh', 'Fresh', 'ok'],
    ['stale', 'Stale', 'warn'],
    ['critical', 'Overdue', 'fail'],
    ['unknown', 'Unknown', 'mute'],
  ] as const)('%s -> %s (%s)', (level, label, tone) => {
    expect(shape(freshnessView(level))).toEqual([label, tone])
  })

  it('says so when a critical pipeline has never succeeded', () => {
    expect(freshnessView('critical', { neverRan: true }).label).toBe('No successful run yet')
    expect(freshnessView('critical', { neverRan: true }).tone).toBe('fail')
  })
})

describe('dqRunStatusView', () => {
  const cases: Array<[DqRunStatus | undefined, string, Tone]> = [
    ['SUCCEEDED', 'Passed', 'ok'],
    ['FAILED', 'Failed', 'fail'],
    ['SKIPPED', 'Skipped', 'mute'],
    ['RUNNING', 'Running', 'live'],
    ['NOT_IMPLEMENTED', 'Coming soon', 'mute'],
    [undefined, 'Unknown', 'mute'],
  ]
  it.each(cases)('%s -> %s (%s)', (input, label, tone) => {
    expect(shape(dqRunStatusView(input))).toEqual([label, tone])
  })
})

describe('alertRuleStatusView', () => {
  it.each([
    ['OK', 'OK', 'ok'],
    ['ALARM', 'In alarm', 'fail'],
    ['INSUFFICIENT_DATA', 'No data yet', 'mute'],
  ] as const)('%s -> %s (%s)', (input, label, tone) => {
    expect(shape(alertRuleStatusView(input))).toEqual([label, tone])
  })

  it('sentence-cases any other value and keeps the raw one', () => {
    const v = alertRuleStatusView('SOME_NEW_STATE')
    expect(shape(v)).toEqual(['Some new state', 'mute'])
    expect(v.raw).toBe('SOME_NEW_STATE')
  })
})

describe('TONE_CLASS', () => {
  it('has a class string for every tone', () => {
    expect(Object.keys(TONE_CLASS).sort()).toEqual(['fail', 'live', 'mute', 'ok', 'warn'])
    expect(TONE_CLASS.ok).toBe('bg-signal-ok-bg text-signal-ok')
    expect(TONE_CLASS.live).toBe('bg-signal-live-bg text-signal-live')
  })
})

describe('goldStatusView', () => {
  it('speaks the sync words, plus Unknown when there are no runs to judge by', () => {
    expect(shape(goldStatusView('healthy'))).toEqual(['Healthy', 'ok'])
    expect(shape(goldStatusView('failing'))).toEqual(['Failing', 'fail'])
    expect(shape(goldStatusView('disabled'))).toEqual(['Disabled', 'mute'])
    expect(shape(goldStatusView('unknown'))).toEqual(['Unknown', 'mute'])
    expect(shape(BEHIND_SCHEDULE_VIEW)).toEqual(['Behind schedule', 'warn'])
  })
})
