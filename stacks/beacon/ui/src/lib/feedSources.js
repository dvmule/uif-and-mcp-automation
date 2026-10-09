// feedSources.js — the wizard's multi-source feed model, as pure functions.
//
// A feed reads one or MORE gold tables (UIFPE-237; made generic end-to-end by
// UIFPE-167). The model is an ordered list of entries:
//   { source: <catalog table record {id, path, kind, domain, ...}>,
//     alias:  string,
//     join:   null | { type, pairs: [{ leftRef: "alias.col", rightColumn }], condition,
//                      through, unnest, relationship, auto } }
// Entry [0] is the DRIVING source and never has a join; every later entry
// joins onto the frame built so far. `pairs` are dropdown-built equalities
// ANDed together; a non-null `condition` means custom Spark SQL. A "cross"
// join has neither (the engine's crossJoin takes no condition).
// UIFPE-581: a join prefilled from the DECLARED relationships
// (GET /registries/relationships, UIFPE-580) carries `relationship`
// ({summary, narrow_by}) and a ready condition; when the relationship goes
// through a list column on the driving row it also carries
// `through: {list, alias}` (the engine spreads the list, then key-matches its
// element, addressed as alias.field / alias.value) and may carry `unnest`
// steps that spread a list on the joined table after the join (one row per
// price). `auto: true` marks a join the wizard built itself (default pairs
// or relationship) so it can be upgraded when the relationships load.
//
// Nothing here touches React or the API — the component owns state and
// effects, this module owns the rules, so the rules are unit-testable
// (vitest, see __tests__/feedSources.test.js) without a browser.

// The join types the wizard offers. The engine accepts inner, left, right,
// full, left_semi, left_anti and cross (config_validator.JOIN_TYPES); the
// wizard exposes the four that make sense for attaching a table to a feed —
// a right join is "make the other table the driving source", and semi/anti
// joins add no columns from the joined table, so they are filters, not
// attachments. Any other type prefilled from a stored version or the
// converter still renders verbatim (JoinRow adds it to the dropdown).
export const JOIN_TYPES = ["left", "inner", "full", "cross"];

// Alias for a newly toggled source: the last dot-segment of its id, sanitized;
// collisions get a numeric suffix (product, product_2, …).
export function deriveAlias(sourceId, takenAliases = []) {
  const base =
    String(sourceId).split(".").pop().toLowerCase().replace(/[^a-z0-9_]/g, "") || "src";
  let alias = base;
  for (let n = 2; takenAliases.includes(alias); n += 1) alias = `${base}_${n}`;
  return alias;
}

// Column names known for a source — live introspection when already fetched,
// else the record's static list (registry rows carry one; UIFPE-229 catalog
// table records don't, so introspection is their only column source).
export function knownColumns(source, attrsBySource) {
  const live = attrsBySource?.[source.id]?.columns;
  if (live?.length) return live;
  return source.columns || [];
}

// ------------------------------------------ declared relationships (UIFPE-581)

// The Gold TABLE a source id denotes: the last dot-segment (product.product,
// gold_product.product and product.marketing_copy → product / marketing_copy).
export function tableOf(sourceId) {
  return String(sourceId || "").split(".").pop();
}

// `{table}` placeholders in a relationship's SQL → the feed's aliases.
function substituteAliases(text, byTable) {
  return String(text ?? "").replace(/\{(\w+)\}/g, (whole, table) => byTable[table] ?? whole);
}

// The join the declared relationships prescribe for `entry` joining a feed
// driven by `driving`, or null when no relationship covers the pair. The
// relationship's `{driving}`/`{related}` placeholders become the two aliases;
// the result is a custom-condition join (the condition is exact Spark SQL)
// carrying `through` / `unnest` when the relationship spreads a list.
export function relationshipFor(relationships, driving, entry) {
  if (!driving || !entry) return null;
  const rel = (relationships || []).find(
    (r) => r.driving === tableOf(driving.source.id) && r.related === tableOf(entry.source.id)
  );
  if (!rel?.join) return null;
  const byTable = { [rel.driving]: driving.alias, [rel.related]: entry.alias };
  const step = (s) => ({ list: substituteAliases(s.list, byTable), alias: s.alias });
  return {
    type: rel.join.type || "left",
    pairs: [],
    condition: substituteAliases(rel.join.condition, byTable),
    through: rel.join.through ? step(rel.join.through) : null,
    unnest: (rel.unnest || []).map(step),
    relationship: {
      summary: rel.summary || "",
      narrow_by: (rel.narrow_by || []).map((c) => substituteAliases(c, byTable)),
      // UIFPE-592: what one exported row is, for the card heading
      row: rel.row || "one row per match",
    },
    auto: true,
  };
}

// The Mapping-step card heading (and the Filter picker's group label) for a
// feed entry. A table joined through a declared relationship reads as what
// its rows are — "Marketing copy · one row per copy" — never as the engine's
// mechanism; the driving table and hand-built joins keep the source id.
export function tableHeading(entry) {
  if (entry?.join?.relationship) {
    const t = tableOf(entry.source.id).replace(/_/g, " ");
    return `${t[0].toUpperCase()}${t.slice(1)} · ${entry.join.relationship.row || "one row per match"}`;
  }
  return `Source attributes · ${entry?.source?.id ?? ""}`;
}

// UIFPE-609: the mapping a chip click creates. A list/record column in a CSV
// export is wrapped in to_json(...) — Spark's csv writer cannot carry it and
// the run's rendering guard would refuse it after filter, join and mapping
// had run; JSON and XML hold nested values natively and keep the bare column.
export function mappingForColumn(alias, col, glueType, format, target) {
  const source = `${alias}.${col}`;
  if (format === "csv" && /^(array|struct|map)</i.test(glueType || "")) {
    return { type: "derived", expression: `to_json(${source})`, target };
  }
  return { type: "one_to_one", source, target };
}

// Whether `mappings` already carries the column, bare or wrapped.
export function columnMapped(mappings, alias, col) {
  const source = `${alias}.${col}`;
  return (mappings || []).some((m) => m.source === source || m.expression === `to_json(${source})`);
}

// UIFPE-701: clicking a ticked attribute takes it out again — every row that
// carries the column bare or wrapped (the same test as columnMapped). Rows
// that only USE it inside a longer expression stay. { kept, removed }.
export function unmapColumn(mappings, alias, col) {
  const source = `${alias}.${col}`;
  const hit = (m) => m.source === source || m.expression === `to_json(${source})`;
  const list = mappings || [];
  return { kept: list.filter((m) => !hit(m)), removed: list.filter(hit) };
}

// UIFPE-607: what one exported row IS, from the declared labels only — the
// driving table's `row` (registry tables.yaml) and each relationship join's
// `row`: "Each row is one product per organization, per copy." "" when the
// driving table declares nothing.
export function rowShape(feedSources) {
  const [driving, ...joined] = feedSources || [];
  const base = driving?.source?.row;
  if (!base) return "";
  const more = joined
    .map((e) => e.join?.relationship?.row)
    .filter(Boolean)
    .map((r) => `, ${r.replace(/^one row per /, "per ")}`);
  return `Each row is ${base.replace(/^one row per /, "one ")}${more.join("")}.`;
}

// "To keep fewer rows, narrow by product.organization_code." — from the
// driving table's declared `narrow_by`; "" when it declares none.
export function narrowHint(feedSources) {
  const driving = (feedSources || [])[0];
  const cols = driving?.source?.narrow_by || [];
  return cols.length ? `To keep fewer rows, narrow by ${cols.map((c) => `${driving.alias}.${c}`).join(" or ")}.` : "";
}

// UIFPE-592: the fields a spread list ADDS to its table's card — the element
// fields minus those the joined table already has by name (after the join
// they are the same value on the same row: product's copy record repeats
// the marketing copy's type, market and audience), and minus a lone `value`
// (a list of ids duplicates the join key). [] when nothing is added; null
// while the list column's type is still unknown.
export function listColumnsFor(el, feedSources, attrsBySource) {
  const fields = elementFields(attrsBySource?.[el.sourceId]?.types?.[el.column]);
  if (fields === null) return null;
  if (fields.length === 1 && fields[0].name === "value") return [];
  const table = (feedSources || []).find((e) => e.alias === el.tableAlias);
  const have = new Set(table ? knownColumns(table.source, attrsBySource).map((c) => c.toLowerCase()) : []);
  return fields.filter((f) => !have.has(f.name.toLowerCase()));
}

// A Glue type string's ELEMENT fields for a list column, as the wizard
// addresses them after a spread: array<struct<a:string,b:int>> →
// [{name: "a", type: "string"}, {name: "b", type: "int"}]; array<string> →
// [{name: "value", type: "string"}]; not a list → null. Mirrors
// dynamic_mapping/lists.py (commas inside <> and () do not split fields).
export function elementFields(glueType) {
  const m = /^array\s*<([\s\S]*)>$/i.exec(String(glueType ?? "").trim());
  if (!m) return null;
  const inner = m[1].trim();
  const s = /^struct\s*<([\s\S]*)>$/i.exec(inner);
  if (!s) return [{ name: "value", type: inner }];
  const fields = [];
  let depth = 0;
  let start = 0;
  const body = s[1];
  const push = (part) => {
    const i = part.indexOf(":");
    const name = (i < 0 ? part : part.slice(0, i)).trim().replace(/^`|`$/g, "");
    if (name) fields.push({ name, type: i < 0 ? "" : part.slice(i + 1).trim() });
  };
  for (let i = 0; i < body.length; i += 1) {
    const ch = body[i];
    if (ch === "<" || ch === "(") depth += 1;
    else if (ch === ">" || ch === ")") depth -= 1;
    else if (ch === "," && depth === 0) {
      push(body.slice(start, i));
      start = i + 1;
    }
  }
  push(body.slice(start));
  return fields;
}

// Every list spread the feed declares, in engine order (joins' `through`
// first, then `unnest`), with where the list lives and which joined table it
// belongs to: [{alias, list, sourceId, column, summary, tableAlias}]. The
// Filter and Mapping steps fold the element's fields into that table's card
// (UIFPE-592) rather than showing the engine's alias.
export function elementAliasesOf(feedSources) {
  const out = [];
  const byAlias = Object.fromEntries((feedSources || []).map((e) => [e.alias, e]));
  const add = (step, tableAlias) => {
    const [sourceAlias, column] = String(step?.list || "").split(".");
    const owner = byAlias[sourceAlias];
    if (!owner || !column) return;
    out.push({ alias: step.alias, list: step.list, sourceId: owner.source.id, column,
               summary: `from ${step.list}`, tableAlias });
  };
  for (const e of feedSources || []) {
    if (e.join?.through) add(e.join.through, e.alias);
  }
  for (const e of feedSources || []) {
    for (const u of e.join?.unnest || []) add(u, e.alias);
  }
  return out;
}

// Every `alias.` qualifier a Spark SQL condition mentions, deduplicated, in
// order of first appearance. Lexical on purpose: it must work on half-typed
// text, and the engine's validator does the real parse at submit. Completed
// single-quoted string literals are blanked first so 'a.b' is not read as
// alias a (an unclosed quote mid-typing is left alone).
export function referencedQualifiers(condition) {
  const text = String(condition || "").replace(/'(?:[^']|'')*'/g, "''");
  const out = [];
  for (const m of text.matchAll(/\b([A-Za-z_]\w*)\s*\./g)) {
    if (!out.includes(m[1])) out.push(m[1]);
  }
  return out;
}

// The subset of `candidates` a condition references (same order rule).
export function referencedAliases(condition, candidates) {
  return referencedQualifiers(condition).filter((a) => candidates.includes(a));
}

// Invariant repair after any reorder/add/remove: the driving entry never has
// a join; every secondary entry has one (default-built if missing); a pair
// whose left side references an alias that is no longer PRIOR to it resets;
// a CUSTOM condition referencing an alias this join cannot see (deselected,
// or only joined LATER — joins fold in declaration order) resets to the
// default pairs, keeping the join TYPE the author chose.
export function normalizeFeedSources(entries, attrsBySource, relationships = []) {
  return entries.map((entry, i) => {
    if (i === 0) return entry.join === null ? entry : { ...entry, join: null };
    const prior = entries.slice(0, i);
    const priorAliases = prior.map((p) => p.alias);
    let join = entry.join;
    // UIFPE-581: a wizard-built join (auto) is upgraded to the declared
    // relationship once the relationships are known; an author-edited one is
    // never touched
    if (!join || (join.auto && !join.relationship)) {
      join =
        relationshipFor(relationships, prior[0], entry) ||
        (join ?? { type: "left", pairs: defaultJoinPairs(entry, prior, attrsBySource), condition: null, auto: true });
    } else if (join.condition != null) {
      // the element alias of a through-list join is visible to its own condition
      const visible = [...priorAliases, entry.alias, ...(join.through ? [join.through.alias] : [])];
      if (referencedQualifiers(join.condition).some((a) => !visible.includes(a))) {
        join = {
          type: join.type || "left",
          pairs: defaultJoinPairs(entry, prior, attrsBySource),
          condition: null,
        };
      }
    } else if (join.pairs?.length) {
      const pairs = join.pairs.map((pair) => {
        const leftAlias = String(pair.leftRef || "").split(".")[0];
        return leftAlias && !priorAliases.includes(leftAlias)
          ? { leftRef: "", rightColumn: "" }
          : pair;
      });
      join = { ...join, pairs };
    }
    return { ...entry, join };
  });
}

// Auto-prefill: join the new source to the DRIVING source on a shared column
// name — ppid when both have it (the gold layer's cross-table key), else the
// first shared name. Nothing shared → one empty pair for the author to fill.
export function defaultJoinPairs(entry, priorEntries, attrsBySource) {
  const driving = priorEntries[0];
  const drivingCols = knownColumns(driving.source, attrsBySource);
  const shared = knownColumns(entry.source, attrsBySource).filter((c) => drivingCols.includes(c));
  const col = shared.includes("ppid") ? "ppid" : shared[0];
  if (!col) return [{ leftRef: "", rightColumn: "" }];
  return [{ leftRef: `${driving.alias}.${col}`, rightColumn: col }];
}

// The Spark SQL condition an entry's join amounts to: nothing for a cross
// join (pairs/condition are kept on the entry so switching type back
// restores them, but never emitted), the custom condition verbatim when set,
// else the complete pairs ANDed (incomplete ones excluded).
export function joinConditionOf(entry) {
  if (!entry?.join) return "";
  if (entry.join.type === "cross") return "";
  if (entry.join.condition != null) return entry.join.condition;
  return (entry.join.pairs || [])
    .filter((p) => p.leftRef && p.rightColumn)
    .map((p) => `${p.leftRef} = ${entry.alias}.${p.rightColumn}`)
    .join(" AND ");
}

// Inverse of joinConditionOf for converter/edit prefill: AND-ed alias.column
// equalities (one side being rightAlias) round-trip into dropdown pairs;
// anything else returns null and the row renders in custom-SQL mode.
export function parseEqualityJoins(condition, rightAlias) {
  const clauses = String(condition || "").split(/\s+AND\s+/i);
  const pairs = [];
  for (const clause of clauses) {
    const m = /^\s*(\w+)\.(\w+)\s*=\s*(\w+)\.(\w+)\s*$/.exec(clause);
    if (!m) return null;
    const [, leftAlias, leftCol, rightSideAlias, rightCol] = m;
    // exactly ONE side must reference rightAlias — a self-comparison
    // (price.a = price.b) or a clause not touching this source has no
    // dropdown shape; custom mode keeps the whole condition verbatim
    if ((leftAlias === rightAlias) === (rightSideAlias === rightAlias)) return null;
    if (rightSideAlias === rightAlias) pairs.push({ leftRef: `${leftAlias}.${leftCol}`, rightColumn: rightCol });
    else pairs.push({ leftRef: `${rightSideAlias}.${rightCol}`, rightColumn: leftCol });
  }
  return pairs;
}

// A stored/converted join declaration → the entry's join model. Equality
// conditions become dropdown pairs; anything else stays custom SQL with the
// type preserved verbatim — the prefill must never lie. A missing type is
// the engine's default (inner, config_loader.parse_config), not the wizard's.
export function joinFromConfig(j, unnest = []) {
  const type = j?.type || "inner";
  if (type === "cross") return { type, pairs: [], condition: null };
  // UIFPE-581: a through-list join's condition names the element alias, which
  // is not a prior source — it stays a custom condition with its `through`
  const steps = (unnest || []).filter((u) => String(u?.list || "").split(".")[0] === j?.right);
  if (j?.through) {
    return { type, pairs: [], condition: j.condition ?? "", through: j.through, unnest: steps };
  }
  const pairs = parseEqualityJoins(j?.condition, j?.right);
  const base = pairs
    ? { type, pairs, condition: null }
    : { type, pairs: [], condition: j?.condition ?? null };
  return steps.length ? { ...base, unnest: steps } : base;
}

// One-line review summary, e.g. "price · left join on product.ppid = price.ppid"
// — or, for a prefilled relationship, its plain sentence (UIFPE-581).
export function describeJoin(entry) {
  if (entry.join?.relationship?.summary) {
    return `${entry.alias} · ${entry.join.type || "left"} join, ${entry.join.relationship.summary}`;
  }
  const condition = joinConditionOf(entry);
  return `${entry.alias} · ${entry.join?.type || "left"} join${condition ? ` on ${condition}` : ""}`;
}

// Can the feed leave the Source step? Every joined source needs a complete
// join: a cross join needs nothing, custom SQL must be non-blank, and every
// dropdown pair must be filled (an added "+ condition" row is filled OR
// removed, never silently dropped).
export function joinsComplete(feedSources) {
  return feedSources.slice(1).every(joinComplete);
}

// One joined entry's own check (UIFPE-717: the Rules step marks the row)
export function joinComplete(e) {
  if (e.join?.type === "cross") return true;
  if (e.join?.condition != null) return Boolean(e.join.condition.trim());
  const pairs = e.join?.pairs || [];
  return pairs.length >= 1 && pairs.every((p) => p.leftRef && p.rightColumn);
}

// UIFPE-717: a join as one compact row on the Rules step — an automatic
// label (no typed names); `position` counts the joins from 1
export function joinRuleLabel(entry, position) {
  return `Join ${position} · ${tableOf(entry.source.id)}`;
}

// The prior source this entry's join ATTACHES to — what the config's `left`
// says. The one prior alias its condition references when there is exactly
// one; otherwise (a cross join, a literal-only condition, or a condition
// spanning several prior tables) the driving alias, which denotes the frame
// the engine has folded so far. Invariant: for entries[1] this is always the
// driving alias, so joins[0].left seeds the engine correctly.
export function joinLeftAlias(entry, priorEntries) {
  if (!priorEntries?.length) return null;
  const driving = priorEntries[0].alias;
  const join = entry?.join;
  if (!join || join.type === "cross") return driving;
  const priorAliases = priorEntries.map((p) => p.alias);
  const refs =
    join.condition != null
      ? referencedAliases(join.condition, priorAliases)
      : [
          ...new Set(
            (join.pairs || [])
              .map((p) => String(p.leftRef || "").split(".")[0])
              .filter((a) => priorAliases.includes(a))
          ),
        ];
  return refs.length === 1 ? refs[0] : driving;
}

// The engine's mapping config for the feed. Every entry becomes a source;
// entries beyond the driving one become joins, attached where their
// condition says (joinLeftAlias). No sources → no sources: the wizard's
// gates make that unreachable, and the engine's own "at least one source
// is required" is the backstop — there is deliberately no fallback table.
// `filters` are the COMPILED Spark SQL strings (lib/rowFilter.js), one per
// complete clause; blanks are dropped, the rest trimmed.
export function buildMappingConfig({ feedSources, mappings, filters = [] }) {
  const configSources = feedSources.map(({ source: src, alias }) => {
    const format = src.kind || "iceberg";
    const source = { name: alias, path: src.path, format };
    if (format === "csv") source.options = { header: "true", inferSchema: "true" };
    return source;
  });
  const joins = feedSources.slice(1).map((e, i) => {
    const join = {
      left: joinLeftAlias(e, feedSources.slice(0, i + 1)),
      right: e.alias,
      type: e.join?.type || "left",
      condition: joinConditionOf(e),
    };
    // UIFPE-581: a relationship through a list column spreads it before the match
    if (e.join?.through && join.type !== "cross") join.through = { ...e.join.through };
    return join;
  });
  const unnest = feedSources.flatMap((e) => (e.join?.unnest || []).map((u) => ({ ...u })));
  return {
    version: "1.0",
    sources: configSources,
    joins,
    ...(unnest.length ? { unnest } : {}),
    mappings,
    filters: (filters || []).map((f) => String(f ?? "").trim()).filter(Boolean),
    validations: [],
    // the publisher normalizes environment-owned paths at publish time
    rejected_path: "s3://placeholder/rejected/${batch_id}/",
    output: {
      targets: [
        { format: "parquet", mode: "overwrite", path: "s3://placeholder/staging/${batch_id}/canonical/" },
      ],
    },
  };
}

// The domain a database-ish name segment denotes, by the platform's naming
// convention (registries.gold_databases: the part after the last "gold_"):
// echo_dev_gold_price, gold_price and price all denote "price".
export function hintDomain(segment) {
  const s = String(segment || "").toLowerCase();
  const i = s.lastIndexOf("gold_");
  return i >= 0 ? s.slice(i + "gold_".length) : s;
}

// Door 2: match the converter's sources (registry_hint = the table reference
// exactly as the query wrote it: `product`, `gold_price.price`,
// `echo_dev_gold_price.price`, `glue_catalog.echo_dev_gold_price.price`)
// against the catalog tables of EVERY configured domain, and attach the
// converted joins by their right alias. Segment-aware and case-insensitive
// (sqlglot keeps identifiers as typed, Glue lowercases): the last segment
// must equal the table name; a database segment, when present, must equal
// the table's database or denote its domain. One match → an entry; none →
// "unknown"; several (a bare table name shared by two domains) →
// "ambiguous", with the candidate ids so the author can qualify it.
export function resolveConvertedSources(conversion, pool) {
  const tables = [...new Map((pool || []).map((t) => [t.path, t])).values()];
  const entries = [];
  const unmatched = [];
  for (const rs of conversion?.sources || []) {
    const hint = rs.registry_hint || rs.name || "";
    const parts = hint.toLowerCase().split(".").filter(Boolean);
    const table = parts[parts.length - 1];
    const db = parts.length >= 2 ? parts[parts.length - 2] : null;
    let candidates = tables.filter(
      (t) => String(t.path).split(".").pop().toLowerCase() === table
    );
    if (db) {
      candidates = candidates.filter((t) => {
        const database = String(String(t.path).split(".")[1] || "").toLowerCase();
        return database === db || String(t.domain || "").toLowerCase() === hintDomain(db);
      });
    }
    if (candidates.length === 1) {
      entries.push({ source: candidates[0], alias: rs.name, join: null });
    } else {
      unmatched.push({
        hint,
        alias: rs.name,
        reason: candidates.length ? "ambiguous" : "unknown",
        candidates: candidates.map((t) => t.id),
      });
    }
  }
  const withJoins = entries.map((entry, i) => {
    if (i === 0) return entry;
    const j = (conversion?.joins || []).find((jn) => jn.right === entry.alias);
    return j ? { ...entry, join: joinFromConfig(j) } : entry;
  });
  return { entries: withJoins, unmatched };
}

// One plain sentence per engine join type, for the JoinRow help note.
export function explainJoin(type, leftAlias, rightAlias) {
  const l = leftAlias || "the driving source";
  const r = rightAlias || "the joined source";
  switch (type || "left") {
    case "left":
      return `Left join keeps every ${l} row and attaches the matching ${r} row — ${r} columns are blank where nothing matches.`;
    case "inner":
      return `Inner join keeps only ${l} rows that have a matching ${r} row — unmatched ${l} rows are dropped.`;
    case "full":
      return `Full join keeps rows from both ${l} and ${r} — whichever side has no match is blank.`;
    case "cross":
      return `Cross join pairs every ${l} row with every ${r} row — no condition, and the row count multiplies.`;
    case "right":
      return `Right join keeps every ${r} row and attaches matching ${l} rows — usually clearer as making ${r} the driving source.`;
    case "left_semi":
      return `Left semi join keeps ${l} rows that have a match in ${r} and adds no ${r} columns — a filter, not an attachment.`;
    case "left_anti":
      return `Left anti join keeps ${l} rows with NO match in ${r} and adds no ${r} columns.`;
    default:
      return `${type} join of ${r} onto ${l}.`;
  }
}
