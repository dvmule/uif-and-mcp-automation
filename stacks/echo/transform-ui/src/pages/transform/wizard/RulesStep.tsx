import { useState, type Dispatch, type SetStateAction } from "react";
import {
  WizardState, MappingColumn, ScalarColumn, ConditionalColumn, NestedColumn,
  DataType, Channels, SourceRef, SourceJoin, BranchScope, ColumnContext,
  columnProblems, taskProblems, sameTable, entityKeyJoin, rememberedJoin,
  convertColumnKind,
} from "@/lib/types";
import { knownSourceTables, REGISTERED } from "@/lib/repo";
import { columnsFor } from "@/lib/silverCatalog";
import { parseCondition, formatCondition, BranchCondition } from "@/lib/conditions";
import { verdictFor, verdictSentence } from "@/lib/classify";
import { previewColumnSql } from "@/lib/sqlPreview";
import { contractLines, isQueryBlock, wrapPreview } from "@/lib/derivations";
import { derivedSqlProblems, emptyDerivation } from "@/lib/derivedSql";
import { manifestView } from "@/lib/manifests";
import { draftDerivedSql } from "@/lib/entryMethods";
import { probeConfigured, runGrainProbe, ProbeResult } from "@/lib/grainProbe";
import { TransformEditor } from "./TransformEditor";

// Step 4 — the column builder. One task authors a BATCH of columns, each
// through the form for its kind — a direct translation of the intake
// template's three sheets (1. Scalar, 2. Conditional, 3. Nested) into UI,
// with the same invariants enforced live (exactly one sortKey, discriminator
// required, camelCase names). Domain-agnostic by construction: every field
// here exists in gold-attribute-mapping.schema.json, nothing else does.

const TYPES: DataType[] = ["string", "integer", "number", "float", "boolean", "date", "timestamp"];

const blank = {
  scalar: (): ScalarColumn => ({
    goldColumnName: "", businessDescription: "", kind: "scalar",
    dataType: "string", source: { table: "", column: "", filter: null },
    transform: null, channels: { event: false, message: false, api: true },
    nullable: true, notes: null }),
  conditional: (): ConditionalColumn => ({
    goldColumnName: "", businessDescription: "", kind: "conditional",
    dataType: "string", discriminator: { table: "", column: "" },
    channels: { event: false, message: false, api: true },
    branches: [{ condition: "ELSE", source: { table: "", column: "", filter: null },
                 transform: null, fallback: "returnNull", notes: null }],
    notes: null }),
  nested: (): NestedColumn => ({
    goldColumnName: "", businessDescription: "", kind: "nested",
    joinKey: "", filter: null, channels: { event: false, message: false, api: true },
    fields: [{ name: "", type: "string", sortKey: true, source: { table: "", column: "" } }],
    notes: null }),
};

export function RulesStep({ state, patch }:
  { state: WizardState; patch: (p: Partial<WizardState>) => void }) {
  // UIFPE-108: the page IS the table structure. `editing` addresses a row of
  // the grid; its logic opens in a MODAL holding a DRAFT — cancel discards,
  // so a half-edit can never corrupt the column (the live-edit cards it
  // replaces had no such safety). `isNew` marks a column created BY opening
  // the modal: cancelling one removes the row again, so cancel can never
  // strand an invalid blank column in the grid.
  const [editing, setEditing] = useState<{ index: number; isNew: boolean } | null>(
    state.intent === "modify" && state.columns.length
      ? { index: 0, isNew: false } : null);
  const [filter, setFilter] = useState("");
  // UIFPE-219: bulk grid for scalars — a 60-column entity must not require
  // 60 modal round-trips. Modal editing remains for conditional/nested/
  // derived, whose shapes need the full forms.
  const [bulkScalars, setBulkScalars] = useState(false);
  const modifying = state.intent === "modify";

  // One validation pass powers the grid's badges AND the summary counts —
  // imported tasks carry dozens of columns, so totals stay visible.
  const ctx: ColumnContext = { sources: state.sources,
                               drivingTable: state.drivingTable,
                               rowGroups: state.rowGroups,
                               transforms: state.transforms };
  const problemsByCol = state.columns.map((c) => columnProblems(c, ctx));
  const invalidCount = problemsByCol.filter((p) => p.length).length;
  const registeredCols = REGISTERED.find(
    (r) => r.table === state.destinationTable)?.columns;
  const registeredNames = state.intent === "add" && !state.isNewTable
    ? (registeredCols?.map((c) => c.goldColumnName) ?? [])
    : [];
  // The OUTCOME chip (UIFPE-219): what submitting this column MEANS, on
  // every row rather than hidden in the modal — the two plain sentences
  // are the wizard's whole vocabulary for the AUTO/PLAN split.
  const registeredKinds = Object.fromEntries(
    (registeredCols ?? []).map((c) => [c.goldColumnName, c.kind]));
  const outcomeOf = (col: MappingColumn) => {
    const v = verdictFor(col, { drivingTable: state.drivingTable ?? "",
                                intent: state.intent, registeredKinds });
    return { auto: v.verdict === "AUTO",
             chip: v.verdict === "AUTO" ? "updates automatically"
                                        : "engineer reviews",
             detail: verdictSentence(v) };
  };
  const crossProblems = taskProblems(state.columns, registeredNames);
  // Key columns are usually ALSO authored columns (product's inventoryItemId
  // is both the key and a scalar) — badge those rows and float them to the
  // top rather than duplicating them; only keys with no column of their own
  // get a synthetic row saying where they are declared.
  const keyNames = new Set(state.entityKey.map((k) => k.goldColumn));
  const keyRank = (name: string) => {
    const i = state.entityKey.findIndex((k) => k.goldColumn === name);
    return i < 0 ? state.entityKey.length : i;
  };
  const visible = state.columns
    .map((col, i) => ({ col, i }))
    .filter(({ col }) => !filter
      || col.goldColumnName.toLowerCase().includes(filter.toLowerCase()))
    .sort((a, b) => {
      const ak = keyNames.has(a.col.goldColumnName), bk = keyNames.has(b.col.goldColumnName);
      if (ak !== bk) return ak ? -1 : 1;
      if (ak && bk) return keyRank(a.col.goldColumnName) - keyRank(b.col.goldColumnName);
      return a.i - b.i;
    });
  const unmappedKeys = state.entityKey.filter((k) =>
    !state.columns.some((c) => c.goldColumnName === k.goldColumn));
  // in bulk mode the scalars live in the grid above — the main table (and
  // its "showing N" count) covers only what it actually renders
  const mainRows = visible.filter(({ col }) =>
    !bulkScalars || modifying || col.kind !== "scalar");

  const setCol = (i: number, col: MappingColumn) =>
    patch({ columns: state.columns.map((c, j) => (j === i ? col : c)) });
  const addCol = (kind: keyof typeof blank) => {
    patch({ columns: [...state.columns, blank[kind]()] });
    setEditing({ index: state.columns.length, isNew: true });   // straight into the modal
  };
  const removeCol = (i: number) => {
    patch({ columns: state.columns.filter((_, j) => j !== i) });
    setEditing(null);
  };

  // The 1:1 source view for the grid — what this column reads, at a glance.
  const sourceOf = (col: MappingColumn): string => {
    const one = (s?: { table?: string; column?: string }) =>
      s?.table ? `${s.table.split(".").pop()}.${s.column || "?"}` : "—";
    if (col.kind === "scalar") return one(col.source);
    if (col.kind === "conditional") {
      const tables = [...new Set(col.branches.map((b) => b.source?.table).filter(Boolean))];
      return col.branches.length === 0 ? "—"
        : `${one(col.branches[0].source)}${col.branches.length > 1
            ? ` +${col.branches.length - 1} branch${col.branches.length > 2 ? "es" : ""}` : ""}`
        + (tables.length > 1 ? "" : "");
    }
    const f = col.fields?.[0];
    return `${one(f?.source ?? undefined)}${(col.fields?.length ?? 0) > 1
      ? ` +${col.fields.length - 1} field${col.fields.length > 2 ? "s" : ""}` : ""}`;
  };

  const typeOf = (col: MappingColumn): string =>
    col.kind === "nested"
      ? `array<${col.fields?.length === 1 ? col.fields[0].type : "struct"}>`
      : ("dataType" in col ? col.dataType : "—");

  return (
    <>
      <h2>Columns</h2>
      <p className="sub">
        {modifying
          ? <>Editing <strong>{state.targetColumn}</strong> — registered values shown; change what needs changing.</>
          : <>The gold table's columns. The grid is the structure; <strong>Edit</strong>{" "}
             opens that column's full logic — source, filter, join, transform,
             branches, fan-out.</>}
      </p>

      {/* type-ahead for every free-text table input on this step */}
      <datalist id="known-source-tables">
        {[...new Set([
          ...knownSourceTables(state.domain ?? ""),
          ...state.sources.map((s) => s.table),
        ])].map((t) => <option key={t} value={t} />)}
      </datalist>

      {crossProblems.length > 0 && (
        <div className="note">{crossProblems.join(" · ")}</div>
      )}

      {state.columns.length > 8 && (
        <div className="field">
          <input type="text" value={filter} placeholder="Filter columns by name…"
            onChange={(e) => setFilter(e.target.value)} />
        </div>
      )}

      {!modifying
        && (state.columns.some((c) => c.kind === "scalar") || bulkScalars) && (
        // never on modify: the grid has no name lock, and a one-column
        // modify gains nothing from bulk editing
        <div className="checks" style={{ marginBottom: 8 }}>
          <button className={"btn" + (bulkScalars ? " primary" : "")}
            onClick={() => setBulkScalars((v) => !v)}>
            {bulkScalars ? "▦ Done with bulk edit" : "▦ Bulk edit scalars"}
          </button>
        </div>
      )}

      {bulkScalars && !modifying
        && <ScalarGrid state={state} patch={patch} filter={filter} />}

      <table className="structure">
        <thead>
          <tr>
            <th style={{ width: "20%" }}>Gold column</th>
            <th style={{ width: "10%" }}>Data type</th>
            <th style={{ width: "10%" }}>Type</th>
            <th>Source (table.column)</th>
            <th style={{ width: "15%" }}>Outcome</th>
            <th style={{ width: "9%" }}>Status</th>
            <th style={{ width: "11%" }}></th>
          </tr>
        </thead>
        <tbody>
          {/* Keys with no column of their own — the grain is visible even
              before the key columns are authored (new-table flow). */}
          {unmappedKeys.map((k) => (
            <tr key={`key-${k.goldColumn}`} className="keyrow">
              <td><strong>{k.goldColumn}</strong>{" "}
                <span className="pill" title="entity key — the gold table's primary key">⚷ key</span>
              </td>
              <td>—</td>
              <td>key</td>
              <td className="src">
                {(state.drivingTable ?? "").split(".").pop()}.{k.drivingColumn}
              </td>
              <td>—</td>
              <td><span className="pill ok">declared</span></td>
              <td style={{ textAlign: "right", fontSize: 12, color: "var(--ink-soft)" }}>
                at Gold table
              </td>
            </tr>
          ))}
          {mainRows.map(({ col, i }) => {
            const problems = problemsByCol[i];
            const isKey = keyNames.has(col.goldColumnName);
            return (
              <tr key={i} className={isKey ? "keyrow" : undefined}>
                <td><strong>{col.goldColumnName || "(unnamed)"}</strong>
                  {isKey && <> <span className="pill"
                    title="entity key — this column is part of the gold table's primary key">⚷ key</span></>}
                </td>
                <td>{typeOf(col)}</td>
                <td><span className="pill">{col.kind}</span></td>
                <td className="src">{sourceOf(col)}</td>
                <td>{(() => {
                  const o = outcomeOf(col);
                  return <span className={"pill" + (o.auto ? " ok" : "")}
                    title={o.detail}>{o.chip}</span>;
                })()}</td>
                <td>{problems.length
                  ? <span className="pill warn">{problems.length} to fix</span>
                  : <span className="pill ok">valid</span>}</td>
                <td style={{ textAlign: "right", whiteSpace: "nowrap" }}>
                  <button className="btn" style={{ padding: "2px 10px" }}
                    onClick={() => setEditing({ index: i, isNew: false })}>Edit</button>{" "}
                  {!modifying && !isKey && (
                    <button className="btn" style={{ padding: "2px 8px" }}
                      title="remove this column" onClick={() => removeCol(i)}>✕</button>
                  )}
                </td>
              </tr>
            );
          })}
          {state.columns.length === 0 && (
            <tr><td colSpan={7} style={{ color: "var(--ink-soft)" }}>
              No columns yet — add the first one below.
            </td></tr>
          )}
        </tbody>
      </table>

      {state.columns.length > 0 && (
        <p className="sub" style={{ margin: "10px 0 0" }}>
          {state.columns.length} column{state.columns.length === 1 ? "" : "s"} ·{" "}
          {state.columns.length - invalidCount} valid
          {invalidCount > 0 && <> · <strong>{invalidCount} to fix</strong></>}
          {filter && <> · showing {mainRows.length} matching “{filter}”</>}
        </p>
      )}

      {!modifying && (
        <div className="field" style={{ marginTop: 14 }}>
          <label>Add a column</label>
          <div className="checks">
            <button className="btn" onClick={() => addCol("scalar")}>＋ Scalar (1:1 value)</button>
            <button className="btn" onClick={() => addCol("conditional")}>＋ Conditional (discriminator + branches)</button>
            <button className="btn" onClick={() => addCol("nested")}>＋ Nested (1-to-many array)</button>
          </div>
          <p className="help">
            Not sure? Start scalar — the type is switchable inside the editor
            without losing your work.
          </p>
        </div>
      )}

      {editing !== null && state.columns[editing.index] && (
        <ColumnModal key={editing.index} column={state.columns[editing.index]}
          index={editing.index} state={state} patch={patch} ctx={ctx}
          lockName={modifying}
          onSave={(c) => { setCol(editing.index, c); setEditing(null); }}
          onCancel={() => editing.isNew
            ? removeCol(editing.index)      // a cancelled add leaves no row
            : setEditing(null)} />
      )}
    </>
  );
}

// ── the attribute editor: ONE column's entire logic, in a modal ─────────────
// Holds a DRAFT: every edit lands here, Save commits, Cancel discards. The
// per-kind forms are the same components the inline cards used — relocated,
// not rewritten — so the SQL preview and validation behave identically.
function ColumnModal({ column, index, state, patch, ctx, lockName,
                      onSave, onCancel }:
  { column: MappingColumn; index: number; state: WizardState;
    patch: (p: Partial<WizardState>) => void; ctx: ColumnContext;
    lockName: boolean; onSave: (c: MappingColumn) => void;
    onCancel: () => void }) {
  const [draft, setDraft] = useState<MappingColumn>(() => structuredClone(column));
  const [pendingKind, setPendingKind] = useState<MappingColumn["kind"] | null>(null);
  // Which branch survives a FROM-conditional conversion (UIFPE-108 scope 3:
  // "asks which branch survives"). -1 = the ELSE branch, the function's own
  // default; losses re-derive live so the prompt always lists exactly what
  // the CURRENT choice drops.
  const [survivor, setSurvivor] = useState(-1);
  const problems = columnProblems(draft, ctx);
  const pending = pendingKind !== null
    ? convertColumnKind(draft, pendingKind, survivor) : null;
  const survivorChoice = pendingKind !== null && draft.kind === "conditional"
    && draft.branches.length > 1;
  const defaultSurvivor = draft.kind === "conditional"
    ? Math.max(0, draft.branches.findIndex(
        (b) => (b.condition ?? "").trim().toUpperCase() === "ELSE"))
    : 0;

  // Kind switching converts rather than resets. A LOSSY conversion asks
  // first — which branch survives, and exactly what would be dropped.
  const requestKind = (to: MappingColumn["kind"]) => {
    if (to === draft.kind) { setPendingKind(null); return; }
    const { column: converted, losses } = convertColumnKind(draft, to);
    if (losses.length) { setPendingKind(to); setSurvivor(-1); return; }
    setDraft(converted); setPendingKind(null);
  };
  const confirmKind = () => {
    if (!pending) return;
    setDraft(pending.column);
    setPendingKind(null); setSurvivor(-1);
  };

  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true"
      onClick={(e) => e.target === e.currentTarget && onCancel()}>
      <div className="modal-panel">
        <div className="modal-head">
          <h2 style={{ margin: 0, fontSize: 19 }}>
            {draft.goldColumnName || "New column"}{" "}
            <span className="pill">{draft.kind}</span>{" "}
            {problems.length
              ? <span className="pill warn">{problems.length} to fix</span>
              : <span className="pill ok">valid</span>}
          </h2>
          <button className="btn" onClick={onCancel} title="discard changes">✕</button>
        </div>

        <div className="field">
          <label>Column type — switchable; your work converts across</label>
          <div className="checks">
            {(["scalar", "conditional", "nested"] as const).map((k) => (
              <button key={k} className={"btn" + (draft.kind === k ? " primary" : "")}
                onClick={() => requestKind(k)}>
                {k === "scalar" ? "Scalar (1:1 value)"
                  : k === "conditional" ? "Conditional (branches)"
                  : "Nested (1-to-many array)"}
              </button>
            ))}
          </div>
          {pending && (
            <div className="note">
              {survivorChoice && draft.kind === "conditional" && (
                <div className="field" style={{ marginBottom: 8 }}>
                  <label>Which branch survives as the column's source?</label>
                  <select value={survivor >= 0 ? survivor : defaultSurvivor}
                    onChange={(e) => setSurvivor(Number(e.target.value))}>
                    {draft.branches.map((b, i) => (
                      <option key={i} value={i}>
                        {b.condition || "(blank)"} ← {(b.source?.table ?? "").split(".").pop() || "?"}.{b.source?.column || "?"}
                      </option>
                    ))}
                  </select>
                </div>
              )}
              <strong>Switching to {pendingKind} drops:</strong>{" "}
              {pending.losses.join(" · ")}
              <div className="checks" style={{ marginTop: 8 }}>
                <button className="btn primary" onClick={confirmKind}>
                  Convert anyway
                </button>
                <button className="btn" onClick={() => { setPendingKind(null); setSurvivor(-1); }}>
                  Keep {draft.kind}
                </button>
              </div>
            </div>
          )}
        </div>

        <CommonFields col={draft} set={setDraft} lockName={lockName} />
        {draft.kind === "scalar" && <ScalarForm col={draft} set={setDraft} state={state} patch={patch} />}
        {draft.kind === "conditional" && <ConditionalForm col={draft} set={setDraft} state={state} patch={patch} />}
        {draft.kind === "nested" && <NestedForm col={draft} set={setDraft} state={state} patch={patch} />}

        {problems.length > 0 && <div className="note">{problems.join(" · ")}</div>}

        <DerivedPanel draft={draft} set={setDraft} state={state} />

        <details style={{ marginTop: 10 }} open>
          <summary style={{ cursor: "pointer", fontWeight: 700, fontSize: 12.5 }}>
            {draft.derivation && isQueryBlock(draft.derivation)
              ? "The query as it runs — your SELECT, spliced verbatim"
              : "Compiled SQL preview — generated deterministically from this form (no AI interpretation)"}
          </summary>
          <pre className="code" style={{ marginTop: 8 }}>
            {(() => {
              // A preview bug must never take the modal down — degrade to a
              // message and keep the form editable.
              try {
                // a derived column's value comes from ITS query — showing
                // the form-rendered lookup here would preview SQL the
                // compiler never generates for it
                if (draft.derivation && isQueryBlock(draft.derivation))
                  return wrapPreview(
                    draft.goldColumnName.split(".").pop() ?? draft.goldColumnName,
                    draft.derivation, draft.kind);
                return previewColumnSql(draft, state.sources, state.drivingTable,
                  state.rowGroups, state.transforms, state.entityKey);
              } catch (e) {
                return `-- preview unavailable while this column is incomplete\n-- (${e instanceof Error ? e.message : e})`;
              }
            })()}
          </pre>
        </details>

        <div className="modal-foot">
          <span style={{ flex: 1, fontSize: 12.5, color: "var(--ink-soft)" }}>
            Column {index + 1} · Cancel discards every change made here.
          </span>
          <button className="btn" onClick={onCancel}>Cancel</button>
          <button className="btn primary" onClick={() => onSave(draft)}>
            Save column
          </button>
        </div>
      </div>
    </div>
  );
}

// ── shared fields (every kind) ──────────────────────────────────────────────

function CommonFields({ col, set, lockName }:
  { col: MappingColumn; set: (c: MappingColumn) => void; lockName: boolean }) {
  const ch = col.channels;
  return (
    <>
      <div className="row2">
        <div className="field">
          <label>Gold column name (camelCase)</label>
          <input type="text" value={col.goldColumnName} disabled={lockName}
            placeholder="e.g. returnsWindow"
            onChange={(e) => set({ ...col, goldColumnName: e.target.value })} />
        </div>
        {"dataType" in col && (
          <div className="field">
            <label>Data type</label>
            <select value={col.dataType}
              onChange={(e) => set({ ...col, dataType: e.target.value as DataType })}>
              {TYPES.map((t) => <option key={t}>{t}</option>)}
            </select>
          </div>
        )}
      </div>
      <div className="field">
        <label>Business description</label>
        <input type="text" value={col.businessDescription}
          placeholder="What this attribute means to the business"
          onChange={(e) => set({ ...col, businessDescription: e.target.value })} />
      </div>
      <div className="row2">
        <div className="field">
          <label>Channels</label>
          <div className="checks">
            {(["event", "message", "api"] as (keyof Channels)[]).map((c) => (
              <label key={c}>
                <input type="checkbox" checked={ch[c]}
                  onChange={(e) => set({ ...col, channels: { ...ch, [c]: e.target.checked } })} />
                {c}
              </label>
            ))}
          </div>
          <p className="help">
            Who receives this attribute: event = real-time notifications,
            message = messaging payloads, api = the full API response. It is
            always stored in the table regardless.
          </p>
        </div>
      </div>
    </>
  );
}

// ── per-kind forms — sheet 1, 2, 3 of the template ─────────────────────────

// Table selector that NEVER hides the current value: registered mappings may
// reference tables outside the task's declared sources (modify flow, seeded
// columns) — those render as an extra option instead of a silently blank
// select.
function TablePick({ value, onChange, state }:
  { value: string; onChange: (t: string) => void; state: WizardState }) {
  const declared = state.sources.map((s) => s.table);
  if (!declared.length)
    return <input type="text" value={value} placeholder="source table"
      list="known-source-tables"
      onChange={(e) => onChange(e.target.value)} />;
  return (
    <select value={value} onChange={(e) => onChange(e.target.value)}>
      <option value="">— pick a table —</option>
      {value && !declared.includes(value) && (
        <option value={value}>{value} (registered source)</option>
      )}
      {declared.map((t) => <option key={t} value={t}>{t}</option>)}
    </select>
  );
}

// Catalog-backed column selector for the Columns step — a dropdown of the
// table's real columns when the Silver snapshot knows it (registered values
// outside the snapshot stay selectable), free text otherwise.
function ColumnPick({ table, value, onChange, placeholder }:
  { table: string; value: string; onChange: (v: string) => void;
    placeholder: string }) {
  const cols = columnsFor(table);
  if (!cols.length)
    return <input type="text" value={value} placeholder={placeholder}
      style={{ minWidth: 0 }}
      onChange={(e) => onChange(e.target.value.trim().toUpperCase())} />;
  return (
    <select value={value} style={{ minWidth: 0 }}
      onChange={(e) => onChange(e.target.value)}>
      <option value="">— pick a column —</option>
      {value && !cols.includes(value) && (
        <option value={value}>{value} (registered)</option>
      )}
      {cols.map((c) => <option key={c} value={c}>{c}</option>)}
    </select>
  );
}

function SourceFields({ src, set, state, label = "Source" }:
  { src: { table: string; column: string; filter?: string | null };
    set: (s: any) => void; state: WizardState; label?: string }) {
  const eav = state.sources.find((s) => sameTable(s.table, src.table))?.eav;
  return (
    <div className="row2">
      <div className="field">
        <label>{label} table</label>
        <TablePick value={src.table} state={state}
          onChange={(table) => set({ ...src, table })} />
      </div>
      <div className="field">
        <label>{label} column {eav && <span className="pill warn">attribute rows — set a row filter</span>}</label>
        <ColumnPick table={src.table} value={src.column} placeholder="e.g. C_EXT_ATTR17"
          onChange={(column) => set({ ...src, column })} />
        {"filter" in src && (
          <input type="text" style={{ marginTop: 6 }} value={src.filter ?? ""}
            placeholder={eav ? "row filter, e.g. ATTR_GROUP_ID = 802 (required — this table holds many attribute rows per item)" : "row filter (optional), e.g. LANGUAGE = 'US'"}
            onChange={(e) => set({ ...src, filter: e.target.value || null })} />
        )}
      </div>
    </div>
  );
}

// The attachment of a lookup source to the entity — EDITABLE and structured
// (schema: sourceRef.join), so re-sourcing an existing attribute can change
// how it joins, not just which column it reads. Offered for any source table
// that is not one of the task's declared sources (those attach via the
// column's own join instead).
function JoinEditor({ src, set, state, noOfferFor = [], editingName }:
  { src: SourceRef; set: (s: SourceRef) => void; state: WizardState;
    noOfferFor?: string[];
    /** the column open in the modal — its own saved copy must not pose as
     *  the task's memory of this table */
    editingName?: string }) {
  const j = src.join;
  const driving = state.drivingTable ?? state.sources[0]?.table ?? "";
  const tShort = (src.table || "this table").split(".").pop();
  if (!j?.on?.length) {
    // The entity's own driving table never attaches to itself — no offer.
    if (!src.table || sameTable(src.table, driving)
        || state.sources.some((s) => sameTable(s.table, src.table))
        || noOfferFor.some((t) => sameTable(t, src.table)))
      return null;
    // UIFPE-219: if this task already attached the same table on another
    // column, that (possibly hand-tuned) join is the memory the offer
    // prefills; otherwise UIFPE-107's entity-key default (same column names
    // both sides, the PDH extension shape). Either way the user edits from
    // a filled form instead of typing key columns.
    const candidate = rememberedJoin(state.columns, src.table);
    const remembered = candidate && candidate.fromColumn !== editingName
      ? candidate : null;
    const prefill = remembered?.join
      ?? entityKeyJoin(state.entityKey, driving)
      ?? { type: "left" as const,
           on: [{ leftTable: driving, leftColumn: "", rightColumn: "" }] };
    return (
      <div className="field">
        <button className="btn" onClick={() => set({ ...src, join: prefill })}>
          ＋ {tShort} is a lookup — attach it to{" "}
          {driving ? driving.split(".").pop() : "the entity"}
          {remembered ? ` the way ${remembered.fromColumn} does`
            : state.entityKey.length ? " on the entity key" : ""}
        </button>
        <p className="help">
          Skip this only if {tShort} is the entity's own primary table.
          {remembered
            ? <>{" "}Prefills the join {remembered.fromColumn} already uses
                for {tShort} — adjust only if this column genuinely joins
                differently.</>
            : state.entityKey.length > 0 && <>{" "}Prefills{" "}
                {state.entityKey.map((p) => p.drivingColumn).join(" + ")} —
                adjust the ON pairs if this lookup keys differently.</>}
        </p>
      </div>
    );
  }
  const setJ = (join: SourceJoin | null) => set({ ...src, join });
  const setOn = (i: number, p: Partial<SourceJoin["on"][number]>) =>
    setJ({ ...j, on: j.on.map((x, k) => (k === i ? { ...x, ...p } : x)) });
  return (
    <div className="field" style={{ borderLeft: "3px solid var(--light-purple)", paddingLeft: 14 }}>
      <label>How {tShort} attaches to the entity</label>
      <select value={j.type ?? "left"} style={{ marginBottom: 8 }}
        onChange={(e) => setJ({ ...j, type: e.target.value as "left" | "inner" })}>
        <option value="left">LEFT JOIN — keep entity rows even with no {tShort} match</option>
        <option value="inner">INNER JOIN — drop entity rows with no {tShort} match</option>
      </select>
      {j.on.map((o, i) => (
        <div key={i} style={{ display: "grid", alignItems: "center", gap: 8,
                              marginBottom: 6, gridTemplateColumns:
                              "minmax(0,1fr) minmax(0,1fr) 18px minmax(0,1fr) 40px" }}>
          <input type="text" list="known-source-tables" value={o.leftTable}
            style={{ minWidth: 0 }}
            placeholder={driving ? `e.g. ${driving.split(".").pop()} (primary table)`
                                 : "the entity's primary table"}
            onChange={(e) => setOn(i, { leftTable: e.target.value })} />
          <ColumnPick table={o.leftTable} value={o.leftColumn} placeholder="its column"
            onChange={(leftColumn) => setOn(i, { leftColumn })} />
          <span style={{ textAlign: "center", fontWeight: 700 }}>=</span>
          <ColumnPick table={src.table} value={o.rightColumn} placeholder={`${tShort} column`}
            onChange={(rightColumn) => setOn(i, { rightColumn })} />
          <button className="btn" style={{ padding: "3px 8px" }} disabled={j.on.length === 1}
            onClick={() => setJ({ ...j, on: j.on.filter((_, k) => k !== i) })}>✕</button>
        </div>
      ))}
      <div className="checks">
        <button className="btn" onClick={() => setJ({ ...j,
          on: [...j.on, { leftTable: j.on[0]?.leftTable ?? "", leftColumn: "", rightColumn: "" }] })}>
          ＋ Add ON condition
        </button>
        <button className="btn" onClick={() => setJ(null)}>
          ✕ Not a lookup — remove the join
        </button>
      </div>
    </div>
  );
}

function ScalarForm({ col, set, state, patch }:
  { col: ScalarColumn; set: (c: ScalarColumn) => void; state: WizardState;
    patch: (p: Partial<WizardState>) => void }) {
  return (
    <>
      <SourceFields src={col.source} set={(source) => set({ ...col, source })} state={state} />
      <JoinEditor src={col.source} set={(source) => set({ ...col, source })}
        state={state} editingName={col.goldColumnName} />
      <TransformEditor value={col.transform} state={state} patch={patch}
        set={(transform) => set({ ...col, transform })} />
      <div className="field">
        <label>Nullable</label>
        <div className="checks">
          <label>
            <input type="checkbox" checked={col.nullable}
              onChange={(e) => set({ ...col, nullable: e.target.checked })} />
            value may be null
          </label>
        </div>
      </div>
    </>
  );
}

function ConditionalForm({ col, set, state, patch }:
  { col: ConditionalColumn; set: (c: ConditionalColumn) => void;
    state: WizardState; patch: (p: Partial<WizardState>) => void }) {
  const setBranch = (i: number, b: any) =>
    set({ ...col, branches: col.branches.map((x, j) => (j === i ? b : x)) });
  return (
    <>
      <div className="field">
        <label>Discriminator (table.column whose value picks the branch)</label>
        <input type="text"
          value={col.discriminator.table ? `${col.discriminator.table}.${col.discriminator.column}` : ""}
          placeholder="e.g. EGO_MTL_SY_ITEMS_EXT_B.ITEM_CATALOG_GROUP_ID"
          onChange={(e) => {
            const v = e.target.value;
            const i = v.lastIndexOf(".");
            const discriminator = i > 0
              ? { table: v.slice(0, i), column: v.slice(i + 1) }
              : { table: v, column: "" };
            // Branches usually read the discriminator's own table — prefill
            // any branch that hasn't picked a table yet (never overwrite).
            set({ ...col, discriminator,
              branches: col.branches.map((b) => b.source.table ? b
                : { ...b, source: { ...b.source, table: discriminator.table } }) });
          }} />
      </div>
      {col.branches.map((b, i) => {
        const cond = parseCondition(b.condition);
        const setCond = (c: BranchCondition) =>
          setBranch(i, { ...b, condition: formatCondition(c) });
        return (
        <div key={i} style={{ borderLeft: "3px solid var(--light-purple)", paddingLeft: 14, marginBottom: 10 }}>
          <div style={{ display: "grid", gap: 10, gridTemplateColumns:
                        "185px minmax(0,1fr) minmax(0,1fr) minmax(0,1fr)" }}>
            <div className="field">
              <label>Branch {i + 1} — when</label>
              <select value={cond.op}
                onChange={(e) => {
                  const op = e.target.value as BranchCondition["op"];
                  setCond(op === "else" ? { op, values: [], label: null }
                                        : { ...cond, op });
                }}>
                <option value="equals">value equals</option>
                <option value="in">value is one of</option>
                <option value="else">ELSE — whatever other branches didn't take</option>
              </select>
            </div>
            <div className="field">
              <label>Value{cond.op === "in" ? "s (comma-separated)" : ""}</label>
              <input type="text" disabled={cond.op === "else"}
                value={cond.values.join(",")} placeholder="e.g. 10002"
                onChange={(e) => setCond({ ...cond,
                  values: e.target.value.split(",").map((v) => v.trim()).filter(Boolean) })} />
            </div>
            <div className="field">
              <label>Label (human name, optional)</label>
              <input type="text" disabled={cond.op === "else"}
                value={cond.label ?? ""} placeholder="e.g. Digital Catalog"
                onChange={(e) => setCond({ ...cond, label: e.target.value || null })} />
            </div>
            <div className="field">
              <label>If branch yields no row</label>
              <select value={b.fallback}
                onChange={(e) => setBranch(i, { ...b, fallback: e.target.value })}>
                <option value="returnNull">leave the value null</option>
                <option value="fallbackToElse">fall back to the ELSE branch</option>
              </select>
            </div>
          </div>
          <SourceFields src={b.source} set={(source) => setBranch(i, { ...b, source })} state={state} />
          <JoinEditor src={b.source} set={(source) => setBranch(i, { ...b, source })}
            editingName={col.goldColumnName}
            state={state} noOfferFor={[col.discriminator.table]} />
          <div className="row2">
            <TransformEditor value={b.transform} state={state} patch={patch}
              label={`Branch ${i + 1} transformation (optional — applies to this branch only)`}
              set={(transform) => setBranch(i, { ...b, transform })} />
            <div className="field">
              <label>Fan-out (optional)</label>
              {!b.scope ? (
                <button className="btn" onClick={() => setBranch(i, { ...b,
                  scope: { table: null, column: "", op: "equals", values: [], label: null } })}>
                  ＋ this value fans out to a row group
                </button>
              ) : (
                <button className="btn" onClick={() => setBranch(i, { ...b, scope: null })}>
                  ✕ no fan-out — value applies where its join reaches
                </button>
              )}
            </div>
          </div>
          {b.scope && (
            <ScopeEditor scope={b.scope} state={state} patch={patch}
              set={(scope) => setBranch(i, { ...b, scope })} />
          )}
          {col.branches.length > 1 && (
            <button className="btn" style={{ padding: "3px 12px" }}
              onClick={() => set({ ...col, branches: col.branches.filter((_, j) => j !== i) })}>
              ✕ remove branch
            </button>
          )}
        </div>
      );})}
      <button className="btn" style={{ marginBottom: 12 }} onClick={() => set({
        ...col, branches: [...col.branches,
          { condition: "",
            source: { table: col.discriminator.table || state.sources[0]?.table || "",
                      column: "", filter: null },
            transform: null, fallback: "returnNull", notes: null }] })}>
        ＋ Add branch
      </button>
      <TransformEditor value={col.transform} state={state} patch={patch}
        label="Transformation AFTER the branch pick (optional — applies to whichever branch won, e.g. URL normalization)"
        set={(transform) => set({ ...col, transform })} />
    </>
  );
}

// A Proliferate branch's fan-out group: pick a NAMED group defined in the
// mapping (single source of truth — this is how users know what groups
// exist), or define one inline; inline definitions can be saved back as a
// named group so the next column reuses them.
function ScopeEditor({ scope, set, state, patch }:
  { scope: BranchScope; set: (s: BranchScope) => void; state: WizardState;
    patch: (p: Partial<WizardState>) => void }) {
  const groups = state.rowGroups ?? {};
  const names = Object.keys(groups);
  const [saveName, setSaveName] = useState("");
  const g = scope.group ? groups[scope.group] : null;

  const saveAsGroup = () => {
    const name = saveName.trim();
    if (!/^[a-zA-Z][a-zA-Z0-9_]*$/.test(name) || scope.op === "else" || !scope.column) return;
    patch({ rowGroups: { ...groups, [name]: {
      table: scope.table ?? null, column: scope.column,
      op: (scope.op ?? "equals") as "equals" | "in" | "startsWith",
      values: scope.values ?? [], description: scope.label ?? null } } });
    set({ group: name });
    setSaveName("");
  };

  return (
    <div className="field" style={{ borderLeft: "3px solid var(--light-purple)", paddingLeft: 14 }}>
      <label>Fan-out group — which rows receive this branch's value</label>
      <select value={scope.group ?? "__inline__"} style={{ marginBottom: 8 }}
        onChange={(e) => set(e.target.value === "__inline__"
          ? { table: null, column: "", op: "equals", values: [], label: null }
          : { group: e.target.value })}>
        {names.map((n) => {
          const d = groups[n];
          return <option key={n} value={n}>
            {"complementOf" in d
              ? `${n} — everything NOT in ${d.complementOf}`
              : `${n} — ${d.column} ${d.op === "startsWith" ? "starts with" : d.op} ${d.values.join(", ")}`}
          </option>;
        })}
        <option value="__inline__">define the group here…</option>
      </select>

      {scope.group ? (
        <p className="help">
          {g ? <>Named group from this mapping — one definition, every column
            that fans out by it uses the same rule.{g.description ? <> {g.description}</> : null}</>
            : <>⚠ “{scope.group}” is not defined in this mapping.</>}
        </p>
      ) : (
        <>
          <div style={{ display: "grid", gap: 8, alignItems: "center", gridTemplateColumns:
                        "minmax(0,1fr) 200px minmax(0,1fr) minmax(0,1fr)" }}>
            <input type="text" style={{ minWidth: 0 }}
              placeholder="row-group column, e.g. MTL_PARAMETERS.ORGANIZATION_CODE"
              value={scope.table ? `${scope.table}.${scope.column}` : scope.column ?? ""}
              onChange={(e) => {
                const v = e.target.value;
                const d = v.lastIndexOf(".");
                set({ ...scope, table: d > 0 ? v.slice(0, d) : null,
                      column: d > 0 ? v.slice(d + 1) : v });
              }} />
            <select value={scope.op ?? "equals"}
              onChange={(e) => set({ ...scope, op: e.target.value as any,
                values: e.target.value === "else" ? [] : scope.values ?? [] })}>
              <option value="equals">value equals</option>
              <option value="in">value is one of</option>
              <option value="startsWith">value starts with</option>
              <option value="else">all remaining rows</option>
            </select>
            <input type="text" style={{ minWidth: 0 }} disabled={scope.op === "else"}
              placeholder={scope.op === "startsWith" ? "prefix, e.g. 01" : "values (comma-separated)"}
              value={(scope.values ?? []).join(",")}
              onChange={(e) => set({ ...scope,
                values: e.target.value.split(",").map((v) => v.trim()).filter(Boolean) })} />
            <input type="text" style={{ minWidth: 0 }}
              placeholder="meaning, e.g. non-UK orgs"
              value={scope.label ?? ""}
              onChange={(e) => set({ ...scope, label: e.target.value || null })} />
          </div>
          {scope.op !== "else" && (
            <div className="checks" style={{ marginTop: 8 }}>
              <input type="text" style={{ width: 220 }} value={saveName}
                placeholder="name it to reuse, e.g. ukOrgs"
                onChange={(e) => setSaveName(e.target.value)} />
              <button className="btn" disabled={!/^[a-zA-Z][a-zA-Z0-9_]*$/.test(saveName.trim()) || !scope.column}
                onClick={saveAsGroup}>
                Save as a named group
              </button>
            </div>
          )}
        </>
      )}
      <p className="help">
        Rendered as the apply-time CASE: rows matching this group get THIS
        branch's value; a sibling branch scoped to "all remaining rows"
        catches everything else.
      </p>
    </div>
  );
}

function NestedForm({ col, set, state, patch }:
  { col: NestedColumn; set: (c: NestedColumn) => void; state: WizardState;
    patch: (p: Partial<WizardState>) => void }) {
  const setField = (i: number, f: any) =>
    set({ ...col, fields: col.fields.map((x, j) => (j === i ? f : x)) });
  return (
    <>
      <div className="field">
        <label>Join key (how array rows attach to the entity)</label>
        <input type="text" value={col.joinKey}
          placeholder="e.g. INVENTORY_ITEM_ID → item (fans out to all orgs)"
          onChange={(e) => set({ ...col, joinKey: e.target.value })} />
      </div>
      <div className="field">
        <label>Shared row filter (optional)</label>
        <input type="text" value={col.filter ?? ""}
          placeholder="e.g. ATTR_GROUP_ID = 1121"
          onChange={(e) => set({ ...col, filter: e.target.value || null })} />
      </div>
      <div className="field">
        <label>Fields inside each element — exactly one is the sort key. Fields
          may come from DIFFERENT tables (joined per list entry).</label>
        {col.fields.map((f, i) => (
          <div key={i} style={{ marginBottom: 6 }}>
            <div style={{ display: "grid", gap: 8, alignItems: "center",
                          gridTemplateColumns:
                          "minmax(0,1fr) 100px minmax(0,1fr) minmax(0,1fr) 60px 44px 40px" }}>
              <input type="text" value={f.name} placeholder="field name"
                onChange={(e) => setField(i, { ...f, name: e.target.value })} />
              <select value={f.type}
                onChange={(e) => setField(i, { ...f, type: e.target.value })}>
                {TYPES.map((t) => <option key={t}>{t}</option>)}
              </select>
              {/* source may be null: declared with no Silver source yet */}
              <TablePick value={f.source?.table ?? ""} state={state}
                onChange={(table) => setField(i, { ...f, source: { column: "", ...f.source, table } })} />
              <ColumnPick table={f.source?.table ?? ""} value={f.source?.column ?? ""}
                placeholder="source column"
                onChange={(column) => setField(i, { ...f, source:
                  { table: "", ...f.source, column } })} />
              <label style={{ fontWeight: 600, fontSize: 12 }}>
                <input type="radio" name={`sortkey-${col.goldColumnName || "new"}`}
                  checked={f.sortKey}
                  onChange={() => set({ ...col, fields: col.fields.map((x, j) =>
                    ({ ...x, sortKey: j === i })) })} />
                {" "}sort
              </label>
              {f.transform == null ? (
                <button className="btn" style={{ padding: "3px 6px" }}
                  title="add a transformation to this field"
                  onClick={() => setField(i, { ...f, transform: { cases: [], steps: [] } })}>
                  ƒx
                </button>
              ) : <span />}
              <button className="btn" style={{ padding: "3px 8px" }} disabled={col.fields.length === 1}
                onClick={() => set({ ...col, fields: col.fields.filter((_, j) => j !== i) })}>✕</button>
            </div>
            {f.transform != null && (
              <div style={{ marginTop: 6 }}>
                <TransformEditor value={f.transform} state={state} patch={patch}
                  label={`Transformation for ${f.name || `field ${i + 1}`}`}
                  set={(transform) => setField(i, { ...f, transform })} />
              </div>
            )}
          </div>
        ))}
        <button className="btn" onClick={() => set({ ...col, fields: [...col.fields,
          { name: "", type: "string", sortKey: false,
            source: { table: col.fields[0]?.source?.table || state.sources[0]?.table || "",
                      column: "" } }] })}>
          ＋ Add field
        </button>
      </div>
    </>
  );
}

// ── Derived (SQL) authoring — ADR-0008, UIFPE-219 ──────────────────────────
// The query IS the contract: the author writes (or AI-drafts) the SELECT,
// declares which output column identifies the entity, and the compiler
// splices it verbatim. Checks here are the browser's advisory mirror of the
// Python battery — the real refusals run at submit and in CI, so the panel
// says what will be refused while the author is still looking at it.
function DerivedPanel({ draft, set, state }:
  { draft: MappingColumn;
    set: Dispatch<SetStateAction<MappingColumn>>;
    state: WizardState }) {
  const [aiOpen, setAiOpen] = useState(false);
  const [aiText, setAiText] = useState("");
  const [aiBusy, setAiBusy] = useState(false);
  const [aiError, setAiError] = useState<string | null>(null);
  const [probe, setProbe] = useState<ProbeResult | null>(null);
  const [probeBusy, setProbeBusy] = useState(false);
  const [probeError, setProbeError] = useState<string | null>(null);

  const name = draft.goldColumnName.split(".").pop() ?? draft.goldColumnName;
  const block = draft.derivation;
  const setBlock = (b: typeof block) => {
    // a verdict is about the EXACT text that was tested — any change to the
    // block invalidates it, or a green "one row per entity" pill would keep
    // vouching for a query that no longer exists
    setProbe(null); setProbeError(null);
    set({ ...draft, derivation: b } as MappingColumn);
  };

  if (!block) {
    return (
      <div className="field" style={{ marginTop: 10 }}>
        <button className="btn" disabled={draft.goldColumnName.includes(".")}
          title={draft.goldColumnName.includes(".")
            ? "a struct member cannot carry its own query — derive the parent"
            : undefined}
          onClick={() => setBlock(emptyDerivation(name, state.entityKey))}>
          ＋ This value needs its own query (derived)
        </button>
        <p className="help" style={{ marginTop: 6 }}>
          For values a single lookup cannot reach — self-joins, best-of-many
          picks, multi-hop chains. You write the SELECT; it runs exactly as
          written, joined back one row per entity.
        </p>
      </div>
    );
  }

  if (!isQueryBlock(block)) {
    // a draft saved before UIFPE-217 carries the retired step-chain shape —
    // recoverable, never a crash
    return (
      <div className="note" style={{ marginTop: 10 }}>
        This column&apos;s derivation was saved in an older format — re-import
        the registered mapping to refresh it, or{" "}
        <button className="btn" onClick={() =>
          setBlock(emptyDerivation(name, state.entityKey))}>
          start a fresh query
        </button>
      </div>
    );
  }

  const problems = derivedSqlProblems(draft, block, state.sources,
    manifestView(state.domain));
  const drivingCols = state.drivingTable
    ? columnsFor(state.drivingTable) : [];

  const runDraft = async () => {
    setAiBusy(true); setAiError(null);
    try {
      // the model reads by the same names the fence accepts: registered
      // aliases first (items_ext, not the physical table), plus any sources
      // this task newly declares
      const view = manifestView(state.domain);
      const known = [...view.aliases,
        ...state.sources
          .map((s) => (s.table.split(".").pop() ?? s.table).toUpperCase())
          .filter((s) => !view.aliasByTable.has(s) && !view.aliases.has(s))];
      const got = await draftDerivedSql(aiText, {
        domain: state.domain, table: state.destinationTable,
        drivingTable: state.drivingTable,
        columnName: name, dataType: (draft as { dataType?: string }).dataType,
        sources: known,
        entityKey: state.entityKey,
      });
      // a DRAFT only: it lands in the same box, where every check, the
      // preview, and the reviewer apply unchanged (ADR-0006 D6). Merged
      // into the CURRENT draft (functional update): edits made during the
      // round-trip survive, and a block removed meanwhile stays removed.
      setProbe(null); setProbeError(null);   // the draft is untested text
      set((d) => {
        const cur = d.derivation;
        if (!cur || !isQueryBlock(cur)) return d;
        return { ...d, derivation: { ...cur, sql: got.sql,
          keyedBy: got.keyedBy?.length ? got.keyedBy : cur.keyedBy,
          valueColumn: got.valueColumn ?? cur.valueColumn } } as MappingColumn;
      });
      setAiOpen(false); setAiText("");
    } catch (e) {
      setAiError(e instanceof Error ? e.message : String(e));
    } finally {
      setAiBusy(false);
    }
  };

  return (
    <details style={{ marginTop: 10 }} open>
      <summary style={{ cursor: "pointer", fontWeight: 700, fontSize: 12.5 }}>
        Derived — this value comes from its own query
      </summary>

      <div className="field" style={{ marginTop: 8 }}>
        <label>The query (one read-only SELECT — it runs exactly as written)</label>
        <textarea value={block.sql}
          rows={Math.min(14, Math.max(5, block.sql.split("\n").length + 1))}
          style={{ fontFamily: "var(--mono, monospace)", fontSize: 12.5,
                   width: "100%", resize: "vertical" }}
          placeholder={"SELECT <key column>, <value> AS " + name
            + "\nFROM <a table from your Sources>\nGROUP BY <key column>"}
          onChange={(e) => setBlock({ ...block, sql: e.target.value })} />
        <div className="checks" style={{ marginTop: 6 }}>
          <button className="btn" onClick={() => setAiOpen((v) => !v)}>
            ✨ Draft it from a description
          </button>
          {probeConfigured() && (
            <button className="btn" disabled={probeBusy || problems.length > 0}
              title={problems.length ? "fix the notes below first" : undefined}
              onClick={async () => {
                setProbeBusy(true); setProbeError(null); setProbe(null);
                try {
                  setProbe(await runGrainProbe(block, state));
                } catch (e) {
                  setProbeError(e instanceof Error ? e.message : String(e));
                } finally {
                  setProbeBusy(false);
                }
              }}>
              {probeBusy ? "Testing against dev data…" : "▶ Test — one row per entity?"}
            </button>
          )}
          <button className="btn" onClick={() => setBlock(undefined)}>
            ✕ Not derived — remove the query
          </button>
        </div>
        {probeError && <div className="note">{probeError}</div>}
        {probe && (
          <div className="field" style={{ marginTop: 8 }}>
            <span className={"pill" + (probe.oneRowPerEntity ? " ok" : " warn")}>
              {probe.note}
            </span>
            {probe.sample.rows.length > 0 && (
              <div style={{ overflowX: "auto", marginTop: 8 }}>
                <table className="props">
                  <thead><tr>
                    {probe.sample.columns.map((c) => <th key={c}>{c}</th>)}
                  </tr></thead>
                  <tbody>
                    {probe.sample.rows.map((r, i) => (
                      <tr key={i}>{r.map((v, j) =>
                        <td key={j}>{v ?? "∅"}</td>)}</tr>
                    ))}
                  </tbody>
                </table>
                <p className="help" style={{ marginTop: 4 }}>
                  First {probe.sample.rows.length} rows from dev data. The
                  test engine speaks a close dialect of the real one — a
                  function it refuses may still be fine; the submit checks
                  are the authority.
                </p>
              </div>
            )}
          </div>
        )}
        {aiOpen && (
          <div style={{ marginTop: 8 }}>
            <textarea value={aiText} rows={3} style={{ width: "100%" }}
              placeholder="Describe the value in plain terms — e.g. the ISBN of this product's parent, following the RELATED_TO link to the owning row"
              onChange={(e) => setAiText(e.target.value)} />
            <div className="checks" style={{ marginTop: 6 }}>
              <button className="btn primary" disabled={aiBusy || !aiText.trim()}
                onClick={runDraft}>
                {aiBusy ? "Drafting…" : "Draft the query"}
              </button>
            </div>
            <p className="help" style={{ marginTop: 4 }}>
              The draft lands in the box above — nothing runs, and every
              check and review applies to it unchanged.
            </p>
            {aiError && <div className="note">{aiError}</div>}
          </div>
        )}
      </div>

      <div className="field">
        <label>How a query row finds its entity (one row per entity)</label>
        {block.keyedBy.map((k, i) => (
          <div key={i} className="row2" style={{ marginBottom: 6 }}>
            <input type="text" value={k.queryColumn}
              placeholder="query output column"
              onChange={(e) => setBlock({ ...block,
                keyedBy: block.keyedBy.map((x, j) =>
                  j === i ? { ...x, queryColumn: e.target.value } : x) })} />
            <span style={{ display: "flex", gap: 6, alignItems: "center" }}>
              <span className="kind">matches</span>
              <input type="text" value={k.entityColumn} list="derived-entity-cols"
                placeholder="entity column"
                onChange={(e) => setBlock({ ...block,
                  keyedBy: block.keyedBy.map((x, j) =>
                    j === i ? { ...x, entityColumn: e.target.value } : x) })} />
              {block.keyedBy.length > 1 && (
                <button className="btn" title="remove this pair"
                  onClick={() => setBlock({ ...block,
                    keyedBy: block.keyedBy.filter((_, j) => j !== i) })}>
                  ✕
                </button>
              )}
            </span>
          </div>
        ))}
        <datalist id="derived-entity-cols">
          {(state.entityKey.map((k) => k.drivingColumn)
            .concat(drivingCols)).filter((v, i, a) => a.indexOf(v) === i)
            .map((c) => <option key={c} value={c} />)}
        </datalist>
        <div className="checks">
          <button className="btn" onClick={() => setBlock({ ...block,
            keyedBy: [...block.keyedBy, { queryColumn: "", entityColumn: "" }] })}>
            ＋ add a key pair
          </button>
        </div>
      </div>

      <div className="field">
        <label>Value column (the query output that becomes {name})</label>
        <input type="text" value={block.valueColumn ?? ""} placeholder={name}
          onChange={(e) => setBlock({ ...block,
            valueColumn: e.target.value || undefined })} />
      </div>

      {problems.length > 0 ? (
        <div className="note">{problems.join(" · ")}</div>
      ) : (
        <>
          <p className="help">
            {contractLines(name, block).join(" · ")} — checks here are
            advisory; the submit pipeline runs the full battery.
          </p>
          <pre className="code" style={{ marginTop: 8 }}>
            {wrapPreview(name, block, draft.kind)}
          </pre>
        </>
      )}
    </details>
  );
}

// ── Bulk grid for scalar columns (UIFPE-219) ────────────────────────────────
// The intake workbook's Scalar sheet, editable in place: every cell writes
// through immediately, validation stays live per row, and the modal remains
// one click away for anything the grid cannot say (joins, transforms).
function ScalarGrid({ state, patch, filter = "" }:
  { state: WizardState; patch: (p: Partial<WizardState>) => void;
    filter?: string }) {
  const rows = state.columns
    .map((col, i) => ({ col, i }))
    .filter((r): r is { col: ScalarColumn; i: number } =>
      r.col.kind === "scalar")
    .filter(({ col }) => !filter
      || col.goldColumnName.toLowerCase().includes(filter.toLowerCase()));
  const setCol = (i: number, col: MappingColumn) =>
    patch({ columns: state.columns.map((c, j) => (j === i ? col : c)) });
  const ctx: ColumnContext = { sources: state.sources,
                               drivingTable: state.drivingTable,
                               rowGroups: state.rowGroups,
                               transforms: state.transforms };
  if (!rows.length)
    return <p className="help">No scalar columns in this task yet.</p>;
  return (
    <div style={{ overflowX: "auto", marginBottom: 14 }}>
      <table className="structure">
        <thead>
          <tr>
            <th style={{ minWidth: 150 }}>Gold column</th>
            <th style={{ minWidth: 96 }}>Data type</th>
            <th style={{ minWidth: 170 }}>Business description</th>
            <th style={{ minWidth: 170 }}>Source table</th>
            <th style={{ minWidth: 130 }}>Source column</th>
            <th style={{ minWidth: 130 }}>Filter</th>
            <th title="event / message / api">Feeds</th>
            <th title="value may be null">Null</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {rows.map(({ col, i }) => {
            const problems = columnProblems(col, ctx);
            const set = (over: Partial<ScalarColumn>) =>
              setCol(i, { ...col, ...over });
            return (
              <tr key={i}>
                <td>
                  <input type="text" value={col.goldColumnName}
                    style={{ width: "100%" }} placeholder="camelCase"
                    onChange={(e) => set({ goldColumnName: e.target.value })} />
                  {problems.length > 0 && (
                    <div className="kind" title={problems.join(" · ")}
                      style={{ color: "var(--warn, #b95)" }}>
                      {problems.length} to fix
                    </div>
                  )}
                </td>
                <td>
                  <select value={col.dataType} style={{ width: "100%" }}
                    onChange={(e) => set({ dataType: e.target.value as DataType })}>
                    {TYPES.map((x) => <option key={x} value={x}>{x}</option>)}
                  </select>
                </td>
                <td>
                  <input type="text" value={col.businessDescription}
                    style={{ width: "100%" }}
                    onChange={(e) => set({ businessDescription: e.target.value })} />
                </td>
                <td>
                  <input type="text" value={col.source.table}
                    list="known-source-tables" style={{ width: "100%" }}
                    onChange={(e) => {
                      // a join's ON columns belong to the OLD table — carrying
                      // them to a new one invisibly poisons the compiled SQL,
                      // so a table change drops the join (the modal's offer
                      // re-prefills it from memory)
                      const changed = !sameTable(e.target.value, col.source.table);
                      set({ source: { ...col.source, table: e.target.value,
                        ...(changed ? { join: undefined } : {}) } });
                    }} />
                </td>
                <td>
                  <ColumnPick table={col.source.table} value={col.source.column}
                    placeholder="column"
                    onChange={(column) => set({ source: { ...col.source, column } })} />
                </td>
                <td>
                  <input type="text" value={col.source.filter ?? ""}
                    style={{ width: "100%" }} placeholder="optional"
                    onChange={(e) => set({ source: { ...col.source,
                      filter: e.target.value || null } })} />
                </td>
                <td style={{ whiteSpace: "nowrap" }}>
                  {(["event", "message", "api"] as const).map((k) => (
                    <label key={k} title={k}
                      style={{ marginRight: 6, fontSize: 12 }}>
                      <input type="checkbox" checked={col.channels[k]}
                        onChange={(e) => set({ channels: { ...col.channels,
                          [k]: e.target.checked } })} />
                      {k[0].toUpperCase()}
                    </label>
                  ))}
                </td>
                <td>
                  <input type="checkbox" checked={col.nullable}
                    title="value may be null"
                    onChange={(e) => set({ nullable: e.target.checked })} />
                </td>
                <td style={{ whiteSpace: "nowrap", fontSize: 12,
                             color: "var(--ink-soft)" }}>
                  join/transform: modal
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <p className="help">
        Every change saves as you type. Joins and transforms stay in the
        column's editor (Edit in the list below closes this grid's gap for
        those) — the grid covers what the intake workbook's Scalar sheet did.
      </p>
    </div>
  );
}
