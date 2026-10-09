/**
 * Strips the Apicurio `-value` or `-key` suffix from a subject name.
 * When showRaw is true the original string is returned unchanged.
 *
 * Examples:
 *   commerce.product.created-value → commerce.product.created
 *   identity.user-key              → identity.user
 *   analytics.pageview-value       → analytics.pageview
 */
export function formatSubject(artifactId: string, showRaw: boolean): string {
  if (showRaw) return artifactId;
  return artifactId.replace(/-(value|key)$/, '');
}

export function splitSubject(artifactId: string): { base: string; suffix: 'value' | 'key' | null } {
  const m = artifactId.match(/^(.*)-((value|key))$/);
  return m ? { base: m[1], suffix: m[2] as 'value' | 'key' } : { base: artifactId, suffix: null };
}

/**
 * Stable domain → colour mapping, used for the table's 3px row stripe and
 * anywhere a domain needs a swatch.
 *
 * These are drawn from the Pearson data-visualisation scale (the amethyst,
 * turquoise, amber and fuchsia tint/shade ramps), not from Tailwind's default
 * hues, which is what they were before the March 2025 palette landed. The
 * previous orange/blue/green set had no relationship to the brand and put
 * `customer` on a #f97316 orange that appears nowhere in the palette.
 *
 * Assigning a domain a colour is a commitment: the same domain must be the
 * same colour on every screen, so add to this map rather than choosing at the
 * call site. `assessment` and `identity` are present but not yet in the
 * registry's data — they are in the scale so the mapping does not shift when
 * they arrive.
 */
export const DOMAIN_COLOURS: Record<string, string> = {
  product: '#512EAB',
  learning: '#168584',
  assessment: '#BF9A00',
  commerce: '#7950E2',
  content: '#22C7C6',
  customer: '#B820A9',
  finance: '#3D1788',
  identity: '#B79BFE',
};

/** Swatch colour for a domain with no entry in the scale. Mist purple reads as
  * "no colour assigned" rather than borrowing a hue that means another domain. */
export const DOMAIN_COLOUR_FALLBACK = '#C1BFFF';

/**
 * Registry schema type → Monaco language id. Shared by `SchemaViewer` and
 * `VersionTimeline`'s diff so the same body is highlighted the same way in both.
 * OPENAPI and ASYNCAPI are carried in either JSON or YAML, so those two are
 * decided by the body rather than the type.
 */
export function getMonacoLanguage(schemaType: string, content: string): string {
  switch (schemaType.toUpperCase()) {
    case 'AVRO':
    case 'JSON':
    case 'KCONNECT':
      return 'json';
    case 'OPENAPI':
    case 'ASYNCAPI':
      return content.trimStart().startsWith('{') ? 'json' : 'yaml';
    case 'GRAPHQL':
      return 'graphql';
    case 'XSD':
    case 'WSDL':
      return 'xml';
    case 'PROTOBUF':
      return 'plaintext';
    default:
      return 'json';
  }
}

/**
 * Filename and media type for downloading a schema body.
 *
 * The switch mirrors `getMonacoLanguage` above, deliberately: the same body
 * that Monaco highlights as XML has to download as `.xsd`, not `.json`. The
 * graph toolbar's download used to hardcode `application/json` and a `.json`
 * extension for every type, so a Protobuf body arrived as `orders.json` and a
 * WSDL as `contract.json` — files nothing downstream would open.
 *
 * The version is in the name because you download one version, not a subject.
 * A directory of `commerce.order.placed-value.avsc` files that each came from a
 * different version is unusable, and nothing in the file itself says which.
 */
export function schemaFile(
  schemaType: string,
  content: string,
  artifactId: string,
  version?: string | null,
): { name: string; mimeType: string } {
  const { ext, mimeType } = ((): { ext: string; mimeType: string } => {
    switch (schemaType.toUpperCase()) {
      case 'AVRO':
        // Avro's own convention for a schema file, and what the tooling expects.
        return { ext: 'avsc', mimeType: 'application/json' };
      case 'JSON':
      case 'KCONNECT':
        return { ext: 'json', mimeType: 'application/json' };
      case 'OPENAPI':
      case 'ASYNCAPI':
        // Carried in either JSON or YAML, so the body decides — the same rule
        // `getMonacoLanguage` uses to pick a highlighter.
        return content.trimStart().startsWith('{')
          ? { ext: 'json', mimeType: 'application/json' }
          : { ext: 'yaml', mimeType: 'application/yaml' };
      case 'GRAPHQL':
        return { ext: 'graphql', mimeType: 'text/plain' };
      case 'XSD':
        return { ext: 'xsd', mimeType: 'application/xml' };
      case 'WSDL':
        return { ext: 'wsdl', mimeType: 'application/xml' };
      case 'PROTOBUF':
        return { ext: 'proto', mimeType: 'text/plain' };
      default:
        return { ext: 'json', mimeType: 'application/json' };
    }
  })();

  return { name: `${subjectFileStem(artifactId, version)}.${ext}`, mimeType };
}

/**
 * A subject as a filename, with the slash guard and version suffix that
 * `schemaFile` applies. Shared so a `.png` of the graph and a `.avsc` of the
 * body cannot disagree about how a subject is spelled on disk.
 */
function subjectFileStem(artifactId: string, version?: string | null): string {
  // A subject is `[a-z0-9.-]` by convention, but it is not validated on the way
  // in — an unconventional one is warned about and allowed. A slash in it would
  // otherwise become a directory separator in the download.
  const safeSubject = artifactId.replace(/[/\\]/g, '_');
  return `${safeSubject}${version ? `.v${version}` : ''}`;
}

/** Filename for a rendered picture of the graph view, not for a schema body. */
export function graphImageName(artifactId: string, version?: string | null): string {
  // `.graph` before the extension so a folder of downloads does not put a
  // picture of a schema next to the schema itself under near-identical names.
  return `${subjectFileStem(artifactId, version)}.graph.png`;
}

/**
 * Hands the browser a file to save. Shared by every download in the app.
 *
 * The anchor is put IN the document before it is clicked. A detached anchor
 * works in Chromium and does nothing at all in Firefox, which is how the
 * e2e suite missed it: it runs Chromium only, so "Download schema" and
 * "Export CSV" were silently no-ops for some users with no error anywhere.
 */
export function triggerDownload(href: string, name: string): void {
  const a = document.createElement('a');
  a.href = href;
  a.download = name;
  a.style.display = 'none';
  document.body.appendChild(a);
  a.click();
  a.remove();
}

/**
 * Saves a blob under `name`, then releases it.
 *
 * The release is deferred, which is the whole point of this existing. Both
 * callers used to revoke on the line after the click, in the same task the
 * download was started from — and a browser that has not yet begun reading
 * the blob then reads a URL that no longer resolves. Chromium tolerates it;
 * Firefox and Safari have not reliably.
 */
export function downloadBlob(blob: Blob, name: string): void {
  const url = URL.createObjectURL(blob);
  triggerDownload(url, name);
  // A task, not a microtask: the fetch of a blob: URL for a download is not
  // guaranteed to have started when the current one ends.
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}

/** Saves a schema body to a file named by `schemaFile`. */
export function downloadSchema(
  schemaType: string,
  content: string,
  artifactId: string,
  version?: string | null,
): void {
  const { name, mimeType } = schemaFile(schemaType, content, artifactId, version);
  downloadBlob(new Blob([content], { type: mimeType }), name);
}

/** A version as both the timeline and the detail response carry it. */
interface VersionRef {
  version: string;
  created_on?: string | null;
}

function versionTime(v: VersionRef): number | null {
  const parsed = Date.parse(v.created_on ?? '');
  return Number.isNaN(parsed) ? null : parsed;
}

function versionOrdinal(v: VersionRef): number | null {
  const parsed = Number(v.version);
  return Number.isFinite(parsed) ? parsed : null;
}

/**
 * Orders two versions OLDEST FIRST. One rule, so nothing in the app can
 * disagree about which of two versions came later.
 *
 * Timestamps first, when both carry one — that is the most direct evidence
 * there is. Otherwise the version number, when both are numeric: Apicurio
 * assigns those sequentially, so v10 follows v2, and a missing `created_on`
 * is a gap in the registry's response rather than a fact about age.
 *
 * That ordering of the two rules is a correction. `resolveCurrentVersion`
 * first shipped preferring a DATED version over an undated one on the
 * reasoning that a timestamp beats a name. It does not beat a sequence: given
 * a dated v2 and an undated v9, v9 is the later version and the absent
 * timestamp says nothing about age.
 *
 * Against a version that is not a number at all — `draft`, `1.0.0-rc1` — a
 * numeric one is taken as the later: it holds a real position in Apicurio's
 * sequence where the other holds a label. With no comparable evidence on
 * either side this returns 0 and leaves the order alone rather than
 * inventing one.
 */
export function compareVersionAge(a: VersionRef, b: VersionRef): number {
  const at = versionTime(a);
  const bt = versionTime(b);
  if (at !== null && bt !== null) return at - bt;

  const ao = versionOrdinal(a);
  const bo = versionOrdinal(b);
  if (ao !== null && bo !== null) return ao - bo;
  if (ao !== null) return 1;
  if (bo !== null) return -1;

  return 0;
}

/**
 * Resolves the version a detail response is actually showing.
 *
 * The BFF echoes `content_version: "latest"` — a sentinel, not a version —
 * when the detail is fetched without an explicit `?version=`, which is how the
 * subject page fetches it. Taken literally, that sentinel matched no <option>
 * (so the version selector displayed v1 while the editor showed the newest
 * content) and matched no entry in the version list (so the timeline offered
 * the current version as something to diff against itself).
 *
 * Resolution is by age, not by position: Apicurio happens to return versions
 * oldest-first, but ordering is not part of the contract, so neither end of
 * the array is reliably the newest.
 *
 * It used to sort on `Date.parse(created_on)` directly, and `created_on` is
 * nullable all the way down — the BFF declares `created_on: datetime | None`
 * and fills it from a dict `.get`. `Date.parse(null ?? '')` is `NaN`, and a
 * comparator returning `NaN` sorts nothing: V8 leaves the order alone, so
 * `[0]` was the FIRST entry, which for an oldest-first response is the OLDEST
 * version. One missing timestamp was enough, and the page then named v1
 * everywhere — selector, facts band, the timeline's "Current" tag, the
 * download filename — while rendering the newest body.
 */
export function resolveCurrentVersion(
  versions: VersionRef[],
  candidate: string,
): string {
  if (versions.some((v) => v.version === candidate)) return candidate;
  // Newest first, then take the head.
  const newest = [...versions].sort((a, b) => compareVersionAge(b, a))[0];
  return newest?.version ?? candidate;
}
