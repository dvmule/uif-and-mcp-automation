// versionDiff.js (UIFPE-760)
// What changed from one published version to the next, for the version
// popup's Changes tab. Pure: it reads only the two version records the
// versions call already returns (settings + mapping_config) and says what
// differs in the four places an author edits — Output, Mappings, Rules and
// Delivery. Schedules are not versioned, so they never appear here.
// Comparing ANY two versions is UIFPE-529; this compares neighbours.
import { recipeFields, recipeFilters, recipeSources, recipeSpreads } from "./exportRecipe.js";

const fmtOf = (s) => String((s?.formats?.length ? s.formats[0] : s?.format) || "").toUpperCase();
const delimOf = (s) => s?.format_options?.[fmtOf(s).toLowerCase()]?.delimiter || "";
const zipOf = (s) => s?.packaging?.zip === true;
const manifestOf = (s) => s?.packaging?.manifest !== false;
const modeOf = (s) => (s?.extraction?.mode === "incremental" ? "Incremental" : "Full load");

/** The version's delivery targets as { key, label } — keyed by destination id,
 *  else by channel and address, so a rename of the destination is not a change. */
export function deliveryKeys(settings, nameOf = () => null) {
  const s = settings || {};
  const list = s.deliveries?.length ? s.deliveries : s.delivery ? [{ ...s.delivery, destination_id: s.destination_id }] : [];
  if (!list.length) return [{ key: "managed", label: "Managed S3 partner-drop" }];
  return list.map((t) => {
    const where = t.channel === "s3" ? t.destination_s3_prefix : t.channel === "email" ? (t.recipients || []).join(", ") : `${t.host || ""}${t.remote_dir || ""}`;
    return {
      key: t.destination_id || `${t.channel}:${where}`,
      label: nameOf(t.destination_id) || `${String(t.channel || "").toUpperCase()} · ${where}`,
    };
  });
}

/**
 * @returns {Array<{kind:"add"|"del"|"mod", area:"Output"|"Mappings"|"Rules"|"Delivery", subject:string, detail:string}>}
 *          in that area order; [] when nothing an author sees differs.
 */
export function versionDiff(prev, next, { nameOf } = {}) {
  if (!prev || !next) return [];
  const out = [];
  const push = (kind, area, subject, detail) => out.push({ kind, area, subject, detail });
  const ps = prev.settings || {};
  const ns = next.settings || {};

  // Output
  if (fmtOf(ps) !== fmtOf(ns)) push("mod", "Output", "Format", `${fmtOf(ps) || "—"} → ${fmtOf(ns) || "—"}`);
  else if (delimOf(ps) !== delimOf(ns)) push("mod", "Output", "Separator", `${delimOf(ps) || "default"} → ${delimOf(ns) || "default"}`);
  if (zipOf(ps) !== zipOf(ns)) push("mod", "Output", "Packaging", zipOf(ns) ? "now a ZIP" : "no longer a ZIP");
  if (manifestOf(ps) !== manifestOf(ns)) push("mod", "Output", "Manifest", manifestOf(ns) ? "now added" : "no longer added");
  if (modeOf(ps) !== modeOf(ns)) push("mod", "Output", "Extraction", `${modeOf(ps)} → ${modeOf(ns)}`);

  // Mappings — by target name, in the new file's order, removals last
  const pf = Object.fromEntries(recipeFields(prev.mapping_config).map((f) => [f.target, f]));
  const nfList = recipeFields(next.mapping_config);
  const nf = Object.fromEntries(nfList.map((f) => [f.target, f]));
  for (const f of nfList) {
    const was = pf[f.target];
    if (!was) push("add", "Mappings", f.target, f.from ? `added · ${f.from}` : "added");
    else if (was.from !== f.from) push("mod", "Mappings", f.target, `now ${f.kind === "fixed" ? "is" : "reads"} ${f.from || "—"}`);
  }
  for (const target of Object.keys(pf)) if (!nf[target]) push("del", "Mappings", target, "removed");

  // Rules — tables and joins, spreads, then filters (compared by their words)
  const psrc = Object.fromEntries(recipeSources(prev.mapping_config).map((x) => [x.alias, x]));
  const nsrc = recipeSources(next.mapping_config);
  const nsrcBy = Object.fromEntries(nsrc.map((x) => [x.alias, x]));
  for (const x of nsrc) {
    const was = psrc[x.alias];
    if (!was) push("add", "Rules", x.alias, x.driving ? "now drives the feed" : `joined (${x.join.type})`);
    else if (!x.driving && was.join && (was.join.on !== x.join.on || was.join.type !== x.join.type || was.join.through !== x.join.through)) push("mod", "Rules", x.alias, "join changed");
  }
  for (const alias of Object.keys(psrc)) if (!nsrcBy[alias]) push("del", "Rules", alias, "no longer joined");
  const pu = new Set(recipeSpreads(prev.mapping_config).map((u) => u.list));
  const nu = new Set(recipeSpreads(next.mapping_config).map((u) => u.list));
  for (const l of nu) if (!pu.has(l)) push("add", "Rules", l, "spread to one row per element");
  for (const l of pu) if (!nu.has(l)) push("del", "Rules", l, "no longer spread");
  const pq = recipeFilters(prev.mapping_config, ps).map((f) => f.text);
  const nq = recipeFilters(next.mapping_config, ns).map((f) => f.text);
  for (const t of nq) if (!pq.includes(t)) push("add", "Rules", "Filter", t);
  for (const t of pq) if (!nq.includes(t)) push("del", "Rules", "Filter", t);

  // Delivery
  const pd = deliveryKeys(ps, nameOf);
  const nd = deliveryKeys(ns, nameOf);
  const pk = new Set(pd.map((d) => d.key));
  const nk = new Set(nd.map((d) => d.key));
  for (const d of nd) if (!pk.has(d.key)) push("add", "Delivery", d.label, "now delivered to");
  for (const d of pd) if (!nk.has(d.key)) push("del", "Delivery", d.label, "no longer delivered to");

  return out;
}
