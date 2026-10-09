// Client for the query preview service (UIFPE-219) — lambda_compile_preview.
// The Review step's trust anchor: the FULL deployed query as a diff, produced
// by the REAL compiler (compile_intake_delta.py, bundled into the service),
// never by a browser rendering. The UI's job is only to hand the service two
// things it already has — the submission envelope the Review step built, and
// the entity's CURRENT artifact texts, bundled here at build time from the
// same merge that deployed them — and to draw the difference.
//
// Same wiring convention as the AI-draft / submit / grain-probe services:
// the function URL is a constant, filled after the first `terraform apply`
// prints `compile_preview_url`. While it is empty the Review step simply
// shows no diff — the summary, artifact and schema views stand as before,
// and the compiler still runs at merge, so nothing is ungated.

// The deploy passes Terraform's `compile_preview_url` output to the build.
export const COMPILE_PREVIEW_URL: string = import.meta.env.VITE_COMPILE_PREVIEW_URL ?? "";

export const compilePreviewConfigured = () => COMPILE_PREVIEW_URL.length > 0;

// The entity's four registered artifacts + the domain's pipeline config,
// discovered by glob exactly like repo.ts discovers mappings — RAW texts,
// because the compiler performs surgical text edits and the diff base must
// be byte-identical to what is deployed.
const RAW_MAPPINGS = import.meta.glob(
  "../../../../../domains/*/src/schemas/mapping/*/v1/*.mapping.json",
  { query: "?raw", import: "default", eager: true }) as Record<string, string>;
const RAW_MASTERS = import.meta.glob(
  "../../../../../domains/*/src/schemas/gold/*/v1/master.json",
  { query: "?raw", import: "default", eager: true }) as Record<string, string>;
const RAW_MANIFESTS = import.meta.glob(
  "../../../../../domains/*/src/schemas/mapping/*/v1/manifest.yaml",
  { query: "?raw", import: "default", eager: true }) as Record<string, string>;
const RAW_SQL = import.meta.glob(
  "../../../../../domains/*/src/schemas/mapping/*/v1/sql/*.sql",
  { query: "?raw", import: "default", eager: true }) as Record<string, string>;
const RAW_PIPELINES = import.meta.glob(
  "../../../../../domains/*/src/apps/pipelines/base/*.yaml",
  { query: "?raw", import: "default", eager: true }) as Record<string, string>;

// …/<domain>/src/… — Vite normalizes the hosting domain's own path so its
// wildcard segment reads "..", the same quirk repo.ts and manifests.ts handle.
function domainOf(path: string): string | null {
  const m = /([^/]+)\/src\//.exec(path);
  return m ? (m[1] === ".." ? "product" : m[1]) : null;
}

function pick(raw: Record<string, string>, domain: string,
              tail: RegExp): string | null {
  for (const [path, text] of Object.entries(raw))
    // CRLF-normalized: a Windows checkout serves \r\n through the dev
    // server, while the service reads the texts back LF-only (Python
    // universal newlines) — without this every line "changes" by an
    // invisible \r and the diff renders a whole-file replace.
    if (domainOf(path) === domain && tail.test(path))
      return text.replace(/\r\n/g, "\n");
  return null;
}

export interface CurrentArtifacts {
  mapping: string;
  master: string;
  manifest: string;
  sql: string;
  pipeline?: string;
}

const esc = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

/** The entity's current artifact texts, or null when any of the four is not
 *  registered (a NEW table has no deployed query to diff against). */
export function currentArtifacts(domain: string | null | undefined,
                                 table: string | null | undefined):
    CurrentArtifacts | null {
  if (!domain || !table) return null;
  const entity = table.split(".").pop() ?? "";
  if (!entity) return null;
  const e = esc(entity);
  const mapping = pick(RAW_MAPPINGS, domain,
    new RegExp(`/mapping/${e}/v1/[^/]+\\.mapping\\.json$`));
  const master = pick(RAW_MASTERS, domain,
    new RegExp(`/gold/${e}/v1/master\\.json$`));
  const manifest = pick(RAW_MANIFESTS, domain,
    new RegExp(`/mapping/${e}/v1/manifest\\.yaml$`));
  const sql = pick(RAW_SQL, domain,
    new RegExp(`/mapping/${e}/v1/sql/${e}\\.sql$`));
  if (!mapping || !master || !manifest || !sql) return null;
  const pipeline = pick(RAW_PIPELINES, domain,
    // keyed by gold_id (= the entity, = the filename stem) — matching the
    // compiler, which reschedules base/{entity}.yaml; a domain-keyed pick
    // would diff another entity's pipeline (UIFPE-252)
    new RegExp(`/pipelines/base/${e}\\.yaml$`));
  return { mapping, master, manifest, sql,
           ...(pipeline ? { pipeline } : {}) };
}

export type PreviewFileKey =
  "mapping" | "master" | "manifest" | "sql" | "pipeline" | "overlay";

export interface PreviewFile { changed: boolean; after?: string }

export type PreviewResult =
  | { ok: true; log: string[];
      files: Partial<Record<PreviewFileKey, PreviewFile>> }
  | { ok: false; error: string;
      /** true = the compiler itself refused the change (a real answer the
       *  author needs); false = the request never reached it. */
      refusal: boolean };

export async function fetchCompilePreview(
    envelope: unknown,
    artifacts: Partial<CurrentArtifacts>): Promise<PreviewResult> {
  try {
    const res = await fetch(COMPILE_PREVIEW_URL, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ envelope, artifacts }),
    });
    const body = await res.json().catch(() => ({}));
    if (!res.ok)
      return { ok: false,
               error: body.error ?? `the service returned ${res.status}`,
               refusal: body.refusal === true };
    return { ok: true, log: body.log ?? [], files: body.files ?? {} };
  } catch (e) {
    return { ok: false, refusal: false,
             error: e instanceof Error ? e.message : String(e) };
  }
}
