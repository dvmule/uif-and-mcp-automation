// partnerSchema.js (UIFPE-558)
// Pure rules for the partner-XSD mapping guide: which of the schema's
// required paths the current mapping leaves unbound. The server enforces the
// same rule at publish; this copy is what lets the wizard say it early, on
// the Mapping and Review steps.

/** Required schema paths not bound by any mapping target. `paths` is the
 *  API's schema_paths list ({path, required, kind}); `targets` is the
 *  mapping's target names. */
export function missingRequired(paths, targets) {
  const bound = new Set((targets || []).filter(Boolean).map(String));
  return (paths || [])
    .filter((p) => p.required && !bound.has(p.path))
    .map((p) => p.path);
}

/** Target names out of the wizard's mapping rows (target or targets[]). */
export function mappingTargets(mappings) {
  const out = [];
  for (const m of mappings || []) {
    if (m?.target) out.push(m.target);
    for (const t of m?.targets || []) out.push(t);
  }
  return out;
}

/** UIFPE-587: mapping targets the schema does not declare, in the order
 *  given. `declared` is the API's `declared` list — every path the schema
 *  mentions at any level, computed server-side so this cannot drift from the
 *  rule publish applies. A warning, never a block: the author may be ahead
 *  of the partner's schema, but a strict schema rejects a document carrying
 *  elements it never declared, so it must not pass in silence. */
export function unknownTargets(declared, targets) {
  if (!declared?.length) return []; // no schema (or none parsed) — nothing to say
  const known = new Set(declared);
  const seen = new Set();
  const out = [];
  for (const target of targets || []) {
    const name = String(target || "");
    if (name && !known.has(name) && !seen.has(name)) {
      seen.add(name);
      out.push(name);
    }
  }
  return out;
}

// UIFPE-586: the author names the wrapper and record elements, so a partner's
// own document shape can be delivered. The same rule the engine applies in
// renderers.validate_rendering — narrower than the XML spec on purpose: no
// colon, because a prefixed name needs a namespace declaration the renderer
// cannot emit (xml_paths excludes ":" for the same reason).
const XML_NAME = /^[A-Za-z_][A-Za-z0-9_.-]*$/;

/** Complaints about the XML tag names, ready to show. Empty is valid — and
 *  an empty NAME is valid too: it means "keep the renderer's default", which
 *  is what keeps already-published exports rendering unchanged. */
export function xmlTagProblems(options) {
  const labels = { root_tag: "Root tag", row_tag: "Row tag" };
  const out = [];
  for (const key of ["root_tag", "row_tag"]) {
    const value = (options || {})[key];
    if (value === undefined || value === null || value === "") continue;
    if (!XML_NAME.test(String(value))) {
      out.push(
        `${labels[key]} ${JSON.stringify(String(value))} is not a valid element ` +
          "name — letters, digits, '_', '.' and '-' only, starting with a letter " +
          "or '_', and no ':' (namespaces are not supported)."
      );
    }
  }
  return out;
}

// UIFPE-566: auto-map — when a schema is attached, the Mapping step
// pre-creates rows instead of asking for each one by hand. Matching is by
// NAME only, ignoring case, underscores and camelCase (productId ↔
// product_id); nothing smarter, so a wrong guess is easy to see and edit.

const nameKey = (s) => String(s || "").toLowerCase().replace(/[^a-z0-9]/g, "");

/** The name a path is matched by: the attribute's own name for `@` paths,
 *  else the last element of the chain (`Identifiers/ISBN13` → `ISBN13`). */
export function pathMatchName(path) {
  const at = String(path).indexOf("@");
  if (at >= 0) return String(path).slice(at + 1);
  const segs = String(path).split("/");
  return segs[segs.length - 1];
}

/** Match schema paths to source columns by name. `columns` is
 *  [{alias, column}] in feed order — the driving source wins a tie between
 *  sources, and a column is used at most once so two similar paths never
 *  silently share it. Returns {matched: [{path, source}], unmatched: [path]}. */
export function autoMapPaths(paths, columns) {
  const byKey = new Map();
  for (const c of columns || []) {
    const key = nameKey(c.column);
    if (key && !byKey.has(key)) byKey.set(key, `${c.alias}.${c.column}`);
  }
  const matched = [];
  const unmatched = [];
  const used = new Set();
  for (const p of paths || []) {
    const source = byKey.get(nameKey(pathMatchName(p.path)));
    if (source && !used.has(source)) {
      used.add(source);
      matched.push({ path: p.path, source });
    } else {
      unmatched.push(p.path);
    }
  }
  return { matched, unmatched };
}
