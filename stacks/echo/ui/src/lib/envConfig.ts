import type { Env } from '@/lib/types'

/**
 * Runtime configuration, fetched from /config.json at boot.
 *
 * WHY THIS IS NOT BUILD-TIME ANY MORE (UIFPE-515). This module used to read
 * `import.meta.env.VITE_API_URL` and `VITE_ECHO_ENV`, which `uif-echo-ui-deploy.sh`
 * exported before `npm run build`. That made the bundle environment-specific:
 * a bundle built for dev carried dev's API URL, so it could not be promoted to
 * uat, and its content hash differed per environment -- which breaks the
 * digest-equality claim the whole promotion model rests on
 * (capabilities/delivery/stack-promotion-model.md).
 *
 * The origin now arrives at runtime instead, so ONE bundle serves every
 * environment. config.json is written per environment at publish time from
 * that environment's own api_endpoint, and is excluded from both the artifact
 * hash and the archive for exactly this reason -- see
 * stacks/echo/scripts/artifact-hash.sh.
 *
 * `VITE_API_URL_DEV` / `_UAT` / `_PROD` are gone with it. They looked like one
 * bundle serving three environments, but `VITE_ECHO_ENV` was itself baked in,
 * so the mechanism was only ever half-built.
 *
 * LOADING IS EXPLICIT AND READS FAIL LOUDLY. `main.tsx` awaits
 * `loadRuntimeConfig()` before the first render, and `getRuntimeConfig()`
 * throws if anything reads config before that. The alternative -- returning an
 * empty base URL -- surfaces as a browser CORS error or a request to the UI's
 * own origin, a long way from the cause.
 */

const ENVS: Env[] = ['dev', 'uat', 'prod']

export interface RuntimeConfig {
  /** API Gateway base URL for this environment. */
  apiUrl: string
  /** Which Echo stack this deployment is bound to. */
  env: Env
}

/** Where the publish step writes this environment's own copy. */
export const CONFIG_URL = '/config.json'

let loaded: RuntimeConfig | null = null

function parseConfig(raw: unknown): RuntimeConfig {
  if (typeof raw !== 'object' || raw === null) {
    throw new Error(`${CONFIG_URL} is not a JSON object.`)
  }
  const obj = raw as Record<string, unknown>

  const apiUrl = typeof obj.apiUrl === 'string' ? obj.apiUrl.trim() : ''
  if (!apiUrl) {
    throw new Error(
      `${CONFIG_URL} has no "apiUrl". The publish step writes it from the ` +
        `environment's api_endpoint output; a bundle synced before the API ` +
        `Gateway existed has nothing to write.`
    )
  }

  // An unknown env is refused rather than defaulted to dev. Defaulting is how
  // a prod deployment ends up quietly labelled DEV and showing the non-prod
  // badge, which is worse than failing to boot.
  const envRaw = typeof obj.env === 'string' ? obj.env.trim().toLowerCase() : ''
  if (!ENVS.includes(envRaw as Env)) {
    throw new Error(
      `${CONFIG_URL} has env "${envRaw}", which is not one of ${ENVS.join(', ')}.`
    )
  }

  return { apiUrl, env: envRaw as Env }
}

/**
 * Fetch and validate /config.json. Call exactly once, before the first render.
 * Resolves with the config and makes it available to the getters below.
 */
export async function loadRuntimeConfig(
  fetchImpl: typeof fetch = fetch
): Promise<RuntimeConfig> {
  let res: Response
  try {
    // no-store: config.json is unhashed and its name never changes, so a
    // cached copy from a previous deployment would point this bundle at
    // another environment's API.
    res = await fetchImpl(CONFIG_URL, { cache: 'no-store' })
  } catch (cause) {
    throw new Error(`could not fetch ${CONFIG_URL}: ${String(cause)}`)
  }

  if (!res.ok) {
    throw new Error(
      `${CONFIG_URL} returned HTTP ${res.status}. The bundle was synced ` +
        `without it, or the CloudFront invalidation did not include it.`
    )
  }

  let raw: unknown
  try {
    raw = await res.json()
  } catch (cause) {
    throw new Error(`${CONFIG_URL} is not valid JSON: ${String(cause)}`)
  }

  loaded = parseConfig(raw)
  return loaded
}

/** The loaded config. Throws if called before loadRuntimeConfig() resolved. */
export function getRuntimeConfig(): RuntimeConfig {
  if (loaded === null) {
    throw new Error(
      'runtime config was read before it was loaded. main.tsx must await ' +
        'loadRuntimeConfig() before rendering.'
    )
  }
  return loaded
}

/** True once config is available, for code that must not throw. */
export function isRuntimeConfigLoaded(): boolean {
  return loaded !== null
}

/** API Gateway base URL for this deployment. */
export function getApiBaseUrl(): string {
  return getRuntimeConfig().apiUrl
}

/** Which Echo stack this deployment is bound to. */
export function getEnv(): Env {
  return getRuntimeConfig().env
}

/** Non-production marker shown next to the product title (DEV/UAT only). */
export function showSandbox(): boolean {
  const env = getEnv()
  return env === 'dev' || env === 'uat'
}

export function getEnvLabel(): string {
  return getEnv().toUpperCase()
}

/**
 * Test seam. Sets or clears the loaded config without a fetch, so a test can
 * exercise a specific environment or the not-yet-loaded state. Not used by
 * application code.
 */
export function __setRuntimeConfigForTests(config: RuntimeConfig | null): void {
  loaded = config
}
