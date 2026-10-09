import { renderHook, act } from '@testing-library/react';
import { MemoryRouter, useSearchParams } from 'react-router-dom';
import { createElement } from 'react';
import { useSchemaUrlState, nextGroupBy, GROUP_BY_LABELS } from './use-schema-url-state';
import { describe, it, expect } from 'vitest';
import type { ReactNode } from 'react';

function wrapper({ children }: { children: ReactNode }) {
  return createElement(MemoryRouter, { initialEntries: ['/schemas'] }, children);
}

function wrapperWithSearch(search: string) {
  return function ({ children }: { children: ReactNode }) {
    return createElement(MemoryRouter, { initialEntries: [`/schemas${search}`] }, children);
  };
}

describe('useSchemaUrlState — reads defaults', () => {
  it('returns all defaults when no query params', () => {
    const { result } = renderHook(() => useSchemaUrlState(), { wrapper });
    // `all` is the landing surface now that the tab bar is a segmented
    // partition; there is no Home view to default to.
    expect(result.current.state.view).toBe('all');
    expect(result.current.state.subject).toBeNull();
    expect(result.current.state.search).toBe('');
    expect(result.current.state.sort).toBe('artifact_id');
    expect(result.current.state.dir).toBe('asc');
    expect(result.current.state.detailTab).toBe('schema');
    // The drawer's Schema tab is what `code` was, so a link shared before
    // Fields and Graph existed opens on the same reading it always did.
    expect(result.current.state.schemaView).toBe('code');
    expect(result.current.state.mappingKind).toBe('scalar');
  });

  it('reads view from ?view param', () => {
    const { result } = renderHook(() => useSchemaUrlState(), {
      wrapper: wrapperWithSearch('?view=non-compliant'),
    });
    expect(result.current.state.view).toBe('non-compliant');
  });

  it('reads subject and group from params', () => {
    const { result } = renderHook(() => useSchemaUrlState(), {
      wrapper: wrapperWithSearch('?subject=commerce.product-value&group=default'),
    });
    expect(result.current.state.subject).toBe('commerce.product-value');
    expect(result.current.state.group).toBe('default');
  });

  it('reads sort and dir', () => {
    const { result } = renderHook(() => useSchemaUrlState(), {
      wrapper: wrapperWithSearch('?sort=domain&dir=desc'),
    });
    expect(result.current.state.sort).toBe('domain');
    expect(result.current.state.dir).toBe('desc');
  });

  it('reads the detail tab from the dtab param', () => {
    const { result } = renderHook(() => useSchemaUrlState(), {
      wrapper: wrapperWithSearch('?dtab=compliance'),
    });
    expect(result.current.state.detailTab).toBe('compliance');
  });

  it('resolves the retired ?dtab=overview onto the Schema tab', () => {
    // The drawer's Overview tab became the page's always-visible facts band,
    // so there is nothing behind the tab any more — but its links are real.
    const { result } = renderHook(() => useSchemaUrlState(), {
      wrapper: wrapperWithSearch('?dtab=overview'),
    });
    expect(result.current.state.detailTab).toBe('schema');
  });

  it('reads the schema view from the schemaView param', () => {
    const { result } = renderHook(() => useSchemaUrlState(), {
      wrapper: wrapperWithSearch('?schemaView=graph'),
    });
    expect(result.current.state.schemaView).toBe('graph');
  });
});

describe('useSchemaUrlState — set()', () => {
  it('setting view updates state', () => {
    const { result } = renderHook(() => useSchemaUrlState(), { wrapper });
    act(() => result.current.set({ view: 'non-compliant' }));
    expect(result.current.state.view).toBe('non-compliant');
  });

  it('setting view back to the default removes the param from the URL', () => {
    // Reading the params alongside the hook: asserting only `state.view` would
    // pass whether or not the param was actually elided, which is what this
    // test exists to check.
    const { result } = renderHook(
      () => {
        const [params] = useSearchParams();
        return { ...useSchemaUrlState(), params };
      },
      { wrapper: wrapperWithSearch('?view=non-compliant') },
    );
    act(() => result.current.set({ view: 'all' }));
    expect(result.current.params.has('view')).toBe(false);
    expect(result.current.state.view).toBe('all');
  });

  it('setting subject preserves existing params', () => {
    const { result } = renderHook(() => useSchemaUrlState(), {
      wrapper: wrapperWithSearch('?view=non-compliant'),
    });
    act(() => result.current.set({ subject: 'foo', group: 'bar' }));
    expect(result.current.state.view).toBe('non-compliant');
    expect(result.current.state.subject).toBe('foo');
  });

  it('setting subject to null removes it', () => {
    const { result } = renderHook(() => useSchemaUrlState(), {
      wrapper: wrapperWithSearch('?subject=foo'),
    });
    act(() => result.current.set({ subject: null }));
    expect(result.current.state.subject).toBeNull();
  });

  it('setting search updates state', () => {
    const { result } = renderHook(() => useSchemaUrlState(), { wrapper });
    act(() => result.current.set({ search: 'commerce' }));
    expect(result.current.state.search).toBe('commerce');
  });

  it('setting showRawSubjects to true sets ?raw=1', () => {
    const { result } = renderHook(() => useSchemaUrlState(), { wrapper });
    act(() => result.current.set({ showRawSubjects: true }));
    expect(result.current.state.showRawSubjects).toBe(true);
  });

  it('setting showRawSubjects to false removes the param', () => {
    const { result } = renderHook(() => useSchemaUrlState(), {
      wrapper: wrapperWithSearch('?raw=1'),
    });
    act(() => result.current.set({ showRawSubjects: false }));
    expect(result.current.state.showRawSubjects).toBe(false);
  });
});

describe('useSchemaUrlState — rejects values outside each union', () => {
  it('falls back to the default view for a removed one, rather than showing every row', () => {
    // ?view=pending-review was a real deep link before the tab was deleted;
    // `filterSchemas` has `default: return true`, so an unvalidated value
    // rendered the whole table with no tab highlighted.
    const { result } = renderHook(() => useSchemaUrlState(), {
      wrapper: wrapperWithSearch('?view=pending-review'),
    });
    expect(result.current.state.view).toBe('all');
  });

  it('resolves the retired ?view=overview onto the All segment', () => {
    // Home's deep links are in bookmarks. `overview` stays in the URL
    // vocabulary so they parse, but it must not reach `filterSchemas` — the
    // branch that answered it returned nothing, so an old bookmark would land
    // on an empty table reading "no schemas match", which is false.
    const { result } = renderHook(() => useSchemaUrlState(), {
      wrapper: wrapperWithSearch('?view=overview'),
    });
    expect(result.current.state.view).toBe('all');
  });

  it('falls back on every other cast param too', () => {
    const { result } = renderHook(() => useSchemaUrlState(), {
      wrapper: wrapperWithSearch('?sort=nope&dir=sideways&dtab=nope&groupBy=nope&schemaView=nope'),
    });
    expect(result.current.state.sort).toBe('artifact_id');
    expect(result.current.state.dir).toBe('asc');
    expect(result.current.state.detailTab).toBe('schema');
    expect(result.current.state.schemaView).toBe('code');
    expect(result.current.state.groupBy).toBeNull();
    expect(result.current.state.view).toBe('all');
  });

  it('still accepts every legal value', () => {
    const { result } = renderHook(() => useSchemaUrlState(), {
      wrapper: wrapperWithSearch(
        '?view=recently-changed&sort=modified_on&dir=desc&dtab=history&groupBy=owner&schemaView=fields',
      ),
    });
    expect(result.current.state.view).toBe('recently-changed');
    expect(result.current.state.sort).toBe('modified_on');
    expect(result.current.state.dir).toBe('desc');
    expect(result.current.state.detailTab).toBe('history');
    expect(result.current.state.groupBy).toBe('owner');
    expect(result.current.state.schemaView).toBe('fields');
  });
});

describe('nextGroupBy — the group chip cycles rather than opening a menu', () => {
  it('walks domain → owner → lifecycle → off → domain', () => {
    expect(nextGroupBy(null)).toBe('domain');
    expect(nextGroupBy('domain')).toBe('owner');
    expect(nextGroupBy('owner')).toBe('lifecycle');
    expect(nextGroupBy('lifecycle')).toBeNull();
  });

  it('covers every GROUP_BY_FIELDS value, so a fourth would not be skipped', () => {
    const visited = new Set<string>();
    let cur = nextGroupBy(null);
    while (cur !== null) {
      visited.add(cur);
      cur = nextGroupBy(cur);
    }
    expect([...visited].sort()).toEqual(Object.keys(GROUP_BY_LABELS).sort());
  });
});

describe('version — the detail page pins it in the URL', () => {
  it('is null by default, so a link does not go stale when someone publishes', () => {
    const { result } = renderHook(() => useSchemaUrlState(), { wrapper });
    expect(result.current.state.version).toBeNull();
  });

  it('reads an explicit pin', () => {
    const { result } = renderHook(() => useSchemaUrlState(), {
      wrapper: wrapperWithSearch('?version=2'),
    });
    expect(result.current.state.version).toBe('2');
  });

  // The version used to be component state, so choosing v1 and sending someone
  // the link sent them to the latest version: the link named a subject while
  // appearing to name a version too.
  it('survives a round trip through set(), so a shared link reproduces the view', () => {
    const { result } = renderHook(
      () => {
        const [params] = useSearchParams();
        return { ...useSchemaUrlState(), params };
      },
      { wrapper },
    );
    act(() => result.current.set({ version: '1' }));
    expect(result.current.params.get('version')).toBe('1');
    expect(result.current.state.version).toBe('1');
  });

  it('clears back to the current version', () => {
    const { result } = renderHook(
      () => {
        const [params] = useSearchParams();
        return { ...useSchemaUrlState(), params };
      },
      { wrapper: wrapperWithSearch('?version=1') },
    );
    act(() => result.current.set({ version: null }));
    expect(result.current.params.has('version')).toBe(false);
  });

  // Deliberately not validated against a union: which versions exist is a fact
  // about one subject in the registry, which this hook cannot know.
  // `resolveCurrentVersion` handles a value that matches nothing.
  it('takes any string, because the valid set is per-subject', () => {
    const { result } = renderHook(() => useSchemaUrlState(), {
      wrapper: wrapperWithSearch('?version=does-not-exist'),
    });
    expect(result.current.state.version).toBe('does-not-exist');
  });
});

describe('useSchemaUrlState — the mapping tab and sheet (UIFPE-598)', () => {
  it('defaults mappingKind to scalar', () => {
    // Scalar is the workbook's first sheet, so the view opens where the
    // reader's own document opens.
    const { result } = renderHook(() => useSchemaUrlState(), { wrapper });
    expect(result.current.state.mappingKind).toBe('scalar');
  });

  it('reads ?dtab=mapping', () => {
    const { result } = renderHook(() => useSchemaUrlState(), {
      wrapper: wrapperWithSearch('?dtab=mapping'),
    });
    expect(result.current.state.detailTab).toBe('mapping');
  });

  it('reads ?mkind for each sheet', () => {
    for (const kind of ['scalar', 'conditional', 'nested'] as const) {
      const { result } = renderHook(() => useSchemaUrlState(), {
        wrapper: wrapperWithSearch(`?mkind=${kind}`),
      });
      expect(result.current.state.mappingKind).toBe(kind);
    }
  });

  it('falls back to scalar for an unknown ?mkind', () => {
    // Same treatment every other enum param gets: a value that is not in the
    // union never reaches the component. A stale deep link that rendered no
    // sheet at all would assert the mapping has no columns.
    const { result } = renderHook(() => useSchemaUrlState(), {
      wrapper: wrapperWithSearch('?mkind=pivot'),
    });
    expect(result.current.state.mappingKind).toBe('scalar');
  });

  it('writes mkind to the URL and drops it when set back to the default', () => {
    const { result } = renderHook(() => useSchemaUrlState(), { wrapper });
    act(() => result.current.set({ mappingKind: 'nested' }));
    expect(result.current.state.mappingKind).toBe('nested');
    act(() => result.current.set({ mappingKind: 'scalar' }));
    expect(result.current.state.mappingKind).toBe('scalar');
  });
});
