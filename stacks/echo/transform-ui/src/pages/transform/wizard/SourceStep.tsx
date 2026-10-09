import { useEffect, useState } from "react";
import { WizardState, SourceDecl, sourceProblems, sameTable } from "@/lib/types";
import { knownSourceTables } from "@/lib/repo";
import { tableSummaries } from "@/lib/sourceGrain";
import { sourceStanding } from "@/lib/manifests";
import { SILVER_DATABASES, CAPTURED_AT, findTable, qualifyTable } from "@/lib/silverCatalog";

// Step 2 — Sources (UIFPE-219): the Silver TABLES this task reads, nothing
// more. How each attribute attaches — join type, ON pairs, filters — is
// column-level logic and lives in the Columns step's editor, because the
// same table legitimately attaches differently per attribute. The first
// table is the PRIMARY table: it decides which rows the gold table has.
export function SourceStep({ state, patch }:
  { state: WizardState; patch: (p: Partial<WizardState>) => void }) {
  const [draft, setDraft] = useState("");

  // When the entity's primary table is known (registered mapping, or an
  // imported one), it IS the first entry — seed it so the user never
  // re-derives their own spine.
  useEffect(() => {
    if (!state.sources.length && state.drivingTable)
      patch({ sources: [{ table: qualifyTable(state.drivingTable, state.domain ?? undefined)
                            ?? state.drivingTable,
                          eav: false, joinType: "left", joins: [] }] });
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const suggestions = state.domain ? knownSourceTables(state.domain) : [];
  const unused = suggestions.filter((s) => !state.sources.some((x) => sameTable(x.table, s)));
  const problems = sourceProblems(state.sources, state.drivingTable);

  // Change-impact sentences derive from how the task's columns attach — in a
  // fresh task nothing reads a table yet, so the card simply says where the
  // attachment will be declared.
  const summaries = tableSummaries({
    drivingTable: state.drivingTable,
    entityKey: state.entityKey,
    columns: state.columns,
  });
  const summaryFor = (table: string) =>
    summaries.find((t) => sameTable(t.table, table));

  // Standing comes from ONE definition shared with the standalone Sources
  // view (lib/manifests.ts) — the two screens asked this separately before
  // and disagreed behind identical-looking pills.
  const standing = (table: string) => sourceStanding(table, state.domain ?? "");

  const add = (table: string) => {
    const t = table.trim();
    if (!t || state.sources.some((x) => sameTable(x.table, t))) return;
    // EAV extension tables are recognizable by name — the hint reminds the
    // column forms to require a row filter; it is derived, never asked.
    patch({ sources: [...state.sources,
      { table: t, eav: /EXT_B$/i.test(t.split(".").pop() ?? ""),
        joinType: "left", joins: [] }] });
    setDraft("");
  };
  const remove = (t: string) =>
    patch({ sources: state.sources.filter((x) => x.table !== t),
            // A NEW table's spine is the user's choice — removing it must
            // also release drivingTable, or validation keeps demanding the
            // removed table as the first source.
            ...(state.isNewTable && sameTable(t, state.drivingTable)
              ? { drivingTable: null } : {}) });

  return (
    <>
      <h2>Sources</h2>
      <p className="sub">
        {state.intent === "modify"
          ? <>The attribute's registered sources are already known. Add a
              table here only if you are re-pointing it somewhere new — how it
              attaches is declared on the attribute itself, in the next step.</>
          : <>Which Silver tables does this read? Just the tables — how each
              attribute attaches to them is declared per attribute in the
              next step, because one table can serve different attributes
              differently.</>}
      </p>

      <div className="field">
        <label>Add a source table</label>
        <div style={{ display: "flex", gap: 8 }}>
          <input type="text" value={draft} placeholder="e.g. echo_{env}_silver_finance.gl_balances"
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && add(draft)} />
          <button className="btn" onClick={() => add(draft)}>Add</button>
        </div>
      </div>

      {unused.length > 0 && (
        <div className="field">
          <label>Already used by {state.domain}'s registered mappings</label>
          <div className="checks" style={{ flexWrap: "wrap" }}>
            {unused.map((s) => (
              <button key={s} className="btn" style={{ fontWeight: 500 }}
                onClick={() => add(qualifyTable(s, state.domain ?? undefined) ?? s)}>＋ {s.split(".").pop()}</button>
            ))}
          </div>
        </div>
      )}

      <CatalogBrowser onAdd={add} declared={state.sources.map((s) => s.table)} />

      {state.sources.map((s, i) => {
        const stands = standing(s.table);
        const summary = summaryFor(s.table);
        const isPrimary = i === 0;
        const registeredPrimary = isPrimary && !state.isNewTable
          && sameTable(s.table, state.drivingTable);
        return (
          <div key={s.table} className="card" style={{ marginBottom: 12, padding: 16 }}>
            <div style={{ display: "flex", justifyContent: "space-between",
                          alignItems: "center", gap: 10 }}>
              <strong style={{ display: "flex", gap: 8, alignItems: "center",
                               flexWrap: "wrap" }}>
                {s.table}
                {isPrimary && (
                  <span className="pill ok">
                    Primary table{registeredPrimary ? " (registered for this entity)" : ""}
                  </span>
                )}
                {stands === "unknown" && (
                  <span className="pill warn" title="This name matches no table in the Silver catalog — check the spelling, or ask for the table to be ingested.">
                    no such table in Silver
                  </span>
                )}
                {stands === "not-wired" && (
                  <span className="pill" title="The table exists in Silver but is not a registered runtime source for this entity yet — attributes reading it ship with empty values until the feed is wired in.">
                    not wired in yet
                  </span>
                )}
                {summary && summary.usedBy > 0 && (
                  <span className="pill" title={summary.examples.join(", ")}>
                    read by {summary.usedBy} attribute{summary.usedBy === 1 ? "" : "s"}
                  </span>
                )}
              </strong>
              {!registeredPrimary && (
                <button className="btn" style={{ padding: "2px 10px" }}
                  onClick={() => remove(s.table)}>✕</button>
              )}
            </div>
            <p className="help" style={{ marginTop: 6, marginBottom: 0 }}>
              {isPrimary
                ? "Decides which rows exist — one changed row affects its own row."
                : summary && summary.usedBy > 0
                  ? summary.sentence
                  : "How it reaches each row is declared on each attribute that reads it (next step)."}
            </p>
          </div>
        );
      })}

      {problems.length > 0 && <div className="note">{problems.join(" · ")}</div>}
    </>
  );
}

// Browse EVERY Silver database in the Glue catalog — cross-domain sources and
// cross-catalog joins are expected, so nothing here is scoped to the
// destination domain.
function CatalogBrowser({ onAdd, declared }:
  { onAdd: (table: string) => void; declared: string[] }) {
  const [db, setDb] = useState("");
  const [q, setQ] = useState("");
  const selected = SILVER_DATABASES.find((d) => d.database === db);
  const tables = (selected?.tables ?? [])
    .filter((t) => t.name.toLowerCase().includes(q.toLowerCase()));

  return (
    <div className="field">
      <label>
        Browse the Silver layer ({SILVER_DATABASES.length} databases · snapshot{" "}
        {CAPTURED_AT.slice(0, 16).replace("T", " ")} UTC — refresh via
        scripts/snapshot_silver_catalog.py; live API coming)
      </label>
      <div className="row2">
        <select value={db} onChange={(e) => { setDb(e.target.value); setQ(""); }}>
          <option value="">— pick a database —</option>
          {SILVER_DATABASES.map((d) => (
            <option key={d.database} value={d.database}>
              {d.database} ({d.tables.length})
            </option>
          ))}
        </select>
        <input type="text" value={q} placeholder="filter tables…"
          disabled={!db} onChange={(e) => setQ(e.target.value)} />
      </div>
      {selected && (
        <div className="collist" style={{ marginTop: 8, maxHeight: 220 }}>
          {tables.map((t) => {
            const qualified = `${selected.database}.${t.name}`;
            const added = declared.some((d) => sameTable(d, qualified));
            return (
              <div key={t.name} className="colrow" style={{ cursor: "default" }}>
                <span>{t.name} <span className="kind">· {t.columns.length} cols</span></span>
                <button className="btn" style={{ padding: "2px 10px" }} disabled={added}
                  onClick={() => onAdd(qualified)}>
                  {added ? "added" : "＋ add"}
                </button>
              </div>
            );
          })}
          {tables.length === 0 && (
            <div className="colrow" style={{ cursor: "default" }}>
              <span className="kind">no tables match</span>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
