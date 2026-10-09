import type { SchemaSummary } from '../types/schema.types';
import type { SchemaUrlState, GroupByField } from './use-schema-url-state';

export const RECENTLY_CHANGED_MS = 7 * 24 * 60 * 60 * 1000;

/** One definition of "changed this week", behind the Recently changed segment. */
export function isRecentlyChanged(item: Pick<SchemaSummary, 'modified_on'>): boolean {
  if (!item.modified_on) return false;
  return Date.now() - new Date(item.modified_on).getTime() < RECENTLY_CHANGED_MS;
}

export function filterSchemas(
  items: SchemaSummary[],
  nonCompliantIds: Set<string>,
  state: SchemaUrlState,
): SchemaSummary[] {
  let result = items.slice();

  // Segment filter. `overview` is gone from `SchemaView` — `useSchemaUrlState`
  // normalises the old URL value to `all` before it reaches here, so the
  // branch that answered it with `return false` (an empty table) is gone too.
  if (state.view !== 'all') {
    result = result.filter((item) => {
      const key = `${item.group_id}/${item.artifact_id}`;
      switch (state.view) {
        case 'non-compliant':
          return nonCompliantIds.has(key);
        case 'recently-changed':
          return isRecentlyChanged(item);
        default:
          return true;
      }
    });
  }

  // Free-text search. Substring, not fuzzy: predictable beats clever at this size.
  if (state.search) {
    const q = state.search.toLowerCase();
    const hit = (v: string | null | undefined) => (v ?? '').toLowerCase().includes(q);
    result = result.filter(
      (item) =>
        hit(item.artifact_id) ||
        hit(item.name) ||
        hit(item.domain) ||
        hit(item.classification) ||
        hit(item.lifecycle) ||
        hit(item.owner.service) ||
        hit(item.owner.team),
    );
  }

  // Chip facets
  if (state.domain) result = result.filter((item) => item.domain === state.domain);
  if (state.classification) result = result.filter((item) => item.classification === state.classification);
  if (state.lifecycle) result = result.filter((item) => item.lifecycle === state.lifecycle);

  // Sort
  result.sort((a, b) => {
    const multiplier = state.dir === 'asc' ? 1 : -1;

    if (state.sort === 'compliance') {
      const aNC = nonCompliantIds.has(`${a.group_id}/${a.artifact_id}`) ? 0 : 1;
      const bNC = nonCompliantIds.has(`${b.group_id}/${b.artifact_id}`) ? 0 : 1;
      return (aNC - bNC) * multiplier;
    }

    const aVal = getSortValue(a, state.sort) ?? '';
    const bVal = getSortValue(b, state.sort) ?? '';

    // `numeric`, because one of these columns is a version. Plain
    // `localeCompare` ordered ['1','2','10','11','3'] as 1, 10, 11, 2, 3, so
    // an ascending sort put the NEWEST version near the top and it got worse
    // as version numbers grew. It is the right comparison for the text
    // columns too: a subject with a digit in it sorts the way a reader reads
    // it, and for text with no digits it changes nothing.
    return aVal.localeCompare(bVal, undefined, { numeric: true }) * multiplier;
  });

  return result;
}

function getSortValue(item: SchemaSummary, field: Exclude<SchemaUrlState['sort'], 'compliance'>): string | null {
  switch (field) {
    case 'artifact_id': return item.artifact_id;
    case 'domain': return item.domain;
    case 'classification': return item.classification;
    case 'lifecycle': return item.lifecycle;
    case 'latest_version': return item.latest_version;
    case 'modified_on': return item.modified_on;
    default: return null;
  }
}

/**
 * Distinct domains across the *unfiltered* set, for the table footer's
 * "n of m domains shown". Taken from `items` rather than from
 * `DOMAIN_COLOURS`, so it counts the domains the registry actually holds and
 * not the ones the palette has a colour for.
 */
export function countDomains(items: SchemaSummary[]): number {
  return new Set(items.map((i) => i.domain).filter((d): d is string => d !== null)).size;
}

export function groupSchemas(
  items: SchemaSummary[],
  groupBy: GroupByField,
): Map<string, SchemaSummary[]> {
  const groups = new Map<string, SchemaSummary[]>();
  for (const item of items) {
    const key = getGroupKey(item, groupBy);
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key)!.push(item);
  }
  return new Map([...groups.entries()].sort(([a], [b]) => a.localeCompare(b)));
}

function getGroupKey(item: SchemaSummary, groupBy: GroupByField): string {
  switch (groupBy) {
    case 'domain': return item.domain ?? '(no domain)';
    case 'owner': return item.owner.service ?? '(unowned)';
    case 'lifecycle': return item.lifecycle ?? '(no lifecycle)';
  }
}
