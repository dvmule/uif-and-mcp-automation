import { create } from 'zustand'
import { getApiBaseUrl, getEnv } from '@/lib/envConfig'
import type { Env } from '@/lib/types'

interface EnvStore {
  env: Env
  baseUrl: string
}

/**
 * Read-only environment binding (no runtime switching).
 *
 * THE FIELDS ARE GETTERS ON PURPOSE. This store used to be built with the
 * values inlined -- `create(() => ({ env: BUILD_ENV, baseUrl: API_BASE_URL }))`
 * -- and zustand runs that initializer when the module is imported. Since
 * UIFPE-515 the config arrives from /config.json at runtime, so an inlined
 * initializer would capture whatever was available at import time, which is
 * nothing: the store would hold an empty base URL for the life of the page and
 * every request would go to the UI's own origin.
 *
 * Getters defer the read to the moment a component selects or `getState()`
 * runs, both of which happen after main.tsx has awaited the config.
 */
export const useEnvStore = create<EnvStore>()(() => ({
  get env(): Env {
    return getEnv()
  },
  get baseUrl(): string {
    return getApiBaseUrl()
  },
}))
