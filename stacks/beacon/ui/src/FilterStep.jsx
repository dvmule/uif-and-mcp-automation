// FilterStep.jsx — which rows an export ships. UIFPE-717: rendered `embedded`
// in the Rules step's Rules card (Filters tab) as one compact row per filter,
// each edited in the step's popup through <ClauseEditor>, with <FilterChecks>
// below the card; the full three-section layout is kept for other hosts. Three sections — Conditions, Compiled Spark SQL, Check &
// preview (UIFPE-629: sections on the open page, not stacked cards).
//
// Thin by design: every rule (what a clause compiles to, when it is complete,
// how pasted identifiers are cleaned, how the engine's issues route back to
// a clause, which columns may be suggested) lives in lib/rowFilter.js and is
// vitest-covered; this file only renders and forwards edits. Wizard.jsx owns
// the clause list, the compiled strings, the issue map, the engine-check
// result and the live-preview state machine, and passes them down. The two
// things this file keeps for itself are presentation: the identifier
// textarea's draft text, and the per-column value suggestions it fetches
// (UIFPE-441) — a nicety cached for the step's lifetime.
import React, { useEffect, useMemo, useRef, useState } from "react";
import { Check, Code, Download, Filter, List, Play, Square, Upload, X } from "lucide-react";
import { api } from "./api.js";
import { Button, Chip, CountUp, Note, Pill, Spinner, Table, Td, inputClass } from "./ui.jsx";
import {
  ID_KINDS,
  LIST_CAP,
  PREVIEW_UI_CAP_S,
  attributeClauseForColumn,
  attributeColumnsFor,
  canDownload,
  cardinalityLabel,
  columnGlueType,
  compileClause,
  completeClauses,
  describeClause,
  describeDownload,
  describeHidden,
  describeMeasuring,
  describePreviewCounts,
  downloadFor,
  emptyClause,
  filterCheckIssues,
  filterRuleDefinition,
  filterRuleLabel,
  fmtBytes,
  isComplete,
  leftJoinHint,
  listIndexOf,
  missingFor,
  normalizeIdentifiers,
  operatorsFor,
  suggestionsEligible,
} from "./lib/rowFilter.js";
import { friendlyApiError } from "./lib/apiErrors.js";

const fmt = (n) => Number(n).toLocaleString("en-US");
const mono = `${inputClass} font-mono`;

// UIFPE-629: a titled section on the open page — heading, one-line purpose,
// an optional control on the right (the wizard's StepSection, kept local so
// this file stays free of Wizard imports)
function Section({ title, sub, right, className = "", children }) {
  return (
    <section className={className}>
      <div className="mb-3 flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="text-[18px] font-extrabold tracking-tight text-ink">{title}</h2>
          {sub && <p className="mt-0.5 max-w-3xl text-meta text-ink-mute">{sub}</p>}
        </div>
        {right}
      </div>
      {children}
    </section>
  );
}

export default function FilterStep({
  clauses,
  onChange,
  ctx,
  detected,
  compiled,
  issues,
  check,
  checkStale,
  onCheck,
  preview,
  previewStale,
  previewEnabled,
  profiles = {},
  onPreview,
  onCancelPreview,
  download = null,
  onDownload,
  onCancelDownload,
  blindSources = [],
  prefillProvenance = null,
  // UIFPE-701: inside the Rules card — the conditions only (the card's header
  // adds them and says what they do); Check & preview sit below the card as
  // <FilterChecks>. UIFPE-717: there each filter is one compact row whose Edit
  // calls `onEdit(id)` (the step's popup)
  embedded = false,
  onEdit,
}) {
  const update = (id, patch) => onChange((cs) => cs.map((c) => (c.id === id ? { ...c, ...patch } : c)));
  const remove = (id) => onChange((cs) => cs.filter((c) => c.id !== id));
  const add = (kind) => onChange((cs) => [...cs, emptyClause(kind, { detected, primaryAlias: ctx.primaryAlias })]);

  const complete = completeClauses(clauses);
  const { engineIssuesFor, previewIssuesFor } = issueRouting(clauses, check, checkStale, preview, previewStale);

  const suggestions = useSuggestions(clauses, ctx, previewEnabled, profiles);
  // UIFPE-514: the per-source cardinality profile (owned by Wizard, keyed by
  // alias here) decides which text attributes are offered; unmeasured text
  // is held back and said so, a failed measurement falls back visibly
  const attributeColumns = useMemo(() => attributeColumnsFor(ctx, profiles), [ctx, profiles]);
  const measuring = describeMeasuring(attributeColumns.pending);

  // the sources as the author knows them — a spread list's engine alias is
  // not a source (UIFPE-592)
  const aliasList = ctx.aliases.filter((a) => !ctx.elementAliases?.includes(a)).join(", ");
  const count = complete.length;

  const conditions = (
    <>
      {prefillProvenance === "filters" && (
        <Note>
          The conditions below were rebuilt from the published Spark SQL — the saved builder model did not
          match it, so each entry is shown as custom SQL.
        </Note>
      )}
      {prefillProvenance === "legacy" && (
        <Note>This export was authored before the builder — its filter is shown as custom SQL.</Note>
      )}

      {clauses.length === 0 && (
        <Note tone="warn">
          No conditions yet — <b>every row</b> of {aliasList || "the source"} will be exported. Add a condition to
          narrow the export.
        </Note>
      )}

      <div>
        {clauses.map((clause, index) => (
          <ClauseFrame
            key={clause.id}
            clause={clause}
            index={index}
            ctx={ctx}
            issues={issues.byId[clause.id] || []}
            engineIssues={engineIssuesFor(clause)}
            previewIssues={previewIssuesFor(clause)}
            onRemove={() => remove(clause.id)}
          >
            <ClauseBody
              clause={clause}
              ctx={ctx}
              detected={detected}
              suggestion={suggestions[clause.column]}
              attributeColumns={attributeColumns}
              profiles={profiles}
              measuring={measuring}
              onPatch={(p) => update(clause.id, p)}
            />
          </ClauseFrame>
        ))}
      </div>

      {issues.set.map((i) => (
        <Note key={i.message} tone={i.level === "block" ? "fail" : "warn"}>
          {i.message}
        </Note>
      ))}
    </>
  );

  // UIFPE-629: a dark code block — the one ink-on-dark surface in the wizard
  const compiledBlock = (
    <pre className="overflow-x-auto rounded-card bg-pearson-purple px-4 py-3 font-mono text-meta leading-relaxed text-[#e6e3ff]">
      {clauses.length === 0
        ? "-- no conditions: every row is exported"
        : clauses.map((c) => {
            const done = isComplete(c);
            return (
              <div key={c.id} className={done ? "" : "italic text-white/50"}>
                {compileClause(c, { maxListPreview: 5 })}
                {done ? "" : "   -- incomplete, not applied"}
              </div>
            );
          })}
    </pre>
  );

  // UIFPE-717: the prototype's rule rows — an automatic label, the compiled
  // SQL under it, Edit (the step's popup) and ✕; a filter that still needs
  // work says so on its row
  const rows = (
    <>
      {prefillProvenance === "filters" && (
        <Note>
          The filters below were rebuilt from the published Spark SQL — the saved builder model did not match it,
          so each one is shown as custom SQL.
        </Note>
      )}
      {prefillProvenance === "legacy" && (
        <Note>This export was authored before the builder — its filter is shown as custom SQL.</Note>
      )}
      {clauses.length === 0 ? (
        <Note tone="warn">
          No filters yet — <b>every row</b> of {aliasList || "the source"} will be exported. Add a filter to narrow
          the export.
        </Note>
      ) : (
        <div className="overflow-hidden rounded-[12px] border border-line">
          {clauses.map((clause, index) => {
            const own = issues.byId[clause.id] || [];
            const needsWork =
              own.some((i) => i.level === "block") || engineIssuesFor(clause).length > 0 || previewIssuesFor(clause).length > 0;
            return (
              <div key={clause.id} className="flex items-center justify-between gap-3 border-b border-line px-3.5 py-2.5 last:border-b-0">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <Pill tone="accent">filter</Pill>
                    <b className="text-body text-ink">{filterRuleLabel(clause, index)}</b>
                    {needsWork && <Pill tone="fail">needs work</Pill>}
                  </div>
                  <div className="mt-0.5 break-all font-mono text-meta text-ink-soft">{filterRuleDefinition(clause)}</div>
                </div>
                <span className="flex shrink-0 items-center gap-1.5">
                  <Button size="sm" onClick={() => onEdit?.(clause.id)}>
                    Edit
                  </Button>
                  <Button variant="ghost" size="sm" onClick={() => remove(clause.id)} title="Remove this filter" aria-label={`Remove filter ${index + 1}`}>
                    <X size={13} />
                  </Button>
                </span>
              </div>
            );
          })}
        </div>
      )}
      {issues.set.map((i) => (
        <Note key={i.message} tone={i.level === "block" ? "fail" : "warn"}>
          {i.message}
        </Note>
      ))}
    </>
  );

  if (embedded) {
    return (
      <div>
        <p className="mb-3 text-meta text-ink-mute">
          Rows that match <b>every</b> filter are exported; anything else never leaves the source.
          {ctx.aliases.length > 1 && <> Filters run after the join, so columns from any source work: {aliasList}.</>}
          {ctx.rowNote && <> {ctx.rowNote}</>}
        </p>
        {rows}
        {clauses.length > 0 && (
          <details className="mt-4">
            <summary className="cursor-pointer text-meta font-semibold text-accent">
              Compiled Spark SQL — exactly what is published in the export's filters block
            </summary>
            <div className="mt-2">{compiledBlock}</div>
          </details>
        )}
      </div>
    );
  }

  return (
    <div className="space-y-7">
      <Section
        title="Conditions"
        sub={
          <>
            Rows that match <b>every</b> condition are exported; anything else never leaves the source.
            {ctx.aliases.length > 1 && (
              <>
                {" "}
                Filters run after the join, so columns from any source work: {aliasList}.
              </>
            )}
            {ctx.rowNote && <> {ctx.rowNote}</>}
          </>
        }
        right={
          <Pill tone={count ? "accent" : "warn"}>
            {count ? `${count} condition${count === 1 ? "" : "s"}` : "every row"}
          </Pill>
        }
      >
        {conditions}
        <div className="mt-4 flex flex-wrap items-center gap-2">
          <span className="text-label font-semibold uppercase tracking-wider text-ink-mute">Add</span>
          <Button size="sm" onClick={() => add("list")}>
            <List size={13} /> Identifier list (PPID / ISBN)
          </Button>
          <Button size="sm" onClick={() => add("attribute")}>
            <Filter size={13} /> Attribute condition
          </Button>
          <Button size="sm" onClick={() => add("custom")}>
            <Code size={13} /> Custom Spark SQL
          </Button>
        </div>
      </Section>

      <Section
        className="border-t border-line pt-6"
        title="Compiled Spark SQL"
        sub={
          <>
            Each line is applied in turn (AND) to the joined rows before mapping. This is exactly what is
            published in the export's <code>filters</code> block.
          </>
        }
      >
        {compiledBlock}
      </Section>

      <Section
        className="border-t border-line pt-6"
        title="Check & preview"
        sub={
          <>
            Check runs the pipeline engine's own static validators on the compiled conditions — parse,
            function allowlist and a live check that every column exists on its source — the same checks
            the batch would run, before you publish. Preview runs the compiled conditions on the real Gold
            tables through Athena: how many rows match, a {10}-row sample of the export as mapped, and — for
            an identifier list — which identifiers match nothing. A few seconds for one table, up to a
            couple of minutes for a large join.
          </>
        }
      >
        <FilterChecks
          clauses={clauses}
          ctx={ctx}
          issues={issues}
          check={check}
          checkStale={checkStale}
          onCheck={onCheck}
          preview={preview}
          previewStale={previewStale}
          previewEnabled={previewEnabled}
          onPreview={onPreview}
          onCancelPreview={onCancelPreview}
          download={download}
          onDownload={onDownload}
          onCancelDownload={onCancelDownload}
          blindSources={blindSources}
        />
      </Section>
    </div>
  );
}

// UIFPE-441/490: Check filter, Preview matches and their results — the Filter
// step's third section, and (UIFPE-701/717) the row under the Rules step's
// Rules card. The matched-rows card, and the CSV download inside it, appear
// only once a preview has been asked for.
export function FilterChecks({
  clauses,
  ctx,
  issues,
  check,
  checkStale,
  onCheck,
  preview,
  previewStale,
  previewEnabled,
  onPreview,
  onCancelPreview,
  download = null,
  onDownload,
  onCancelDownload,
  blindSources = [],
}) {
  const complete = completeClauses(clauses);
  const routed = check?.status === "done" ? filterCheckIssues(check.issues || []) : null;
  const count = complete.length;
  const canRun = clauses.length > 0 && !issues.blocking && ctx.aliases.length > 0;
  const blockedReason =
    ctx.aliases.length === 0
      ? "Select a source first"
      : clauses.length === 0
        ? "Add a filter first — the preview shows the rows a filter keeps"
        : issues.blocking
          ? "Complete or remove the incomplete conditions first"
          : "";
  const previewBusy = preview?.status === "starting" || preview?.status === "running";
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2.5">
        <Button variant="primary" onClick={onPreview} busy={previewBusy} disabled={!canRun || previewEnabled === false}
          title={previewEnabled === false ? "Live preview is not configured on this environment" : blockedReason}
        >
          <Play size={13} /> Preview matches
        </Button>
        {previewBusy && (
          <Button onClick={onCancelPreview}>
            <Square size={12} /> Cancel
          </Button>
        )}
        <Button onClick={onCheck} busy={check?.status === "running"} disabled={!canRun} title={blockedReason}>
          <Check size={13} /> Check filter
        </Button>
        <span className="text-meta text-ink-mute">
          {canRun
            ? "Up to 10 sample rows in the file's own columns, through every rule and transform, run on the real Gold tables."
            : blockedReason}
        </span>
      </div>
      {blindSources.length > 0 && (
        <Note tone="warn">
          Live schema unavailable for <b>{blindSources.join(", ")}</b> — the validator skips ALL column
          checks when any source can't be introspected, so a wrong column name would surface at run time.
        </Note>
      )}
      <CheckResult check={check} stale={checkStale} routed={routed} count={count} blind={blindSources.length > 0} />
      {previewEnabled === false && (
        <Note tone="warn">
          Live preview is not configured on this environment (no Athena workgroup) — Check filter still
          runs the engine's static checks.
        </Note>
      )}
      {preview && (
        <div className="rounded-card border border-white/70 bg-paper-card px-[26px] py-[22px] shadow-card">
          <h2 className="mb-2 text-[18px] font-extrabold tracking-tight text-ink">Matched rows</h2>
          <PreviewResult
            preview={preview}
            stale={previewStale}
            clauses={clauses}
            download={download}
            onDownload={onDownload}
            onCancelDownload={onCancelDownload}
          />
        </div>
      )}
    </div>
  );
}

// Value suggestions per attribute column: fetched once per column when an
// eligible clause first points at it, kept for the step's lifetime. Every
// way the API cannot answer is a `reason`, never an error state — this is a
// nicety beside the value box, not a gate.
function useSuggestions(clauses, ctx, previewEnabled, profiles) {
  const [suggestions, setSuggestions] = useState({});
  const requested = useRef(new Set());
  useEffect(() => {
    if (previewEnabled === false) return;
    for (const clause of clauses) {
      if (clause.kind !== "attribute" || !clause.column) continue;
      // UIFPE-514: an over-cap column (a stored clause can still name one)
      // never fires the full-table GROUP BY
      const { eligible } = suggestionsEligible(clause, ctx, profiles);
      const key = clause.column;
      if (!eligible || requested.current.has(key)) continue;
      const dot = key.indexOf(".");
      const alias = key.slice(0, dot);
      const column = key.slice(dot + 1);
      const sourceId = ctx.sourceIdByAlias?.[alias];
      if (!sourceId) continue;
      requested.current.add(key);
      setSuggestions((s) => ({ ...s, [key]: { status: "loading", values: [], reason: null } }));
      api
        .columnValues(sourceId, column)
        .then((r) =>
          setSuggestions((s) => ({
            ...s,
            [key]: { status: "done", values: r.values || [], reason: r.reason || null, truncated: Boolean(r.truncated) },
          }))
        )
        .catch((e) =>
          setSuggestions((s) => ({
            ...s,
            [key]: { status: "done", values: [], reason: `suggestions unavailable (${e.message})` },
          }))
        );
    }
  }, [clauses, ctx, previewEnabled, profiles]);
  return suggestions;
}

// Engine and preview issues are keyed by the index among COMPLETE clauses;
// a refused preview routes the same way — Athena could not run THAT clause
function issueRouting(clauses, check, checkStale, preview, previewStale) {
  const complete = completeClauses(clauses);
  const routed = check?.status === "done" ? filterCheckIssues(check.issues || []) : null;
  const previewRouted = preview?.status === "refused" && !previewStale ? filterCheckIssues(preview.issues || []) : null;
  const at = (map, clause) => {
    if (!map) return [];
    const i = complete.indexOf(clause);
    return i < 0 ? [] : map.byIndex[i] || [];
  };
  return {
    engineIssuesFor: (clause) => (checkStale ? [] : at(routed, clause)),
    previewIssuesFor: (clause) => at(previewRouted, clause),
  };
}

// One clause's editor by kind — the list's rows and the popup share it
function ClauseBody({ clause, ctx, detected, suggestion, attributeColumns, profiles, measuring, onPatch }) {
  if (clause.kind === "list") return <ListClauseRow clause={clause} ctx={ctx} detected={detected} onPatch={onPatch} />;
  if (clause.kind === "attribute")
    return (
      <AttributeClauseRow
        clause={clause}
        ctx={ctx}
        suggestion={suggestion}
        attributeColumns={attributeColumns}
        profiles={profiles}
        measuring={measuring}
        onPatch={onPatch}
      />
    );
  if (clause.kind === "custom") return <CustomClauseRow clause={clause} ctx={ctx} onPatch={onPatch} />;
  return null;
}

// UIFPE-717: one filter's editor on its own — the Rules step's Add / Edit
// popup. The same rows, issue lines and plain-English read-back as the list;
// edits go straight into the clause list (the popup restores on Cancel).
export function ClauseEditor({ clause, clauses, onChange, ctx, detected, issues, check, checkStale, preview, previewStale, previewEnabled, profiles = {} }) {
  const one = useMemo(() => [clause], [clause]);
  const suggestions = useSuggestions(one, ctx, previewEnabled, profiles);
  const attributeColumns = useMemo(() => attributeColumnsFor(ctx, profiles), [ctx, profiles]);
  const { engineIssuesFor, previewIssuesFor } = issueRouting(clauses, check, checkStale, preview, previewStale);
  const onPatch = (patch) => onChange((cs) => cs.map((c) => (c.id === clause.id ? { ...c, ...patch } : c)));
  return (
    <ClauseFrame
      clause={clause}
      index={0}
      ctx={ctx}
      issues={issues.byId[clause.id] || []}
      engineIssues={engineIssuesFor(clause)}
      previewIssues={previewIssuesFor(clause)}
      bare
    >
      <ClauseBody
        clause={clause}
        ctx={ctx}
        detected={detected}
        suggestion={suggestions[clause.column]}
        attributeColumns={attributeColumns}
        profiles={profiles}
        measuring={describeMeasuring(attributeColumns.pending)}
        onPatch={onPatch}
      />
    </ClauseFrame>
  );
}

function ClauseFrame({ clause, index, ctx, issues, engineIssues, previewIssues, onRemove, bare = false, children }) {
  const kindLabel = { list: "Identifier list", attribute: "Attribute", custom: "Custom Spark SQL" }[clause.kind];
  const hint = leftJoinHint(clause, ctx);
  return (
    <>
      {/* UIFPE-629: the AND between clauses as a divider line, not a pill */}
      {index > 0 && !bare && (
        <div className="my-2 flex items-center gap-3 text-[10.5px] font-bold uppercase tracking-[0.08em] text-ink-mute">
          <span className="h-px flex-1 bg-line" />
          AND
          <span className="h-px flex-1 bg-line" />
        </div>
      )}
    <div className={bare ? "" : "rounded-card border border-line bg-paper-card p-3 shadow-[inset_3px_0_0_var(--color-accent)]"}>
      <div className="mb-2 flex items-center gap-2">
        <span className="text-label font-semibold uppercase tracking-wider text-ink-mute">{kindLabel}</span>
        <span className="flex-1" />
        {onRemove && (
          <Button variant="ghost" size="sm" onClick={onRemove} title="Remove this condition">
            <X size={13} />
          </Button>
        )}
      </div>
      {children}
      {issues.map((i) => (
        <div
          key={i.message}
          className={`mt-1.5 text-meta ${i.level === "block" ? "text-signal-fail" : "text-signal-warn"}`}
        >
          {i.level === "block" ? "✗ " : "⚠ "}
          {i.message}
        </div>
      ))}
      {hint && <div className="mt-1.5 text-meta text-signal-warn">⚠ {hint}</div>}
      {engineIssues.map((m) => (
        <Note key={m} tone="fail">
          <b>engine:</b> {m}
        </Note>
      ))}
      {previewIssues.map((m) => (
        <Note key={m} tone="warn">
          <b>preview:</b> {m}
        </Note>
      ))}
      <div className="mt-2 text-meta text-ink-mute">{describeClause(clause)}</div>
    </div>
    </>
  );
}

// Native select with one group per source alias — JoinRow's idiom. A value
// not in any list (a stored clause whose column vanished, or one the profile
// has since hidden) still renders as its own option so the author sees what
// the clause points at. `columnsByAlias` narrows the offer (the attribute
// picker passes the profiled set); `optionLabel` annotates an option.
function ColumnSelect({ value, onChange, ctx, ariaLabel, columnsByAlias = ctx.columnsByAlias, optionLabel }) {
  const listed = ctx.aliases.some((a) => (columnsByAlias[a] || []).some((c) => `${a}.${c}` === value));
  // UIFPE-592: one group per TABLE — a spread list's fields sit under the
  // table they belong to, and the heading says what a row is, not the
  // engine's element alias
  const groups = [];
  for (const alias of ctx.aliases) {
    const key = ctx.groupOfAlias?.[alias] ?? alias;
    let group = groups.find((g) => g.key === key);
    if (!group) {
      group = { key, label: ctx.groupLabelByAlias?.[key] ?? `${key} · ${ctx.sourceIdByAlias[key]}`, aliases: [] };
      groups.push(group);
    }
    group.aliases.push(alias);
  }
  return (
    <select className={mono} value={value} onChange={(e) => onChange(e.target.value)} aria-label={ariaLabel}>
      <option value="">— column —</option>
      {value && !listed && <option value={value}>{value}</option>}
      {groups.map((group) => {
        const rows = group.aliases.flatMap((alias) => (columnsByAlias[alias] || []).map((c) => [alias, c]));
        return (
          <optgroup key={group.key} label={group.label}>
            {rows.length === 0 && (
              <option disabled value="">
                columns unavailable — use Custom Spark SQL
              </option>
            )}
            {rows.map(([alias, c]) => {
              const note = optionLabel ? optionLabel(alias, c) : "";
              return (
                <option key={`${alias}.${c}`} value={`${alias}.${c}`}>
                  {alias}.{c}
                  {note ? ` · ${note}` : ""}
                </option>
              );
            })}
          </optgroup>
        );
      })}
    </select>
  );
}

function ListClauseRow({ clause, ctx, detected, onPatch }) {
  const onKind = (idKind) => {
    // keep the column unless it is clearly the other kind's column
    const current = detected.find((d) => `${d.alias}.${d.column}` === clause.column);
    const keep = clause.column && (!current || current.idKind === idKind);
    const suggested =
      detected.find((d) => d.idKind === idKind && d.alias === ctx.primaryAlias) ?? detected.find((d) => d.idKind === idKind);
    onPatch({ idKind, column: keep ? clause.column : suggested ? `${suggested.alias}.${suggested.column}` : "" });
  };
  return (
    <div className="space-y-2">
      <div className="grid gap-2 sm:grid-cols-[160px_1fr]">
        <select className={inputClass} value={clause.idKind} onChange={(e) => onKind(e.target.value)} aria-label="Identifier kind">
          {ID_KINDS.map((k) => (
            <option key={k.id} value={k.id}>
              {k.label}
            </option>
          ))}
        </select>
        <ColumnSelect value={clause.column} onChange={(column) => onPatch({ column })} ctx={ctx} ariaLabel="Identifier column" />
      </div>
      <IdentifierListEditor clause={clause} onPatch={onPatch} />
    </div>
  );
}

function IdentifierListEditor({ clause, onPatch }) {
  const [text, setText] = useState(() => (clause.values || []).join("\n"));
  const [fileNote, setFileNote] = useState(null);
  const fileRef = useRef(null);
  const tally = useMemo(() => normalizeIdentifiers(text, clause.idKind), [text, clause.idKind]);

  const apply = (nextText) => {
    setText(nextText);
    const r = normalizeIdentifiers(nextText, clause.idKind);
    onPatch({ values: r.values, invalid: r.invalid, suspicious: r.suspicious });
  };

  // the same text means different things under another kind — re-clean it
  useEffect(() => {
    const r = normalizeIdentifiers(text, clause.idKind);
    onPatch({ values: r.values, invalid: r.invalid, suspicious: r.suspicious });
  }, [clause.idKind]); // eslint-disable-line react-hooks/exhaustive-deps

  const onFile = async (e) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    if (file.size > 2_000_000) {
      setFileNote({ tone: "fail", text: `${file.name} is larger than 2 MB — split the list` });
      return;
    }
    const content = await file.text();
    const lines = content.split(/\r?\n/).filter((l) => l.trim()).length;
    apply(text.trim() ? `${text.trim()}\n${content}` : content);
    setFileNote({ tone: "mute", text: `Loaded ${fmt(lines)} line${lines === 1 ? "" : "s"} from ${file.name}` });
  };

  const overCap = tally.values.length > LIST_CAP;
  const kindLabel = ID_KINDS.find((k) => k.id === clause.idKind)?.label ?? "value";
  return (
    <div>
      <textarea
        rows={6}
        className={mono}
        value={text}
        onChange={(e) => apply(e.target.value)}
        placeholder={`Paste ${kindLabel}s — one per line, or comma-separated`}
        aria-label="Identifiers"
      />
      <div className="mt-1.5 flex flex-wrap items-center gap-2 text-[11.5px] text-ink-mute">
        <Button size="sm" onClick={() => fileRef.current?.click()}>
          <Upload size={13} /> Upload .txt / .csv
        </Button>
        <input ref={fileRef} type="file" accept=".txt,.csv,text/plain,text/csv" hidden onChange={onFile} />
        <span className={overCap ? "font-semibold text-signal-fail" : ""}>
          {fmt(tally.values.length)} identifier{tally.values.length === 1 ? "" : "s"}
          {tally.duplicates > 0 && ` · ${fmt(tally.duplicates)} duplicate${tally.duplicates === 1 ? "" : "s"} removed`}
          {tally.invalid.length > 0 && ` · ${fmt(tally.invalid.length)} invalid`}
          {tally.suspicious.length > 0 && ` · ${fmt(tally.suspicious.length)} look unusual`}
          {tally.headerSkipped && " · header row skipped"}
          {overCap && ` · limit ${fmt(LIST_CAP)}`}
        </span>
        {(tally.invalid.length > 0 || tally.duplicates > 0) && (
          <Button variant="danger" size="sm" onClick={() => apply(tally.values.join("\n"))}>
            Remove invalid & duplicates
          </Button>
        )}
      </div>
      {fileNote && <Note tone={fileNote.tone}>{fileNote.text}</Note>}
      {tally.invalid.length > 0 && <ValueStrip values={tally.invalid.map((b) => b.value)} titles={tally.invalid.map((b) => b.reason)} />}
    </div>
  );
}

// A bounded strip of offending values — the invalid-identifier idiom, reused
// for the preview's "not found" identifiers.
function ValueStrip({ values, titles = [], tone = "fail", limit = 20 }) {
  return (
    <div className="mt-1.5 flex flex-wrap items-center gap-1">
      {values.slice(0, limit).map((v, i) => (
        <Pill key={`${v}-${i}`} tone={tone} className="font-mono" title={titles[i]}>
          {v}
        </Pill>
      ))}
      {values.length > limit && <span className="text-[11px] text-ink-mute">…and {fmt(values.length - limit)} more</span>}
    </div>
  );
}

function AttributeClauseRow({ clause, ctx, suggestion, attributeColumns, profiles, measuring, onPatch }) {
  const glueType = columnGlueType(clause.column, ctx);
  const category = clause.category || "unknown";
  const ops = operatorsFor(category, glueType);
  const meta = ops.find((o) => o.op === clause.op) || ops[0];
  const { eligible, reason: ineligibleReason } = suggestionsEligible(clause, ctx, profiles);
  const hiddenNote = describeHidden(attributeColumns?.hidden);
  const unmeasured = Object.entries(attributeColumns?.unmeasured || {});
  // a stored clause may point at a column the rules have since hidden
  const dot = clause.column.indexOf(".");
  const ownAlias = dot > 0 ? clause.column.slice(0, dot) : "";
  const ownColumn = dot > 0 ? clause.column.slice(dot + 1) : "";
  const hiddenSelf = (attributeColumns?.hidden?.[ownAlias] || []).find((h) => h.column === ownColumn);

  // UIFPE-514: the rule picks the category and the operator (dates land on
  // "is between" — owner decision 2026-09-22)
  const onColumn = (column) => onPatch(attributeClauseForColumn(clause, column, ctx));

  const inputType = meta.input === "none" ? "text" : meta.input;
  const listId = `values-${clause.id}`;
  const hasValues = eligible && suggestion?.status === "done" && suggestion.values.length > 0;
  return (
    <div className="space-y-1.5">
      <div className="grid gap-2 sm:grid-cols-[1fr_180px_1fr]">
        <ColumnSelect
          value={clause.column}
          onChange={onColumn}
          ctx={ctx}
          ariaLabel="Column"
          columnsByAlias={attributeColumns?.offered || ctx.columnsByAlias}
          optionLabel={(alias, column) => cardinalityLabel(profiles?.[alias], column)}
        />
        <select
          className={inputClass}
          value={meta.op}
          onChange={(e) => onPatch({ op: e.target.value, value: "", value2: "" })}
          aria-label="Operator"
        >
          {ops.map((o) => (
            <option key={o.op} value={o.op}>
              {o.label}
            </option>
          ))}
        </select>
        {meta.arity === 0 ? (
          <span className="self-center text-[11.5px] text-ink-mute">no value needed</span>
        ) : (
          <div className="flex items-center gap-2">
            <input
              type={inputType}
              step={inputType === "number" ? "any" : undefined}
              className={mono}
              value={clause.value}
              onChange={(e) => onPatch({ value: e.target.value })}
              list={hasValues ? listId : undefined}
              autoComplete="off"
              aria-label="Value"
            />
            {hasValues && (
              <datalist id={listId}>
                {suggestion.values.map((v) => (
                  <option key={v} value={v} />
                ))}
              </datalist>
            )}
            {meta.arity === 2 && (
              <>
                <span className="font-mono text-[11.5px] text-ink-mute">and</span>
                <input
                  type={inputType}
                  step={inputType === "number" ? "any" : undefined}
                  className={mono}
                  value={clause.value2}
                  onChange={(e) => onPatch({ value2: e.target.value })}
                  list={hasValues ? listId : undefined}
                  autoComplete="off"
                  aria-label="Second value"
                />
              </>
            )}
          </div>
        )}
      </div>
      <div className="flex flex-wrap items-center gap-2 text-[11.5px] text-ink-mute">
        {/* UIFPE-562: what the chosen attribute means, from the entity's Gold master */}
        {clause.column && ctx.descriptionsByAlias?.[ownAlias]?.[ownColumn] && (
          <span className="basis-full text-ink-soft">{ctx.descriptionsByAlias[ownAlias][ownColumn]}</span>
        )}
        {clause.column && <Chip>{glueType ?? "type unknown"}</Chip>}
        {clause.column && category === "unknown" && (
          <span>Column types are not available for this source — text comparisons only; use Custom Spark SQL for numbers or dates.</span>
        )}
        {category === "struct" && (
          <span>
            Reach a field inside with Custom Spark SQL, e.g. <code>{clause.column}.currency = 'GBP'</code>.
          </span>
        )}
        {/* UIFPE-441: live values beside the box — a hint, never a constraint */}
        {eligible && suggestion?.status === "loading" && (
          <span className="flex items-center gap-1">
            <Spinner /> loading the column's common values…
          </span>
        )}
        {hasValues && (
          <span>
            {fmt(suggestion.values.length)} most common value{suggestion.values.length === 1 ? "" : "s"} suggested as you
            type — any value is still allowed
          </span>
        )}
        {eligible && suggestion?.status === "done" && suggestion.values.length === 0 && suggestion.reason && (
          <span>no suggestions: {suggestion.reason}</span>
        )}
        {!eligible && ineligibleReason && clause.column && <span>no suggestions: {ineligibleReason}</span>}
      </div>
      {/* UIFPE-441/514: the picker offers only attributes a person could pick
          a value for — identifiers by name never, text over 1,000 distinct
          values never, unmeasured text held back until the profile lands */}
      <div className="flex flex-wrap items-center gap-2 text-[11px] text-ink-mute">
        {measuring && (
          <span className="flex items-center gap-1">
            <Spinner /> {measuring} text attributes appear once their value counts are known.
          </span>
        )}
        {hiddenSelf && (
          <span className="text-signal-warn">
            ⚠ {clause.column}{" "}
            {hiddenSelf.reason === "identifier"
              ? "is an identifier — this condition still works, but an identifier list is the better tool."
              : "has more than 1,000 values — this condition still works, but an identifier list or Custom Spark SQL is the better tool."}
          </span>
        )}
        {hiddenNote && <span>{hiddenNote}</span>}
        {unmeasured.map(([alias, u]) => (
          <span key={alias} className="text-signal-warn">
            ⚠ {alias}'s attributes could not be measured ({u.reason}) — its text attributes are offered unfiltered.
          </span>
        ))}
      </div>
    </div>
  );
}

function CustomClauseRow({ clause, ctx, onPatch }) {
  const primary = ctx.primaryAlias || "product";
  const second = ctx.aliases[1];
  return (
    <div>
      <input
        className={mono}
        value={clause.sql}
        onChange={(e) => onPatch({ sql: e.target.value })}
        placeholder={
          second ? `${primary}.itemType = 'BOOK' AND size(${second}.priceSpecifications) > 0` : `${primary}.itemType = 'BOOK'`
        }
        aria-label="Spark SQL condition"
      />
      <div className="mt-1.5 text-[11.5px] text-ink-mute">
        Alias-qualify every column ({ctx.aliases.join(", ") || primary}). Allowed functions include lower, upper,
        trim, coalesce, cast, to_date, size, array_contains, regexp_extract. Write an apostrophe inside a value as{" "}
        <code>\'</code>.
      </div>
    </div>
  );
}

function CheckResult({ check, stale, routed, count, blind }) {
  if (!check) return null;
  if (check.status === "running") return <Note>Checking with the engine…</Note>;
  if (stale) return <Note>The filter changed since the last check — run it again.</Note>;
  if (check.engine_available === false) {
    return <Note tone="warn">The engine is not installed in this deployment — only presence checks ran.</Note>;
  }
  if (check.valid) {
    return (
      <Note tone="ok">
        Engine check passed — {count} condition{count === 1 ? "" : "s"} parsed
        {blind ? "; column names were NOT checked (live schema unavailable)" : " and every column resolved"}.
      </Note>
    );
  }
  const clauseHits = Object.values(routed?.byIndex || {}).reduce((n, list) => n + list.length, 0);
  return (
    <>
      {clauseHits > 0 && (
        <Note tone="fail">
          {clauseHits} engine issue{clauseHits === 1 ? "" : "s"} — shown under the condition{clauseHits === 1 ? "" : "s"} above.
          Fix and check again: a parse error in one condition hides the column checks for the others.
        </Note>
      )}
      {/* UIFPE-750: an API failure arrives here as its raw server string —
          say it plainly and keep the original behind Details */}
      {(routed?.general || []).map((m) => {
        const f = friendlyApiError(m);
        return (
          <Note key={m} tone="fail">
            {f.text}
            {f.detail && (
              <details className="mt-1">
                <summary className="cursor-pointer text-meta font-semibold">Details</summary>
                <code className="mt-1 block break-all font-mono text-meta">{f.detail}</code>
              </details>
            )}
          </Note>
        );
      })}
      {routed?.otherCount > 0 && (
        <div className="text-[11.5px] text-ink-mute">
          {routed.otherCount} other issue{routed.otherCount === 1 ? "" : "s"} outside the filter will show on Review.
        </div>
      )}
    </>
  );
}

// The preview's state machine, rendered. Wizard owns the transitions
// (start → running → done | failed | refused | cancelled | error); a result
// stays on screen after an edit, marked stale, until the next run.
function PreviewResult({ preview, stale, clauses, download, onDownload, onCancelDownload }) {
  if (!preview) return null;
  if (preview.status === "starting") return <Note>Starting the preview on Athena…</Note>;
  if (preview.status === "running") {
    return (
      <Note>
        Running on Athena{preview.elapsed_s != null ? ` — ${Math.round(preview.elapsed_s)} s` : "…"}. The wizard stops it
        after {PREVIEW_UI_CAP_S} s.
      </Note>
    );
  }
  if (preview.status === "error") return <Note tone="fail">{preview.message}</Note>;
  if (preview.status === "cancelled") {
    return <Note tone="warn">Preview stopped{preview.reason ? ` — ${preview.reason}` : "."}</Note>;
  }
  if (preview.status === "refused") {
    const routed = filterCheckIssues(preview.issues || []);
    const clauseHits = Object.values(routed.byIndex).reduce((n, list) => n + list.length, 0);
    return (
      <>
        <Note tone="warn">
          Athena cannot run this filter as written
          {clauseHits > 0 ? ` — see the ${clauseHits === 1 ? "note" : "notes"} under the condition${clauseHits === 1 ? "" : "s"} above` : ""}
          . The export itself may still accept it: Check filter is the authority for that.
        </Note>
        {routed.general.map((m) => (
          <Note key={m} tone="warn">
            {m}
          </Note>
        ))}
        {Object.entries(routed.byIndex).length === 0 && routed.general.length === 0 &&
          (preview.issues || []).map((i) => (
            <Note key={`${i.where}-${i.message}`} tone="warn">
              <b>{i.where}:</b> {i.message}
            </Note>
          ))}
      </>
    );
  }
  if (preview.status === "failed") {
    return (
      <>
        <Note tone="fail">
          Athena stopped on the {preview.error?.query || "preview"} query: {preview.error?.message}
        </Note>
        <div className="text-[11.5px] text-ink-mute">{preview.note}</div>
      </>
    );
  }
  if (preview.status !== "done") return null;

  const lists = clauses.filter((c) => c.kind === "list" && listIndexOf(clauses, c) >= 0);
  const columns = preview.sample?.columns || [];
  const rows = preview.sample?.rows || [];
  return (
    <div className="space-y-3">
      {stale && <Note>The filter or the mapping changed since this preview — run it again to refresh it.</Note>}
      {/* UIFPE-629: the count as a figure, with the scan stats beside it */}
      <div className={`flex flex-wrap items-baseline gap-x-3 ${stale ? "text-ink-mute" : "text-ink"}`}>
        <span className="text-[22px] font-semibold tracking-tight">
          {/* UIFPE-770: the figure rolls up to its value; the wording stays describePreviewCounts' */}
          {(Number(preview.matched) || 0) > 0 ? (
            <>
              <CountUp value={preview.matched} format={(n) => n.toLocaleString("en-US")} /> row{Number(preview.matched) === 1 ? "" : "s"} match
              {Number(preview.matched) === 1 ? "es" : ""}
            </>
          ) : (
            describePreviewCounts(preview.total, preview.matched)
          )}
        </span>
        <span className="text-meta text-ink-mute">
          scanned {fmtBytes(preview.scanned_bytes)}
          {preview.elapsed_s != null ? ` in ${Math.round(preview.elapsed_s)} s` : ""}
        </span>
      </div>
      {lists.map((clause) => {
        const entry = missingFor(preview, listIndexOf(clauses, clause));
        if (!entry) return null;
        const requested = clause.values.length;
        return (
          <div key={clause.id} className="text-[12px]">
            <b>{clause.column}</b>:{" "}
            {entry.missing.length === 0 ? (
              <span className="text-signal-ok">every one of the {fmt(requested)} identifiers was found</span>
            ) : (
              <span className="text-signal-warn">
                {fmt(entry.missing.length)}
                {entry.truncated ? "+" : ""} of {fmt(requested)} identifiers match nothing
              </span>
            )}
            {entry.missing.length > 0 && <ValueStrip values={entry.missing} tone="warn" />}
          </div>
        );
      })}
      {columns.length > 0 && (
        <div className="overflow-hidden rounded-card border border-line bg-paper-card shadow-card">
          <Table stagger head={columns}>
            {rows.length === 0 && (
              <tr>
                <Td colSpan={columns.length} className="py-6 text-center text-ink-mute">
                  No rows match — the sample is empty.
                </Td>
              </tr>
            )}
            {rows.map((row, r) => (
              <tr key={r} className="transition-colors hover:bg-paper-head">
                {row.map((cell, c) => (
                  <Td key={c} className="max-w-[240px] truncate font-mono text-meta" title={cell ?? ""}>
                    {cell == null ? <span className="text-ink-mute">null</span> : cell}
                  </Td>
                ))}
              </tr>
            ))}
          </Table>
        </div>
      )}
      {/* UIFPE-603: the sample is the export's own columns; a field Athena
          could not transform shows its source value instead, and says so */}
      {preview.untransformed?.length > 0 && (
        <Note tone="warn">
          Shown untransformed: {preview.untransformed.join(", ")} — Athena cannot run that expression, so the
          sample shows the source value. The export itself applies it.
        </Note>
      )}
      <div className="text-meta text-ink-mute">
        Sample of up to 10 matching rows in the export's columns. {preview.note}
      </div>
      <DownloadMatched
        preview={preview}
        stale={stale}
        download={download}
        onDownload={onDownload}
        onCancel={onCancelDownload}
      />
    </div>
  );
}

// UIFPE-490: beside a finished preview, the matched rows as one CSV — every
// row the filter keeps, in the sample's columns, capped — or carry on and
// schedule the feed. The author clicks; nothing downloads by itself. Wizard
// owns the execution's state machine (start → running → done | failed |
// refused | cancelled | error); this renders it, and only for the preview
// result it was asked for (downloadFor).
const linkButton =
  "inline-flex h-8 items-center gap-1.5 rounded-brand bg-pearson-purple px-3 text-body font-semibold text-white shadow-card transition-colors hover:bg-accent";

function DownloadMatched({ preview, stale, download, onDownload, onCancel }) {
  const gate = canDownload(preview, stale);
  const mine = downloadFor(download, preview, stale);
  const busy = mine?.status === "starting" || mine?.status === "running";
  const done = mine?.status === "done";
  const described = done ? describeDownload(preview.matched, mine.row_cap, mine.bytes) : null;
  return (
    <div className="space-y-2 rounded-card border border-white/70 bg-paper-card px-[26px] py-[22px] shadow-card">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-[240px] flex-1 text-body text-ink-mute">
          <b className="text-ink">Take the matched rows with you.</b>
          <div className="mt-0.5 text-meta">
            Every row the filter keeps, in the export's columns above, as one CSV — capped, and before the
            output format's own rendering. Or carry on to Job properties to schedule this as a feed.
          </div>
        </div>
        <span className="flex shrink-0 items-center gap-2">
          {busy && (
            <Button onClick={onCancel}>
              <Square size={12} /> Cancel
            </Button>
          )}
          {done ? (
            <>
              <a className={linkButton} href={mine.url} target="_blank" rel="noopener noreferrer">
                <Download size={13} /> {described.label}
              </a>
              <Button variant="ghost" size="sm" onClick={onDownload} title="Write a fresh CSV and a new link">
                Prepare again
              </Button>
            </>
          ) : (
            <Button onClick={onDownload} busy={busy} disabled={!gate.ok} title={gate.reason || ""}>
              <Download size={13} /> {busy ? "Preparing the CSV…" : "Download matched rows (CSV)"}
            </Button>
          )}
        </span>
      </div>
      {mine?.status === "starting" && <Note>Starting the download on Athena…</Note>}
      {mine?.status === "running" && (
        <Note>
          Writing the CSV on Athena{mine.elapsed_s != null ? ` — ${Math.round(mine.elapsed_s)} s` : "…"}. The
          wizard stops it after {PREVIEW_UI_CAP_S} s.
        </Note>
      )}
      {done && (
        <div className="text-[11.5px] text-ink-mute">
          {mine.filename} · link valid {Math.round((mine.expires_seconds || 900) / 60)} minutes (signed by the
          API) · opens in a new tab and saves.
        </div>
      )}
      {done && described.truncated && (
        <Note tone="warn">
          The CSV is capped at {fmt(mine.row_cap)} rows and {fmt(preview.matched)} match. Narrow the filter to take
          them all, or schedule the feed — a run has no cap.
        </Note>
      )}
      {mine?.status === "failed" && (
        <Note tone="fail">
          Athena could not write the CSV: {mine.error?.message}
          {mine.note ? <div className="text-[11.5px] text-ink-mute">{mine.note}</div> : null}
        </Note>
      )}
      {mine?.status === "error" && <Note tone="fail">{mine.message}</Note>}
      {mine?.status === "cancelled" && (
        <Note tone="warn">Download stopped{mine.reason ? ` — ${mine.reason}` : "."}</Note>
      )}
      {mine?.status === "refused" && (
        <Note tone="fail">
          Athena cannot run this filter as a download:
          {(mine.issues || []).map((i) => (
            <div key={`${i.where}:${i.message}`}>
              <b>{i.where}</b> — {i.message}
            </div>
          ))}
        </Note>
      )}
      {!gate.ok && gate.reason && !mine && <div className="text-[11.5px] text-ink-mute">{gate.reason}</div>}
    </div>
  );
}
