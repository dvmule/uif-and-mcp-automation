// Deterministic SQL preview — shows the user EXACTLY what their form inputs
// compile into for this domain's mapping SQL. This is the compiler contract
// made visible: the same rendering rules the delta compiler (UIFPE-100)
// implements, with placeholders only where runtime state fills in (entity
// keys come from the manifest's key_mapping / the task's declared joins).
// No AI interpretation anywhere in this path.
import {
  MappingColumn, ScalarColumn, ConditionalColumn, NestedColumn, SourceDecl,
  RowGroupDef, TransformDef, TransformRef, EntityKeyPair, SourceJoin,
  sameTable, resolveScope, resolveTransform,
} from "./types";
import { parseCondition, branchWhere, scopeWhere, scopeLabel } from "./conditions";

const short = (t: string) => t.split(".").pop() ?? t;
const alias = (t: string) => short(t).toLowerCase().replace(/[^a-z0-9]+/g, "_");
// Multi-line transform expressions (CASE WHEN …) embed in a SELECT line —
// re-indent their continuation lines so the preview stays readable.
const indent = (expr: string, pad: string) => expr.split("\n").join(`\n${pad}`);

// UIFPE-107: with the entity key declared, previews render the REAL key
// columns instead of a {entity_key} placeholder. A source's declared join
// narrows the set to the key columns it actually carries (an item-level
// lookup pivots on INVENTORY_ITEM_ID alone); no join means the full key.
function keyExpr(entityKey: EntityKeyPair[] | undefined,
                 join?: SourceJoin | null): string {
  if (!entityKey?.length) return "{entity_key}";
  const driving = entityKey.map((p) => p.drivingColumn);
  const hits = (join?.on ?? [])
    .filter((o) => driving.includes((o.leftColumn || "").toUpperCase()))
    .map((o) => o.rightColumn || o.leftColumn);
  return (hits.length ? hits : driving).join(", ");
}

// ── Transform rendering (UIFPE-105) ────────────────────────────────────────
// A structured transform compiles around the source expression x with NO
// interpretation: cases become a CASE WHEN evaluated in order, steps wrap the
// result as a pipeline, sqlExpr substitutes {x}. This is the exact shape the
// hand-written mapping SQL uses (product.sql's coverImage CASE, the unit*
// CAST(round(...))) — the preview and the compiler render the same rules.

const lit = (v: string) => `'${v.replace(/'/g, "''")}'`;

function caseWhen(when: NonNullable<TransformDef["cases"]>[number]["when"],
                  x: string): string {
  const vals = when.values ?? [];
  const one = (v: string) => {
    if (when.op === "startsWith") return `substring(${x}, 1, ${v.length}) = ${lit(v)}`;
    if (when.op === "like") return `${x} LIKE ${lit(v)}`;
    return `${x} = ${lit(v)}`;
  };
  if (when.op === "isNull") return `${x} IS NULL`;
  if (when.op === "in") return `${x} IN (${vals.map(lit).join(", ")})`;
  return vals.map(one).join(" OR ") || `${x} = {value}`;
}

function caseThen(then: NonNullable<TransformDef["cases"]>[number]["then"],
                  x: string): string {
  if (then.op === "prepend") return `concat(${lit(then.text ?? "")}, ${x})`;
  if (then.op === "append") return `concat(${x}, ${lit(then.text ?? "")})`;
  if (then.op === "replaceText")
    return `replace(${x}, ${lit(then.find ?? "")}, ${lit(then.replaceWith ?? "")})`;
  if (then.op === "literal") return lit(then.text ?? "");
  if (then.op === "null") return "NULL";
  return x;
}

export function transformSql(def: TransformDef, x: string): string {
  let expr = x;
  if (def.cases?.length) {
    const branches = def.cases.map((c) =>
      `WHEN ${caseWhen(c.when, x)} THEN ${caseThen(c.then, x)}`);
    const fallthrough = def.else === "null" ? "NULL" : x;
    expr = `CASE\n  ${branches.join("\n  ")}\n  ELSE ${fallthrough}\nEND`;
  }
  for (const s of def.steps ?? []) {
    if (s.op === "round") expr = `round(${expr}, ${s.precision ?? 0})`;
    else if (s.op === "cast") expr = `CAST(${expr} AS ${s.to ?? "{type}"})`;
    else if (s.op === "coalesceTo") expr = `COALESCE(${expr}, ${lit(s.value ?? "")})`;
    else expr = `${s.op}(${expr})`;   // trim / upper / lower
  }
  if (def.sqlExpr) expr = def.sqlExpr.split("{x}").join(expr);
  return expr;
}

// Wrap x in a resolved transform; unresolvable shapes (prose, missing rule)
// surface as a comment so the gap is visible in the preview instead of the
// transform silently vanishing.
function applyTransform(t: TransformRef | undefined,
                        transforms: Record<string, TransformDef>,
                        x: string): { expr: string; note?: string } {
  const r = resolveTransform(t, transforms);
  if (!r) return { expr: x };
  if (r.prose)
    return { expr: x, note: `-- transform is FREE TEXT (deprecated) — restructure it: ${r.prose}` };
  if (r.missing)
    return { expr: x, note: `-- transform rule "${r.rule}" is not defined in this mapping` };
  return { expr: transformSql(r.def!, x) };
}

function joinLines(sources: SourceDecl[]): string[] {
  return sources.slice(1).map((s) => {
    const on = s.joins
      .map((j) => `${alias(j.leftTable)}.${j.leftColumn} = ${alias(s.table)}.${j.rightColumn}`)
      .join(" AND ") || "/* join per registered manifest */";
    return `${s.joinType.toUpperCase()} JOIN ${short(s.table)} ${alias(s.table)} ON ${on}`;
  });
}

export function previewColumnSql(col: MappingColumn, sources: SourceDecl[],
                                 drivingTable?: string | null,
                                 rowGroups?: Record<string, RowGroupDef>,
                                 transforms?: Record<string, TransformDef>,
                                 entityKey?: EntityKeyPair[]): string {
  if (col.kind === "scalar") return scalarSql(col, sources, drivingTable, transforms ?? {}, entityKey);
  if (col.kind === "conditional") return conditionalSql(col, sources, rowGroups ?? {}, transforms ?? {}, entityKey);
  return nestedSql(col as NestedColumn, sources, drivingTable, transforms ?? {});
}

function scalarSql(col: ScalarColumn, sources: SourceDecl[],
                   drivingTable: string | null | undefined,
                   transforms: Record<string, TransformDef>,
                   entityKey?: EntityKeyPair[]): string {
  const src = col.source;
  const eav = sources.find((s) => sameTable(s.table, src.table))?.eav
    // registered product mappings: the EAV table is recognizable by name
    || /EXT_B$/i.test(short(src.table));
  const t = alias(src.table || "src");
  if (eav) {
    // The transform wraps the PIVOTED value in the final SELECT — the CTE's
    // max() pick stays untouched, matching the hand-written SQL's shape.
    const tf = applyTransform(col.transform, transforms, `${t}_grp.${col.goldColumnName}`);
    const keys = keyExpr(entityKey, src.join);
    return [
      `-- EAV pivot: one CTE per attribute group, deduped with max()`,
      `${t}_grp AS (`,
      `  SELECT ${keys},`,
      `         max(${src.column}) AS ${col.goldColumnName}`,
      `  FROM ${short(src.table)}`,
      `  WHERE ${src.filter ?? "/* row filter required for EAV sources */"}`,
      `  GROUP BY ${keys}`,
      `)`,
      `-- final SELECT:`,
      `${tf.expr} AS ${col.goldColumnName}`,
      ...(tf.note ? [tf.note] : []),
    ].join("\n");
  }
  // FROM is always the entity's driving table (registered drivingTable, or
  // the task's first declared source); the source attaches to it explicitly —
  // via the structured per-column join (intake sourceRef.join) or the task's
  // declared join graph. A non-driving source with NEITHER renders a
  // placeholder join, so the missing attachment is visible instead of the
  // source silently posing as the entity's spine.
  const drv = drivingTable || sources[0]?.table || "";
  const isDriving = sameTable(src.table, drv);
  const declared = sources.some((s, i) => i > 0 && sameTable(s.table, src.table));
  // Structured lookup (UIFPE-106): a dedicated dimension CTE over the lookup
  // table — the orgs-CTE shape of the hand-written mapping SQL, exactly what
  // the compiler's sql_add_lookup() renders. max() per join key, so a 1:N
  // lookup can never fan the driving rows out; _ID keys CAST to BIGINT on
  // both sides; the declared filter folded INSIDE the CTE's WHERE — an outer
  // WHERE on a joined column would null away every unmatched driving row,
  // turning the LEFT JOIN into a de-facto INNER.
  if (!isDriving && src.join?.on?.length) {
    const leaf = col.goldColumnName.split(".").pop() || "{column}";
    const cte = `lk_${leaf}`;
    const keyName: Record<string, string> =
      { INVENTORY_ITEM_ID: "iid", ORGANIZATION_ID: "oid" };
    const used = new Set<string>();
    const keys: string[] = [];
    const terms: string[] = [];
    src.join.on.forEach((o, i) => {
      const lc = (o.leftColumn || "{column}").toUpperCase();
      const rc = (o.rightColumn || o.leftColumn || "{column}").toUpperCase();
      let k = keyName[lc] ?? `k${i + 1}`;
      if (used.has(k)) k = `k${i + 1}`;   // two pairs sharing a left column
      used.add(k);
      keys.push(`${rc.endsWith("_ID") ? `CAST(${rc} AS BIGINT)` : rc} AS ${k}`);
      const d = alias(o.leftTable || drv || "{driving_table}");
      terms.push(
        `${lc.endsWith("_ID") ? `CAST(${d}.${lc} AS BIGINT)` : `${d}.${lc}`} = ${cte}.${k}`);
    });
    const tf = applyTransform(col.transform, transforms, `${cte}.${leaf}`);
    return [
      `-- lookup dimension (the orgs-CTE shape): max() per key, so a 1:N lookup`,
      `-- can never fan the driving rows out; the filter lives INSIDE the CTE,`,
      `-- so unmatched driving rows survive the LEFT JOIN as NULL`,
      `${cte} AS (`,
      `  SELECT ${keys.join(", ")},`,
      `         max(${src.column || "{column}"}) AS ${leaf}`,
      `  FROM ${short(src.table || "{table}")}`,
      ...(src.filter ? [`  WHERE ${src.filter}`] : []),
      `  GROUP BY ${keys.map((_, i) => i + 1).join(", ")}`,
      `)`,
      `-- attaches after the existing dimension joins:`,
      `FROM ${drv ? `${short(drv)} ${alias(drv)}` : "{driving_table}"}`,
      `LEFT JOIN ${cte} ON ${terms.join("\n  AND ")}`,
      ...(src.join.type === "inner"
        ? [`-- declared INNER join: the dimension shape compiles as LEFT only (UIFPE-106)`]
        : []),
      `-- final SELECT:`,
      `${tf.expr} AS ${col.goldColumnName}`,
      ...(tf.note ? [tf.note] : []),
    ].join("\n");
  }
  let fromJoin: string[];
  if (declared) {
    fromJoin = [
      `FROM ${short(drv)} ${alias(drv)}`,
      ...joinLines(sources.filter((s, i) => i === 0 || sameTable(s.table, src.table))),
    ];
  } else if (isDriving || !drv || !src.table) {
    fromJoin = [`FROM ${short(src.table || drv || "{driving_table}")}${src.table ? ` ${t}` : ""}`];
  } else {
    fromJoin = [
      `FROM ${short(drv)} ${alias(drv)}`,
      `LEFT JOIN ${short(src.table)} ${t} ON /* attachment not defined — use "${short(src.table)} is a lookup" above */`,
    ];
  }
  const tf = applyTransform(col.transform, transforms, `${t}.${src.column}`);
  const lines = [
    `SELECT ${tf.expr} AS ${col.goldColumnName}`,
    ...fromJoin,
    ...(src.filter ? [`WHERE ${src.filter}`] : []),
    ...(tf.note ? [tf.note] : []),
  ];
  return lines.join("\n");
}

function conditionalSql(col: ConditionalColumn, sources: SourceDecl[],
                        rowGroups: Record<string, RowGroupDef>,
                        transforms: Record<string, TransformDef>,
                        entityKey?: EntityKeyPair[]): string {
  const disc = col.discriminator.column || "{discriminator}";
  const parsed = col.branches.map((b) => ({ b, c: parseCondition(b.condition),
    rs: b.scope ? resolveScope(b.scope, rowGroups) : null }));
  const named = parsed.filter((p) => p.c.op !== "else").map((p) => p.c);
  const lines: string[] = [
    `-- one CTE per branch; each filters on BOTH the branch condition and its`,
    `-- row filter, so exactly one branch can have rows per entity —`,
    `-- COALESCE below is a strict branch pick, never a fallback`,
  ];
  parsed.forEach(({ b, c }, i) => {
    const a = `br${i + 1}_${alias(b.source.table || "src")}`;
    // A branch's own transform applies BEFORE the pick, to this branch's
    // expression only (the after-pick transform wraps the COALESCE below).
    const tf = applyTransform(b.transform, transforms,
      `max(${b.source.column || "{column}"})`);
    const keys = keyExpr(entityKey, b.source.join);
    lines.push(
      `${a} AS (` +
        (parsed[i].rs ? `  -- receives: ${scopeLabel(parsed[i].rs!)}` : ""),
      `  SELECT ${keys}, ${indent(tf.expr, "  ")} AS ${col.goldColumnName}`,
      `  FROM ${short(b.source.table || "{table}")}`,
      ...(b.source.join?.on?.length ? [
        `  -- attaches: ${(b.source.join.type ?? "left").toUpperCase()} JOIN ON ` +
        b.source.join.on.map((jn) =>
          `${short(jn.leftTable)}.${jn.leftColumn} = ${short(b.source.table)}.${jn.rightColumn}`)
          .join(" AND ")] : []),
      `  WHERE ${branchWhere(disc, c, named)}` +
        (b.source.filter ? ` AND ${b.source.filter}` : ""),
      `  GROUP BY ${keys}`,
      ...(tf.note ? [`  ${tf.note}`] : []),
      `),`);
  });
  // Final pick. Scoped (Proliferate) branches fan out via an apply-time CASE
  // on the scope predicate — exactly the shape the hand-written mapping SQL
  // uses today; unscoped branches join the outer COALESCE strict pick.
  const pick = (i: number) =>
    `br${i + 1}_${alias(parsed[i].b.source.table || "src")}.${col.goldColumnName}`;
  const scoped = parsed.map((p, i) => ({ ...p, i })).filter((p) => p.rs);
  // named branches first, ELSE last — the compiler's pick order (and
  // product.sql's digital-first hand convention); intakes declare ELSE
  // first, so without this sort the preview and the compiled SQL would
  // show opposite COALESCE argument orders
  const unscoped = parsed.map((p, i) => ({ ...p, i })).filter((p) => !p.rs)
    .sort((a, b) => Number(a.c.op === "else") - Number(b.c.op === "else"));
  // The after-pick transform wraps the value the winning branch produced —
  // exactly product.sql's coverImage shape: CASE WHEN substring(COALESCE(...)).
  let pickExpr: string;
  if (scoped.length) {
    const sibs = scoped.map((p) => p.rs!);
    // Complement groups and inline "all remaining rows" both take the CASE's
    // ELSE slot — matching the hand-written mapping SQL's shape.
    const whens = scoped.filter((p) => p.rs!.op !== "else" && !p.rs!.not);
    const other = scoped.find((p) => p.rs!.op === "else" || p.rs!.not);
    const caseExpr = "CASE " +
      whens.map((p) => `WHEN ${scopeWhere(p.rs!, sibs)} THEN ${pick(p.i)}`).join(" ") +
      (other ? ` ELSE ${pick(other.i)}` : "") + " END";
    lines.push(`-- final SELECT (scoped branches fan out by row group):`);
    pickExpr = `COALESCE(${[caseExpr, ...unscoped.map((p) => pick(p.i))].join(", ")})`;
  } else {
    lines.push(`-- final SELECT:`);
    pickExpr = `COALESCE(${unscoped.map((p) => pick(p.i)).join(", ")})`;
  }
  const tf = applyTransform(col.transform, transforms, pickExpr);
  lines.push(`${tf.expr} AS ${col.goldColumnName}`, ...(tf.note ? [tf.note] : []));
  void sources;
  return lines.join("\n");
}

function nestedSql(col: NestedColumn, sources: SourceDecl[],
                   drivingTable: string | null | undefined,
                   transforms: Record<string, TransformDef>): string {
  const sortField = col.fields.find((f) => f.sortKey)?.name ?? "{sort_key}";
  // Fields may span tables: the first field's table drives the array; every
  // other table joins at the array's grain (ON from the task's source graph).
  const tables = [...new Set(col.fields.map((f) => f.source?.table)
    .filter((t): t is string => !!t))];
  const drv = tables[0] || sources[0]?.table || drivingTable || "{table}";
  const multi = tables.length > 1;
  // ON pairs: a task carried on the pre-UIFPE-219 shape may still hold them
  // in its source graph, but the Sources step declares TABLES ONLY now — a
  // multi-table array attaches at the column's own join key, which is why the
  // fallback below points at the column rather than at a step that no longer
  // has a join editor.
  const joinFor = (tb: string): string | undefined => {
    const own = sources.find((s) => sameTable(s.table, tb))?.joins;
    if (own?.length)
      return own.map((j) =>
        `${alias(j.leftTable)}.${j.leftColumn} = ${alias(tb)}.${j.rightColumn}`).join(" AND ");
    const rev = sources.find((s) => s.joins?.some((j) => sameTable(j.leftTable, tb)));
    if (rev)
      return rev.joins.filter((j) => sameTable(j.leftTable, tb)).map((j) =>
        `${alias(rev.table)}.${j.rightColumn} = ${alias(tb)}.${j.leftColumn}`).join(" AND ");
  };
  const atJoinKey = col.joinKey
    ? `/* at this list's join key: ${col.joinKey} */`
    : "/* set this list's join key on the column */";
  const joins = tables.slice(1).map((tb) =>
    `  LEFT JOIN ${short(tb)} ${alias(tb)} ON ${joinFor(tb) || atJoinKey}`);
  const qual = (f: NestedColumn["fields"][number]) => {
    if (!f.source) return "CAST(NULL AS STRING)"; // declared, no Silver source yet
    const x = (multi && f.source.table ? `${alias(f.source.table)}.` : "") +
      (f.source.column || "{column}");
    const tf = applyTransform(f.transform, transforms, x);
    return tf.note ? `${tf.expr} /* ${tf.note.replace(/^-- /, "")} */`
                   : indent(tf.expr, "           ");
  };
  return [
    `-- 1-to-many: collect rows into a stably-ordered array (stable order =>`,
    `-- stable channel hashes)`,
    `${col.goldColumnName}_agg AS (`,
    `  SELECT {join_key},`,
    `         array_sort(collect_list(named_struct(`,
    ...col.fields.map((f) => `           '${f.name}', ${qual(f)},`),
    `         )), /* by ${sortField} */) AS ${col.goldColumnName}`,
    `  FROM ${short(drv)}${multi ? ` ${alias(drv)}` : ""}`,
    ...joins,
    ...(col.filter ? [`  WHERE ${col.filter}`] : []),
    `  GROUP BY {join_key}`,
    `)`,
  ].join("\n");
}
