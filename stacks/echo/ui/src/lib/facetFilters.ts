// URL-backed facet filters for list pages: each facet is a comma list under its own query key. Pure.

export type FacetValues = Record<string, string[]>

function list(raw: string | null): string[] {
  if (!raw) return []
  return Array.from(new Set(raw.split(',').map((s) => s.trim()).filter(Boolean)))
}

/** The selected values of each facet key; keys absent from the URL read as empty. */
export function parseFacets(params: URLSearchParams, keys: readonly string[]): FacetValues {
  return Object.fromEntries(keys.map((k) => [k, list(params.get(k))]))
}

/** A copy of `params` with `value` added to or removed from facet `key`; other keys are kept. */
export function toggleFacet(params: URLSearchParams, key: string, value: string): URLSearchParams {
  const next = new URLSearchParams(params.toString())
  const current = list(params.get(key))
  const updated = current.includes(value) ? current.filter((v) => v !== value) : [...current, value]
  if (updated.length === 0) next.delete(key)
  else next.set(key, updated.join(','))
  return next
}

/** A copy of `params` with facet `key` set to `values` (removed when empty); other keys are kept. */
export function setFacet(params: URLSearchParams, key: string, values: string[]): URLSearchParams {
  const next = new URLSearchParams(params.toString())
  if (values.length === 0) next.delete(key)
  else next.set(key, values.join(','))
  return next
}

/** A copy of `params` with every facet key removed. */
export function clearFacets(params: URLSearchParams, keys: readonly string[]): URLSearchParams {
  const next = new URLSearchParams(params.toString())
  for (const k of keys) next.delete(k)
  return next
}

/** True when a row's value passes the facet: nothing selected, or the value is selected. */
export function passesFacet(selected: string[] | undefined, value: string): boolean {
  return !selected || selected.length === 0 || selected.includes(value)
}
