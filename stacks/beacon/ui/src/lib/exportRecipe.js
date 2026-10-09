// exportRecipe.js (UIFPE-639)
// "What this export sends", read from a published version: its sources and
// joins, its filters in plain words, its fields in file order, one summary
// sentence, and the last run's numbers. Pure — the export page and the
// version popup (UIFPE-640) render what these return, and nothing here
// shows a value the version or run does not hold.
import { describeClause, filtersFromConfig } from "./rowFilter.js";

const tableOf = (path) => String(path || "").split(".").pop();
const shortPath = (path) => String(path || "").split(".").slice(-2).join(".");

/** The version's mapping rules as a list, whatever shape they were stored in:
 *  `mappings` (the engine's rules) or, in very old records, a `mapping`
 *  object of target → source. */
function rulesOf(mc) {
  if (Array.isArray(mc?.mappings)) return mc.mappings;
  if (mc?.mapping && typeof mc.mapping === "object")
    return Object.entries(mc.mapping).map(([target, source]) => ({ type: "one_to_one", source, target }));
  return [];
}

/** Sources in feed order: the driving table first, then each join. */
export function recipeSources(mc) {
  const sources = mc?.sources || [];
  const aliasOf = (s) => s?.name || s?.alias || tableOf(s?.path);
  const byAlias = Object.fromEntries(sources.map((s) => [aliasOf(s), s]));
  const joins = mc?.joins || [];
  const joinedAliases = new Set(joins.map((j) => j.right));
  const driving = sources.find((s) => !joinedAliases.has(aliasOf(s))) || sources[0];
  const out = [];
  if (driving) out.push({ alias: aliasOf(driving), table: shortPath(driving.path), driving: true, join: null });
  for (const j of joins) {
    const s = byAlias[j.right];
    out.push({
      alias: j.right,
      table: s ? shortPath(s.path) : j.right,
      driving: false,
      join: {
        type: String(j.type || "left").replace(/_/g, " "),
        on: String(j.condition || "").trim(),
        through: j.through?.list || null,
      },
    });
  }
  return out;
}

/** Lists spread into one row per element after the joins (`unnest`). */
export function recipeSpreads(mc) {
  return (mc?.unnest || []).map((u) => ({ list: u.list, alias: u.alias }));
}

/** Each filter as { kind: "plain" | "sql", text } — the Filter step's own
 *  sentences where the saved builder model still matches what ran, else the
 *  published Spark SQL verbatim. */
export function recipeFilters(mc, settings) {
  return filtersFromConfig(mc, settings).clauses.map((c) =>
    c.kind === "custom"
      ? { kind: "sql", text: String(c.sql || "").trim() }
      : { kind: "plain", text: describeClause(c) }
  );
}

/** One entry per field of the file, in file order. */
export function recipeFields(mc) {
  const out = [];
  for (const m of rulesOf(mc)) {
    if (m.type === "one_to_many") {
      (m.targets || []).forEach((t, i) =>
        out.push({ target: t, from: (m.expressions || [])[i] || "", kind: "expression" })
      );
    } else if (m.type === "literal") {
      const v = m.value;
      out.push({ target: m.target, from: typeof v === "string" ? `'${v}'` : String(v), kind: "fixed" });
    } else if (m.type === "derived" || m.type === "many_to_one") {
      out.push({ target: m.target, from: m.expression || "", kind: "expression" });
    } else {
      out.push({ target: m.target, from: m.source || m.expression || "", kind: "column" });
    }
  }
  return out.map((f, i) => ({ ...f, n: i + 1 }));
}

/** "2 columns · 1 expression · 1 fixed" — only the kinds present. */
export function fieldKindsText(fields) {
  const count = (k) => fields.filter((f) => f.kind === k).length;
  const parts = [
    [count("column"), "column", "columns"],
    [count("expression"), "expression", "expressions"],
    [count("fixed"), "fixed", "fixed"],
  ]
    .filter(([n]) => n > 0)
    .map(([n, one, many]) => `${n} ${n === 1 ? one : many}`);
  return parts.join(" · ");
}

/** The declared row grain of the feed ("one row per price"), from the
 *  relationships the panel serves, or "" when none of its joins is declared. */
export function recipeGrain(mc, relationships) {
  const joined = (mc?.joins || []).map((j) => j.right);
  const rels = relationships || [];
  for (let i = joined.length - 1; i >= 0; i -= 1) {
    const rel = rels.find((r) => r.related === joined[i] && r.row);
    if (rel) return rel.row;
  }
  return "";
}

const joinWords = (items) =>
  items.length <= 1 ? items.join("") : `${items.slice(0, -1).join(", ")} and ${items[items.length - 1]}`;

/** The one-sentence summary: what one row is, how many fields, which format.
 *  Tables go by their short name; the filter conditions are NOT here (UIFPE-642,
 *  owner review 2026-09-28) — they live under the Rows part only. */
export function recipeSentence(version, relationships) {
  const mc = version?.mapping_config || {};
  const settings = version?.settings || {};
  const sources = recipeSources(mc);
  const grain = recipeGrain(mc, relationships);
  const name = (s) => tableOf(s.table);
  const first = sources[0] ? name(sources[0]) : "the source";
  const lead = grain
    ? `${grain.charAt(0).toUpperCase()}${grain.slice(1)} of ${first}`
    : `Rows of ${first}${sources.length > 1 ? ` joined with ${joinWords(sources.slice(1).map(name))}` : ""}`;
  const n = recipeFields(mc).length;
  const format = String(settings.formats?.[0] || settings.format || "").toUpperCase();
  const article = /^[AEFHILMNORSX]/.test(format) && format !== "CSV" ? "an" : "a"; // an XML, a CSV, a JSON
  return `${lead}, sent as ${n} field${n === 1 ? "" : "s"}${format ? ` in ${article} ${format} file` : ""}.`;
}

/** The last finished run's numbers. `null` for a figure the run did not
 *  record — older runs have no row counts, and that is "not measured",
 *  never 0. */
export function lastRunFigures(run, filesOf) {
  if (!run) return null;
  const s = run.summary || {};
  const num = (v) => (typeof v === "number" && Number.isFinite(v) ? v : null);
  return {
    status: run.status,
    at: run.recorded_at || null,
    delivered: num(s.output_rows),
    rejected: num(s.rejected_rows),
    filtered: num(s.filtered_rows),
    files: filesOf ? filesOf(s) : null,
  };
}

/** Fields whose name or source contains the query (case-insensitive). */
export function searchFields(fields, query) {
  const q = String(query || "").trim().toLowerCase();
  if (!q) return fields;
  return fields.filter((f) => f.target.toLowerCase().includes(q) || f.from.toLowerCase().includes(q));
}
