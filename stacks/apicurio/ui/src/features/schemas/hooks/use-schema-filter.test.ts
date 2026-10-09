import { describe, it, expect } from 'vitest';
import { filterSchemas, countDomains, groupSchemas } from './use-schema-filter';
import type { SchemaSummary } from '../types/schema.types';
import type { SchemaUrlState } from './use-schema-url-state';

const DEFAULT_STATE: SchemaUrlState = {
  view: 'all',
  subject: null,
  group: null,
  search: '',
  domain: null,
  classification: null,
  lifecycle: null,
  sort: 'artifact_id',
  dir: 'asc',
  detailTab: 'schema',
  schemaView: 'code',
  mappingKind: 'scalar',
  groupBy: null,
  showRawSubjects: false,
  // Also required, and also absent until these files were typechecked.
  version: null,
};

function makeItem(overrides: Partial<SchemaSummary> = {}): SchemaSummary {
  return {
    artifact_id: 'commerce.product-value',
    group_id: 'default',
    name: null,
    type: 'AVRO',
    state: 'ENABLED',
    created_on: '2024-01-01T00:00:00Z',
    modified_on: '2024-06-01T00:00:00Z',
    latest_version: '3',
    owner: { service: 'CI0001', team: null, email: null },
    domain: 'commerce',
    classification: 'internal',
    lifecycle: 'active',
    pii_fields: [],
    ...overrides,
  };
}

describe('filterSchemas — segments', () => {
  const a = makeItem({ artifact_id: 'a', group_id: 'g' });
  const b = makeItem({ artifact_id: 'b', group_id: 'g' });
  const nonCompliant = new Set(['g/a']);

  it('all returns everything', () => {
    expect(filterSchemas([a, b], nonCompliant, DEFAULT_STATE)).toHaveLength(2);
  });

  it('non-compliant tab filters to non-compliant ids', () => {
    const result = filterSchemas([a, b], nonCompliant, { ...DEFAULT_STATE, view: 'non-compliant' });
    expect(result).toHaveLength(1);
    expect(result[0].artifact_id).toBe('a');
  });

  it('recently-changed returns items modified within 7 days', () => {
    const fresh = makeItem({ artifact_id: 'fresh', modified_on: new Date(Date.now() - 60_000).toISOString() });
    const stale = makeItem({ artifact_id: 'stale', modified_on: '2020-01-01T00:00:00Z' });
    const result = filterSchemas([fresh, stale], new Set(), { ...DEFAULT_STATE, view: 'recently-changed' });
    expect(result).toHaveLength(1);
    expect(result[0].artifact_id).toBe('fresh');
  });
});

describe('filterSchemas — free-text search', () => {
  const items = [
    makeItem({ artifact_id: 'commerce.product-value', domain: 'commerce', owner: { service: 'CI0001', team: null, email: null } }),
    makeItem({ artifact_id: 'learning.course-value', domain: 'learning', owner: { service: 'CI0002', team: null, email: null } }),
  ];

  it('matches artifact_id substring', () => {
    const r = filterSchemas(items, new Set(), { ...DEFAULT_STATE, search: 'course' });
    expect(r).toHaveLength(1);
    expect(r[0].artifact_id).toBe('learning.course-value');
  });

  it('matches domain', () => {
    const r = filterSchemas(items, new Set(), { ...DEFAULT_STATE, search: 'learning' });
    expect(r).toHaveLength(1);
  });

  it('matches owner CI id', () => {
    const r = filterSchemas(items, new Set(), { ...DEFAULT_STATE, search: 'CI0002' });
    expect(r).toHaveLength(1);
    expect(r[0].artifact_id).toBe('learning.course-value');
  });

  it('is case-insensitive', () => {
    const r = filterSchemas(items, new Set(), { ...DEFAULT_STATE, search: 'COMMERCE' });
    expect(r).toHaveLength(1);
  });

  it('returns empty when no match', () => {
    const r = filterSchemas(items, new Set(), { ...DEFAULT_STATE, search: 'zzzznotfound' });
    expect(r).toHaveLength(0);
  });

  describe('search field coverage', () => {
    const items = [
      makeItem({ artifact_id: 'commerce.order.placed-value', name: 'OrderPlaced', domain: 'commerce', classification: 'confidential', lifecycle: 'active', owner: { service: 'CI0093423', team: 'TM-COMMERCE-PLATFORM', email: null } }),
      makeItem({ artifact_id: 'learning.learner-value', name: 'Learner', domain: 'learning', classification: 'internal', lifecycle: 'deprecated', owner: { service: 'CI0093422', team: 'TM-LEARNING-PLATFORM', email: null } }),
    ];
    const base = { ...DEFAULT_STATE, view: 'all' as const };

    it('matches the record name', () => {
      expect(filterSchemas(items, new Set(), { ...base, search: 'OrderPlaced' })).toHaveLength(1);
    });

    it('matches the owning team', () => {
      expect(filterSchemas(items, new Set(), { ...base, search: 'TM-LEARNING' })).toHaveLength(1);
    });

    it('matches the classification', () => {
      expect(filterSchemas(items, new Set(), { ...base, search: 'confidential' })).toHaveLength(1);
    });

    it('matches the lifecycle', () => {
      expect(filterSchemas(items, new Set(), { ...base, search: 'deprecated' })).toHaveLength(1);
    });

    it('is case insensitive', () => {
      expect(filterSchemas(items, new Set(), { ...base, search: 'orderplaced' })).toHaveLength(1);
    });
  });
});

describe('filterSchemas — facet filters', () => {
  const items = [
    makeItem({ artifact_id: 'a', domain: 'commerce', classification: 'internal', lifecycle: 'active' }),
    makeItem({ artifact_id: 'b', domain: 'learning', classification: 'public', lifecycle: 'deprecated' }),
  ];

  it('filters by domain', () => {
    const r = filterSchemas(items, new Set(), { ...DEFAULT_STATE, domain: 'commerce' });
    expect(r).toHaveLength(1);
    expect(r[0].artifact_id).toBe('a');
  });

  it('filters by classification', () => {
    const r = filterSchemas(items, new Set(), { ...DEFAULT_STATE, classification: 'public' });
    expect(r).toHaveLength(1);
    expect(r[0].artifact_id).toBe('b');
  });

  it('filters by lifecycle', () => {
    const r = filterSchemas(items, new Set(), { ...DEFAULT_STATE, lifecycle: 'deprecated' });
    expect(r).toHaveLength(1);
    expect(r[0].artifact_id).toBe('b');
  });

  it('stacks multiple facets', () => {
    const r = filterSchemas(items, new Set(), { ...DEFAULT_STATE, domain: 'commerce', lifecycle: 'active' });
    expect(r).toHaveLength(1);
    expect(r[0].artifact_id).toBe('a');
  });
});

describe('filterSchemas — sorting', () => {
  const items = [
    makeItem({ artifact_id: 'z-schema', domain: 'aaa' }),
    makeItem({ artifact_id: 'a-schema', domain: 'zzz' }),
  ];

  it('sorts artifact_id asc', () => {
    const r = filterSchemas(items, new Set(), { ...DEFAULT_STATE, sort: 'artifact_id', dir: 'asc' });
    expect(r[0].artifact_id).toBe('a-schema');
  });

  it('sorts artifact_id desc', () => {
    const r = filterSchemas(items, new Set(), { ...DEFAULT_STATE, sort: 'artifact_id', dir: 'desc' });
    expect(r[0].artifact_id).toBe('z-schema');
  });

  it('sorts domain', () => {
    const r = filterSchemas(items, new Set(), { ...DEFAULT_STATE, sort: 'domain', dir: 'asc' });
    expect(r[0].domain).toBe('aaa');
  });

  it('sorts versions as numbers, not as text', () => {
    // Plain `localeCompare` ordered these 1, 10, 11, 2, 3 — so an ASCENDING
    // sort put the newest version near the top, and the error grew with the
    // version count.
    const versioned = ['1', '2', '10', '11', '3'].map((latest_version, i) =>
      makeItem({ artifact_id: `s${i}`, latest_version }),
    );
    const asc = filterSchemas(versioned, new Set(), {
      ...DEFAULT_STATE,
      sort: 'latest_version',
      dir: 'asc',
    });
    expect(asc.map((i) => i.latest_version)).toEqual(['1', '2', '3', '10', '11']);

    const desc = filterSchemas(versioned, new Set(), {
      ...DEFAULT_STATE,
      sort: 'latest_version',
      dir: 'desc',
    });
    expect(desc.map((i) => i.latest_version)).toEqual(['11', '10', '3', '2', '1']);
  });

  it('sorts a subject with a number in it the way it reads', () => {
    // The same comparison, applied to the text columns. `v2` before `v10`.
    const numbered = ['api-v10', 'api-v2', 'api-v1'].map((artifact_id) =>
      makeItem({ artifact_id }),
    );
    const r = filterSchemas(numbered, new Set(), {
      ...DEFAULT_STATE,
      sort: 'artifact_id',
      dir: 'asc',
    });
    expect(r.map((i) => i.artifact_id)).toEqual(['api-v1', 'api-v2', 'api-v10']);
  });

  it('sorts compliance: non-compliant first when asc', () => {
    const nc = new Set([`${items[1].group_id}/${items[1].artifact_id}`]);
    const r = filterSchemas(items, nc, { ...DEFAULT_STATE, sort: 'compliance', dir: 'asc' });
    expect(r[0].artifact_id).toBe('a-schema');
  });
});

// `computeTabCounts` went with the tab bar it fed. The segmented partition
// shows no per-segment badge, and the one count on the screen — the toolbar's
// `n / m subjects` pill — is the filtered length against the unfiltered one,
// which needs no helper.
describe('countDomains', () => {
  it('counts distinct domains', () => {
    const items = [
      makeItem({ artifact_id: 'a', domain: 'commerce' }),
      makeItem({ artifact_id: 'b', domain: 'learning' }),
      makeItem({ artifact_id: 'c', domain: 'commerce' }),
    ];
    expect(countDomains(items)).toBe(2);
  });

  it('ignores a null domain rather than counting it as one', () => {
    const items = [
      makeItem({ artifact_id: 'a', domain: 'commerce' }),
      makeItem({ artifact_id: 'b', domain: null }),
      makeItem({ artifact_id: 'c', domain: null }),
    ];
    expect(countDomains(items)).toBe(1);
  });

  it('is 0 for an empty list', () => {
    expect(countDomains([])).toBe(0);
  });
});

describe('groupSchemas', () => {
  const items = [
    makeItem({ artifact_id: 'a', domain: 'commerce', owner: { service: 'CI001', team: null, email: null }, lifecycle: 'active' }),
    makeItem({ artifact_id: 'b', domain: 'learning', owner: { service: 'CI002', team: null, email: null }, lifecycle: 'deprecated' }),
    makeItem({ artifact_id: 'c', domain: 'commerce', owner: { service: 'CI001', team: null, email: null }, lifecycle: 'active' }),
    makeItem({ artifact_id: 'd', domain: null,     owner: { service: null, team: null, email: null }, lifecycle: null }),
  ];

  it('groups by domain', () => {
    const result = groupSchemas(items, 'domain');
    expect([...result.keys()]).toEqual(['(no domain)', 'commerce', 'learning']);
    expect(result.get('commerce')).toHaveLength(2);
  });

  it('groups by owner', () => {
    const result = groupSchemas(items, 'owner');
    expect([...result.keys()]).toEqual(['(unowned)', 'CI001', 'CI002']);
    expect(result.get('CI001')).toHaveLength(2);
  });

  it('groups by lifecycle', () => {
    const result = groupSchemas(items, 'lifecycle');
    expect([...result.keys()]).toEqual(['(no lifecycle)', 'active', 'deprecated']);
  });

  it('groups are sorted alphabetically', () => {
    const keys = [...groupSchemas(items, 'domain').keys()];
    expect(keys).toEqual([...keys].sort());
  });

  it('handles all-null values gracefully', () => {
    const nullItems = [
      makeItem({ artifact_id: 'x', domain: null }),
      makeItem({ artifact_id: 'y', domain: null }),
    ];
    const result = groupSchemas(nullItems, 'domain');
    expect(result.get('(no domain)')).toHaveLength(2);
  });
});

