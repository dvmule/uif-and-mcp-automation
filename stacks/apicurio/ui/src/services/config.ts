/**
 * Runtime configuration, fetched at boot rather than compiled in.
 *
 * The API origin used to arrive as VITE_API_URL, inlined by Vite at build time
 * from `terraform output -raw bff_api_url`. That made the bundle
 * environment-bound: it could not be promoted from dev to UAT, because dev's
 * API Gateway URL was baked into it. Fetching it instead makes the bundle
 * byte-identical everywhere, which is what gives it one content hash and makes
 * it a promotable artifact.
 *
 * config.json is written per environment at deploy time and is deliberately
 * excluded from the bundle hash -- see scripts/bundle-hash.sh.
 */

export interface AppConfig {
  apiUrl: string;
}

let config: AppConfig | null = null;

export async function loadConfig(): Promise<AppConfig> {
  // no-store, not the default: a promoted release writes a new config.json to
  // the same key, and a cached copy would point the UI at the previous
  // environment's API for as long as the browser held it.
  const response = await fetch('/config.json', { cache: 'no-store' });

  if (!response.ok) {
    throw new Error(
      `Could not load /config.json (HTTP ${response.status}). The deploy writes this ` +
        `file from terraform output bff_api_url; if it is missing, the UI deploy did not complete.`,
    );
  }

  const parsed = (await response.json()) as Partial<AppConfig>;

  // Explicitly reject the empty string as well as absence. An empty origin is
  // the old silent failure: it built clean, issued same-origin requests, and
  // CloudFront answered them with index.html.
  if (!parsed.apiUrl) {
    throw new Error('/config.json is missing apiUrl, so there is no API origin to call.');
  }

  config = { apiUrl: parsed.apiUrl.replace(/\/+$/, '') };
  return config;
}

export function getConfig(): AppConfig {
  if (!config) {
    throw new Error('getConfig() was called before loadConfig() resolved.');
  }
  return config;
}

/** Seed or clear the module state. Tests only — never called by app code. */
export function setConfigForTesting(next: AppConfig | null): void {
  config = next;
}
