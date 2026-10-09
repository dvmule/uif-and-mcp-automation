import { useCallback } from 'react';
import { useSearchParams } from 'react-router-dom';

/**
 * The allowed values are the lists, and each union is derived from its list, so
 * a value can never be in the type without also being validatable at the URL
 * boundary. Without that check a stale deep link — `?view=pending-review`
 * survived the tab's deletion — reaches `filterSchemas`, whose `default: return
 * true` then renders every row with no tab highlighted.
 */

/**
 * The URL vocabulary, which is deliberately wider than the segments the
 * toolbar offers. `overview` was the Home tab; the tab bar is gone and
 * `all` is the surface you land on, but links minted while Home existed are
 * in people's bookmarks and in the `/non-compliant` redirect's blast radius,
 * so the value still has to parse. It resolves to `all`.
 *
 * It cannot simply be left in `SchemaView`: `filterSchemas` used to answer
 * `overview` with `return false`, so an old bookmark would now land on the
 * redesigned list and render an empty table — which asserts "no schemas
 * match", a different and false statement. Normalising here means the rest of
 * the app never sees the value at all.
 */
const URL_VIEWS = ['overview', 'all', 'non-compliant', 'recently-changed'] as const;

/** The three the segmented partition actually offers. */
const VIEWS = ['all', 'non-compliant', 'recently-changed'] as const;
const SORT_DIRS = ['asc', 'desc'] as const;
const SORT_FIELDS = [
  'artifact_id',
  'domain',
  'classification',
  'lifecycle',
  'latest_version',
  'modified_on',
  'compliance',
] as const;
/**
 * The detail page's tabs. `overview` is gone as a tab: its `<dl>` is now the
 * always-visible facts band at the top of the page, so there was nothing left
 * behind the tab, and `History` took its place carrying the version list and
 * the diff. It stays in the URL vocabulary below for the same reason
 * `view=overview` does — `?dtab=overview` links exist.
 */
const URL_DETAIL_TABS = ['overview', 'schema', 'mapping', 'compliance', 'services', 'history'] as const;
const DETAIL_TABS = ['schema', 'mapping', 'compliance', 'services', 'history'] as const;

/** Fields / Code / Graph — three readings of the same body. */
const SCHEMA_VIEWS = ['fields', 'code', 'graph'] as const;

/**
 * The mapping view's three sheets, in the order the
 * *-gold-attribute-mapping-template.xlsx workbook lists them. A mapping is a
 * different body from a schema, so these are not a fourth `SCHEMA_VIEWS`
 * entry — the two lists never apply to the same artifact.
 */
const MAPPING_KINDS = ['scalar', 'conditional', 'nested'] as const;

const GROUP_BY_FIELDS = ['domain', 'owner', 'lifecycle'] as const;

export type SchemaView = (typeof VIEWS)[number];

export type SortDir = (typeof SORT_DIRS)[number];

export type SortField = (typeof SORT_FIELDS)[number];

/** Renamed from `DrawerTab` with the drawer it belonged to. */
export type DetailTab = (typeof DETAIL_TABS)[number];

export type SchemaViewMode = (typeof SCHEMA_VIEWS)[number];

export type MappingKindView = (typeof MAPPING_KINDS)[number];

export type GroupByField = (typeof GROUP_BY_FIELDS)[number];

/** A URL param is whatever someone typed. Take it only if it is in the union. */
function parseEnum<T extends string, F>(
  value: string | null,
  allowed: readonly T[],
  fallback: F,
): T | F {
  return allowed.includes(value as T) ? (value as T) : fallback;
}

/** Accepts the wider URL vocabulary, returns only a live segment. */
function parseView(value: string | null): SchemaView {
  const parsed = parseEnum(value, URL_VIEWS, DEFAULTS.view);
  return parsed === 'overview' ? 'all' : parsed;
}

/** Same treatment for the retired `Overview` tab: it resolves to `Schema`. */
function parseDetailTab(value: string | null): DetailTab {
  const parsed = parseEnum(value, URL_DETAIL_TABS, DEFAULTS.detailTab);
  return parsed === 'overview' ? 'schema' : parsed;
}

/** Menu labels for the sort control. The field names are the URL vocabulary
  * and the API's, which is why they are not shown to anyone directly. */
export const SORT_FIELD_LABELS: Record<SortField, string> = {
  artifact_id: 'Subject',
  domain: 'Domain',
  classification: 'Classification',
  lifecycle: 'Lifecycle',
  latest_version: 'Version',
  modified_on: 'Last modified',
  compliance: 'Compliance',
};

/** Segment labels, in the order the partition shows them. */
export const VIEW_LABELS: Record<SchemaView, string> = {
  all: 'All',
  'non-compliant': 'Non-compliant',
  'recently-changed': 'Recently changed',
};

export const SEGMENTS = VIEWS;

export const DETAIL_TAB_LABELS: Record<DetailTab, string> = {
  schema: 'Schema',
  mapping: 'Mapping',
  compliance: 'Compliance',
  services: 'Services',
  history: 'History',
};

export const DETAIL_TAB_ORDER = DETAIL_TABS;

export const SCHEMA_VIEW_LABELS: Record<SchemaViewMode, string> = {
  fields: 'Fields',
  code: 'Code',
  graph: 'Graph',
};

export const SCHEMA_VIEW_ORDER = SCHEMA_VIEWS;

export const MAPPING_KIND_LABELS: Record<MappingKindView, string> = {
  scalar: 'Scalar',
  conditional: 'Conditional',
  nested: 'Nested',
};

export const MAPPING_KIND_ORDER = MAPPING_KINDS;

export const GROUP_BY_LABELS: Record<GroupByField, string> = {
  domain: 'Domain',
  owner: 'Owner',
  lifecycle: 'Lifecycle',
};

/**
 * The group chip cycles rather than opening a menu, so it needs a defined
 * order with `null` (ungrouped) in it: domain → owner → lifecycle → off.
 */
export function nextGroupBy(current: GroupByField | null): GroupByField | null {
  if (current === null) return 'domain';
  const i = GROUP_BY_FIELDS.indexOf(current);
  return GROUP_BY_FIELDS[i + 1] ?? null;
}

export interface SchemaUrlState {
  view: SchemaView;
  subject: string | null;
  group: string | null;
  search: string;
  domain: string | null;
  classification: string | null;
  lifecycle: string | null;
  sort: SortField;
  dir: SortDir;
  detailTab: DetailTab;
  /** Which reading of the schema body the detail page is showing. */
  schemaView: SchemaViewMode;
  /** Which of the mapping's three sheets the Mapping tab is showing. */
  mappingKind: MappingKindView;
  /**
   * The version the detail page is showing, or null for whatever the registry
   * calls current.
   *
   * This is in the URL rather than in component state so that a link to a
   * schema reproduces the schema you were looking at. It used to be local, so
   * choosing v1 and sending someone the link sent them to the latest version —
   * the link named a subject, not a version, while appearing to name both.
   */
  version: string | null;
  groupBy: GroupByField | null;
  showRawSubjects: boolean;
}

type StateUpdate = Partial<SchemaUrlState>;

const DEFAULTS: SchemaUrlState = {
  // `all` is the surface you land on now that the tab bar is a segmented
  // partition inside the toolbar. There is no Home view to default to.
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
  // `code` is what the drawer's Schema tab showed, so a link shared before
  // Fields and Graph existed opens on the same thing it always did.
  schemaView: 'code',
  // Scalar is the workbook's first sheet, so the view opens where the
  // reader's own document opens.
  mappingKind: 'scalar',
  // Null, not a version number: the newest version is a moving target, and
  // pinning it into every URL would make links go stale the moment someone
  // publishes. An explicit `?version=` is a deliberate pin.
  version: null,
  groupBy: null,
  showRawSubjects: false,
};

const KEY_MAP: Record<keyof SchemaUrlState, string> = {
  view: 'view',
  subject: 'subject',
  group: 'group',
  search: 'search',
  domain: 'domain',
  classification: 'classification',
  lifecycle: 'lifecycle',
  sort: 'sort',
  dir: 'dir',
  detailTab: 'dtab',
  schemaView: 'schemaView',
  mappingKind: 'mkind',
  version: 'version',
  groupBy: 'groupBy',
  showRawSubjects: 'raw',
};

export function useSchemaUrlState() {
  const [params, setParams] = useSearchParams();

  const state: SchemaUrlState = {
    view: parseView(params.get('view')),
    subject: params.get('subject'),
    group: params.get('group'),
    search: params.get('search') ?? DEFAULTS.search,
    domain: params.get('domain'),
    classification: params.get('classification'),
    lifecycle: params.get('lifecycle'),
    sort: parseEnum(params.get('sort'), SORT_FIELDS, DEFAULTS.sort),
    dir: parseEnum(params.get('dir'), SORT_DIRS, DEFAULTS.dir),
    detailTab: parseDetailTab(params.get('dtab')),
    schemaView: parseEnum(params.get('schemaView'), SCHEMA_VIEWS, DEFAULTS.schemaView),
    mappingKind: parseEnum(params.get('mkind'), MAPPING_KINDS, DEFAULTS.mappingKind),
    // Not validated against a union: the valid versions are whatever the
    // registry holds for this subject, which this hook cannot know.
    // `resolveCurrentVersion` handles a value that matches nothing.
    version: params.get('version'),
    groupBy: parseEnum(params.get('groupBy'), GROUP_BY_FIELDS, DEFAULTS.groupBy),
    showRawSubjects: params.get('raw') === '1',
  };

  const set = useCallback(
    (updates: StateUpdate) => {
      setParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          for (const [stateKey, value] of Object.entries(updates) as [keyof SchemaUrlState, unknown][]) {
            const param = KEY_MAP[stateKey];
            const defaultVal = DEFAULTS[stateKey];
            if (value === null || value === undefined || value === defaultVal) {
              next.delete(param);
            } else if (typeof value === 'boolean') {
              next.set(param, value ? '1' : '0');
            } else {
              next.set(param, String(value));
            }
          }
          return next;
        },
        { replace: true },
      );
    },
    [setParams],
  );

  return { state, set };
}
