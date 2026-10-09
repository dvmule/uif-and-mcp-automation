import { describe, expect, it } from 'vitest'
import {
  countByStatus,
  EMPTY_RUNS_FILTER,
  filterRunRows,
  formatUtcClockTime,
  formatUtcStamp,
  goldRunRow,
  groupRunsByObject,
  isDefaultRunsFilter,
  parseRunsFilter,
  pipelineRunRow,
  runRowStatusView,
  runsToCsv,
  serializeRunsFilter,
  sortRunRows,
  statusBucket,
  type RunRow,
  type RunsFilter,
} from '@/lib/runsTable'
import type { GoldPipelineConfig, GoldRunMetrics, PipelineConfig, RunMetrics } from '@/lib/types'

const NOW = new Date('2026-09-29T12:00:00Z')

const pipeline = {
  pipeline_id: 'eds-price-list-daily',
  extract: { table: 'EDS.PRICE_LIST', mode: 'incremental' },
} as unknown as PipelineConfig

const run = (over: Partial<RunMetrics> = {}): RunMetrics => ({
  job_name: 'uif-echo-dev-bronze-ingest-eds',
  run_id: 'jr_1',
  pipeline_id: 'eds-price-list-daily',
  status: 'SUCCEEDED',
  started_at: '2026-09-29T11:59:00Z',
  duration_sec: 90,
  rows_read: 10,
  rows_written: 8,
  phase: 'incremental',
  ...over,
})

const goldRun = (over: Partial<GoldRunMetrics> = {}): GoldRunMetrics => ({
  job_name: 'uif-echo-dev-gold-transform',
  run_id: 'jr_g1',
  gold_id: 'price',
  status: 'SUCCEEDED',
  started_at: '2026-09-29T06:31:00Z',
  duration_sec: 540,
  rows_read: 500,
  rows_written: 0,
  ...over,
})

const gold = { gold_id: 'price', target: { table: 'gold_price' } } as GoldPipelineConfig

const row = (over: Partial<RunRow> = {}): RunRow => ({ ...pipelineRunRow(pipeline, run()), ...over })

describe('pipelineRunRow', () => {
  it.each([
    ['uif-echo-dev-bronze-ingest-x', 'bronze', 'Incremental'],
    ['uif-echo-dev-silver-transform-x', 'silver', 'Incremental'],
    ['uif-echo-dev-silver-reconcile-x', 'reconcile', 'Reconcile'],
    ['uif-echo-dev-gcs-replicator', 'other', 'Bronze Replicator'],
    ['something-else', 'other', 'incremental'],
  ])('%s -> layer %s, kind %s', (job, layer, kind) => {
    const r = pipelineRunRow(pipeline, run({ job_name: job }))
    expect(r.layer).toBe(layer)
    expect(r.kind).toBe(kind)
  })

  it('carries names, link, counts and the pipeline id', () => {
    const r = pipelineRunRow(pipeline, run())
    expect(r).toMatchObject({
      key: 'eds-price-list-daily:jr_1',
      runId: 'jr_1',
      object: 'EDS.PRICE_LIST',
      objectSub: 'eds-price-list-daily',
      objectHref: '/m/data-sync/syncs/eds-price-list-daily',
      rowsRead: 10,
      rowsWritten: 8,
      durationSec: 90,
      pipelineId: 'eds-price-list-daily',
    })
    expect(r.goldId).toBeUndefined()
  })

  it('reads rows_extracted when rows_read is absent, and marks an initial load', () => {
    const r = pipelineRunRow(pipeline, run({ rows_read: undefined, rows_extracted: 7, phase: 'initial' }))
    expect(r.rowsRead).toBe(7)
    expect(r.kind).toBe('Initial')
  })

  it('has no sub-line when the table name is the pipeline id', () => {
    const p = { pipeline_id: 'a', extract: { table: 'a' } } as unknown as PipelineConfig
    expect(pipelineRunRow(p, run()).objectSub).toBeUndefined()
  })
})

describe('goldRunRow', () => {
  it('is layer gold, a Transform, linked to the gold page, with no rows-read side invented', () => {
    const r = goldRunRow(goldRun({ rows_read: undefined }), gold)
    expect(r).toMatchObject({
      key: 'gold:price:jr_g1',
      object: 'price',
      objectSub: 'gold_price',
      objectHref: '/m/data-sync/gold/price',
      layer: 'gold',
      kind: 'Transform',
      rowsRead: null,
      rowsWritten: 0,
      goldId: 'price',
    })
    expect(r.pipelineId).toBeUndefined()
  })

  it('names the table from the run itself when no gold config is given', () => {
    expect(goldRunRow(goldRun({ gold_table: 'gold_price_v2' })).objectSub).toBe('gold_price_v2')
  })

  it('reads a zero-write success as "no changes" only for gold', () => {
    expect(runRowStatusView(goldRunRow(goldRun(), gold)).label).toBe('Succeeded · no changes')
    expect(runRowStatusView(row({ rowsWritten: 0 })).label).toBe('Succeeded')
    expect(runRowStatusView(goldRunRow(goldRun({ rows_written: 3 }), gold)).label).toBe('Succeeded')
  })
})

describe('statusBucket', () => {
  it.each([
    ['SUCCEEDED', 'SUCCEEDED'],
    ['RUNNING', 'RUNNING'],
    ['STARTING', 'RUNNING'],
    ['WARNING', 'WARNING'],
    ['STOPPED', 'WARNING'],
    ['FAILED', 'FAILED'],
    ['TIMEOUT', 'FAILED'],
    ['SKIPPED', 'SKIPPED'],
  ])('%s -> %s', (status, bucket) => {
    expect(statusBucket(status)).toBe(bucket)
  })
})

describe('parseRunsFilter / serializeRunsFilter', () => {
  it('defaults to the last 24 hours with nothing else set', () => {
    const f = parseRunsFilter(new URLSearchParams())
    expect(f).toEqual({ window: '24h', status: [], layer: [], object: [], search: '', view: 'runs' })
    expect(isDefaultRunsFilter(f)).toBe(true)
  })

  it('reads every key and ignores values it does not know', () => {
    const f = parseRunsFilter(
      new URLSearchParams('window=7d&status=failed,BOGUS,warning&layer=Gold,nope&object=a,b&q= eds &view=objects'),
    )
    expect(f).toEqual({
      window: '7d', status: ['FAILED', 'WARNING'], layer: ['gold'], object: ['a', 'b'], search: 'eds', view: 'objects',
    })
  })

  it('an unknown view reads as runs, and the object view is not a filter', () => {
    expect(parseRunsFilter(new URLSearchParams('view=grid')).view).toBe('runs')
    expect(isDefaultRunsFilter(parseRunsFilter(new URLSearchParams('view=objects')))).toBe(true)
  })

  it('writes view=objects only, and drops the key for the runs view', () => {
    const objects = serializeRunsFilter({ ...EMPTY_RUNS_FILTER, view: 'objects' }, new URLSearchParams('run=r1'))
    expect(objects.toString()).toBe('run=r1&view=objects')
    expect(parseRunsFilter(objects).view).toBe('objects')
    expect(serializeRunsFilter(EMPTY_RUNS_FILTER, objects).toString()).toBe('run=r1')
  })

  it('an unknown window falls back to the default', () => {
    expect(parseRunsFilter(new URLSearchParams('window=3y')).window).toBe('24h')
  })

  it('reads the legacy ?sync= link as an object filter over all loaded runs', () => {
    const f = parseRunsFilter(new URLSearchParams('sync=p1'))
    expect(f.object).toEqual(['p1'])
    expect(f.window).toBe('all')
  })

  it('an explicit window wins over the legacy default, and sync is not repeated', () => {
    const f = parseRunsFilter(new URLSearchParams('sync=p1&object=p1,p2&window=24h'))
    expect(f.window).toBe('24h')
    expect(f.object).toEqual(['p1', 'p2'])
  })

  it('round-trips, drops defaults, and keeps unrelated keys such as run', () => {
    const f: RunsFilter = {
      window: 'all', status: ['FAILED'], layer: ['gold'], object: ['x'], search: 'q1', view: 'objects',
    }
    const out = serializeRunsFilter(f, new URLSearchParams('run=jr_9&sync=old'))
    expect(out.get('run')).toBe('jr_9')
    expect(out.has('sync')).toBe(false)
    expect(parseRunsFilter(out)).toEqual(f)
    const plain = serializeRunsFilter(
      { window: '24h', status: [], layer: [], object: [], search: '', view: 'runs' }, out,
    )
    expect(plain.toString()).toBe('run=jr_9')
  })
})

describe('filterRunRows', () => {
  const rows = [
    row({ runId: 'r-new', startedAt: '2026-09-29T11:00:00Z' }),
    row({ runId: 'r-30h', startedAt: '2026-09-28T06:00:00Z' }),
    row({ runId: 'r-old', startedAt: '2026-09-25T11:00:00Z' }),
    row({ runId: 'r-fail', status: 'FAILED', startedAt: '2026-09-29T10:00:00Z' }),
    row({ runId: 'r-nostart', startedAt: null }),
    goldRunRow(goldRun({ run_id: 'r-gold', started_at: '2026-09-29T06:31:00Z' }), gold),
  ]
  const base: RunsFilter = { window: 'all', status: [], layer: [], object: [], search: '', view: 'runs' }
  const ids = (f: Partial<RunsFilter>) => filterRunRows(rows, { ...base, ...f }, NOW).map((r) => r.runId)

  it('all: keeps every row, including one with no start time', () => {
    expect(ids({})).toHaveLength(6)
  })
  it('24h: drops rows over 24 h old (30 h counts) and rows with no start time', () => {
    expect(ids({ window: '24h' })).toEqual(['r-new', 'r-fail', 'r-gold'])
  })
  it('7d: keeps the four-day-old row', () => {
    expect(ids({ window: '7d' })).toEqual(['r-new', 'r-30h', 'r-old', 'r-fail', 'r-gold'])
  })
  it('status', () => {
    expect(ids({ status: ['FAILED'] })).toEqual(['r-fail'])
  })
  it('layer', () => {
    expect(ids({ layer: ['gold'] })).toEqual(['r-gold'])
  })
  it('object matches a pipeline id or a gold id', () => {
    expect(ids({ object: ['price'] })).toEqual(['r-gold'])
    expect(ids({ object: ['eds-price-list-daily'] })).toHaveLength(5)
  })
  it('search matches object, sub-line and run id, ignoring case', () => {
    expect(ids({ search: 'edS.PRICE' })).toHaveLength(5)
    expect(ids({ search: 'r-gold' })).toEqual(['r-gold'])
    expect(ids({ search: 'gold_price' })).toEqual(['r-gold'])
    expect(ids({ search: 'nothing-like-this' })).toEqual([])
  })
  it('combines filters', () => {
    expect(ids({ window: '24h', layer: ['bronze'], status: ['FAILED'] })).toEqual(['r-fail'])
  })
})

describe('sortRunRows', () => {
  const rows = [
    row({ runId: 'a', startedAt: '2026-09-29T01:00:00Z' }),
    row({ runId: 'none', startedAt: null }),
    row({ runId: 'b', startedAt: '2026-09-29T03:00:00Z' }),
  ]
  it('newest first, unstamped last', () => {
    expect(sortRunRows(rows, 'desc').map((r) => r.runId)).toEqual(['b', 'a', 'none'])
  })
  it('oldest first, unstamped still last', () => {
    expect(sortRunRows(rows, 'asc').map((r) => r.runId)).toEqual(['a', 'b', 'none'])
  })
  it('does not mutate its input', () => {
    sortRunRows(rows)
    expect(rows[0].runId).toBe('a')
  })
})

describe('groupRunsByObject', () => {
  const at = (h: number) => `2026-09-29T${String(h).padStart(2, '0')}:00:00Z`
  const twin = { pipeline_id: 'eds-price-list-hourly', extract: { table: 'EDS.PRICE_LIST' } } as unknown as PipelineConfig

  it('groups by object key, not by label, the most recently run first', () => {
    const groups = groupRunsByObject([
      row({ runId: 'a1', startedAt: at(1) }),
      pipelineRunRow(twin, run({ run_id: 'b1', started_at: at(5) })),
      goldRunRow(goldRun({ run_id: 'g1', started_at: at(3) }), gold),
      row({ runId: 'a2', startedAt: at(2) }),
    ])
    expect(groups.map((g) => g.key)).toEqual(['eds-price-list-hourly', 'gold:price', 'eds-price-list-daily'])
    expect(groups[0].object).toBe('EDS.PRICE_LIST')
    expect(groups[2].object).toBe('EDS.PRICE_LIST')
    expect(groups[1].objectHref).toBe('/m/data-sync/gold/price')
    expect(groups[2].runs.map((r) => r.runId)).toEqual(['a2', 'a1'])
    expect(groups[2].latest?.runId).toBe('a2')
  })

  it('lists the layers once each, bronze before silver', () => {
    const [g] = groupRunsByObject([
      row({ runId: 's1', layer: 'silver', startedAt: at(4) }),
      row({ runId: 'b1', layer: 'bronze', startedAt: at(3) }),
      row({ runId: 's2', layer: 'silver', startedAt: at(2) }),
    ])
    expect(g.layers).toEqual(['bronze', 'silver'])
  })

  it('keeps the last five, oldest to newest', () => {
    const [g] = groupRunsByObject(Array.from({ length: 7 }, (_, i) => row({ runId: `r${i}`, startedAt: at(i + 1) })))
    expect(g.runs).toHaveLength(7)
    expect(g.lastFive.map((r) => r.runId)).toEqual(['r2', 'r3', 'r4', 'r5', 'r6'])
  })

  it('success share counts warnings as good and leaves running out of the finished runs', () => {
    const [g] = groupRunsByObject([
      row({ runId: 'ok', status: 'SUCCEEDED', startedAt: at(1) }),
      row({ runId: 'warn', status: 'WARNING', startedAt: at(2) }),
      row({ runId: 'bad', status: 'FAILED', startedAt: at(3) }),
      row({ runId: 'live', status: 'RUNNING', startedAt: at(4) }),
    ])
    expect(g).toMatchObject({ finished: 3, succeeded: 2, successPct: 67 })
    expect(g.latest?.runId).toBe('live')
  })

  it('no finished runs: no success share', () => {
    const [g] = groupRunsByObject([
      row({ runId: 'live', status: 'RUNNING', startedAt: at(4) }),
      row({ runId: 'skip', status: 'SKIPPED', startedAt: at(3) }),
    ])
    expect(g).toMatchObject({ finished: 0, succeeded: 0, successPct: null })
  })

  it('no rows: no groups', () => {
    expect(groupRunsByObject([])).toEqual([])
  })
})

describe('countByStatus', () => {
  it('counts by status and by layer; skipped counts nowhere', () => {
    const c = countByStatus([
      row({ status: 'SUCCEEDED' }),
      row({ status: 'SUCCEEDED', layer: 'silver' }),
      goldRunRow(goldRun(), gold),
      row({ status: 'WARNING' }),
      row({ status: 'FAILED' }),
      row({ status: 'TIMEOUT' }),
      row({ status: 'RUNNING' }),
      row({ status: 'SKIPPED' }),
    ])
    expect(c).toMatchObject({ succeeded: 3, warning: 1, failed: 2, running: 1 })
    expect(c.byLayer.gold.succeeded).toBe(1)
    expect(c.byLayer.bronze).toEqual({ succeeded: 1, warning: 1, failed: 2, running: 1 })
    expect(c.byLayer.silver.succeeded).toBe(1)
    expect(c.byLayer.reconcile).toEqual({ succeeded: 0, warning: 0, failed: 0, running: 0 })
  })
  it('an empty list is all zeros', () => {
    expect(countByStatus([])).toMatchObject({ succeeded: 0, warning: 0, failed: 0, running: 0 })
  })
})

describe('formatUtcStamp / formatUtcClockTime', () => {
  it('formats in UTC', () => {
    expect(formatUtcStamp('2026-09-29T11:59:00Z')).toBe('29 Sep 2026, 11:59 UTC')
    expect(formatUtcClockTime('2026-09-29T12:16:00Z')).toBe('12:16 UTC')
  })
  it('em dash for missing or unreadable', () => {
    expect(formatUtcStamp(null)).toBe('—')
    expect(formatUtcStamp('not a date')).toBe('—')
    expect(formatUtcClockTime(undefined)).toBe('—')
  })
})

describe('runsToCsv', () => {
  it('writes a header and one line per row, CRLF separated, empty where a side is missing', () => {
    const csv = runsToCsv([row(), goldRunRow(goldRun({ rows_read: undefined }), gold)])
    const lines = csv.split('\r\n')
    expect(lines).toHaveLength(3)
    expect(lines[0]).toBe('Started (UTC),Object,Layer,Kind,Status,Duration (s),Rows read,Rows written,Run id')
    expect(lines[1]).toBe('2026-09-29T11:59:00Z,EDS.PRICE_LIST,Bronze,Incremental,Succeeded,90,10,8,jr_1')
    expect(lines[2]).toBe('2026-09-29T06:31:00Z,price,Gold,Transform,Succeeded · no changes,540,,0,jr_g1')
  })

  it('quotes cells holding a comma, a quote or a line break (RFC 4180)', () => {
    const csv = runsToCsv([row({ object: 'A,B', objectSub: undefined, runId: 'he said "hi"\nbye' })])
    expect(csv.split('\r\n')[1]).toBe(
      '2026-09-29T11:59:00Z,"A,B",Bronze,Incremental,Succeeded,90,10,8,"he said ""hi""\nbye"',
    )
  })

  it('an empty list is just the header', () => {
    expect(runsToCsv([]).split('\r\n')).toHaveLength(1)
  })
})
