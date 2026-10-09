import { useEffect, useState } from "react";
import { WizardState, SourceDecl, EntityKeyPair, entityKeyProblems } from "@/lib/types";
import { findMaster } from "@/lib/masterSchema";
import { fetchEntityStatus, statusConfigured, EntityStatus }
  from "@/lib/entityStatus";
import { DOMAINS, SETTLED_DOMAINS, REGISTERED } from "@/lib/repo";
import { SILVER_DATABASES, qualifyTable, columnsFor, envTemplateSilver } from "@/lib/silverCatalog";
import { pipelineFor, DEFAULT_FREQUENCY, DEFAULT_WEEKLY_DAYS, defaultStartTimeFor }
  from "@/lib/pipelines";

// The add flow starts its join graph from the entity's registered driving
// table (qualified via the Silver catalog when the snapshot knows it).
const seedDriving = (drivingTable: string, domain?: string | null): SourceDecl[] => [{
  table: qualifyTable(drivingTable, domain ?? undefined) ?? drivingTable,
  eav: false, joinType: "left", joins: [],
}];

// Step 2 — domain and gold table, both discovered from the repo's registered
// mappings (any domain that registers a *.mapping.json appears here), plus
// "introduce a new table" as a first-class choice.
export function DestinationStep({ state, patch }:
  { state: WizardState; patch: (p: Partial<WizardState>) => void }) {
  const tables = REGISTERED.filter((r) => r.domain === state.domain);
  const reg = tables.find((r) => r.table === state.destinationTable);

  const pickModify = (name: string) => {
    const col = reg?.columns.find((c) => c.goldColumnName === name);
    patch({ targetColumn: name, columns: col ? [structuredClone(col)] : [] });
  };

  return (
    <>
      <h2>Gold table</h2>
      <p className="sub">Which domain, which gold table?</p>

      <div className="row2">
        <div className="field">
          <label>Domain</label>
          <select value={state.domain ?? ""}
            onChange={(e) => {
              const d = e.target.value || null;
              const hasMappings = d !== null && DOMAINS.includes(d);
              // A domain with no registered mappings has no existing tables —
              // its first wizard task necessarily introduces one.
              // Changing domain restarts the decision chain — nothing
              // authored for another destination may leak through.
              // Schedules are per ENTITY (gold_id), and no table is picked
              // yet — seed the platform default; picking a table below
              // re-seeds that entity's own registered schedule.
              patch({ domain: d, destinationTable: hasMappings ? null : "",
                      drivingTable: null, entityKey: [], drivingFilter: "",
                      frequency: DEFAULT_FREQUENCY,
                      startTime: defaultStartTimeFor(DEFAULT_FREQUENCY),
                      weeklyDays: [...DEFAULT_WEEKLY_DAYS],
                      isNewTable: !hasMappings && d !== null,
                      intent: hasMappings ? null : "add", targetColumn: null,
                      sources: [], columns: [] });
            }}>
            <option value="">— select —</option>
            <optgroup label="with registered mappings">
              {DOMAINS.map((d) => <option key={d} value={d}>{d}</option>)}
            </optgroup>
            <optgroup label="no mappings yet — first table starts here">
              {SETTLED_DOMAINS.filter((d) => !DOMAINS.includes(d)).map((d) => (
                <option key={d} value={d}>{d}</option>
              ))}
            </optgroup>
          </select>
          <p className="help">
            The settled UIF domains. A domain beyond these needs
            framework-owner approval and a create-domain.sh stamp first — the
            review on your submission is where that gets decided.
          </p>
        </div>
        <div className="field">
          <label>Gold table</label>
          {state.isNewTable ? (
            <>
              <input type="text"
                placeholder={`e.g. gold_${state.domain ?? "domain"}.entity`}
                value={state.destinationTable ?? ""}
                onChange={(e) => patch({ destinationTable: e.target.value || null })} />
              <p className="help">
                A NEW table compiles end to end (UIFPE-260): the Review step
                shows every generated file — the query, the registered
                schema, the runtime manifest and the schedule — and the
                submission goes through the same pull-request review as any
                other change. Nothing deploys without a merge.
              </p>
              {tables.length > 0 && (
                <button className="btn" style={{ marginTop: 8 }}
                  onClick={() => patch({ isNewTable: false, destinationTable: null, drivingFilter: "",
                                         drivingTable: null, intent: null })}>
                  &#8249; never mind — pick an existing table instead
                </button>
              )}
            </>
          ) : (
            <select value={state.destinationTable ?? ""}
              onChange={(e) => {
                if (e.target.value === "__new__") {
                  // A NEW table has no registered columns to conflict with —
                  // authored/imported work travels with it (that's how "clone
                  // an existing mapping to a new table" works: Import, then
                  // retarget here). With nothing authored, start clean.
                  const keep = state.columns.length > 0;
                  patch({ isNewTable: true, destinationTable: null,
                          intent: "add", targetColumn: null,
                          drivingTable: keep ? state.drivingTable : null,
                          entityKey: keep ? state.entityKey : [],
                          rowGroups: keep ? state.rowGroups : {},
                          transforms: keep ? state.transforms : {},
                          sources: keep ? state.sources : [],
                          columns: keep ? state.columns : [] });
                } else {
                  // Switching between EXISTING tables resets — columns
                  // authored for one destination don't apply to another.
                  // The schedule re-seeds from THIS entity's pipeline
                  // (gold_id = table stem): two tables in one domain run on
                  // different clocks, and carrying the other one's time into
                  // the envelope would silently reschedule it at merge.
                  const picked = tables.find((t) => t.table === e.target.value);
                  const pipe = pipelineFor(
                    e.target.value.split(".").pop() ?? "");
                  const freq = pipe?.frequency ?? DEFAULT_FREQUENCY;
                  patch({ destinationTable: e.target.value || null,
                          drivingTable: picked?.drivingTable ?? null,
                          entityKey: picked?.entityKey ?? [],
                          rowGroups: picked?.rowGroups ?? {},
                          transforms: picked?.transforms ?? {},
                          frequency: freq,
                          startTime: pipe?.startTime
                            ?? defaultStartTimeFor(freq),
                          weeklyDays: pipe?.weeklyDays ?? [...DEFAULT_WEEKLY_DAYS],
                          targetColumn: null, sources: [], columns: [] });
                }
              }}>
              <option value="">— select —</option>
              {tables.map((t) => (
                <option key={t.table} value={t.table}>
                  {t.table} (existing — {t.columns.length} columns)
                </option>
              ))}
              <option value="__new__">＋ Introduce a new table…</option>
            </select>
          )}
        </div>
      </div>

      {/* Entity cards with live status (UIFPE-219): what the user is about
          to touch — lifecycle, size, last run — before they touch it. */}
      {!state.isNewTable && tables.length > 0 && (
        <div className="field">
          <div className="collist">
            {tables.map((t) => (
              <EntityCard key={t.table} reg={t}
                picked={state.destinationTable === t.table}
                onPick={() => {
                  // re-clicking the current table is a no-op — a card is a
                  // one-click control, and the reset it carries must never
                  // fire by accident; a genuine SWITCH with authored work
                  // asks first, because the reset is silent and persisted
                  if (state.destinationTable === t.table) return;
                  if (state.columns.length > 0 && !window.confirm(
                    `Switch to ${t.table}? The ${state.columns.length} `
                    + "column(s) authored for the current table don't apply "
                    + "there and will be cleared.")) return;
                  // same re-seed as the dropdown: the schedule belongs to
                  // THIS entity's pipeline, not to whatever was picked before
                  const pipe = pipelineFor(t.table.split(".").pop() ?? "");
                  const freq = pipe?.frequency ?? DEFAULT_FREQUENCY;
                  patch({ destinationTable: t.table,
                          drivingTable: t.drivingTable ?? null,
                          entityKey: t.entityKey ?? [],
                          rowGroups: t.rowGroups ?? {},
                          transforms: t.transforms ?? {},
                          frequency: freq,
                          startTime: pipe?.startTime
                            ?? defaultStartTimeFor(freq),
                          weeklyDays: pipe?.weeklyDays ?? [...DEFAULT_WEEKLY_DAYS],
                          targetColumn: null, sources: [], columns: [] });
                }} />
            ))}
          </div>
        </div>
      )}

      {/* ── the gold table's IDENTITY: its spine and its key (UIFPE-107).
             Every column's logic attaches to this table on this key — it was
             invisible here until the 2026-08-10 UX review asked "where is it
             defined?". Existing tables show it; new tables DECLARE it. ── */}
      {!state.isNewTable && reg && (
        <div className="field">
          <label>Table identity — what every attribute attaches to</label>
          <div className="checks" style={{ flexWrap: "wrap", gap: 8 }}>
            <span className="pill ok">
              primary silver table: {reg.drivingTable ?? "not declared"}
            </span>
            <span className="pill">
              entity key:{" "}
              {reg.entityKey.length
                ? reg.entityKey.map((p) => `${p.goldColumn} ← ${p.drivingColumn}`).join("  ·  ")
                : "not declared"}
            </span>
          </div>
          <p className="help">
            One gold row per row of the primary table; the entity key is the
            gold table's primary key, mapped from the primary table's columns.
            Lookups attach to it per attribute in the Columns step (the join editor prefills
            these key columns).
          </p>
        </div>
      )}
      {state.isNewTable && (
        <NewTableIdentity state={state} patch={patch} />
      )}

      {!state.isNewTable && state.destinationTable && (
        <div className="field">
          <label>What are you doing?</label>
          <select value={state.intent ?? ""}
            onChange={(e) => {
              const intent = (e.target.value || null) as any;
              patch({ intent, targetColumn: null, columns: [],
                      // add flow: the join graph starts from the entity's
                      // registered driving table; modify flow declares none.
                      sources: intent === "add" && state.drivingTable
                        ? seedDriving(state.drivingTable, state.domain) : [] });
            }}>
            <option value="">— select —</option>
            <option value="add">Add new attribute(s)</option>
            <option value="modify">Modify an existing attribute</option>
          </select>
        </div>
      )}

      {state.intent === "modify" && reg && (
        <div className="field">
          <label>Which attribute? ({reg.columns.length} registered)</label>
          <div className="collist">
            {reg.columns.map((c) => (
              <div key={c.goldColumnName}
                className={"colrow" + (state.targetColumn === c.goldColumnName ? " selected" : "")}
                onClick={() => pickModify(c.goldColumnName)}>
                <span>{c.goldColumnName}</span>
                <span className="kind">{c.kind}{"dataType" in c && c.dataType ? ` · ${c.dataType}` : ""}</span>
              </div>
            ))}
          </div>
          <p className="help">
            Modifying changes values on historical rows — the submission
            includes the F3 refresh plan (hash recalculation, consumer
            notification) automatically.
          </p>
        </div>
      )}
    </>
  );
}

// A NEW gold table declares its identity up front: the primary (driving)
// Silver table it mirrors, and the entity key — its primary key, mapped
// column-by-column from that table. Both are required before Next; the
// Sources step seeds the table list from the primary table and the Columns
// step's join editors prefill the key.
function NewTableIdentity({ state, patch }:
  { state: WizardState; patch: (p: Partial<WizardState>) => void }) {
  const drivingCols = state.drivingTable ? columnsFor(state.drivingTable) : [];
  const problems = state.drivingTable
    ? entityKeyProblems(state.entityKey, drivingCols)
    : ["pick the primary silver table first"];
  const setPair = (i: number, p: EntityKeyPair) =>
    patch({ entityKey: state.entityKey.map((x, k) => (k === i ? p : x)) });

  // camelCase suggestion from the driving column: INVENTORY_ITEM_ID → inventoryItemId
  const suggest = (col: string) => col.toLowerCase()
    .replace(/_([a-z0-9])/g, (_, c: string) => c.toUpperCase());

  const [db, table] = (() => {
    const t = state.drivingTable ?? "";
    const i = t.lastIndexOf(".");
    return i > 0 ? [t.slice(0, i), t.slice(i + 1)] : ["", t];
  })();
  const dbEntry = SILVER_DATABASES.find((d) => d.database === db);

  return (
    <div className="field" style={{ borderLeft: "3px solid var(--light-purple)", paddingLeft: 14 }}>
      <label>New table identity — primary silver table and entity key</label>
      <div className="row2">
        <select value={db}
          onChange={(e) => patch({ drivingTable: null, entityKey: [], drivingFilter: "",
            ...(e.target.value ? { drivingTable: `${e.target.value}.` } : {}) })}>
          <option value="">— silver database —</option>
          {SILVER_DATABASES.map((d) => (
            <option key={d.database} value={d.database}>
              {d.database} ({d.tables.length})
            </option>
          ))}
        </select>
        <select value={table} disabled={!dbEntry}
          onChange={(e) => patch({
            drivingTable: e.target.value ? `${db}.${e.target.value}` : `${db}.`,
            entityKey: [], drivingFilter: "" })}>
          <option value="">— primary table —</option>
          {(dbEntry?.tables ?? []).map((t) => (
            <option key={t.name} value={t.name}>{t.name}</option>
          ))}
        </select>
      </div>
      <p className="help">
        The gold table gets ONE ROW PER ROW of this table — pick the table
        that IS the entity, not a lookup. Other tables are added in the
        Sources step and joined per attribute.
      </p>
      {state.drivingTable && !state.drivingTable.endsWith(".")
        && envTemplateSilver(state.drivingTable) === null && (
        <p className="note">
          This database's name carries no environment marker (dev/uat/prod),
          so the table cannot be templated per environment — pick the same
          table from an echo_* database, or hand the task to engineering.
        </p>
      )}

      {state.drivingTable && !state.drivingTable.endsWith(".") && (
        <>
          <label style={{ fontWeight: 700, fontSize: 12.5 }}>
            Row filter (optional) — which source rows belong to this entity
          </label>
          <input type="text"
            placeholder="e.g. PPID IS NOT NULL"
            value={state.drivingFilter ?? ""}
            onChange={(e) => patch({ drivingFilter: e.target.value })} />
          <p className="help">
            A SQL condition over {state.drivingTable.split(".").pop()}'s
            columns. Rows that don't match never reach the gold table — and
            rows that stop matching are removed by the nightly reconcile (the
            filter becomes the query's WHERE and the reconcile's
            source-of-truth filter together).
          </p>
        </>
      )}

      {state.drivingTable && !state.drivingTable.endsWith(".") && (
        <>
          <label style={{ fontWeight: 700, fontSize: 12.5 }}>
            Entity key — the gold table's primary key, mapped from{" "}
            {state.drivingTable.split(".").pop()}
          </label>
          {state.entityKey.map((p, i) => (
            <div key={i} style={{ display: "grid", gap: 8, margin: "6px 0",
                                  alignItems: "center", gridTemplateColumns:
                                  "minmax(0,1fr) 24px minmax(0,1fr) 40px" }}>
              <input type="text" value={p.goldColumn}
                placeholder="gold column, e.g. inventoryItemId"
                onChange={(e) => setPair(i, { ...p, goldColumn: e.target.value })} />
              <span style={{ textAlign: "center", fontWeight: 700 }}>←</span>
              {drivingCols.length ? (
                <select value={p.drivingColumn}
                  onChange={(e) => setPair(i, {
                    drivingColumn: e.target.value,
                    goldColumn: p.goldColumn || suggest(e.target.value) })}>
                  <option value="">— driving-table column —</option>
                  {drivingCols.map((c) => <option key={c} value={c}>{c}</option>)}
                </select>
              ) : (
                <input type="text" value={p.drivingColumn}
                  placeholder="driving-table column"
                  onChange={(e) => setPair(i, { ...p,
                    drivingColumn: e.target.value.trim().toUpperCase() })} />
              )}
              <button className="btn" style={{ padding: "3px 8px" }}
                onClick={() => patch({ entityKey:
                  state.entityKey.filter((_, k) => k !== i) })}>✕</button>
            </div>
          ))}
          <button className="btn" style={{ marginTop: 4 }}
            onClick={() => patch({ entityKey: [...state.entityKey,
              { goldColumn: "", drivingColumn: "" }] })}>
            ＋ Add key column
          </button>
          <p className="help">
            Composite keys = multiple rows (product uses two:
            inventoryItemId ← INVENTORY_ITEM_ID, organizationId ←
            ORGANIZATION_ID). The scaffold turns this into the runtime
            key_mapping; join editors prefill it for every lookup.
          </p>
        </>
      )}
      {problems.length > 0 && <div className="note">{problems.join(" · ")}</div>}
    </div>
  );
}

// One entity, one card (UIFPE-219): the repository's facts render at once
// (lifecycle, columns, key); the LIVE half — row count and the last run —
// arrives from the status service when it is wired, and simply stays absent
// when it is not. A fetch failure degrades to the static card, never an
// error state: the card is orientation, not monitoring.
function EntityCard({ reg, picked, onPick }:
  { reg: (typeof REGISTERED)[number]; picked: boolean; onPick: () => void }) {
  const [live, setLive] = useState<EntityStatus | null>(null);
  const entity = reg.table.split(".").pop() ?? reg.table;
  const master = findMaster(reg.domain, entity);
  const lifecycle = String(master?.metadata?.lifecycle ?? "draft");

  useEffect(() => {
    // no pipeline = no gold table to count and no runs to report — skip the
    // doomed fetch instead of burning an Athena execution on a known 400
    if (!statusConfigured() || !pipelineFor(entity)) return;
    let gone = false;
    fetchEntityStatus(entity, `uif_echo_{env}_gold_${reg.domain}`, entity)
      .then((s) => { if (!gone) setLive(s); })
      .catch(() => {});   // static card is the fallback, not an error
    return () => { gone = true; };
  }, [reg.domain, entity]);

  const ago = (iso: string) => {
    const h = Math.round((Date.now() - new Date(iso).getTime()) / 3600000);
    return h < 1 ? "under an hour ago" : h < 48 ? `${h}h ago`
      : `${Math.round(h / 24)}d ago`;
  };

  return (
    <div className={"colrow" + (picked ? " keyrow" : "")}
      style={{ cursor: "pointer" }} onClick={onPick}
      role="button" aria-pressed={picked} tabIndex={0}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onPick(); }
      }}>
      <div>
        <strong>{reg.table}</strong>{" "}
        <span className={"pill" + (lifecycle === "active" ? " ok" : "")}>
          {lifecycle === "active" ? "live" : lifecycle}
        </span>
        {picked && <span className="pill ok" style={{ marginLeft: 6 }}>selected</span>}
        <div className="kind">
          {reg.columns.length} columns · key: {(reg.entityKey ?? [])
            .map((k) => k.goldColumn).join(" + ") || "—"}
          {live && <> · {live.rowCount.toLocaleString()} rows</>}
          {live?.lastRun && <> · last run{" "}
            {(live.lastRun.state ?? "unknown").toLowerCase()}{" "}
            {ago(live.lastRun.startedOn)}</>}
        </div>
      </div>
      <span className="kind">{picked ? "✓" : "pick →"}</span>
    </div>
  );
}
