// New-export wizard — same six steps and same API calls as before, restyled
// to the platform's wizard chrome: "Step N of M: <name>" header, numbered
// progress strip, Back/Continue nav directly under it, card-per-section body.
//
// Door 1 (browse): domain first, then that domain's gold tables — read LIVE
// from the Glue catalog (UIFPE-229: GET /registries/domains + .../tables).
// Door 2 (query):  POST /convert — deterministic sqlglot conversion.
// Attributes come LIVE from the source (GET .../columns) — a new column in
// the source appears here with no registry edit and no deployment.
// UIFPE-237: a feed may read multiple sources — the first selection drives,
// later ones join onto it (dropdown-built equality pairs or custom Spark SQL);
// both doors and edit mode prefill the same feedSources model.
// UIFPE-167: the model's rules live in lib/feedSources.js (pure, vitest-
// covered): any gold table in any configured domain can drive or join, the
// query door resolves tables across every domain, and there is no fallback
// table — a feed with no source cannot leave the Source step.
import React, { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import {
  AlertCircle,
  ArrowRight,
  Braces,
  Check,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ChevronUp,
  Code2,
  FileCode2,
  FileSpreadsheet,
  GripVertical,
  Info,
  ListChecks,
  Plus,
  Search,
  Sparkles,
  Table2,
  Trash2,
  Undo2,
  Wand2,
  X,
} from "lucide-react";
import { api } from "./api.js";
import {
  TIMEZONES,
  WEEKDAYS,
  describeSchedule,
  nextRun,
  pickerToCron,
  cronToPicker,
} from "./schedule.js";
import {
  BeaconSweep,
  Button,
  Chip,
  DASH,
  EmptyState,
  Field,
  InfoTip,
  Note,
  Pill,
  Table,
  Td,
  fmtAbsolute,
  fmtNum,
  inputClass,
} from "./ui.jsx";
import {
  JOIN_TYPES,
  buildMappingConfig,
  columnMapped,
  defaultJoinPairs,
  deriveAlias,
  describeJoin,
  elementAliasesOf,
  elementFields,
  explainJoin,
  joinConditionOf,
  joinFromConfig,
  joinLeftAlias,
  joinComplete,
  joinRuleLabel,
  joinsComplete,
  knownColumns,
  listColumnsFor,
  mappingForColumn,
  normalizeFeedSources,
  resolveConvertedSources,
  rowShape,
  tableHeading,
  tableOf,
  unmapColumn,
} from "./lib/feedSources.js";
import {
  compileFilters,
  customClause,
  describeFilters,
  detectIdentifierColumns,
  emptyClause,
  filterCheckConfig,
  filterContext,
  filterIssues,
  filtersFromConfig,
  glueTypeCategory,
  upgradeClauseCategories,
  PREVIEW_POLL_MS,
  PREVIEW_UI_CAP_S,
  previewRequest,
  filterRuleDefinition,
  filterRuleLabel,
  previewSignature,
  renameStaleReferences,
} from "./lib/rowFilter.js";
import { friendlyApiError } from "./lib/apiErrors.js";
import FilterStep, { ClauseEditor, FilterChecks } from "./FilterStep.jsx";
import { useConfirm } from "./ConfirmDialog.jsx";
import {
  canLeaveOut,
  canRemoveRow,
  editSource,
  mappingCountLine,
  mappingFilled,
  mappingText,
  renameError,
  schemaLocked,
  validLeftOut,
} from "./lib/xsdMapping.js";
import DestinationPicker from "./DestinationPicker.jsx";
import MultiSelect from "./MultiSelect.jsx";
import AddDestinationDialog from "./AddDestinationDialog.jsx";
import XsdBuilderDrawer from "./XsdBuilder.jsx";
import { PICKER_THRESHOLD, channelLabel, isVerified, statusText } from "./lib/destinationPicker.js";
import {
  autoMapPaths,
  mappingTargets,
  missingRequired,
  unknownTargets,
  xmlTagProblems,
} from "./lib/partnerSchema.js";
import { readShellSession } from "./lib/session.js";
import { MANAGED_KEY, deliverToIds, deliverToKeys, deliveredFileName } from "./lib/jobProperties.js";
import { dropIndex, moveRow, reorderNeedsConfirm } from "./lib/mappingOrder.js";
import {
  TRANSFORMS,
  TRANSFORM_GROUPS,
  buildTransform,
  previewTransform,
  suggestTarget,
  transformById,
} from "./lib/transformBuilder.js";

// UIFPE-201: no Quality step — gate defaults are a platform concern (min 1
// row, ≤2% rejected, applied by the publisher); an edit keeps the version's
// existing gates. The author never has to think about them.
// UIFPE-207: Schedule takes the freed slot. The schedule is NOT part of the
// published version (D22) — it is PUT separately after publish succeeds.
// Row-filter builder story: Filter is its own step after Mapping — authors
// use it on nearly every export, and a card at the foot of Mapping was easy
// to miss. Step bodies after it shifted by one; the strip and footer are
// generic over STEPS.
// UIFPE-701: Mapping and Filter are one step, Fields & rules — the field
// mapping, the joins and the row filters side by side, the preview below.
// UIFPE-702: Output & delivery and Schedule are one step, Job properties.
// UIFPE-717 (owner, prototype v51): Fields & rules splits again — Mappings
// (the field mapping and the Attributes card) and Rules (joins and filters,
// with Preview matches under them). The export page's ?step= links follow.
const STEPS = ["Format", "Source", "Mappings", "Rules", "Job properties", "Review & publish"];
// UIFPE-700: the format cards — icon and one line each; a format the API
// adds later still gets a card, with no line
const FORMAT_CARD = {
  csv: [FileSpreadsheet, "Rows and columns. Safe defaults: header, quoting, UTF-8, formula escaping."],
  json: [Braces, "One record per row. Lists and records ship as they are."],
  xml: [FileCode2, "The partner's own schema when they have one — upload the XSD, or build one from a sample."],
};
const DELIMITER_LABEL = { comma: "Comma", tab: "Tab", pipe: "Pipe" };
// UIFPE-514: how long the wizard keeps polling a cardinality profile — the
// server's own age stop (PREVIEW_MAX_AGE_S) is five minutes too
const PROFILE_UI_CAP_S = 300;

const BROWSER_TZ = (() => {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "Europe/London";
  } catch {
    return "Europe/London";
  }
})();

const SAMPLE_SQL = `SELECT p.inventoryitemid AS product_id,
       p.ppid,
       p.description AS title,
       to_json(pr.priceSpecifications) AS prices
FROM   gold_product.product p
LEFT JOIN gold_price.price pr ON p.ppid = pr.ppid
WHERE  p.itemtype = 'BOOK'`;

const SENSITIVITY = { internal: "mute", commercial: "warn", restricted: "fail" };

// UIFPE-566: the expression inputs' type-ahead — typing searches the feed's
// source columns (substring, case-insensitive) and shows the matches right
// under the box; click or ↑↓ + Enter picks one, a second Enter commits the
// row. The native datalist this replaces gave no visible search feedback.
// UIFPE-616: a Mapping-table cell edited in place. Enter or ✓ saves; leaving
// the box without saving puts the saved value back, so half-typed text never
// changes a mapping (the UIFPE-566 lesson). `onSave(text)` returns an error
// string to show, or null when saved. `suggest` = the attribute search box.
// UIFPE-655: a change saves when the author clicks away or presses Enter;
// Escape puts the saved value back. A value the row cannot take (empty name,
// taken name, bad expression) stays in the box with the reason under it, so
// nothing is lost silently. The amber "needs a source" rows keep their
// explicit ✓ on purpose (UIFPE-566): a half-typed expression there would
// become a row.
function InlineCell({ value, onSave, options, placeholder, suggest = false }) {
  const [draft, setDraft] = useState(value || "");
  const [error, setError] = useState(null);
  // the saved value is the prop itself — a ref lagged one render behind it
  // and left "Click away or Enter saves" showing after a successful save
  const saved = value || "";
  const draftRef = useRef(value || ""); // what blur saves — state may lag it
  const boxRef = useRef(null);
  useEffect(() => {
    draftRef.current = value || "";
    setDraft(value || "");
    setError(null);
  }, [value]);
  const dirty = draft.trim() !== saved.trim();
  const save = () => {
    if (draftRef.current.trim() === saved.trim()) return;
    const problem = onSave(draftRef.current);
    setError(problem || null);
  };
  const revert = () => {
    draftRef.current = saved;
    setDraft(saved);
    setError(null);
  };
  // picking a suggestion keeps focus inside (its mousedown is prevented), so
  // only a real click-away reaches the save
  const saveUnlessInside = () =>
    setTimeout(() => {
      if (boxRef.current?.contains(document.activeElement)) return;
      save();
    }, 200);
  const change = (v) => {
    draftRef.current = v;
    setDraft(v);
    setError(null);
  };
  return (
    <div
      ref={boxRef}
      className="min-w-0"
      onKeyDown={(e) => {
        if (e.key === "Escape" && (dirty || error)) {
          e.preventDefault();
          revert();
        }
      }}
    >
      {suggest ? (
        <SourceSuggest value={draft} onChange={change} onCommit={save} options={options}
          placeholder={placeholder} onBlur={saveUnlessInside} />
      ) : (
        <input
          className={`${inputClass} font-mono`}
          value={draft}
          spellCheck={false}
          placeholder={placeholder}
          onChange={(e) => change(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && save()}
          onBlur={saveUnlessInside}
        />
      )}
      {error ? (
        <div className="mt-0.5 text-[11px] text-signal-fail">
          {error} · Esc goes back to {saved.trim() ? <span className="font-mono">{saved}</span> : "the saved value"}
        </div>
      ) : dirty ? (
        <div className="mt-0.5 text-[10.5px] text-ink-mute">Click away or Enter saves · Esc undoes</div>
      ) : null}
    </div>
  );
}

function SourceSuggest({ value, onChange, onCommit, options, placeholder, inputRef, onFocus, onBlur }) {
  const [open, setOpen] = useState(false);
  const [hi, setHi] = useState(0);
  const query = (value || "").trim().toLowerCase();
  const matches =
    open && query && !options.includes(value)
      ? options.filter((o) => o.toLowerCase().includes(query)).slice(0, 8)
      : [];
  // The list renders in a portal, positioned off the input's viewport rect —
  // inside the table it sat in a scroll container that clipped it. Tracked
  // while open so page scroll and resize keep it attached to the box.
  const boxRef = useRef(null);
  const [rect, setRect] = useState(null);
  useEffect(() => {
    if (!open) return undefined;
    const update = () => {
      const r = boxRef.current?.getBoundingClientRect();
      if (!r || !Number.isFinite(r.bottom)) return;
      setRect({ inputTop: r.top, inputBottom: r.bottom, left: r.left, width: r.width });
    };
    update();
    window.addEventListener("scroll", update, true);
    window.addEventListener("resize", update);
    return () => {
      window.removeEventListener("scroll", update, true);
      window.removeEventListener("resize", update);
    };
  }, [open, value]);
  return (
    <div className="relative" ref={boxRef}>
      <input
        ref={inputRef}
        className={`${inputClass} font-mono`}
        placeholder={placeholder}
        spellCheck={false}
        value={value}
        onChange={(e) => {
          onChange(e.target.value);
          setOpen(true);
          setHi(0);
        }}
        onFocus={(e) => {
          setOpen(true);
          onFocus?.(e);
        }}
        onBlur={(e) => {
          setTimeout(() => setOpen(false), 150); // let a click on a match land first
          onBlur?.(e);
        }}
        onKeyDown={(e) => {
          if (matches.length && e.key === "ArrowDown") {
            e.preventDefault();
            setHi((h) => Math.min(h + 1, matches.length - 1));
          } else if (matches.length && e.key === "ArrowUp") {
            e.preventDefault();
            setHi((h) => Math.max(h - 1, 0));
          } else if (e.key === "Escape") {
            setOpen(false);
          } else if (e.key === "Enter") {
            if (matches.length) {
              onChange(matches[Math.min(hi, matches.length - 1)]);
              setOpen(false);
            } else {
              onCommit?.();
            }
          }
        }}
      />
      {matches.length > 0 && Number.isFinite(rect?.inputBottom) &&
        createPortal(
          <div
            style={{
              position: "fixed",
              left: rect.left,
              width: Math.max(rect.width, 220),
              // open downward unless the list (max-h-48 = 192px) would run
              // off the bottom of the window — then anchor it above the box
              ...(rect.inputBottom + 200 <= window.innerHeight
                ? { top: rect.inputBottom + 4 }
                : { bottom: window.innerHeight - rect.inputTop + 4 }),
            }}
            className="z-50 max-h-48 overflow-auto rounded-brand border border-paper-line/60 bg-paper-card shadow-lg"
          >
            {matches.map((m, i) => (
              <button
                key={m}
                type="button"
                onMouseDown={(e) => {
                  e.preventDefault(); // keep the input focused
                  onChange(m);
                  setOpen(false);
                }}
                className={`block w-full px-2.5 py-1.5 text-left font-mono text-[11.5px] transition-colors ${
                  i === hi ? "bg-accent-soft text-accent" : "text-ink hover:bg-accent-soft/50"
                }`}
              >
                {m}
              </button>
            ))}
          </div>,
          document.body
        )}
    </div>
  );
}

export default function Wizard() {
  const navigate = useNavigate();
  // UIFPE-199: with a :jobId route param the wizard EDITS that export —
  // prefilled from its latest version; publish appends the next version
  const { jobId: editJobId } = useParams();
  const [editOf, setEditOf] = useState(null); // latest version being edited
  // UIFPE-696: the export page's per-card Edit opens an edit at that card's
  // step (?step=N); a new export always starts at the first step
  const [searchParams] = useSearchParams();
  const [step, setStep] = useState(() => {
    const n = Number(searchParams.get("step"));
    return editJobId && Number.isInteger(n) && n > 0 && n < STEPS.length ? n : 0;
  });
  const [door, setDoor] = useState(0);
  // UIFPE-700: the Method step — how a NEW export starts. It sits in front of
  // today's steps, whose numbers and gates are unchanged; an edit skips it.
  // "sql" is today's query door (POST /convert); AI is a placeholder.
  const [atMethod, setAtMethod] = useState(!editJobId);
  const [method, setMethod] = useState("guided");
  // a query read on the Method step before a format was picked: the Format
  // step says what already happened
  const [sqlLanded, setSqlLanded] = useState(false);
  // UIFPE-701: the Attributes card's table and find box (Mappings) and the
  // Rules card's tab (null = joins when the feed has any, else filters)
  const [attrTable, setAttrTable] = useState(null);
  const [attrQuery, setAttrQuery] = useState("");
  const [rulesTab, setRulesTab] = useState(null);
  // UIFPE-717: the Rules step's Add / Edit popup — null, or
  // { mode: "add", kind: "filter" | "join", clauseId, sourceId, query } or
  // { mode: "edit", kind, clauseId | sourceId, snapshot }
  const [rulePopup, setRulePopup] = useState(null);
  const [confirm, confirmDialog] = useConfirm();
  // UIFPE-700: the Source grid's Domains filter (switched-off domains)
  const [domainsOff, setDomainsOff] = useState([]);
  const [jobName, setJobName] = useState("");
  const [outputFormat, setOutputFormat] = useState(null);
  // UIFPE-558: the partner's XSD — the draft keeps only {id, sha256,
  // filename}; the parsed guide (paths + required flags) is fetched fresh
  const [partnerSchema, setPartnerSchema] = useState(null);
  const [schemaGuide, setSchemaGuide] = useState(null);
  const [schemaBusy, setSchemaBusy] = useState(false);
  const [schemaError, setSchemaError] = useState(null);
  const [xsdBuilderOpen, setXsdBuilderOpen] = useState(false); // UIFPE-660
  // author-chosen format options, keyed by format id (e.g. { csv: { delimiter: "tab" } });
  // anything not chosen keeps the publisher's safe default
  const [formatOptions, setFormatOptions] = useState({});
  // UIFPE-172: zip is default-on; unchecking is the per-feed opt-out
  const [zipOutput, setZipOutput] = useState(true);
  // UIFPE-258: the manifest is opt-in — most partners want only the data file.
  // UIFPE-535: the checkbox is hidden; new feeds ship none and an edit keeps
  // whatever the published version had (set below), so nothing changes silently.
  const [manifestOutput, setManifestOutput] = useState(false);
  // UIFPE-168: full snapshot vs incremental (changed rows only). The author
  // picks the MODE; the watermark column is platform-owned and resolved at
  // publish from the source registry.
  const [extractionMode, setExtractionMode] = useState("full");
  const [formats, setFormats] = useState([]);
  // UIFPE-229: domain-first discovery — the Glue catalog is the source list
  const [goldDomains, setGoldDomains] = useState([]);
  // UIFPE-673: no domain picker — every domain's tables load together.
  // { [domain]: { tables } | { error } }; a domain not yet in it is loading.
  const [domainLists, setDomainLists] = useState({});
  // whether the domain list itself arrived — "loading" | "ok" | "failed"
  const [domainsState, setDomainsState] = useState("loading");
  const [tableQuery, setTableQuery] = useState("");
  // UIFPE-673: the table whose attributes are shown (UIFPE-729: in a popup
  // behind an ⓘ), and the columns fetched for a table that is not in the feed
  // (feed tables already have theirs in attrsBySource) — { [sourceId]: result | { error } }
  const [attrPopup, setAttrPopup] = useState(null);
  const [peekedColumns, setPeekedColumns] = useState({});
  // UIFPE-237: a feed reads one or MORE sources. Ordered — [0] is the driving
  // source; every later entry joins onto it. Entry shape:
  //   { source: <catalog table record>, alias: string,
  //     join: null | { type, pairs: [{leftRef, rightColumn}], condition } }
  // pairs are dropdown-built equalities ANDed together; a non-null condition
  // means the author switched to (or the converter required) custom Spark SQL.
  const [feedSources, setFeedSources] = useState([]);
  // sourceId -> the columns result from api.sourceColumns (live or fallback)
  const [attrsBySource, setAttrsBySource] = useState({});
  // UIFPE-581: GET /registries/relationships — how Gold tables relate, so a
  // table added to the feed arrives with the correct join already filled in
  const [relationships, setRelationships] = useState([]);
  // UIFPE-562: the attribute chip under the pointer (or focus) on the Mapping
  // step — its Gold-master description shows in one line under the chips
  const [peekAttr, setPeekAttr] = useState(null);
  // UIFPE-568: references rewritten from pre-rename camelCase to the live
  // snake_case columns, [{from, to}], shown until the author publishes
  const [renamedRefs, setRenamedRefs] = useState([]);
  // UIFPE-237: edit mode — why this version can't be rebuilt in the wizard
  // (a message string, or null). Non-null blocks the edit: republishing a
  // partial rebuild would silently drop a source or corrupt a join.
  const [editSourceGap, setEditSourceGap] = useState(null);
  // UIFPE-167: converter tables that matched no catalog table, or several —
  // [{ hint, alias, reason: "unknown" | "ambiguous", candidates }]. Any entry
  // means the conversion was NOT applied (all-or-nothing: the query's joins
  // and mappings reference a source the feed would not have).
  const [convUnmatched, setConvUnmatched] = useState([]);
  // domains whose catalog could not be fetched during a convert (warned)
  const [convFailedDomains, setConvFailedDomains] = useState([]);
  // UIFPE-167: domain -> catalog tables, fetched once per session for the
  // query door's cross-domain resolution (a ref: nothing renders from it,
  // and the async convert must not read a stale closure)
  const tablesCache = useRef({});
  // Row filter: an ordered list of clauses (lib/rowFilter.js), ANDed; each
  // complete clause compiles to one entry of the engine's `filters` block.
  // The structured model is persisted UI-side in settings.filter_model.
  const [filterClauses, setFilterClauses] = useState([]);
  // the last "Check filter" result, with the signature of the config it
  // checked so a later edit marks it stale
  const [filterCheck, setFilterCheck] = useState(null);
  // UIFPE-441: the live preview on Athena — {status: starting|running|done|
  // failed|refused|cancelled|error, id, signature, startedAt, ...result}.
  // Athena holds the state; this is the wizard's view of it.
  const [filterPreview, setFilterPreview] = useState(null);
  const previewRef = useRef(null);
  previewRef.current = filterPreview;
  // UIFPE-490: the matched rows as one CSV, beside the preview — its own
  // execution and its own state machine, signed like the preview so an edit
  // hides the link
  const [previewDownload, setPreviewDownload] = useState(null);
  const downloadRef = useRef(null);
  downloadRef.current = previewDownload;
  // /health once: preview_enabled decides whether the Filter step asks for
  // suggestions and offers Preview matches at all
  const [health, setHealth] = useState(null);
  const [sql, setSql] = useState(SAMPLE_SQL);
  const [conversion, setConversion] = useState(null);
  const [convError, setConvError] = useState(null);
  const [mappings, setMappings] = useState([]);
  // UIFPE-652: drag-to-reorder — the row being dragged, where it would land
  // (insertion index), a move waiting for the owner's yes, and whether this
  // editing session has already been asked
  const [dragFrom, setDragFrom] = useState(null);
  const [dropAt, setDropAt] = useState(null);
  const [pendingMove, setPendingMove] = useState(null);
  const [reorderAsked, setReorderAsked] = useState(false);
  const [unresolved, setUnresolved] = useState([]);
  const [newField, setNewField] = useState({ target: "", expression: "" });
  // UIFPE-659: the new-field row is behind a "+ Add field" button until asked for
  const [addOpen, setAddOpen] = useState(false);
  // UIFPE-566: schema paths auto-created as rows the author still has to
  // point at a source — {target, expression (draft text)}. Never part of
  // `mappings` (they would fail validation); Continue is blocked while any
  // remain, and committing one (Enter/blur with text) makes it a real row.
  const [pendingRows, setPendingRows] = useState([]);
  // UIFPE-616: optional schema paths the author chose to leave out — nothing
  // is mapped for them, so the element is not written. Saved UI-side in
  // settings.left_out_paths (like filter_model) so an edit keeps the choice.
  const [leftOut, setLeftOut] = useState([]);
  const autoMappedFor = useRef(null); // schema id already auto-mapped
  const newTargetRef = useRef(null); // the add-row's target input, for "Use"
  // UIFPE-659: anything that fills the new-field row from elsewhere ("Use" on
  // a schema path, "Edit as expression" from the drawer) opens it; opening
  // puts the cursor in the target box
  useEffect(() => {
    if (newField.target || newField.expression) setAddOpen(true);
  }, [newField.target, newField.expression]);
  useEffect(() => {
    if (addOpen) newTargetRef.current?.focus({ preventScroll: true });
  }, [addOpen]);
  const pendingInputRefs = useRef({}); // path -> that pending row's input
  // the pending row whose input is (or was a moment ago) focused — a source
  // attribute chip clicked then fills THAT row. Cleared a beat after blur so
  // the click, which lands after the blur, still sees it.
  const activePendingRef = useRef(null);
  // UIFPE-425 v2: the fx popup — null closed, { index } edits that mapping
  // row, { index: null } adds a computed field. fxMeta remembers, per target
  // and per session only, which builder choices made a row (mapping.json
  // keeps just the compiled expression).
  const [fxPopup, setFxPopup] = useState(null);
  const [fxMeta, setFxMeta] = useState({});
  const [gates, setGates] = useState({ min_rows: 1, max_rejection_pct: 2.0, row_count_delta_pct_warn: 40 });
  const [destinations, setDestinations] = useState([]);
  // UIFPE-170: one export may deliver to several destinations. [] = the
  // managed partner-drop (the publisher fills that default in); otherwise
  // the ids of every chosen author-owned destination.
  const [destinationIds, setDestinationIds] = useState([]);
  // UIFPE-599: the add-destination dialog, open on the Output & delivery step
  const [addDestOpen, setAddDestOpen] = useState(false);
  // UIFPE-702: the Destinations card's Type filter (switched-off channels)
  const [destTypesOff, setDestTypesOff] = useState([]);
  // UIFPE-719: the Test button on an untested destination in the picked table
  const [destTesting, setDestTesting] = useState(null);
  const [destTestMsg, setDestTestMsg] = useState(null);
  const [review, setReview] = useState(null);
  const [submitState, setSubmitState] = useState(null);
  // UIFPE-418: publish spans several calls (draft, submit, schedule) — the
  // button stays locked and honest the whole time, so no double publish
  const [publishing, setPublishing] = useState(false);
  // UIFPE-207: picker state — the stored value is always cron + timezone
  const [schedule, setSchedule] = useState({
    mode: "off", time: "06:00", weekday: "MON", monthday: 1, timezone: BROWSER_TZ,
  });
  // edit mode: whether a schedule already exists (so "off" means delete it)
  const [hadSchedule, setHadSchedule] = useState(false);

  // UIFPE-237: derived views of feedSources so single-source JSX stays simple —
  // `selected` is the driving source's table record, exactly what the old
  // scalar state held.
  const primaryEntry = feedSources[0] ?? null;
  const selected = primaryEntry?.source ?? null;
  const isMultiSource = feedSources.length > 1;
  const primaryAlias = primaryEntry?.alias ?? conversion?.sources?.[0]?.name ?? "product";

  useEffect(() => {
    api.formats().then(setFormats).catch(() => {});
    api.destinations().then(setDestinations).catch(() => {});
    api.health().then(setHealth).catch(() => {});
    // UIFPE-581: the declared relationships — an unavailable list leaves the
    // old "first shared column" default in place, never blocks the wizard
    api.relationships().then((r) => setRelationships(r.relationships || [])).catch(() => {});
  }, []);

  // UIFPE-581: joins the wizard built before the relationships landed are
  // upgraded to the declared relationship; author-edited joins are untouched
  useEffect(() => {
    if (!relationships.length) return;
    setFeedSources((entries) => normalizeFeedSources(entries, attrsBySource, relationships));
  }, [relationships]); // eslint-disable-line react-hooks/exhaustive-deps

  // UIFPE-566: the wizard survives a route change without remounting (new →
  // edit, or one export to another), so anything tied to the PREVIOUS target
  // is cleared here — otherwise its pending rows leak into this export and
  // auto-map thinks it has already run.
  useEffect(() => {
    autoMappedFor.current = null;
    setPendingRows([]);
    setLeftOut([]);
  }, [editJobId]);

  // Edit mode: prefill every step from the latest published version
  useEffect(() => {
    if (!editJobId) return;
    api
      .catalogVersions(editJobId)
      .then((versions) => {
        const v = versions[versions.length - 1];
        if (!v) return;
        const s = v.settings || {};
        setEditOf(v);
        setJobName(v.name || editJobId);
        setOutputFormat(s.formats?.[0] || "csv");
        setFormatOptions(s.format_options || {});
        // UIFPE-558: re-attach the version's partner schema and re-read its
        // guide; a vanished staged file is said out loud, never silent
        if (s.partner_schema?.id) {
          setPartnerSchema(s.partner_schema);
          api.getSchema(s.partner_schema.id)
            .then(setSchemaGuide)
            .catch((e) => setSchemaError(e.message));
        }
        setZipOutput(s.packaging?.zip !== false);
    // pre-258 feeds carried no manifest key and always shipped one — an edit
    // shows the box CHECKED for them so re-publishing changes nothing silently
    setManifestOutput(s.packaging?.manifest !== false);
        setGates((g) => ({ ...g, ...(s.gates || {}) }));
        // every published filters[i] comes back (the old prefill kept only
        // filters[0]); the saved model is used when it matches what ran
        setFilterClauses(filtersFromConfig(v.mapping_config, s).clauses);
        setMappings(v.mapping_config?.mappings || []);
        setLeftOut(s.left_out_paths || []); // UIFPE-616
        // UIFPE-170: multi-target versions carry settings.deliveries; older
        // single-target versions carry destination_id at the settings top
        const targetIds = (s.deliveries || [])
          .map((t) => t.destination_id)
          .filter(Boolean);
        setDestinationIds(
          targetIds.length ? targetIds : s.destination_id ? [s.destination_id] : []
        );
        setExtractionMode(s.extraction?.mode || "full");
      })
      .catch(() => {});
    // the schedule lives beside the versions, not inside them (D22)
    api
      .getSchedule(editJobId)
      .then((r) => {
        const p = cronToPicker(r.expression);
        if (!p) return; // advanced shape — leave the picker off rather than lie
        setHadSchedule(true);
        setSchedule((s) => ({
          ...s, ...p, timezone: r.timezone || s.timezone, enabled: r.enabled !== false,
        }));
      })
      .catch(() => {}); // 404 = no schedule
  }, [editJobId]);

  // UIFPE-229: the domain list, once — live from the Glue catalog via the API
  useEffect(() => {
    api
      .domains()
      .then((r) => {
        setGoldDomains(r.domains || []);
        setDomainsState("ok");
      })
      .catch(() => setDomainsState("failed"));
  }, []);

  // UIFPE-673: every configured domain's tables, live, in parallel, as soon as
  // the domain list arrives — the same per-domain call the picker used to make
  // one at a time. Each domain settles on its own, so one catalog outage is
  // said in its group while the others stay usable. The results also fill the
  // session cache the query door's cross-domain resolution reads.
  useEffect(() => {
    if (!goldDomains.length) return undefined;
    let cancelled = false;
    goldDomains.forEach(({ domain }) => {
      api
        .domainTables(domain)
        .then((r) => {
          const tables = r.tables || [];
          tablesCache.current[domain] = tables;
          if (!cancelled) setDomainLists((m) => ({ ...m, [domain]: { tables } }));
        })
        .catch((e) => {
          if (!cancelled) setDomainLists((m) => ({ ...m, [domain]: { error: e.message } }));
        });
    });
    return () => {
      cancelled = true;
    };
  }, [goldDomains]);

  // UIFPE-673: a table's attributes, for one not in the feed, are fetched
  // once (the same route the feed uses); feed tables reuse theirs
  const openAttrs = (sourceId) => {
    setAttrPopup(sourceId);
    if (attrsBySource[sourceId] || peekedColumns[sourceId]) return;
    setPeekedColumns((m) => ({ ...m, [sourceId]: { loading: true } }));
    api
      .sourceColumns(sourceId)
      .then((r) => setPeekedColumns((m) => ({ ...m, [sourceId]: r })))
      .catch((e) => setPeekedColumns((m) => ({ ...m, [sourceId]: { error: e.message } })));
  };

  // Edit mode (UIFPE-237 × UIFPE-229): rebuild EVERY source entry once the
  // domain list is loaded — each stored path names its gold database, so the
  // referenced domains' tables are fetched and matched by path (a feed may
  // span domains). Stored aliases and joins are preserved verbatim. Anything
  // the wizard can't rebuild honestly (a path outside the configured gold
  // domains or gone from the catalog, a join layout the builder can't
  // express) blocks the edit via editSourceGap: publishing a partial rebuild
  // would silently corrupt it. The gap is re-evaluated (set OR cleared) on
  // every run so it never latches stale.
  useEffect(() => {
    if (!editOf || feedSources.length || !goldDomains.length) return;
    const stored = editOf.mapping_config?.sources || [];
    if (!stored.length) return;
    let cancelled = false;
    (async () => {
      const domainByDatabase = Object.fromEntries(
        goldDomains.map((d) => [d.database, d.domain])
      );
      const needed = [
        ...new Set(
          stored.map((rs) => domainByDatabase[(rs.path || "").split(".")[1]]).filter(Boolean)
        ),
      ];
      const lists = await Promise.all(
        needed.map((d) => api.domainTables(d).then((r) => r.tables || []).catch(() => []))
      );
      if (cancelled) return;
      const pool = lists.flat();
      const gaps = stored
        .map((rs) => rs.path)
        .filter((path) => !pool.some((t) => t.path === path));
      if (gaps.length) {
        setEditSourceGap(
          `This version reads ${gaps.join(", ")}, which is not in the Glue catalog under Beacon's configured gold domains — publishing from this wizard would drop it. Restore the table (or its catalog access) and reopen this edit, or pick sources manually.`
        );
        return;
      }
      const joins = editOf.mapping_config?.joins || [];
      const consumed = new Set();
      const entries = stored.map((rs, i) => {
        const match = pool.find((t) => t.path === rs.path);
        const entry = { source: match, alias: rs.name, join: null };
        if (i === 0) return entry;
        const jIdx = joins.findIndex((jn) => jn.right === rs.name);
        if (jIdx < 0) return entry;
        consumed.add(jIdx);
        // equality-only conditions round-trip into dropdown pairs; anything
        // else stays a custom condition — the prefill must never lie
        // UIFPE-609: with the config's list spreads, or a reopened export
        // loses its price_spec / product_copy element alias
        entry.join = joinFromConfig(joins[jIdx], editOf.mapping_config?.unnest || []);
        return entry;
      });
      // an API-authored config can hold joins this builder can't express (a
      // reversed right side, several joins onto one alias, …) — fabricating a
      // default in their place would corrupt the version on republish. Block.
      if (consumed.size !== joins.length || entries.slice(1).some((e) => !e.join)) {
        setEditSourceGap(
          "This version's join layout can't be represented in the wizard — edit it via the API instead."
        );
        return;
      }
      setEditSourceGap(null);
      // never clobber a selection the author built while the catalog loaded
      setFeedSources((existing) =>
        existing.length ? existing : normalizeFeedSources(entries, attrsBySource, relationships)
      );
    })();
    return () => {
      cancelled = true;
    };
  }, [editOf, goldDomains]); // eslint-disable-line react-hooks/exhaustive-deps

  // UIFPE-237: fetch live columns once per source — every selected source
  // needs them for the join dropdowns and the per-source attribute cards
  useEffect(() => {
    feedSources.forEach(({ source }) => {
      if (attrsBySource[source.id]) return;
      api
        .sourceColumns(source.id)
        .then((r) => setAttrsBySource((a) => ({ ...a, [source.id]: r })))
        .catch(() =>
          setAttrsBySource((a) => ({
            ...a,
            [source.id]: { columns: [], provenance: "registry", reason: "unavailable" },
          }))
        );
    });
  }, [feedSources]); // eslint-disable-line react-hooks/exhaustive-deps

  // UIFPE-514: the cardinality profile per source, started the moment a
  // source joins the feed (usually finished by the time the author reaches
  // Filter). Keyed by SOURCE ID; FilterStep gets it re-keyed by alias. The
  // shape the picker rules read: {status: "pending"} | {status: "done", rows,
  // columns} | {status: "failed", reason}. Every way the profile cannot be
  // had becomes a visible reason — the old design hid nothing and said
  // nothing when the scan timed out.
  const [profilesBySource, setProfilesBySource] = useState({});
  const profileRequested = useRef(new Set());
  const setProfile = (sourceId, value) => setProfilesBySource((p) => ({ ...p, [sourceId]: value }));
  const profileFailed = (sourceId, reason) => setProfile(sourceId, { status: "failed", reason });
  const profileLanded = (sourceId, body) => {
    if (body.status === "done") setProfile(sourceId, { status: "done", rows: body.rows, columns: body.columns || {} });
    else if (body.status === "running") setProfile(sourceId, { status: "pending", profileId: body.profile_id, startedAt: Date.now() });
    else profileFailed(sourceId, body.reason || body.error?.message || "the cardinality profile is unavailable");
  };
  // /health says preview is configured off (null while unanswered — still try)
  const previewOff = Boolean(health) && health.preview_enabled === false;
  useEffect(() => {
    feedSources.forEach(({ source }) => {
      if (profileRequested.current.has(source.id)) return;
      if (previewOff) {
        // no Athena on this environment: say so, offer text attributes unfiltered
        profileRequested.current.add(source.id);
        profileFailed(source.id, "live preview is not configured on this environment");
        return;
      }
      profileRequested.current.add(source.id);
      setProfile(source.id, { status: "pending" });
      api
        .getProfile(source.id)
        .then((body) => (body.status === "absent" ? api.startProfile(source.id) : body))
        .then((body) => profileLanded(source.id, body))
        .catch((e) => profileFailed(source.id, `profile unavailable (${e.message})`));
    });
  }, [feedSources, previewOff]); // eslint-disable-line react-hooks/exhaustive-deps
  // poll every running profile — same idiom as the preview: fixed interval,
  // transient failures ignored, a UI cap matching the server's age stop
  const runningProfiles = Object.entries(profilesBySource)
    .filter(([, p]) => p.status === "pending" && p.profileId)
    .map(([sourceId, p]) => `${sourceId}|${p.profileId}`)
    .join(",");
  useEffect(() => {
    if (!runningProfiles) return undefined;
    let cancelled = false;
    const poll = async () => {
      for (const pair of runningProfiles.split(",")) {
        const [sourceId, profileId] = pair.split("|");
        let body;
        try {
          body = await api.profileStatus(sourceId, profileId);
        } catch {
          continue; // transient — keep polling
        }
        if (cancelled) return;
        if (body.status === "running") {
          const startedAt = profilesBySource[sourceId]?.startedAt || Date.now();
          if ((Date.now() - startedAt) / 1000 > PROFILE_UI_CAP_S) {
            profileFailed(sourceId, "measuring took longer than five minutes — text attributes are offered without value counts");
          }
          continue;
        }
        profileLanded(sourceId, body);
      }
    };
    poll();
    const timer = setInterval(poll, PREVIEW_POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [runningProfiles]); // eslint-disable-line react-hooks/exhaustive-deps

  // UIFPE-237: once live columns land, upgrade default joins that are still
  // empty — Product + Price arrives pre-joined on ppid even though catalog
  // table records carry no column list (only introspection knows them)
  useEffect(() => {
    setFeedSources((entries) => {
      const next = entries.map((entry, i) => {
        if (i === 0 || !entry.join || entry.join.condition != null) return entry;
        if (entry.join.type === "cross") return entry; // a cross join has no condition
        if ((entry.join.pairs || []).some((p) => p.leftRef || p.rightColumn)) return entry;
        const pairs = defaultJoinPairs(entry, entries.slice(0, i), attrsBySource);
        return pairs[0]?.leftRef ? { ...entry, join: { ...entry.join, pairs } } : entry;
      });
      return next.some((entry, i) => entry !== entries[i]) ? next : entries;
    });
  }, [attrsBySource]);

  // Row filter: once live column types land, an attribute condition authored
  // while the type was unknown moves to its real operator family (same
  // reference when nothing changes, so this never loops)
  useEffect(() => {
    const ctx = filterContext(feedSources, attrsBySource);
    // UIFPE-568: a config authored before the snake_case rename says
    // `product.itemType`; Spark cannot resolve that against `item_type`, so
    // the stale references are rewritten to the live names and the author is
    // told (renamedRefs) — publishing then stores the names the run resolves
    const fixed = renameStaleReferences({ clauses: filterClauses, mappings }, ctx.columnsByAlias);
    if (fixed.renamed.length) {
      setMappings(fixed.mappings);
      setRenamedRefs((prev) => [...prev, ...fixed.renamed.filter((r) => !prev.some((p) => p.from === r.from))]);
    }
    setFilterClauses((cs) => upgradeClauseCategories(fixed.renamed.length ? fixed.clauses : cs, ctx));
  }, [attrsBySource]); // eslint-disable-line react-hooks/exhaustive-deps

  // UIFPE-168 guard, extended for UIFPE-237: no watermark column OR a
  // multi-source feed silently reverts to full load — the incremental window
  // applies to a single source before the join (see the Extraction card)
  useEffect(() => {
    if (feedSources.length && (!selected?.watermark_column || isMultiSource))
      setExtractionMode("full");
  }, [feedSources]); // eslint-disable-line react-hooks/exhaustive-deps

  // UIFPE-237: table cards toggle in and out of the feed; first in drives.
  // Toggling also clears a blocked edit — the author taking manual control of
  // the selection IS the resolution of the gap.
  // UIFPE-770: the catalog row that just joined the feed flashes once
  const [justJoined, setJustJoined] = useState(null);
  useEffect(() => {
    if (!justJoined) return undefined;
    const t = setTimeout(() => setJustJoined(null), 900);
    return () => clearTimeout(t);
  }, [justJoined]);
  const toggleSource = (s) => {
    setEditSourceGap(null);
    if (!feedSources.some((e) => e.source.id === s.id)) setJustJoined(s.id);
    setFeedSources((entries) => {
      const without = entries.filter((e) => e.source.id !== s.id);
      if (without.length !== entries.length) return normalizeFeedSources(without, attrsBySource, relationships);
      const alias = deriveAlias(s.id, entries.map((e) => e.alias));
      return normalizeFeedSources([...entries, { source: s, alias, join: null }], attrsBySource, relationships);
    });
  };

  // UIFPE-237: the join an entry had when it was promoted to driving, keyed
  // by alias — restored if it is demoted again, so a custom condition
  // survives a promote/re-demote round trip instead of being fabricated over
  const rememberedJoins = useRef({});

  // UIFPE-237: reorder so the given alias drives; normalize rebuilds the old
  // driving source's join and resets pairs that referenced a now-later alias
  const setPrimary = (alias) => {
    setFeedSources((entries) => {
      const i = entries.findIndex((e) => e.alias === alias);
      if (i <= 0) return entries;
      const promoted = entries[i];
      // idempotent writes only (no deletes) — updaters may run twice in dev
      if (promoted.join) rememberedJoins.current[promoted.alias] = promoted.join;
      const rest = entries
        .filter((_, j) => j !== i)
        .map((e) =>
          !e.join && rememberedJoins.current[e.alias]
            ? { ...e, join: rememberedJoins.current[e.alias] }
            : e
        );
      return normalizeFeedSources([promoted, ...rest], attrsBySource, relationships);
    });
  };

  const jobSlug = () =>
    jobName.trim().toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");

  // UIFPE-237: attribute clicks are per-source — the ref carries the alias.
  // Same column name from two sources: the second target gets alias-prefixed,
  // then numbered until free (targets must stay unique).
  // UIFPE-770: a clicked attribute chip flies to the row it became, which then
  // flashes. The flight is a throw-away clone on the Web Animations API; under
  // reduced motion or a hidden tab the row just flashes (or, reduced, nothing).
  const flyRef = useRef(null); // { from: DOMRect, text, keys: [candidate data-mapping-key values] }
  const [landedRow, setLandedRow] = useState(null);
  useEffect(() => {
    const fly = flyRef.current;
    if (!fly) return undefined;
    flyRef.current = null;
    const row = fly.keys.map((k) => document.querySelector(`tr[data-mapping-key="${CSS.escape(k)}"]`)).find(Boolean);
    if (!row) return undefined;
    const key = row.getAttribute("data-mapping-key");
    const still = window.matchMedia("(prefers-reduced-motion: reduce)").matches || document.hidden || !fly.from || typeof row.animate !== "function";
    const land = () => setLandedRow(key);
    if (still) {
      land();
      return undefined;
    }
    const to = row.getBoundingClientRect();
    const clone = document.createElement("span");
    clone.textContent = fly.text;
    clone.setAttribute("aria-hidden", "true");
    Object.assign(clone.style, {
      position: "fixed", left: `${fly.from.left}px`, top: `${fly.from.top}px`, zIndex: 500, pointerEvents: "none",
      padding: "4px 10px", borderRadius: "9999px", background: "var(--color-pearson-mist)", color: "var(--color-accent)",
      fontSize: "12px", fontWeight: 600, boxShadow: "0 8px 24px rgb(13 0 77 / 0.25)", whiteSpace: "nowrap",
    });
    document.body.appendChild(clone);
    const anim = clone.animate(
      [
        { transform: "translate(0, 0) scale(1)", opacity: 1 },
        { transform: `translate(${to.left + 48 - fly.from.left}px, ${to.top + to.height / 2 - fly.from.top - 12}px) scale(0.85)`, opacity: 0.25 },
      ],
      { duration: 280, easing: "cubic-bezier(0.2, 0.7, 0.2, 1)" }
    );
    anim.onfinish = () => {
      clone.remove();
      land();
    };
    return () => {
      clone.remove();
    };
  }, [mappings]);
  useEffect(() => {
    if (!landedRow) return undefined;
    const t = setTimeout(() => setLandedRow(null), 900);
    return () => clearTimeout(t);
  }, [landedRow]);
  const addFromColumn = (alias, col) => {
    if (columnMapped(mappings, alias, col)) return;
    // UIFPE-609: a list/record column in a CSV export maps as to_json(...)
    const rule = (target) => mappingForColumn(alias, col, filterCtx.typesByAlias?.[alias]?.[col], outputFormat, target);
    // UIFPE-566: a pending schema row was focused — the chip click is the
    // author CHOOSING that row's source, not adding a separate row
    const pendingTarget = activePendingRef.current;
    if (pendingTarget && pendingRows.some((r) => r.target === pendingTarget)) {
      activePendingRef.current = null;
      setMappings((m) => [...m, rule(pendingTarget)]);
      setPendingRows((rows) => rows.filter((x) => x.target !== pendingTarget));
      return;
    }
    let target = col;
    if (mappings.some((m) => m.target === target)) {
      target = `${alias}_${col}`;
      for (let n = 2; mappings.some((m) => m.target === target); n += 1)
        target = `${alias}_${col}_${n}`;
    }
    setMappings((m) => [...m, rule(target)]);
  };

  const addField = () => {
    const target = newField.target.trim();
    const expression = newField.expression.trim();
    if (!target || !expression) return;
    const isColumn = /^[A-Za-z_][\w]*(\.[A-Za-z_][\w]*)+$/.test(expression);
    setMappings((m) => [
      ...m,
      isColumn ? { type: "one_to_one", source: expression, target } : { type: "derived", expression, target },
    ]);
    setNewField({ target: "", expression: "" });
    newTargetRef.current?.focus({ preventScroll: true }); // UIFPE-659: the row stays open for the next one
  };

  // UIFPE-566: a pending row with text becomes a real mapping (same column-
  // vs-expression rule as addField); empty text just stays pending.
  const commitPendingRow = (target) => {
    const row = pendingRows.find((r) => r.target === target);
    const expression = (row?.expression || "").trim();
    if (!expression) return;
    const isColumn = /^[A-Za-z_][\w]*(\.[A-Za-z_][\w]*)+$/.test(expression);
    setMappings((m) => [
      ...m,
      isColumn ? { type: "one_to_one", source: expression, target } : { type: "derived", expression, target },
    ]);
    setPendingRows((rows) => rows.filter((r) => r.target !== target));
  };

  // UIFPE-566: when a partner schema is attached, arriving on the Mapping
  // step pre-creates one row per schema path — matched columns filled in,
  // the rest pending. Once per uploaded schema.
  //
  // Edits included (owner decision 2026-09-24: nothing is live yet, so the
  // two modes behave alike). Only paths the version does NOT already bind
  // become rows, so a republish changes the output only where the author
  // fills a row in; the `editOf` guard keeps this behind the prefill, which
  // sets `mappings` in the same block — without it the published mapping
  // could be read as empty and every path would arrive as a pending row.
  useEffect(() => {
    if (step !== 2 || outputFormat !== "xml") return;
    if (editJobId && !editOf) return;
    if (!partnerSchema?.id || !schemaGuide?.paths?.length) return;
    // keyed by target AND schema: the wizard is reused across route changes
    // (new → edit, one export to another) without remounting, so a schema id
    // alone would count the previous target's run as this one's
    const stamp = `${editJobId || "new"}:${partnerSchema.id}`;
    if (autoMappedFor.current === stamp) return;
    const columns = feedSources.flatMap(({ source, alias }) =>
      (attrsBySource[source.id]?.columns || []).map((column) => ({ alias, column }))
    );
    if (!columns.length) return; // columns still loading — runs again when they land
    autoMappedFor.current = stamp;
    const bound = new Set(mappingTargets(mappings));
    const usedSources = new Set(mappings.map((m) => m.source).filter(Boolean));
    const { matched, unmatched } = autoMapPaths(
      schemaGuide.paths.filter((p) => !bound.has(p.path)),
      columns.filter((c) => !usedSources.has(`${c.alias}.${c.column}`))
    );
    if (matched.length)
      setMappings((m) => [
        ...m,
        ...matched.map(({ path, source }) => ({ type: "one_to_one", source, target: path })),
      ]);
    // UIFPE-616: a path the author left out stays out — never re-offered
    const out = new Set(validLeftOut(schemaGuide, leftOut));
    setPendingRows(unmatched.filter((t) => !out.has(t)).map((target) => ({ target, expression: "" })));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [step, outputFormat, editJobId, editOf, partnerSchema, schemaGuide, attrsBySource, feedSources]);

  // UIFPE-167: the catalog tables of EVERY configured domain, for the query
  // door — a query that joins product to price names two domains, so the one
  // loaded on the Browse tab is not enough. Fetched once per domain and
  // cached for the session; a domain whose fetch fails is reported, not
  // silently treated as empty.
  const loadGoldTablePool = async () => {
    const pending = goldDomains.filter((d) => !tablesCache.current[d.domain]);
    const results = await Promise.allSettled(
      pending.map((d) => api.domainTables(d.domain).then((r) => r.tables || []))
    );
    const failed = [];
    results.forEach((r, i) => {
      if (r.status === "fulfilled") tablesCache.current[pending[i].domain] = r.value;
      else failed.push(pending[i].domain);
    });
    return { pool: Object.values(tablesCache.current).flat(), failed };
  };

  const runConvert = async () => {
    setConvError(null);
    setConversion(null);
    setConvUnmatched([]);
    setConvFailedDomains([]);
    try {
      const result = await api.convert(sql);
      // UIFPE-237/167: match EVERY converted source against the catalog
      // tables of every configured domain (lib/feedSources.js has the
      // matching rule) and carry the query's own aliases; JOINs land as
      // prefilled join entries.
      const { pool, failed } = await loadGoldTablePool();
      const { entries, unmatched } = resolveConvertedSources(result, pool);
      setConversion(result);
      setConvFailedDomains(failed);
      // all-or-nothing: an unmatched table means the query's joins and
      // mappings reference a source the feed would not have — apply nothing
      // (the notes below say exactly which table and why)
      if (unmatched.length) {
        setConvUnmatched(unmatched);
        return false;
      }
      setMappings(result.mappings);
      setUnresolved(result.unresolved);
      // one custom clause per converted WHERE filter — never joined into one
      if (result.filters.length) setFilterClauses(result.filters.map(customClause));
      setFeedSources(normalizeFeedSources(entries, attrsBySource, relationships));
      return true;
    } catch (e) {
      setConvError(e.message);
      return false;
    }
  };

  // UIFPE-700 "Write it yourself": the same conversion, started on the Method
  // step. Applied → straight to Mappings when the format is already chosen,
  // else to Format (its gate still applies) with a note saying so.
  const readQueryFromMethod = async () => {
    const ok = await runConvert();
    if (!ok) return;
    setDoor(1);
    setAtMethod(false);
    if (canLeaveFormat) setStep(2);
    else {
      setStep(0);
      setSqlLanded(true);
    }
  };

  // UIFPE-237/167: the engine config for the feed — every entry a source,
  // every later entry a join attached where its condition says. Pure, in
  // lib/feedSources.js; there is no fallback table when nothing is selected
  // (the Source-step and Publish gates make that unreachable).
  const compiledFilters = useMemo(() => compileFilters(filterClauses), [filterClauses]);
  const mappingConfig = () => buildMappingConfig({ feedSources, mappings, filters: compiledFilters });

  // "Check filter": the engine's static validators on the compiled clauses
  // alone — real sources and joins, a stub mapping (lib/rowFilter.js), so it
  // works before any field is mapped and only filter issues come back
  const checkFilters = async () => {
    const cfg = filterCheckConfig(feedSources, compiledFilters);
    const signature = JSON.stringify(cfg);
    setFilterCheck({ status: "running", signature });
    const result = await api
      .validateMapping(cfg)
      .catch((e) => ({ valid: false, engine_available: true, issues: [{ where: "api", message: e.message }] }));
    setFilterCheck({ status: "done", signature, ...result });
  };

  // UIFPE-441 "Preview matches": start the Athena executions (a running
  // previous preview is stopped first — one per author at a time), then the
  // effect below polls until a terminal state. A refused start carries the
  // validator's issue shape, routed to clauses like Check filter's.
  const startFilterPreview = async () => {
    const request = previewRequest(feedSources, filterClauses, mappings, outputFormat);
    const signature = previewSignature(request);
    const previous = previewRef.current;
    if (previous?.id && (previous.status === "running" || previous.status === "starting")) {
      api.cancelPreview(previous.id).catch(() => {});
    }
    // a CSV still being written for a DIFFERENT filter is not worth its scan
    const download = downloadRef.current;
    if (download?.id && download.signature !== signature && (download.status === "running" || download.status === "starting")) {
      api.cancelPreviewDownload(download.id).catch(() => {});
      setPreviewDownload(null);
    }
    setFilterPreview({ status: "starting", signature, startedAt: Date.now() });
    try {
      const started = await api.startPreview(request);
      if (started.status === "refused") {
        setFilterPreview({ status: "refused", signature, issues: started.issues || [] });
        return;
      }
      setFilterPreview({
        status: "running",
        signature,
        id: started.preview_id,
        startedAt: Date.now(),
        lists: started.lists || [],
        untransformed: started.untransformed || [],
      });
    } catch (e) {
      setFilterPreview({ status: "error", signature, message: e.message });
    }
  };
  const cancelFilterPreview = () => {
    const current = previewRef.current;
    if (current?.id) api.cancelPreview(current.id).catch(() => {});
    setFilterPreview((p) => (p ? { ...p, status: "cancelled", id: undefined } : p));
  };
  useAthenaPolling(filterPreview, setFilterPreview, {
    status: api.previewStatus,
    cancel: api.cancelPreview,
    capReason: `the preview took longer than ${PREVIEW_UI_CAP_S} s and was stopped — narrow the filter or preview a smaller source`,
  });

  // UIFPE-490 "Download matched rows": the same request the preview sent,
  // one execution writing every matched row (capped) as a CSV to Beacon's
  // preview/ prefix; the done state carries a 15-minute signed link the
  // author clicks. The wizard never fetches the file.
  const startPreviewDownload = async () => {
    const request = previewRequest(feedSources, filterClauses, mappings, outputFormat);
    const signature = previewSignature(request);
    const previous = downloadRef.current;
    if (previous?.id && (previous.status === "running" || previous.status === "starting")) {
      api.cancelPreviewDownload(previous.id).catch(() => {});
    }
    setPreviewDownload({ status: "starting", signature, startedAt: Date.now() });
    try {
      const started = await api.startPreviewDownload(request);
      if (started.status === "refused") {
        setPreviewDownload({ status: "refused", signature, issues: started.issues || [] });
        return;
      }
      setPreviewDownload({
        status: "running",
        signature,
        id: started.download_id,
        startedAt: Date.now(),
        row_cap: started.row_cap,
      });
    } catch (e) {
      setPreviewDownload({ status: "error", signature, message: e.message });
    }
  };
  const cancelPreviewDownload = () => {
    const current = downloadRef.current;
    if (current?.id) api.cancelPreviewDownload(current.id).catch(() => {});
    setPreviewDownload((p) => (p ? { ...p, status: "cancelled", id: undefined } : p));
  };
  useAthenaPolling(previewDownload, setPreviewDownload, {
    status: api.previewDownloadStatus,
    cancel: api.cancelPreviewDownload,
    capReason: `the download took longer than ${PREVIEW_UI_CAP_S} s and was stopped — narrow the filter or download a smaller source`,
  });
  // leaving the wizard stops a preview or download still running — nobody will read it
  useEffect(
    () => () => {
      const p = previewRef.current;
      if (p?.id && (p.status === "running" || p.status === "starting")) api.cancelPreview(p.id).catch(() => {});
      const d = downloadRef.current;
      if (d?.id && (d.status === "running" || d.status === "starting")) api.cancelPreviewDownload(d.id).catch(() => {});
    },
    []
  );

  const validateDraft = async () => {
    setReview(
      await api
        .validateMapping(mappingConfig(), outputFormat)
        .catch((e) => ({ valid: false, issues: [{ where: "api", message: e.message }] }))
    );
  };

  // validated => published (UIFPE-195): saving + submitting publishes the
  // next immutable version directly — the approval queue is gone
  const publishExport = async () => {
    setSubmitState(null);
    setPublishing(true);
    try {
      // UIFPE-170: one chosen destination keeps the original single
      // `delivery` shape (old and new engines read it identically); several
      // publish as a `deliveries` list, each target carrying its own
      // destination_id (the publisher pins each server's host key from it).
      const targets = chosenDestinations.map((d) => ({
        ...deliveryFromDestination(d),
        destination_id: d.id,
      }));
      const payload = {
        // UIFPE-196: names are repeatable labels; the API mints the identity
        // (edit mode carries the EXISTING id instead — UIFPE-199)
        name: jobSlug(),
        // UIFPE-229: the author's chosen domain, no longer hardcoded —
        // for a multi-source feed the DRIVING source's domain leads
        domain: selected?.domain || editOf?.domain || "product",
        // UIFPE-611: no author here — the API stamps the logged-in user
        mapping_config: mappingConfig(),
        unresolved,
        settings: {
          formats: [outputFormat],
          // UIFPE-558: reference + hash only — publish freezes the file
          ...(outputFormat === "xml" && partnerSchema
            ? { partner_schema: partnerSchema }
            : {}),
          // UIFPE-616: UI-only, read back on edit — the engine sees only the
          // mappings, which carry nothing for these paths
          ...(outputFormat === "xml" && partnerSchema && validLeftOut(schemaGuide, leftOut).length
            ? { left_out_paths: validLeftOut(schemaGuide, leftOut) }
            : {}),
          // always transmit the delimiter the author SAW (registry default or
          // their choice) — the displayed value must be the published value
          ...(delimiterChoices || formatOptions[outputFormat]
            ? {
                format_options: {
                  [outputFormat]: {
                    ...formatOptions[outputFormat],
                    ...(delimiterChoices ? { delimiter: chosenDelimiter } : {}),
                  },
                },
              }
            : {}),
          // always explicit (both true and false) so the stored draft is
          // self-describing; the publisher default is only a safety net for
          // pre-feature drafts (UIFPE-172)
          packaging: { zip: zipOutput, manifest: manifestOutput },
          // UIFPE-168: the mode only — the watermark column is resolved from
          // the source registry at publish, and the watermark value is
          // runtime state, never authored
          extraction: { mode: extractionMode },
          gates,
          // the builder's own model — UI-only, opaque to the API, read back
          // on edit; the engine sees only mapping_config.filters
          filter_model: { version: 1, clauses: filterClauses },
          // omitted for the managed partner-drop — the publisher owns that default
          ...(targets.length === 1
            ? { delivery: targets[0], destination_id: chosenDestinations[0].id }
            : targets.length > 1
              ? { deliveries: targets }
              : {}),
        },
      };
      const draft = editJobId
        ? await api.editExport(editJobId, payload)
        : await api.saveDraft(payload);
      const version = await api.submitDraft(draft.id);
      // UIFPE-207: the schedule is applied AFTER publish, outside the version
      // (D22). A schedule failure must not read as a publish failure.
      let scheduleNote = "";
      try {
        if (schedule.mode !== "off") {
          await api.putSchedule(String(version.export_id), {
            expression: pickerToCron(schedule),
            timezone: schedule.timezone,
            enabled: schedule.enabled !== false,
          });
          scheduleNote = ` Schedule set: ${describeSchedule(pickerToCron(schedule), schedule.timezone)}.`;
        } else if (editJobId && hadSchedule) {
          await api.deleteSchedule(editJobId);
          scheduleNote = " Schedule removed — runs are manual again.";
        }
      } catch (e) {
        scheduleNote = ` Published, but the schedule was NOT saved (${e.message}) — set it from the export's page.`;
      }
      setSubmitState({
        ok: true,
        exportId: version.export_id,
        version: version.version,
        text: editJobId
          ? `Published v${version.version} of export #${version.export_id} — runs use it from now on.${scheduleNote}`
          : `Published as export #${version.export_id} (v${version.version}) — ${jobSlug()} is eligible to run.${scheduleNote}`,
      });
    } catch (e) {
      setSubmitState({ ok: false, text: e.message });
    } finally {
      setPublishing(false);
    }
  };

  // UIFPE-586: the XML wrapper/record names come FROM the partner schema, never
  // from the author (owner decision 2026-09-25 — the first cut asked for them
  // in two fields, which was the wrong question: the XSD already states them).
  // Uploading a schema writes its root/row names into settings.format_options
  // (the same place the delimiter lives, so they reach the renderer through
  // the published rendering options); removing it deletes them, and no
  // schema = the renderer's own defaults (rows / row), which keeps
  // already-published exports rendering unchanged (D22). The name check
  // stays: an XSD may legally use characters our renderer refuses (it is
  // ASCII-only, no colon), and that is said here rather than at publish.
  const xmlOptions = formatOptions.xml || {};
  const xmlTagIssues = outputFormat === "xml" ? xmlTagProblems(xmlOptions) : [];
  const schemaFileRef = useRef(null);
  // a staged schema record becomes THE partner schema of this draft — from
  // the upload, or from the XSD builder's "Use it for this export" (UIFPE-660)
  const adoptSchemaRecord = (record) => {
    setPartnerSchema({ id: record.id, sha256: record.sha256, filename: record.filename });
    setSchemaGuide(record);
    setLeftOut([]); // UIFPE-616: a new schema, a fresh choice
    // UIFPE-586: the schema states the partner's own wrapper and record
    // names — they become the delivered file's, full stop.
    setFormatOptions((o) => {
      const xml = { ...(o.xml || {}) };
      if (record.root_tag) xml.root_tag = record.root_tag;
      else delete xml.root_tag;
      if (record.row_tag) xml.row_tag = record.row_tag;
      else delete xml.row_tag;
      return { ...o, xml };
    });
  };

  const canLeaveFormat = outputFormat && jobSlug() && xmlTagIssues.length === 0;
  // Filter step (lib/rowFilter.js): the feed context its editors need, the
  // per-clause issues, and the gate — an incomplete clause, or one whose
  // source left the feed, blocks; zero clauses passes ("every row" is
  // surfaced on the step and again on Review)
  const filterCtx = useMemo(() => filterContext(feedSources, attrsBySource), [feedSources, attrsBySource]);
  // UIFPE-607: plain notes for the two mapping shapes that make a surprising
  // file — a list mapped whole AND spread (every row repeats the list), and a
  // joined table nothing is mapped from (the join still multiplies rows)
  const mappingNotes = useMemo(() => {
    const refs = mappings.map((m) => m.source || m.expression || "");
    const refersTo = (text) => refs.some((r) => new RegExp(`(^|[^\\w.])${text.replace(/\./g, "\\.")}(?![\\w.])`).test(r));
    const usesAlias = (al) => refs.some((r) => new RegExp(`(^|[^\\w.])${al}\\.\\w`).test(r));
    const elements = elementAliasesOf(feedSources);
    const notes = [];
    for (const el of elements) {
      if (refersTo(el.list) && usesAlias(el.alias)) {
        notes.push(`${el.list} is mapped as a whole list and as one row per element (${el.alias}.…). Every row will repeat the full list. Keep one.`);
      }
    }
    for (const e of feedSources.slice(1)) {
      const row = e.join?.relationship?.row;
      if (!row) continue;
      const aliases = [e.alias, ...elements.filter((el) => el.tableAlias === e.alias).map((el) => el.alias)];
      if (!aliases.some(usesAlias)) {
        notes.push(`Nothing from ${tableHeading(e).split(" · ")[0]} is mapped, but the join still makes ${row}. Map a field from it, or remove the table on the Source step.`);
      }
    }
    return notes;
  }, [mappings, feedSources]);
  // the picker rules read profiles by alias (UIFPE-514)
  const profilesByAlias = useMemo(
    () =>
      Object.fromEntries(
        filterCtx.aliases.map((alias) => [alias, profilesBySource[filterCtx.sourceIdByAlias?.[alias]] || null])
      ),
    [filterCtx, profilesBySource]
  );
  const detectedIdColumns = useMemo(
    () => detectIdentifierColumns(feedSources, attrsBySource),
    [feedSources, attrsBySource]
  );
  const filterIssueMap = useMemo(() => filterIssues(filterClauses, filterCtx), [filterClauses, filterCtx]);
  const canLeaveFilter = !filterIssueMap.blocking;
  const currentCheckSignature = useMemo(
    () => JSON.stringify(filterCheckConfig(feedSources, compiledFilters)),
    [feedSources, compiledFilters]
  );
  const checkStale = Boolean(
    filterCheck && filterCheck.status === "done" && filterCheck.signature !== currentCheckSignature
  );
  const currentPreviewSignature = useMemo(
    () => previewSignature(previewRequest(feedSources, filterClauses, mappings, outputFormat)),
    [feedSources, filterClauses, mappings, outputFormat]
  );
  const previewStale = Boolean(
    filterPreview &&
      ["done", "failed", "refused", "cancelled", "error"].includes(filterPreview.status) &&
      filterPreview.signature !== currentPreviewSignature
  );
  // null until /health answers (buttons stay enabled); false = configured off
  const previewEnabled = health ? health.preview_enabled === true : null;
  // the validator's column checks are all-or-nothing (UIFPE-237): name the
  // sources it cannot introspect so a passing check reads honestly
  const blindSources = feedSources
    .filter((e) => attrsBySource[e.source.id]?.provenance !== "live")
    .map((e) => e.source.id);
  const prefillProvenance = useMemo(
    () => (editOf ? filtersFromConfig(editOf.mapping_config, editOf.settings || {}).provenance : null),
    [editOf]
  );
  // UIFPE-237/167: the Source step needs at least one source — there is no
  // fallback table — and no half-built join: an added "+ condition" pair
  // must be filled OR removed, never silently dropped (a cross join is the
  // one type needing no condition); a blocked edit (editSourceGap) can't
  // leave at all. The query door applies all-or-nothing, so a conversion
  // with an unmatched table never reaches here as a feed.
  const joinsOk = joinsComplete(feedSources);
  const canLeaveSource = feedSources.length > 0 && joinsOk && !editSourceGap;
  // UIFPE-566: schema rows still waiting for a source — leaving them behind
  // would publish an export that silently drops those elements
  const canLeaveMapping = pendingRows.length === 0;
  // every column the feed offers, alias-qualified — the type-ahead's options
  const columnOptions = feedSources.flatMap(({ source, alias }) =>
    (attrsBySource[source.id]?.columns || []).map((c) => `${alias}.${c}`)
  );
  const formatMeta = formats.find((f) => f.id === outputFormat);
  // UIFPE-168: incremental is offered only when the source carries the
  // platform CDC watermark column — declared on a registry row or detected
  // live from the Glue catalog (UIFPE-229 puts it on the table record).
  // UIFPE-237: never for multi-source feeds — the window applies to one
  // source before the join.
  const sourceSupportsIncremental = Boolean(selected?.watermark_column) && !isMultiSource;
  // UIFPE-170: the chosen author-owned destinations, in selection order;
  // empty = the managed partner-drop default
  const chosenDestinations = destinationIds
    .map((id) => destinations.find((d) => d.id === id))
    .filter(Boolean);
  const toggleDestination = (id) => {
    if (!id) return setDestinationIds([]); // managed drop is exclusive
    setDestinationIds((ids) =>
      ids.includes(id) ? ids.filter((x) => x !== id) : [...ids, id]
    );
  };
  const delimiterSchema = formatMeta?.options_schema?.properties?.delimiter;
  const delimiterChoices = delimiterSchema?.enum || null;
  const chosenDelimiter = formatOptions[outputFormat]?.delimiter ?? delimiterSchema?.default;

  // UIFPE-627: the Continue gate and its reason, unchanged in substance — held
  // in two names so the reason can be shown as text beside the button as well
  // as in its tooltip
  const continueDisabled =
    (step === 0 && !canLeaveFormat) ||
    (step === 1 && !canLeaveSource) ||
    (step === 2 && !canLeaveMapping) ||
    (step === 3 && (!canLeaveFilter || !joinsOk));
  const continueReason =
    step === 0 && xmlTagIssues.length > 0
      ? "Fix the XML tag name(s) — see the note on this step"
      : step === 0 && !canLeaveFormat
      ? "Name the export and select a format"
      : step === 1 && !canLeaveSource
        ? editSourceGap
          ? "This version can't be rebuilt in the wizard — see the note on the Source step"
          : "Select a source or convert a query — every joined source needs a complete join (fill or remove any empty condition row)"
        : step === 2 && !canLeaveMapping
          ? "Fill or remove the schema rows that still need a source (highlighted in the field mapping)"
          : step === 3 && !canLeaveFilter
            ? "Finish or remove the filter(s) marked \u201cneeds work\u201d in the Rules card"
            : step === 3 && !joinsOk
              ? "Finish or remove the join(s) marked \u201cneeds work\u201d in the Rules card"
              : "";

  // UIFPE-700: the steps as shown — a new export opens on Method, in front of
  // today's steps (`offset` maps a shown index to today's step index)
  const offset = editJobId ? 0 : 1;
  const VIEW_STEPS = editJobId ? STEPS : ["Method", ...STEPS];
  const viewIndex = atMethod ? 0 : step + offset;
  // UIFPE-770: the connector fills towards the way we are going — from the left
  // on Next, back from the right on Back
  const prevViewIndex = useRef(viewIndex);
  const forward = viewIndex >= prevViewIndex.current;
  useEffect(() => {
    prevViewIndex.current = viewIndex;
  }, [viewIndex]);
  const navNext = viewIndex < VIEW_STEPS.length - 1 ? VIEW_STEPS[viewIndex + 1] : null;
  const navDisabled = !atMethod && continueDisabled;
  const canGoBack = !atMethod && (step > 0 || !editJobId);
  const jumpTo = (i) => {
    setSqlLanded(false);
    if (i < 0) return setAtMethod(true);
    setAtMethod(false);
    setStep(i);
  };
  const goNext = () => {
    setSqlLanded(false);
    if (atMethod) {
      // the query door opens on Source for "Write it yourself"
      setDoor(method === "sql" ? 1 : 0);
      setAtMethod(false);
      setStep(0);
      return;
    }
    setStep((x) => x + 1);
  };
  const goBack = () => {
    setSqlLanded(false);
    if (!atMethod && step === 0 && !editJobId) return setAtMethod(true);
    setStep((x) => Math.max(0, x - 1));
  };

  // UIFPE-719: test a picked destination that has not passed yet — the same
  // call the Destinations screen makes; the server's list is read back after
  const testChosen = async (d) => {
    setDestTestMsg(null);
    setDestTesting(d.id);
    try {
      const r = await api.testDestination(d.id);
      if (r.ok) {
        if (d.channel !== "email")
          setDestinations((ds) => ds.map((x) => (x.id === d.id ? { ...x, status: "verified", host_key: r.host_key || x.host_key } : x)));
        setDestTestMsg({
          tone: "ok",
          text:
            d.channel === "email"
              ? `Test email sent for ${d.name}.`
              : `${d.name} verified${r.host_key_pinned ? " — server host key pinned on first contact" : ""}.`,
        });
      } else {
        setDestTestMsg({ tone: "fail", text: `${d.name}: the test failed — ${r.error || "no reason given"}` });
      }
      api
        .destinations()
        .then((list) => setDestinations((ds) => (list.length ? list : ds)))
        .catch(() => {});
    } catch (e) {
      setDestTestMsg({ tone: "fail", text: e.message });
    } finally {
      setDestTesting(null);
    }
  };

  // UIFPE-717: the Rules step's Add / Edit popup. Edits apply as they are
  // made (the same editors as before); Cancel puts back what was there — a
  // rule added in the popup is taken out again, an edited one restored.
  const openRuleAdd = (kind) => setRulePopup({ mode: "add", kind, clauseId: null, sourceId: null, query: "" });
  const openFilterEdit = (id) => {
    const clause = filterClauses.find((c) => c.id === id);
    if (clause) setRulePopup({ mode: "edit", kind: "filter", clauseId: id, snapshot: clause });
  };
  const openJoinEdit = (sourceId) => {
    const entry = feedSources.find((e) => e.source.id === sourceId);
    if (entry) setRulePopup({ mode: "edit", kind: "join", sourceId, snapshot: entry.join });
  };
  const addRuleFilter = (kind) => {
    const clause = emptyClause(kind, { detected: detectedIdColumns, primaryAlias: filterCtx.primaryAlias });
    setFilterClauses((cs) => [...cs, clause]);
    setRulePopup((p) => ({ ...p, clauseId: clause.id }));
    setRulesTab("filter");
  };
  const addRuleJoin = (table) => {
    toggleSource(table);
    setRulePopup((p) => ({ ...p, sourceId: table.id }));
    setRulesTab("join");
  };
  // "choose another" in the Add popup: the rule just added goes again
  const undoRuleAdd = () => {
    const p = rulePopup;
    if (p?.clauseId) setFilterClauses((cs) => cs.filter((c) => c.id !== p.clauseId));
    if (p?.sourceId) {
      const entry = feedSources.find((e) => e.source.id === p.sourceId);
      if (entry) toggleSource(entry.source);
    }
    setRulePopup((x) => ({ ...x, clauseId: null, sourceId: null }));
  };
  const cancelRule = () => {
    const p = rulePopup;
    if (!p) return;
    if (p.mode === "add") undoRuleAdd();
    else if (p.kind === "filter") setFilterClauses((cs) => cs.map((c) => (c.id === p.clauseId ? p.snapshot : c)));
    else setFeedSources((entries) => entries.map((e) => (e.source.id === p.sourceId ? { ...e, join: p.snapshot } : e)));
    setRulePopup(null);
  };
  const cancelRuleRef = useRef(cancelRule);
  cancelRuleRef.current = cancelRule;
  useEffect(() => {
    if (!rulePopup) return undefined;
    const onKey = (e) => e.key === "Escape" && cancelRuleRef.current();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [Boolean(rulePopup)]); // eslint-disable-line react-hooks/exhaustive-deps
  // ✕ on a join takes the table out of the feed — the same as unticking it
  // on the Source step, so it asks first
  const removeJoin = async (entry) => {
    const table = tableOf(entry.source.id);
    const ok = await confirm({
      title: `Remove the join to ${table}?`,
      body: `${table} leaves this export's feed, with its columns.`,
      points: [
        "Fields mapped from its columns stop resolving — check the Mappings step afterwards.",
        "Tick it again on the Source step, or add the join again here, to bring it back.",
      ],
      action: "Remove join",
    });
    if (ok) toggleSource(entry.source);
  };

  // today's query door, shown on the Method step (Write it yourself) and on
  // the Source step's "Read a query instead"
  const queryPanel = (onMethod) => (
    <div className="rounded-card border border-white/70 border-l-[3px] border-l-pearson-light-purple bg-paper-card px-[26px] py-[22px] shadow-card">
      <h3 className="text-[15px] font-bold text-ink">
        {onMethod ? "Your SQL — one SELECT, any common dialect" : "Paste a source query"}
      </h3>
      <p className="mt-0.5 text-meta text-ink-mute">
        Deterministic conversion, never executed — tables become sources, columns become the field
        mapping, the WHERE becomes filter rules.
      </p>
      <textarea
        rows={9}
        className={`${inputClass} mt-3 font-mono`}
        value={sql}
        onChange={(e) => setSql(e.target.value)}
        aria-label="Source query"
      />
      <div className="mt-2 flex flex-wrap items-center gap-3">
        <Button variant="primary" onClick={onMethod ? readQueryFromMethod : runConvert}>
          {onMethod ? <Code2 size={14} /> : <Wand2 size={14} />} {onMethod ? "Read the query" : "Convert to draft mapping"}
        </Button>
        {conversion && (
          <span className="text-meta text-ink-mute">
            dialect detected: <b>{conversion.dialect_detected}</b>
          </span>
        )}
      </div>
      {convError && <Note tone="fail">{convError}</Note>}
      {conversion && (
        <>
          {convUnmatched.length === 0 && (
            <>
              <Note tone="ok">
                Converted {conversion.mappings.length} expression
                {conversion.mappings.length === 1 ? "" : "s"}
                {conversion.filters.length > 0 &&
                  `; WHERE captured as ${conversion.filters.length} filter condition${
                    conversion.filters.length === 1 ? "" : "s"
                  }`}
                .
              </Note>
              {conversion.unresolved.map((u, i) => (
                <Note key={i} tone="warn">
                  <b>{u.target || "clause"}</b>: <code>{u.expression}</code> — {u.reason}
                </Note>
              ))}
              {conversion.notes.map((n, i) => (
                <Note key={i}>{n}</Note>
              ))}
              {/* UIFPE-237: JOINs survive conversion — show what was kept */}
              {(conversion.joins || []).map((j, i) => (
                <Note key={`j${i}`} tone="ok">
                  JOIN captured: <b>{j.right}</b> {j.type} join
                  {j.condition && (
                    <>
                      {" "}
                      on <code>{j.condition}</code>
                    </>
                  )}
                  .
                </Note>
              ))}
            </>
          )}
          {/* UIFPE-167: all-or-nothing — an unmatched table means the
              query's joins and mappings reference a source the feed
              would not have, so nothing was applied */}
          {convUnmatched.map((u, i) => (
            <Note key={`u${i}`} tone="fail">
              <code>{u.hint}</code> (alias <b>{u.alias}</b>){" "}
              {u.reason === "ambiguous" ? (
                <>
                  matches {u.candidates.join(", ")} — qualify it as{" "}
                  <code>&lt;domain&gt;.&lt;table&gt;</code>.
                </>
              ) : (
                <>
                  matched no gold table in any configured domain (
                  {goldDomains.map((d) => d.domain).join(", ") || "none configured"}).
                </>
              )}
            </Note>
          ))}
          {convUnmatched.length > 0 && (
            <Note tone="fail">
              Nothing from this query was applied — fix the table name and read it again,
              or pick the sources on the Source step.
            </Note>
          )}
          {convFailedDomains.length > 0 && (
            <Note tone="warn">
              The catalog for <b>{convFailedDomains.join(", ")}</b> was unavailable —
              tables there could not be matched.
            </Note>
          )}
        </>
      )}
    </div>
  );

  return (
    <section>
      {/* UIFPE-700: the top bar — the export's name edited in place, its
          format and version as pills, Back / Next on the right */}
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3 rounded-card border border-white/70 bg-paper-card px-5 py-3.5 shadow-card">
        {/* UIFPE-748: the name group wraps its pills under the name at narrow
            widths instead of letting them run into the Cancel / Back buttons */}
        <div className="flex min-w-0 flex-1 flex-wrap items-center gap-2.5">
          <Sparkles size={18} className="shrink-0 text-pearson-amethyst" aria-hidden="true" />
          <input
            value={jobName}
            onChange={(e) => setJobName(e.target.value)}
            placeholder={editJobId ? editOf?.name ?? `Export #${editJobId}` : "Name this export"}
            aria-label="Export name"
            title={
              editJobId
                ? "Export name — the display label; the export keeps its id either way"
                : "Export name — a display label (names may repeat); the export gets its own id at publish, and both appear in delivered filenames"
            }
            className="min-w-[220px] max-w-[460px] flex-1 border-b-[1.5px] border-dashed border-transparent bg-transparent px-0.5 text-[22px] font-extrabold tracking-tight text-ink outline-none placeholder:font-semibold placeholder:text-ink-mute/70 hover:border-pearson-amethyst focus:border-pearson-amethyst"
          />
          {outputFormat && <Pill tone="accent" className="shrink-0 uppercase">{outputFormat}</Pill>}
          {/* UIFPE-719: says so once the version is published */}
          <Pill key={submitState?.ok ? "published" : "draft"} tone={submitState?.ok ? "ok" : "mute"} className={`shrink-0 whitespace-nowrap${submitState?.ok ? " motion-pop-badge" : ""}`}>
            {submitState?.ok
              ? `v${submitState.version} · published`
              : editJobId
                ? `#${editJobId} · v${editOf?.version ?? "…"} → v${(editOf?.version ?? 0) + 1}`
                : "new · will publish v1"}
          </Pill>
        </div>
        <div className="flex flex-wrap items-center gap-2.5">
          <Button variant="ghost" onClick={() => navigate(editJobId ? `/m/beacon/exports/${editJobId}` : "/m/beacon/exports")}>
            <X size={14} /> Cancel
          </Button>
          <Button onClick={goBack} disabled={!canGoBack}>
            <ChevronLeft size={14} /> Back
          </Button>
          {navNext && (
            <Button variant="primary" onClick={goNext} disabled={navDisabled} title={navDisabled ? continueReason : ""}>
              Next › {navNext}
            </Button>
          )}
        </div>
        {/* UIFPE-627: when Next is disabled its reason is said here, not only in the tooltip */}
        {navNext && navDisabled && continueReason && (
          <div className="flex basis-full items-center justify-end gap-1.5 text-meta text-signal-warn">
            <AlertCircle size={13} className="shrink-0" aria-hidden="true" /> {continueReason}
          </div>
        )}
      </div>

      {/* UIFPE-700: the stepper — done / current / upcoming pills joined by a
          rule; each is a button so the author can jump to it (UIFPE-533) */}
      <ol className="mb-5 flex items-center gap-1 overflow-x-auto rounded-2xl border border-line bg-paper-card px-5 py-3.5 shadow-card" data-tour="wizard-steps">
        {VIEW_STEPS.map((label, v) => {
          const i = v - offset; // today's step index; -1 = Method
          const state = v === viewIndex ? "current" : v < viewIndex ? "done" : "todo";
          // Same gates as Next: a step opens once every gated step before it can be left.
          const reachable =
            i < 0 ||
            (!atMethod && i === step) ||
            !((i > 0 && !canLeaveFormat) || (i > 1 && !canLeaveSource) || (i > 2 && !canLeaveMapping) || (i > 3 && (!canLeaveFilter || !joinsOk)));
          return (
            <React.Fragment key={label}>
              {/* UIFPE-750: a 16px connector floor — seven labelled pills overflowed 1280px by 12px with 24px */}
              {/* UIFPE-770: the green fill is an inner bar that scales towards the
                  next step (360ms) instead of the rule swapping colour */}
              {v > 0 && (
                <li aria-hidden="true" className="relative h-[3px] min-w-4 flex-1 overflow-hidden rounded bg-line">
                  <span
                    className={`motion-fill absolute inset-0 rounded bg-signal-ok ${v <= viewIndex ? "scale-x-100" : "scale-x-0"}`}
                    style={{ transformOrigin: forward ? "left center" : "right center" }}
                  />
                </li>
              )}
              <li className="shrink-0">
                <button
                  type="button"
                  onClick={() => jumpTo(i)}
                  disabled={!reachable}
                  aria-current={state === "current" ? "step" : undefined}
                  title={reachable ? label : `${label} — complete the earlier steps first`}
                  className={`motion-pill flex items-center gap-2 whitespace-nowrap rounded-full py-1.5 pl-1.5 ${state === "current" ? "pr-3" : "pr-1.5 xl:pr-3"} text-[13px] font-semibold transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${
                    state === "current"
                      ? "border border-line bg-paper-card text-ink shadow-[0_1px_6px_rgb(13_0_77_/_0.12)]"
                      : state === "done"
                        ? "bg-signal-ok/10 text-signal-ok hover:underline"
                        : "bg-pearson-mist text-ink-soft enabled:hover:text-ink"
                  }`}
                >
                  <span
                    className={`grid h-[26px] w-[26px] place-items-center rounded-full text-[12px] font-bold ${
                      state === "current" ? "bg-pearson-purple text-white" : state === "done" ? "bg-signal-ok text-white" : "bg-paper-card text-ink-soft"
                    }`}
                  >
                    {state === "done" ? (
                      <span className="motion-tick-in grid place-items-center">
                        <Check size={13} />
                      </span>
                    ) : (
                      v + 1
                    )}
                  </span>
                  {/* UIFPE-750: every label shows from xl (1280px) up — the review
                      found number-only pills hard to place yourself by; below
                      that only the current step keeps its label */}
                  <span className={state === "current" ? "" : "max-xl:sr-only"}>{label}</span>
                </button>
              </li>
            </React.Fragment>
          );
        })}
      </ol>

      {/* UIFPE-568: said once, on every step, until publish stores the new names */}
      {renamedRefs.length > 0 && (
        <div className="mb-4">
          <Note tone="warn">
            <b>Renamed Gold columns:</b> {renamedRefs.length} reference{renamedRefs.length === 1 ? "" : "s"} in this
            export used the old camelCase names and now point{renamedRefs.length === 1 ? "s" : ""} at the live
            snake_case columns — {renamedRefs.map((r) => `${r.from} → ${r.to}`).join(", ")}. Review the Mappings and
            Rules steps, then publish to store the new names.
          </Note>
        </div>
      )}

      {confirmDialog}
      {attrPopup && (
        <AttributesDialog
          table={tableOf(attrPopup)}
          data={attrsBySource[attrPopup] || peekedColumns[attrPopup]}
          onClose={() => setAttrPopup(null)}
        />
      )}
      {/* UIFPE-717: the Rules step's Add / Edit popup (prototype: Filter | Join) */}
      {rulePopup && (() => {
        const p = rulePopup;
        const clauseIndex = p.clauseId ? filterClauses.findIndex((c) => c.id === p.clauseId) : -1;
        const clause = clauseIndex >= 0 ? filterClauses[clauseIndex] : null;
        const joinIndex = p.sourceId ? feedSources.findIndex((e) => e.source.id === p.sourceId) : -1;
        const joinEntry = joinIndex > 0 ? feedSources[joinIndex] : null;
        const chosen = p.kind === "filter" ? Boolean(clause) : Boolean(joinEntry) || joinIndex === 0;
        const title =
          p.mode === "add"
            ? "Add a rule"
            : p.kind === "filter"
              ? `Edit filter ${clauseIndex + 1}`
              : `Edit the join to ${tableOf(p.sourceId)}`;
        const q = (p.query || "").trim().toLowerCase();
        const inFeed = new Set(feedSources.map((e) => e.source.id));
        const tables = goldDomains
          .flatMap((d) => (domainLists[d.domain]?.tables || []).map((t) => ({ t, domain: d.domain })))
          .filter(({ t }) => !inFeed.has(t.id) && (!q || t.id.toLowerCase().includes(q)));
        const listsPending = goldDomains.some((d) => !domainLists[d.domain]);
        return (
          <div
            className="motion-fade-in fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-pearson-purple/45 px-4 py-[4vh]"
            onMouseDown={(e) => e.target === e.currentTarget && cancelRule()}
          >
            <div
              role="dialog"
              aria-modal="true"
              aria-labelledby="rule-dialog-title"
              className="motion-pop-in w-full max-w-[860px] rounded-card bg-paper-card px-[26px] py-[22px] shadow-[0_24px_64px_rgb(13_0_77_/_0.35)]"
            >
              <div className="mb-3.5 flex items-center justify-between gap-3">
                <h2 id="rule-dialog-title" className="text-[18px] font-extrabold tracking-tight text-ink">
                  {title}
                </h2>
                <Button size="sm" onClick={cancelRule} aria-label="Close" className="w-7 px-0">
                  <X size={14} />
                </Button>
              </div>
              {p.mode === "add" && (
                <div role="tablist" aria-label="Kind of rule to add" className="mb-4 inline-flex rounded-full border border-line bg-paper-card p-0.5">
                  {[
                    ["filter", "▽ Filter"],
                    ["join", "⋈ Join"],
                  ].map(([id, label]) => (
                    <button
                      key={id}
                      type="button"
                      role="tab"
                      aria-selected={p.kind === id}
                      disabled={chosen && p.kind !== id}
                      onClick={() => setRulePopup((x) => ({ ...x, kind: id }))}
                      className={`rounded-full px-4 py-1 text-body font-semibold transition-colors disabled:cursor-not-allowed disabled:opacity-45 ${
                        p.kind === id ? "bg-pearson-purple text-white" : "text-ink-soft hover:text-ink"
                      }`}
                    >
                      {label}
                    </button>
                  ))}
                </div>
              )}

              {p.kind === "filter" && !clause && (
                <div className="grid gap-3 sm:grid-cols-3">
                  {[
                    ["attribute", "Attribute condition", "A column compared with a value."],
                    ["list", "Identifier list", "PPID or ISBN-13 — paste up to 5,000."],
                    ["custom", "Custom Spark SQL", "Any boolean expression."],
                  ].map(([kind, label, hint]) => (
                    <button
                      key={kind}
                      type="button"
                      onClick={() => addRuleFilter(kind)}
                      className="rounded-[14px] border-[1.5px] border-line bg-paper-card p-4 text-left transition-colors hover:border-pearson-light-purple hover:bg-paper-head"
                    >
                      <span className="block text-body font-bold text-ink">{label}</span>
                      <span className="mt-0.5 block text-meta text-ink-mute">{hint}</span>
                    </button>
                  ))}
                </div>
              )}
              {p.kind === "filter" && clause && (
                <ClauseEditor
                  clause={clause}
                  clauses={filterClauses}
                  onChange={setFilterClauses}
                  ctx={filterCtx}
                  detected={detectedIdColumns}
                  issues={filterIssueMap}
                  check={filterCheck}
                  checkStale={checkStale}
                  preview={filterPreview}
                  previewStale={previewStale}
                  previewEnabled={previewEnabled}
                  profiles={profilesByAlias}
                />
              )}

              {p.kind === "join" && !joinEntry && joinIndex !== 0 && (
                <div>
                  <p className="mb-2 text-meta text-ink-mute">
                    Pick the table to join onto <b className="text-ink">{feedSources[0] ? tableOf(feedSources[0].source.id) : "the feed"}</b>. A declared
                    relationship fills the join in for you.
                  </p>
                  <div className="relative mb-2 max-w-[320px]">
                    <Search size={14} className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-ink-mute" aria-hidden="true" />
                    <input
                      value={p.query || ""}
                      onChange={(e) => setRulePopup((x) => ({ ...x, query: e.target.value }))}
                      placeholder="Find a table"
                      aria-label="Find a table to join"
                      className={`${inputClass} pl-8`}
                    />
                  </div>
                  {listsPending && !tables.length ? (
                    <BeaconSweep label="Reading the gold tables live from the catalog…" />
                  ) : tables.length === 0 ? (
                    <Note>{q ? "No table matches — clear the find box." : "Every gold table is already in the feed."}</Note>
                  ) : (
                    <div className="max-h-[46vh] overflow-y-auto rounded-[12px] border border-line">
                      {tables.map(({ t, domain }) => (
                        <button
                          key={t.id}
                          type="button"
                          onClick={() => addRuleJoin(t)}
                          className="flex w-full items-center justify-between gap-3 border-b border-line px-3.5 py-2.5 text-left last:border-b-0 hover:bg-paper-head"
                        >
                          <span className="inline-flex items-center gap-2 font-semibold text-ink">
                            <Table2 size={16} className="shrink-0 text-pearson-amethyst" aria-hidden="true" />
                            {tableOf(t.id)}
                          </span>
                          <span className="text-meta text-ink-mute">{domain}</span>
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              )}
              {p.kind === "join" && joinIndex === 0 && (
                <Note>
                  <b>{tableOf(p.sourceId)}</b> now drives the feed — there was no table before it, so it has no join to set.
                </Note>
              )}
              {p.kind === "join" && joinEntry && (
                <JoinRow
                  entry={joinEntry}
                  index={joinIndex}
                  feedSources={feedSources}
                  attrsBySource={attrsBySource}
                  onChange={(index, join) =>
                    setFeedSources((entries) => entries.map((en, j) => (j === index ? { ...en, join } : en)))
                  }
                />
              )}

              <div className="mt-[18px] flex flex-wrap items-center justify-between gap-3 border-t border-line pt-3.5">
                <span>
                  {p.mode === "add" && chosen && (
                    <button type="button" onClick={undoRuleAdd} className="text-meta font-semibold text-accent hover:underline">
                      ← Choose another {p.kind === "filter" ? "kind of filter" : "table"}
                    </button>
                  )}
                </span>
                <span className="flex gap-2.5">
                  <Button onClick={cancelRule}>Cancel</Button>
                  <Button variant="primary" onClick={() => setRulePopup(null)} disabled={p.mode === "add" && !chosen}>
                    {p.mode === "add" ? `Add ${p.kind}` : "Done"}
                  </Button>
                </span>
              </div>
            </div>
          </div>
        );
      })()}

      {/* UIFPE-770: the step's content rises in on every change (keyed on the
          shown step, so nothing replays while the author works inside a step) */}
      <div key={viewIndex} className="motion-step-in">
      {/* ---------------------------------------------------------- 0 Method */}
      {atMethod && (
        <div className="space-y-4">
          <div className="rounded-card border border-white/70 bg-paper-card px-[26px] py-[22px] shadow-card">
            <CardTitle
              title="Method"
              info="How do you want to start? Every way in ends in the same place — a published version you review before it runs."
            />
            <div className="motion-choices mt-4 grid gap-3.5 md:grid-cols-3">
              <MethodCard icon={ListChecks} title="Guided wizard" hint="Most exports start here" pressed={method === "guided"} onClick={() => setMethod("guided")}>
                Answer a few questions — tables, fields, rows, destination, schedule — one step at a time.
              </MethodCard>
              {/* owner decision 2026-09-29: a placeholder until there is an AI API */}
              <MethodCard icon={Sparkles} title="Generate with AI" hint="Drafts only — nothing publishes itself" badge="Coming soon" disabled>
                Describe the feed in a sentence or two. Beacon drafts the tables, fields and filters; you review every one.
              </MethodCard>
              <MethodCard icon={Code2} title="Write it yourself" hint="Lands on the field mapping, seeded" pressed={method === "sql"} onClick={() => setMethod("sql")}>
                Paste the SQL you run today. Beacon reads the tables, joins and fields out of it.
              </MethodCard>
            </div>
          </div>
          {method === "sql" && queryPanel(true)}
        </div>
      )}

      {/* ---------------------------------------------------------- 1 Format */}
      {!atMethod && step === 0 && (
        <StepSection
          title="Format"
          sub="What does the partner receive? The format decides which tables can feed the export, so it comes before the sources."
        >
          {sqlLanded && (
            <div className="mb-4">
              <Note tone="ok">
                Your query was read — its tables are on the Source step and its fields on Mapping. Name the export and
                pick the format, then continue.
              </Note>
            </div>
          )}
          {/* UIFPE-700: the formats as cards, icon and title on one line */}
          <span className="mb-1.5 block text-label font-semibold uppercase tracking-wide text-ink-mute">Output format</span>
          <div className="motion-choices grid gap-3.5 md:grid-cols-3" role="group" aria-label="Output format">
            {formats.map((f) => {
              const [Icon, line] = FORMAT_CARD[f.id] || [FileCode2, ""];
              return (
                <MethodCard key={f.id} icon={Icon} title={f.id.toUpperCase()} pressed={outputFormat === f.id} onClick={() => setOutputFormat(f.id)}>
                  {line}
                </MethodCard>
              );
            })}
          </div>
          {formatMeta && (
            <p className="mt-2 text-meta text-ink-mute">
              Safe defaults applied (
              {Object.keys(formatMeta.options_schema?.properties || {})
                .filter((k) => !(delimiterChoices && k === "delimiter"))
                .join(", ")}
              ). One format per export — the same data in another format is a separate export.
            </p>
          )}
          {/* UIFPE-700: packaging lives with the format now; same zip flag as
              before (UIFPE-172 default-on; the manifest stays hidden, UIFPE-535) */}
          <div className="mt-5">
            <span className="mb-1.5 block text-label font-semibold uppercase tracking-wide text-ink-mute">Packaging</span>
            <label className="inline-flex items-center gap-2 text-body text-ink">
              <input type="checkbox" className="h-4 w-4 accent-accent" checked={zipOutput} onChange={(e) => setZipOutput(e.target.checked)} />
              Compress as ZIP <Pill tone="ok">recommended</Pill>
            </label>
            <span className="mt-1 block text-meta text-ink-mute">
              The partner receives a <code>.{outputFormat || "csv"}.zip</code> holding the data file under its normal name
              — turn it off only if the partner requires uncompressed files.
            </span>
          </div>
          {delimiterChoices && (
            <div className="mt-5" role="group" aria-label="Separator">
              <span className="mb-1.5 block text-label font-semibold uppercase tracking-wide text-ink-mute">Separator</span>
              <div className="inline-flex rounded-full border border-line bg-paper-card p-0.5">
                {delimiterChoices.map((d) => (
                  <button
                    key={d}
                    type="button"
                    aria-pressed={chosenDelimiter === d}
                    onClick={() =>
                      setFormatOptions((o) => ({
                        ...o,
                        [outputFormat]: { ...o[outputFormat], delimiter: d },
                      }))
                    }
                    className={`rounded-full px-4 py-1 text-body font-semibold transition-colors ${
                      chosenDelimiter === d ? "bg-pearson-purple text-white" : "text-ink-soft hover:text-ink"
                    }`}
                  >
                    {DELIMITER_LABEL[d] || d}
                  </button>
                ))}
              </div>
              <span className="mt-1 block text-meta text-ink-mute">
                How fields are separated in the delivered file — the partner's requirement leads.
              </span>
            </div>
          )}
          {/* UIFPE-558: the partner's own XML schema, staged now and frozen
              beside the config at publish. UIFPE-586: it also decides the
              delivered file's wrapper and record element names — nothing
              asks the author for them. */}
          {outputFormat === "xml" && (
            <div className="mt-5 rounded-card border border-dashed border-paper-line bg-paper-card p-4">
              <Field
                label="Partner schema (optional)"
                hint="An XSD file from the partner — the Mappings step then guides you through its structure, and required elements are enforced before publish."
              >
                {partnerSchema ? (
                  <div className="flex items-center gap-2 text-[12.5px]">
                    <Chip>{partnerSchema.filename || "schema.xsd"}</Chip>
                    <span className="font-mono text-[10.5px] text-ink-mute">
                      {(partnerSchema.sha256 || "").slice(0, 12)}…
                    </span>
                    <Button
                      size="sm"
                      onClick={() => {
                        setPartnerSchema(null);
                        setSchemaGuide(null);
                        setSchemaError(null);
                        setPendingRows([]); // UIFPE-566: its auto-created rows go with it
                        setLeftOut([]); // UIFPE-616: and the paths left out of it
                        // UIFPE-586: the tag names were the schema's — back
                        // to the renderer's defaults with it
                        setFormatOptions((o) => {
                          const xml = { ...(o.xml || {}) };
                          delete xml.root_tag;
                          delete xml.row_tag;
                          return { ...o, xml };
                        });
                      }}
                    >
                      Remove
                    </Button>
                  </div>
                ) : (
                  // UIFPE-586 feedback: the bare <input type="file"> rendered
                  // as "Choose File  No file chosen" — a control nobody could
                  // see as a button. A real button drives a hidden input.
                  <>
                    <Button
                      size="sm"
                      variant="primary"
                      busy={schemaBusy}
                      onClick={() => schemaFileRef.current?.click()}
                    >
                      {schemaBusy ? "Uploading…" : "Choose XSD file…"}
                    </Button>
                    <input
                      ref={schemaFileRef}
                      type="file"
                      accept=".xsd,.xml"
                      className="hidden"
                      onChange={async (e) => {
                        const file = e.target.files?.[0];
                        if (!file) return;
                        setSchemaError(null);
                        setSchemaBusy(true);
                        try {
                          const content = await file.text();
                          adoptSchemaRecord(await api.uploadSchema(file.name, content));
                        } catch (err) {
                          setSchemaError(err.message);
                        } finally {
                          setSchemaBusy(false);
                          e.target.value = "";
                        }
                      }}
                    />
                  </>
                )}
              </Field>
              {/* UIFPE-660: the other door — no XSD yet, build one from a sample */}
              {!partnerSchema && (
                <div className="mt-3 flex flex-wrap items-center gap-1.5 border-t border-line pt-3 text-body text-ink-soft">
                  <span>No XSD from the partner yet?</span>
                  <button
                    type="button"
                    onClick={() => setXsdBuilderOpen(true)}
                    className="font-semibold text-accent hover:underline"
                  >
                    Build one from a sample XML file →
                  </button>
                </div>
              )}
              {xsdBuilderOpen && (
                <XsdBuilderDrawer
                  onClose={() => setXsdBuilderOpen(false)}
                  onUse={async (filename, xsd) => {
                    const record = await api.uploadSchema(filename, xsd);
                    adoptSchemaRecord(record);
                    setXsdBuilderOpen(false);
                  }}
                />
              )}
              {schemaError && <Note tone="fail">{schemaError}</Note>}
              {schemaGuide?.unsupported?.length > 0 && (
                <Note tone="warn">
                  Parts of this schema the guide does not cover:{" "}
                  {schemaGuide.unsupported.join("; ")}.
                </Note>
              )}
              {/* UIFPE-586: the schema decides the delivered file's shape —
                  say so, rather than asking the author to do anything */}
              {schemaGuide?.row_tag && (
                <div className="mt-1 text-meta text-ink-mute">
                  The delivered file will use the schema's own names — one{" "}
                  <code>{schemaGuide.row_tag}</code> per data row
                  {schemaGuide.root_tag
                    ? <>, inside <code>{schemaGuide.root_tag}</code>.</>
                    : "."}
                </div>
              )}
              {xmlTagIssues.length > 0 && (
                <Note tone="fail">
                  This schema's element names cannot be written by the XML renderer:{" "}
                  {xmlTagIssues.join(" ")}
                </Note>
              )}
            </div>
          )}
        </StepSection>
      )}

      {/* ---------------------------------------------------------- 2 Source */}
      {!atMethod && step === 1 && (
        <div className="space-y-4">
          {/* UIFPE-237: an edited version the wizard can't rebuild honestly —
              block rather than silently drop a source or corrupt a join.
              Toggling any table card clears the block (manual takeover). */}
          {editSourceGap && <Note tone="fail">{editSourceGap}</Note>}

          {/* UIFPE-700: one heading; the query door is a quiet switch (the
              Method step is its front door for a new export) */}
          <div className="flex flex-wrap items-start justify-between gap-3">
            <CardTitle
              title="Which Gold data feeds this export?"
              info={
                <>
                  Combine tables from one or more domains. The first table you tick <b>drives the feed</b>; the others join
                  onto it.
                </>
              }
            />
            <button type="button" onClick={() => setDoor(door === 0 ? 1 : 0)} className="text-meta font-semibold text-accent hover:underline">
              {door === 0 ? "Have the SQL? Read a query instead →" : "← Back to the table list"}
            </button>
          </div>

          {door === 0 && (
            <>
              {/* UIFPE-673: every table Beacon can read, at once — searchable,
                  ticked into the feed. The list is what the Gold catalog returns
                  to Beacon's own role (live, never a hand-kept list — UIFPE-229).
                  UIFPE-700: one grid with a Domains filter; ticking is the same
                  toggleSource, Make driving the same setPrimary. */}
              {(() => {
                const q = tableQuery.trim().toLowerCase();
                const all = goldDomains.flatMap((d) => domainLists[d.domain]?.tables || []);
                const pending = goldDomains.filter((d) => !domainLists[d.domain]).length;
                // configured but no tables yet (a domain ahead of its first gold load)
                const empty = goldDomains
                  .filter((d) => domainLists[d.domain]?.tables?.length === 0)
                  .map((d) => d.domain);
                const failedLists = goldDomains.filter((d) => domainLists[d.domain]?.error);
                const shownDomains = goldDomains.filter((d) => !domainsOff.includes(d.domain));
                const rows = shownDomains.flatMap((d) =>
                  (domainLists[d.domain]?.tables || [])
                    .filter((t) => !q || t.id.toLowerCase().includes(q))
                    .map((t) => ({ t, domain: d.domain }))
                );
                const withTables = goldDomains.filter((d) => domainLists[d.domain]?.tables?.length).length;
                return (
                  <div className="rounded-card border border-white/70 bg-paper-card px-[26px] py-[22px] shadow-card">
                    <div className="max-w-[420px]">
                      <span className="mb-1 block text-label font-semibold uppercase tracking-wide text-ink-mute">Domains</span>
                      <MultiSelect
                        label="Domains"
                        noun="domains"
                        items={goldDomains.map((d) => ({
                          key: d.domain,
                          label: d.domain,
                          meta: domainLists[d.domain]?.tables ? String(domainLists[d.domain].tables.length) : "…",
                        }))}
                        selected={goldDomains.map((d) => d.domain).filter((k) => !domainsOff.includes(k))}
                        onChange={(on) => setDomainsOff(goldDomains.map((d) => d.domain).filter((k) => !on.includes(k)))}
                      />
                    </div>
                    <div className="mt-5 flex flex-wrap items-center justify-between gap-3">
                      <span className="text-body">
                        <b className="text-[15px] text-ink">Source tables</b>{" "}
                        <span className="text-meta text-ink-mute">
                          {domainsState === "failed"
                            ? "The list of gold domains could not be read right now — try again shortly."
                            : domainsState === "loading" || (pending && !all.length)
                              ? "Reading the gold tables live from the catalog…"
                              : `${all.length} gold table${all.length === 1 ? "" : "s"} in ${withTables} domain${
                                  withTables === 1 ? "" : "s"
                                }, read live from the catalog${feedSources.length ? ` · ${feedSources.length} in the feed` : ""}`}
                        </span>
                      </span>
                      <div className="relative w-[240px] max-w-full">
                        <Search size={14} className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-ink-mute" aria-hidden="true" />
                        <input
                          value={tableQuery}
                          onChange={(e) => setTableQuery(e.target.value)}
                          placeholder="Find a table"
                          aria-label="Find a table"
                          className={`${inputClass} h-8 pl-8`}
                        />
                      </div>
                    </div>
                    {domainsState === "ok" && !goldDomains.length && (
                      <Note>No gold domains are configured for Beacon in this environment.</Note>
                    )}
                    {pending > 0 && !all.length && domainsState !== "failed" && (
                      <BeaconSweep label="Reading the gold tables live from the catalog…" />
                    )}
                    {/* UIFPE-167: a domain configured ahead of its first gold load
                        has no tables — named once, not shown as an empty group */}
                    {empty.length > 0 && (
                      <div className="mt-2 text-meta text-ink-mute">
                        No gold tables have landed yet in {empty.join(", ")}; they appear here after their first load.
                      </div>
                    )}
                    {failedLists.map((d) => (
                      <Note key={d.domain} tone="fail">
                        <b>{d.domain}</b> could not be read — {domainLists[d.domain].error}
                      </Note>
                    ))}
                    {all.length > 0 && (
                      <div className="mt-3">
                        <Table head={[{ label: "" }, "Table", "Domain", { label: "Columns", align: "right" }, "Data class", { label: " " }]}>
                          {rows.map(({ t, domain }) => {
                            const entry = feedSources.find((e) => e.source.id === t.id);
                            const driving = entry && primaryEntry?.source.id === t.id;
                            return (
                              <React.Fragment key={t.id}>
                                <tr className={`transition-colors ${entry ? "bg-paper-head" : "hover:bg-paper-head"}${justJoined === t.id ? " motion-row-flash" : ""}`}>
                                  <Td className="w-10">
                                    <input
                                      type="checkbox"
                                      checked={Boolean(entry)}
                                      onChange={() => toggleSource(t)}
                                      aria-label={`Use ${t.id}`}
                                      className={`h-4 w-4 accent-[var(--color-accent)]${justJoined === t.id ? " motion-pop-badge" : ""}`}
                                    />
                                  </Td>
                                  <Td>
                                    <button type="button" onClick={() => toggleSource(t)} className="inline-flex items-center gap-2 text-left font-semibold text-ink">
                                      <Table2 size={16} className="shrink-0 text-pearson-amethyst" aria-hidden="true" />
                                      {tableOf(t.id)}
                                    </button>
                                    <span className="ml-2 inline-flex flex-wrap gap-1.5 align-middle">
                                      {driving && <Pill tone="accent">drives the feed</Pill>}
                                      {entry && !driving && <Pill tone="mute">joins · {entry.alias}</Pill>}
                                    </span>
                                  </Td>
                                  <Td className="text-ink-soft">{domain}</Td>
                                  <Td className="tnum text-right text-ink-soft">{t.columns_count}</Td>
                                  <Td>
                                    {t.sensitivity ? <Pill tone={SENSITIVITY[t.sensitivity] || "mute"}>{t.sensitivity}</Pill> : <span className="text-ink-mute">{DASH}</span>}
                                  </Td>
                                  <Td className="whitespace-nowrap text-right">
                                    {entry && !driving && (
                                      <Button size="sm" onClick={() => setPrimary(entry.alias)}>
                                        Make driving
                                      </Button>
                                    )}
                                    {/* UIFPE-729: an ⓘ opens the table's attributes in a popup */}
                                    <button
                                      type="button"
                                      aria-haspopup="dialog"
                                      aria-label={`Attributes of ${tableOf(t.id)}`}
                                      title="Attributes and what they mean"
                                      onClick={() => openAttrs(t.id)}
                                      className="ml-1.5 inline-grid h-[26px] w-[26px] place-items-center rounded-full border border-line bg-paper-card align-middle text-ink-mute transition-colors hover:border-accent hover:bg-accent-soft hover:text-accent"
                                    >
                                      <Info size={13} strokeWidth={2} aria-hidden="true" />
                                    </button>
                                  </Td>
                                </tr>
                              </React.Fragment>
                            );
                          })}
                          {rows.length === 0 && (
                            <EmptyState colSpan={6}>
                              {q ? `No table matches “${tableQuery.trim()}”.` : "No tables in the chosen domains."}
                            </EmptyState>
                          )}
                        </Table>
                      </div>
                    )}
                  </div>
                );
              })()}

              {/* UIFPE-237: the join builder — one row per secondary source */}
              {isMultiSource && (
                <StepSection
                  className="border-t border-line pt-6"
                  title="Join sources"
                  sub="Every other source joins onto the driving one; it defines the feed's row grain."
                >
                  {/* UIFPE-673: the driving table is chosen in the list above
                      ("Make driving", the same setPrimary); this says which */}
                  <p className="mb-3 text-meta text-ink-mute">
                    <b className="font-semibold text-ink">{feedSources[0]?.source.id}</b> drives the feed
                    {feedSources[0]?.source?.row ? ` (${feedSources[0].source.row})` : ""}; change it with “Make
                    driving” on a table above.
                  </p>
                  <div className="space-y-3">
                    {feedSources.slice(1).map((e, i) => (
                      <JoinRow
                        key={e.alias}
                        entry={e}
                        index={i + 1}
                        feedSources={feedSources}
                        attrsBySource={attrsBySource}
                        onChange={(index, join) =>
                          setFeedSources((entries) =>
                            entries.map((en, j) => (j === index ? { ...en, join } : en))
                          )
                        }
                      />
                    ))}
                  </div>
                </StepSection>
              )}
            </>
          )}

          {door === 1 && queryPanel(false)}
        </div>
      )}

      {/* ---------------------------------------------------------- 4 Mappings */}
      {!atMethod && step === 2 && (() => {
        // UIFPE-616: held to the uploaded XSD — rows come from it, stay, and
        // no extra ones are added; optional ones may be left out
        const locked = schemaLocked(outputFormat, partnerSchema, schemaGuide);
        const leftOutRows = locked ? validLeftOut(schemaGuide, leftOut) : [];
        const leaveOut = (target) => {
          setMappings((ms) => ms.filter((m) => m.target !== target));
          setPendingRows((rows) => rows.filter((r) => r.target !== target));
          setFxMeta((old) => {
            const next = { ...old };
            delete next[target];
            return next;
          });
          setLeftOut((xs) => [...new Set([...xs, target])]);
        };
        const bringBack = (target) => {
          setLeftOut((xs) => xs.filter((t) => t !== target));
          setPendingRows((rows) => [...rows.filter((r) => r.target !== target), { target, expression: "" }]);
        };
        const leaveOutButton = (target) => (
          <Button variant="ghost" size="sm" onClick={() => leaveOut(target)}
            title="Optional in the schema — leave this element out of the file">
            Leave out
          </Button>
        );
        // UIFPE-652: the order of the rows is the file's column order. A row
        // is dragged by its grip; the drop lands before or after the row under
        // the pointer. Editing a published export asks once before the first
        // move (lib/mappingOrder.js). An XSD fixes the order: no grips.
        const canReorder = !locked && mappings.length > 1;
        const endDrag = () => {
          setDragFrom(null);
          setDropAt(null);
        };
        const requestMove = (from, to) => {
          endDrag();
          if (moveRow(mappings, from, to) === mappings) return;
          if (reorderNeedsConfirm({ editing: Boolean(editJobId), asked: reorderAsked })) {
            setPendingMove({ from, to });
            return;
          }
          setMappings((ms) => moveRow(ms, from, to));
        };
        const rowDragProps = (i) =>
          canReorder
            ? {
                onDragOver: (e) => {
                  if (dragFrom === null) return;
                  e.preventDefault();
                  e.dataTransfer.dropEffect = "move";
                  const box = e.currentTarget.getBoundingClientRect();
                  setDropAt(dropIndex(i, e.clientY, box.top, box.height));
                },
                onDrop: (e) => {
                  e.preventDefault();
                  if (dragFrom !== null && dropAt !== null) requestMove(dragFrom, dropAt);
                },
              }
            : {};
        const dropLine = (i) =>
          dragFrom === null || dropAt === null
            ? ""
            : dropAt === i
              ? " shadow-[inset_0_2px_0_var(--color-accent)]"
              : dropAt === i + 1 && i === mappings.length - 1
                ? " shadow-[inset_0_-2px_0_var(--color-accent)]"
                : "";
        // UIFPE-701: the Attributes card — one table at a time, find, click to
        // add, click a ticked one to take it out; the Gold-master meaning of
        // the hovered one underneath (UIFPE-562/592/607 notes as before)
        const attrEntries = feedSources.filter((e) => attrsBySource[e.source.id]?.columns?.length);
        const attrEntry = attrEntries.find((e) => e.source.id === attrTable) || attrEntries[0] || null;
        const helpersFor = (entry) => {
          const { source, alias } = entry;
          const a = attrsBySource[source.id];
          // UIFPE-592: a list the join spreads adds only the fields the table
          // does not already have — after the join they are the same row
          const added = elementAliasesOf(feedSources)
            .filter((el) => el.tableAlias === alias)
            .flatMap((el) => (listColumnsFor(el, feedSources, attrsBySource) || []).map((f) => ({ alias: el.alias, col: f.name, from: el.list })));
          const isList = (col) => /^array</i.test(a?.types?.[col] || "");
          const listNote = (col) => {
            if (!isList(col)) return null;
            const spread = elementAliasesOf(feedSources).find((el) => el.list === `${alias}.${col}`);
            if (spread) {
              const where = spread.tableAlias === alias
                ? "the fields below"
                : `the ${tableHeading(feedSources.find((e) => e.alias === spread.tableAlias)).split(" · ")[0]} table`;
              return `Spread into ${where}, one row per element. Pick this only to ship the whole list as one JSON cell.`;
            }
            const rel = relationships.find(
              (r) => r.driving === tableOf(source.id) && r.join?.through?.list === `{${tableOf(source.id)}}.${col}`
            );
            return `A list, shipped as one JSON cell.${rel ? ` For one row per element, add the ${rel.related.replace(/_/g, " ")} table on the Source step.` : ""}`;
          };
          return { a, added, isList, listNote };
        };
        const peekEntry = peekAttr ? feedSources.find((e) => e.source.id === peekAttr.sourceId) : null;
        const peek = peekEntry ? helpersFor(peekEntry) : null;
        const removeColumn = (alias, col) => {
          const { kept, removed } = unmapColumn(mappings, alias, col);
          if (!removed.length) return;
          setMappings(kept);
          setFxMeta((old) => {
            const next = { ...old };
            removed.forEach((m) => delete next[m.target]);
            return next;
          });
        };
        return (
        <div className="grid items-start gap-5 xl:grid-cols-[minmax(0,1fr)_clamp(300px,22vw,380px)]">
        <div className="min-w-0 space-y-5">
          <div className="rounded-card border border-white/70 bg-paper-card px-[26px] py-[22px] shadow-card">
            <div className="mb-3 flex flex-wrap items-start justify-between gap-3">
              <CardTitle
                title="Mapping"
                info={
                  <>
                    The file's columns, left to right. Type straight into a target or source cell; click <i>fx</i> to
                    transform a value.
                    {canReorder ? " Drag ⋮⋮ to reorder." : locked ? " The partner schema sets the column order." : ""}
                    {!locked && " Click an attribute on the right to add it as a field; click a ticked one to take it out."}
                  </>
                }
              />
              {/* UIFPE-659: one button until the author wants a new field */}
              {!locked && (
                <Button onClick={() => setAddOpen(true)} disabled={addOpen} title="Add a field to the file">
                  <Plus size={14} /> Add field
                </Button>
              )}
            </div>
           <div className="overflow-hidden rounded-card border border-line">
            <Table head={["Target field", "Expression / source", "Transform", "Status", ""]}>
              {mappings.length === 0 && unresolved.length === 0 && pendingRows.length === 0 && (
                <EmptyState colSpan={5}>
                  {locked
                    ? "Loading the schema's elements…"
                    : "Nothing mapped yet — click an attribute on the right, or add a field."}
                </EmptyState>
              )}
              {mappings.map((m, i) => {
                // UIFPE-425 v2: the fx cell — grey fx on a plain column,
                // accent pill naming the transformation on a built row
                const builderLabel = fxMeta[m.target]
                  ? transformById(fxMeta[m.target].id)?.label
                  : m.type === "derived"
                    ? "expression"
                    : m.type === "literal"
                      ? "fixed value"
                      : null;
                return (
                  <tr
                    key={i}
                    data-mapping-key={m.source || m.target || ""}
                    className={`transition-colors hover:bg-paper-head${dragFrom === i ? " opacity-40" : ""}${
                      fxPopup?.index === i ? " bg-accent-soft shadow-[inset_3px_0_0_var(--color-accent)]" : ""
                    }${landedRow && landedRow === (m.source || m.target) ? " motion-row-flash" : ""}${dropLine(i)}`}
                    {...rowDragProps(i)}
                  >
                    {/* UIFPE-616: the target is the schema's with an XSD, the
                        author's (editable) without one. UIFPE-652: grip and
                        column number in front of it */}
                    <Td className="font-mono">
                     <div className="flex items-center gap-1.5">
                      {canReorder && (
                        <button
                          type="button"
                          draggable
                          aria-label={`Drag to reorder ${m.target || "this row"}`}
                          title="Drag to change the column order"
                          onDragStart={(e) => {
                            e.dataTransfer.effectAllowed = "move";
                            e.dataTransfer.setData("text/plain", String(i));
                            setDragFrom(i);
                          }}
                          onDragEnd={endDrag}
                          className="-ml-1 shrink-0 cursor-grab rounded p-0.5 text-ink-mute/60 hover:bg-accent-soft hover:text-accent active:cursor-grabbing"
                        >
                          <GripVertical size={14} aria-hidden="true" />
                        </button>
                      )}
                      <span className="tnum w-5 shrink-0 text-right text-meta text-ink-mute" aria-label={`column ${i + 1}`}>{i + 1}</span>
                      <div className="min-w-0 flex-1">
                      {locked || !m.target ? (
                        m.target || (m.targets || []).join(", ")
                      ) : (
                        <InlineCell
                          value={m.target}
                          placeholder="target field"
                          onSave={(text) => {
                            const problem = renameError(mappings, i, text);
                            if (problem) return problem;
                            const next = text.trim();
                            setMappings((ms) => ms.map((x, j) => (j === i ? { ...x, target: next } : x)));
                            setFxMeta((old) => {
                              if (!old[m.target]) return old;
                              const moved = { ...old, [next]: old[m.target] };
                              delete moved[m.target];
                              return moved;
                            });
                            return null;
                          }}
                        />
                      )}
                      </div>
                     </div>
                    </Td>
                    {/* UIFPE-616: the source is always editable in place */}
                    <Td className="max-w-[340px] font-mono text-[11.5px]">
                      {!m.target ? (
                        <span className="block truncate">{mappingText(m)}</span>
                      ) : (
                        <InlineCell
                          suggest
                          value={mappingText(m)}
                          options={columnOptions}
                          placeholder="type to search attributes —or— an expression"
                          onSave={(text) => {
                            const result = editSource(m, text);
                            if (result.error) return result.error;
                            if (!result.changed) return null;
                            setMappings((ms) => ms.map((x, j) => (j === i ? result.mapping : x)));
                            // typed over a built row: it is a plain expression now
                            setFxMeta((old) => {
                              if (!old[m.target]) return old;
                              const next = { ...old };
                              delete next[m.target];
                              return next;
                            });
                            return null;
                          }}
                        />
                      )}
                    </Td>
                    <Td>
                      <button
                        onClick={() => setFxPopup({ index: i })}
                        title={builderLabel ? `Edit this transformation` : "Transform this field"}
                        className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-label font-semibold transition-colors ${
                          builderLabel
                            ? "border-accent/35 bg-accent/10 text-accent hover:bg-accent/15"
                            : "border-line text-ink-mute hover:border-accent hover:text-accent"
                        }`}
                      >
                        <span className="font-mono italic">fx</span>
                        {builderLabel && <span className="ml-1">{builderLabel}</span>}
                      </button>
                    </Td>
                    {/* UIFPE-718: filled in = valid, as the prototype shows it */}
                    <Td>{mappingFilled(m) ? <Pill tone="ok">valid</Pill> : <Pill tone="warn">empty</Pill>}</Td>
                    <Td>
                      {canRemoveRow(locked, schemaGuide, m.target) ? (
                        <Button variant="ghost" size="sm" title="Remove this row"
                          onClick={() => setMappings(mappings.filter((_, j) => j !== i))}>
                          <Trash2 size={13} />
                        </Button>
                      ) : canLeaveOut(schemaGuide, m.target) ? (
                        leaveOutButton(m.target)
                      ) : null}
                    </Td>
                  </tr>
                );
              })}
              {/* UIFPE-566: schema paths auto-created without a matching
                  column — the author points each at a source and confirms
                  with Enter or the ✓, or removes the row. Nothing commits by
                  itself: blur-committing turned half-typed text into rows. */}
              {pendingRows.map((r) => (
                <tr key={`p:${r.target}`} className="bg-pearson-amber/[0.08] shadow-[inset_3px_0_0_var(--color-pearson-amber)]">
                  <Td className="font-mono">
                    {r.target} <Pill tone="warn">needs a source</Pill>
                  </Td>
                  <Td>
                    <SourceSuggest
                      inputRef={(el) => { pendingInputRefs.current[r.target] = el; }}
                      placeholder="type to search attributes —or— an expression"
                      options={columnOptions}
                      value={r.expression}
                      onChange={(v) =>
                        setPendingRows((rows) =>
                          rows.map((x) => (x.target === r.target ? { ...x, expression: v } : x))
                        )
                      }
                      onCommit={() => commitPendingRow(r.target)}
                      onFocus={() => { activePendingRef.current = r.target; }}
                      onBlur={() =>
                        setTimeout(() => {
                          if (activePendingRef.current === r.target) activePendingRef.current = null;
                        }, 250)
                      }
                    />
                  </Td>
                  <Td className="text-meta text-ink-mute">from the partner schema</Td>
                  <Td>
                    <Pill tone="warn">empty</Pill>
                  </Td>
                  <Td>
                    <span className="flex items-center gap-1">
                      <Button
                        size="sm"
                        disabled={!r.expression.trim()}
                        onClick={() => commitPendingRow(r.target)}
                        title="Confirm this row (Enter does the same)"
                      >
                        <Check size={13} />
                      </Button>
                      {canRemoveRow(locked, schemaGuide, r.target) ? (
                        <Button
                          variant="ghost"
                          size="sm"
                          title="Remove this row"
                          onClick={() => setPendingRows((rows) => rows.filter((x) => x.target !== r.target))}
                        >
                          <Trash2 size={13} />
                        </Button>
                      ) : canLeaveOut(schemaGuide, r.target) ? (
                        leaveOutButton(r.target)
                      ) : null}
                    </span>
                  </Td>
                </tr>
              ))}
              {/* UIFPE-616: optional elements the author left out — kept in
                  view, one click brings each back */}
              {leftOutRows.map((target) => (
                <tr key={`o:${target}`} className="text-ink-mute">
                  <Td className="font-mono line-through decoration-ink-mute/50">{target}</Td>
                  <Td className="text-meta italic">left out — not in the file</Td>
                  <Td className="text-meta">optional in the schema</Td>
                  <Td>
                    <Pill tone="mute">left out</Pill>
                  </Td>
                  <Td>
                    <Button variant="ghost" size="sm" onClick={() => bringBack(target)} title="Map this element again">
                      <Undo2 size={13} /> Include
                    </Button>
                  </Td>
                </tr>
              ))}
              {unresolved.map((u, i) => (
                <tr key={`u${i}`} className="bg-pearson-amber/[0.08] shadow-[inset_3px_0_0_var(--color-pearson-amber)]">
                  <Td className="font-mono">
                    {u.target || "—"} <Pill tone="warn">unresolved</Pill>
                  </Td>
                  <Td className="font-mono text-meta">{u.expression}</Td>
                  <Td className="text-meta text-signal-warn">{u.reason.split("(")[0]}</Td>
                  <Td>
                    <Pill tone="fail">unresolved</Pill>
                  </Td>
                  <Td>
                    <Button variant="ghost" size="sm" title="Remove this row"
                      onClick={() => setUnresolved(unresolved.filter((_, j) => j !== i))}>
                      <Trash2 size={13} />
                    </Button>
                  </Td>
                </tr>
              ))}
              {/* UIFPE-616: no extra elements when held to an XSD.
                  UIFPE-659: one button until the author wants a new field */}
              {!locked && addOpen && (
              <tr className="bg-paper-head">
                <Td>
                  <input
                    ref={newTargetRef}
                    className={`${inputClass} font-mono`}
                    placeholder="New target field"
                    value={newField.target}
                    onChange={(e) => setNewField({ ...newField, target: e.target.value })}
                  />
                </Td>
                <Td>
                  <SourceSuggest
                    placeholder={`${primaryAlias}.column  —or—  to_json(price.priceSpecifications)`}
                    options={columnOptions}
                    value={newField.expression}
                    onChange={(v) => setNewField((f) => ({ ...f, expression: v }))}
                    onCommit={addField}
                  />
                </Td>
                <Td>
                  {/* UIFPE-425 v2: the guided door for fields with no source
                      column — same popup the row fx buttons open */}
                  <button
                    onClick={() => setFxPopup({ index: null })}
                    title="Build a computed field without SQL"
                    className="inline-flex items-center gap-1 whitespace-nowrap rounded-full border border-line px-2 py-0.5 text-label font-semibold text-ink-mute transition-colors hover:border-accent hover:text-accent"
                  >
                    + <span className="font-mono italic">fx</span> computed
                  </button>
                </Td>
                <Td />
                <Td className="text-right">
                  <span className="inline-flex items-center gap-1">
                    <Button variant="primary" size="sm" onClick={addField} disabled={!newField.target.trim() || !newField.expression.trim()}>
                      <Plus size={13} /> Add
                    </Button>
                    {/* UIFPE-659: delete clears and closes the row, empty or not */}
                    <Button
                      variant="ghost"
                      size="sm"
                      title="Remove this row"
                      onClick={() => {
                        setNewField({ target: "", expression: "" });
                        setAddOpen(false);
                      }}
                    >
                      <Trash2 size={13} />
                    </Button>
                  </span>
                </Td>
              </tr>
              )}
            </Table>
            {locked && (
              <div className="border-t border-line px-4 py-2 text-meta text-ink-mute">
                Rows come from <b>{partnerSchema?.filename || "the partner schema"}</b> — they stay,
                and no other elements can be added. Optional ones can be left out.
              </div>
            )}
            {mappingNotes.map((n) => (
              <div key={n} className="px-4 pb-3">
                <Note>{n}</Note>
              </div>
            ))}
           </div>
           {/* UIFPE-718: the prototype's count line, under the table */}
           <p className="mt-2 text-meta text-ink-mute">{mappingCountLine({ mappings, pending: pendingRows.length, unresolved: unresolved.length, shape: rowShape(feedSources) })}</p>
          </div>

          {/* UIFPE-652: the first reorder of a published export asks once */}
          {pendingMove && (
            <ReorderDialog
              version={editOf?.version}
              onKeep={() => setPendingMove(null)}
              onChange={() => {
                const { from, to } = pendingMove;
                setPendingMove(null);
                setReorderAsked(true);
                setMappings((ms) => moveRow(ms, from, to));
              }}
            />
          )}
          {fxPopup && (
            <TransformPopup
              position={fxPopup.index === null ? null : { index: fxPopup.index, total: mappings.length }}
              columns={feedSources.flatMap(({ source, alias }) =>
                (attrsBySource[source.id]?.columns || []).map((c) => `${alias}.${c}`)
              )}
              describe={(ref) => {
                // UIFPE-562: the Gold master's meaning of `alias.column`
                const dot = String(ref).indexOf(".");
                const entry = feedSources.find((e) => e.alias === ref.slice(0, dot));
                return entry ? attrsBySource[entry.source.id]?.descriptions?.[ref.slice(dot + 1)] ?? null : null;
              }}
              mapping={fxPopup.index != null ? mappings[fxPopup.index] : null}
              meta={fxPopup.index != null ? fxMeta[mappings[fxPopup.index]?.target] : null}
              takenTargets={mappings
                .filter((_, j) => j !== fxPopup.index)
                .map((m) => m.target)}
              onClose={() => setFxPopup(null)}
              onApply={(row, meta) => {
                const oldTarget = fxPopup.index != null ? mappings[fxPopup.index]?.target : null;
                setMappings((ms) =>
                  fxPopup.index != null
                    ? ms.map((m, j) => (j === fxPopup.index ? row : m))
                    : [...ms, row]
                );
                setFxMeta((old) => {
                  const next = { ...old };
                  if (oldTarget) delete next[oldTarget];
                  next[row.target] = meta;
                  return next;
                });
                setFxPopup(null);
              }}
              onRevert={() => {
                const m = mappings[fxPopup.index];
                const col = fxMeta[m?.target]?.values?.column;
                setMappings((ms) =>
                  col
                    ? ms.map((x, j) =>
                        j === fxPopup.index
                          ? { type: "one_to_one", source: col, target: x.target }
                          : x
                      )
                    : ms.filter((_, j) => j !== fxPopup.index)
                );
                setFxMeta((old) => {
                  const next = { ...old };
                  delete next[m?.target];
                  return next;
                });
                setFxPopup(null);
              }}
              onEditAsExpression={(target, expression) => {
                setNewField({ target, expression });
                setFxPopup(null);
              }}
            />
          )}

          {unresolved.length > 0 && (
            <Note tone="warn">
              Resolve or remove the {unresolved.length} unresolved item
              {unresolved.length === 1 ? "" : "s"} — a draft with unresolved items cannot pass validation.
            </Note>
          )}

          {!locked && feedSources.length > 0 && (
            <div className="text-meta text-ink-mute">
              Structured (array/object) columns can't ship raw in CSV — wrap them, e.g.{" "}
              <code>to_json(price.priceSpecifications)</code>.
            </div>
          )}
          {/* UIFPE-557: partner-shaped XML — the target can be a path */}
          {outputFormat === "xml" && !locked && (
            <div className="text-meta text-ink-mute">
              XML: a target field can be a path — <code>/</code> nests elements and{" "}
              <code>@</code> writes an attribute, e.g. <code>Identifiers/ISBN13</code> or{" "}
              <code>Title@lang</code>. Plain names stay flat, as today. Validate checks the
              paths before publish.
            </div>
          )}
          {/* UIFPE-558/566: the schema's paths now arrive as mapping rows, so
              this panel only carries what the table doesn't already show —
              paths that are neither mapped nor pending (i.e. rows the author
              removed), and one status line. Nothing to say = no panel. */}
          {outputFormat === "xml" && schemaGuide?.paths?.length > 0 && (() => {
            const targets = mappingTargets(mappings);
            const pendingSet = new Set([
              ...pendingRows.map((r) => r.target),
              ...validLeftOut(schemaGuide, leftOut), // UIFPE-616: a choice, not a gap
            ]);
            const missing = missingRequired(schemaGuide.paths, targets);
            const missingNotPending = missing.filter((p) => !pendingSet.has(p));
            const unlisted = schemaGuide.paths.filter(
              (p) => !targets.includes(p.path) && !pendingSet.has(p.path)
            );
            // UIFPE-587: the other direction — elements the schema never
            // declares. Never blocks; a strict schema would reject them.
            const unknown = unknownTargets(schemaGuide.declared, targets);
            if (unlisted.length === 0 && missing.length === 0 && unknown.length === 0)
              return null;
            return (
              <StepSection
                className="border-t border-line pt-6"
                title={`Partner schema · ${partnerSchema?.filename || "schema.xsd"}`}
              >
                {unknown.length > 0 && (
                  <Note tone="warn">
                    Not in this schema:{" "}
                    <b className="font-mono">{unknown.join(", ")}</b> — a partner
                    that validates strictly will reject a file carrying{" "}
                    {unknown.length === 1 ? "it" : "them"}. Publishing is still
                    allowed.
                  </Note>
                )}
                {missingNotPending.length > 0 ? (
                  <Note tone="warn">
                    Required by the schema and not mapped:{" "}
                    <b className="font-mono">{missingNotPending.join(", ")}</b> — publish is
                    blocked until these are bound.
                  </Note>
                ) : missing.length > 0 ? (
                  <Note tone="warn">
                    The highlighted row{pendingRows.length === 1 ? "" : "s"} in the field
                    mapping still need{pendingRows.length === 1 ? "s" : ""} a source.
                  </Note>
                ) : null}
                {unlisted.length > 0 && (
                  <div className="mt-2 flex flex-col gap-1">
                    {unlisted.map((p) => (
                      <div key={p.path} className="flex items-center gap-2 text-[12px]">
                        <code className="min-w-0 flex-1 truncate">{p.path}</code>
                        {p.kind === "attribute" && <Chip>attribute</Chip>}
                        <Pill tone={p.required ? "warn" : "mute"}>
                          {p.required ? "required" : "optional"}
                        </Pill>
                        <Button
                          size="sm"
                          onClick={() => {
                            // UIFPE-616: held to an XSD there is no Add row — the
                            // path comes back as a row that needs a source
                            if (locked) {
                              setPendingRows((rows) => [...rows, { target: p.path, expression: "" }]);
                              return;
                            }
                            setNewField((f) => ({ ...f, target: p.path }));
                            newTargetRef.current?.scrollIntoView({ behavior: "smooth", block: "center" });
                            newTargetRef.current?.focus({ preventScroll: true });
                          }}
                          title={locked ? "Add this element as a row to map" : "Fill the target field in the mapping table with this path"}
                        >
                          Use
                        </Button>
                      </div>
                    ))}
                  </div>
                )}
              </StepSection>
            );
          })()}

        </div>

        {/* UIFPE-701: the Attributes card on the right (UIFPE-616: not with an
            XSD — every row comes from it and has its own attribute search) */}
        {!locked && attrEntry && (() => {
          const { source, alias } = attrEntry;
          const { a, added, isList } = helpersFor(attrEntry);
          const q = attrQuery.trim().toLowerCase();
          const items = [...a.columns.map((col) => ({ alias, col, from: null })), ...added].filter((x) => !q || x.col.toLowerCase().includes(q));
          const all = attrEntries.flatMap((e) => attrsBySource[e.source.id].columns.map((col) => [e.alias, col]));
          const mappedCount = all.filter(([al, col]) => columnMapped(mappings, al, col)).length;
          const typeOf = (x) => (x.from ? "" : String(a.types?.[x.col] || "").replace(/<.*$/, "").toLowerCase());
          return (
            <aside aria-label="Attributes" className="rounded-card border border-white/70 bg-paper-card px-[26px] py-[22px] shadow-card xl:sticky xl:top-4">
              <div className="mb-2 flex items-baseline justify-between gap-2">
                <b className="text-[16px] text-ink">Attributes</b>
                <span className="text-meta text-ink-mute">{mappedCount} of {all.length} in the file</span>
              </div>
              <div className="relative">
                <Search size={14} className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-ink-mute" aria-hidden="true" />
                <input
                  value={attrQuery}
                  onChange={(e) => setAttrQuery(e.target.value)}
                  placeholder="Find an attribute"
                  aria-label="Find an attribute"
                  className={`${inputClass} h-8 pl-8`}
                />
              </div>
              {attrEntries.length > 1 && (
                <div role="tablist" aria-label="Table" className="mt-2.5 flex flex-wrap gap-1">
                  {attrEntries.map((e) => (
                    <button
                      key={e.source.id}
                      type="button"
                      role="tab"
                      aria-selected={e === attrEntry}
                      onClick={() => setAttrTable(e.source.id)}
                      className={`rounded-full px-3 py-1 text-meta font-semibold transition-colors ${
                        e === attrEntry ? "bg-pearson-purple text-white" : "bg-paper-head text-ink-soft hover:text-ink"
                      }`}
                    >
                      {tableOf(e.source.id)}
                    </button>
                  ))}
                </div>
              )}
              {/* UIFPE-632: provenance always in sight */}
              <div className="mt-2 text-meta">
                {a.provenance === "live" ? (
                  <span className="text-ink-mute">Read live from {tableOf(source.id)} — new columns appear automatically.</span>
                ) : (
                  <span className="inline-flex items-center gap-1 font-semibold text-signal-warn">
                    <AlertCircle size={12} aria-hidden="true" /> registry fallback ({a.reason || "offline"})
                  </span>
                )}
              </div>
              <div className="mt-2 max-h-[52vh] overflow-y-auto rounded-card border border-line">
                {items.length === 0 && <div className="px-3 py-4 text-center text-meta text-ink-mute">No attribute matches.</div>}
                {items.map((x) => {
                  const mapped = columnMapped(mappings, x.alias, x.col);
                  return (
                    <button
                      key={`${x.alias}.${x.col}`}
                      type="button"
                      aria-pressed={mapped}
                      onClick={(e) => {
                        if (mapped) return removeColumn(x.alias, x.col);
                        flyRef.current = { from: e.currentTarget.getBoundingClientRect(), text: x.col, keys: [`${x.alias}.${x.col}`, x.col] };
                        return addFromColumn(x.alias, x.col);
                      }}
                      onMouseEnter={() => setPeekAttr({ sourceId: source.id, col: x.col, from: x.from })}
                      onMouseLeave={() => setPeekAttr(null)}
                      onFocus={() => setPeekAttr({ sourceId: source.id, col: x.col, from: x.from })}
                      onBlur={() => setPeekAttr(null)}
                      title={mapped ? "In the file — click to take it out" : "Click to add it as a field"}
                      className={`flex w-full items-center gap-2.5 border-b border-line/70 px-3 py-2 text-left transition-colors last:border-b-0 ${
                        mapped ? "bg-pearson-mist shadow-[inset_3px_0_0_var(--color-pearson-amethyst)]" : "hover:bg-paper-head"
                      }`}
                    >
                      <span
                        aria-hidden="true"
                        className={`grid h-5 w-5 shrink-0 place-items-center rounded-full text-[11px] font-bold ${
                          mapped ? "bg-pearson-amethyst text-white" : "border border-line text-accent"
                        }`}
                      >
                        {mapped ? "✓" : "+"}
                      </span>
                      <span className="min-w-0 flex-1 truncate font-mono text-meta text-ink">{x.col}</span>
                      <span className="shrink-0 text-label text-ink-mute">
                        {x.from ? `from ${x.from.split(".").pop()}` : isList(x.col) ? "list" : typeOf(x)}
                      </span>
                    </button>
                  );
                })}
              </div>
              {/* UIFPE-562: what the hovered attribute means, from the Gold master */}
              <div className="mt-3 min-h-[3rem] rounded-r-brand border-l-[3px] border-pearson-light-purple bg-paper-head px-3 py-2 text-meta text-ink-soft">
                {peekAttr && peek ? (
                  <>
                    <span className="font-mono font-semibold text-ink">{peekAttr.col}</span> ·{" "}
                    {peekAttr.from ? (
                      <span className="text-ink-mute">from the {peekAttr.from} record on each row</span>
                    ) : (
                      peek.a.descriptions?.[peekAttr.col] ?? <span className="text-ink-mute">no description in the Gold master</span>
                    )}
                    {!peekAttr.from && peek.listNote(peekAttr.col) && <span className="text-ink-mute"> · {peek.listNote(peekAttr.col)}</span>}
                  </>
                ) : (
                  <span className="text-ink-mute">Hover an attribute to see what it means.</span>
                )}
              </div>
            </aside>
          );
        })()}
        </div>
        );
      })()}

      {/* ------------------------------------------------------------- 5 Rules */}
      {/* UIFPE-717: Rules is its own step (owner, prototype v51) — the joins
          and filters as compact rows with an automatic label, each opened in
          the popup below; Preview matches and the matched rows underneath.
          The rules themselves are the same state and editors as before. */}
      {!atMethod && step === 3 && (() => {
        const tab = rulesTab ?? (feedSources.length > 1 ? "join" : "filter");
        const joins = feedSources.slice(1);
        const driving = feedSources[0]?.source;
        return (
          <div className="space-y-5">
            <div className="rounded-card border border-white/70 bg-paper-card px-[26px] py-[22px] shadow-card">
              <div className="mb-2 flex flex-wrap items-center justify-between gap-3">
                <div className="flex flex-wrap items-center gap-4">
                  <CardTitle
                    title={
                      <>
                        Rules <span className="text-[14px] font-semibold text-ink-mute">({joins.length + filterClauses.length})</span>
                      </>
                    }
                    info="Joins bring another table's columns onto each row; filters keep only the rows that match — by attribute, identifier list or custom Spark SQL."
                  />
                  <div role="tablist" aria-label="Rule kind" className="inline-flex rounded-full border border-line bg-paper-card p-0.5">
                    {[
                      ["join", "⋈ Joins", joins.length],
                      ["filter", "▽ Filters", filterClauses.length],
                    ].map(([id, label, n]) => (
                      <button
                        key={id}
                        type="button"
                        role="tab"
                        aria-selected={tab === id}
                        onClick={() => setRulesTab(id)}
                        className={`inline-flex items-center gap-1.5 rounded-full px-3.5 py-1 text-body font-semibold transition-colors ${
                          tab === id ? "bg-pearson-purple text-white" : "text-ink-soft hover:text-ink"
                        }`}
                      >
                        {label}
                        <span className={`rounded-full px-1.5 text-label ${tab === id ? "bg-white/20" : "bg-paper-head"}`}>{n}</span>
                      </button>
                    ))}
                  </div>
                </div>
                <Button onClick={() => openRuleAdd(tab)}>
                  <Plus size={14} /> Add {tab === "join" ? "join" : "filter"}
                </Button>
              </div>
              <div className="mb-4" />
              {tab === "join" ? (
                joins.length === 0 ? (
                  <Note>
                    No joins — <b>{driving ? tableOf(driving.id) : "the driving table"}</b> is the only table. <b>Add join</b> brings in
                    another one.
                  </Note>
                ) : (
                  <div className="space-y-3">
                    <p className="text-meta text-ink-mute">
                      <b className="font-semibold text-ink">{driving?.id}</b> drives the feed
                      {driving?.row ? ` (${driving.row})` : ""}; every other table joins onto it, in this order.
                    </p>
                    <div className="overflow-hidden rounded-[12px] border border-line">
                      {joins.map((e, i) => (
                        <div key={e.alias} className="flex items-center justify-between gap-3 border-b border-line px-3.5 py-2.5 last:border-b-0">
                          <div className="min-w-0">
                            <div className="flex flex-wrap items-center gap-2">
                              <Pill tone="live">join</Pill>
                              <b className="text-body text-ink">{joinRuleLabel(e, i + 1)}</b>
                              {!joinComplete(e) && <Pill tone="fail">needs work</Pill>}
                            </div>
                            <div className="mt-0.5 break-all font-mono text-meta text-ink-soft">{describeJoin(e)}</div>
                          </div>
                          <span className="flex shrink-0 items-center gap-1.5">
                            <Button size="sm" onClick={() => openJoinEdit(e.source.id)}>
                              Edit
                            </Button>
                            <Button variant="ghost" size="sm" onClick={() => removeJoin(e)} title="Remove this join — the table leaves the feed" aria-label={`Remove join ${i + 1}`}>
                              <X size={13} />
                            </Button>
                          </span>
                        </div>
                      ))}
                    </div>
                  </div>
                )
              ) : (
                <FilterStep
                  embedded
                  onEdit={openFilterEdit}
                  clauses={filterClauses}
                  onChange={setFilterClauses}
                  ctx={filterCtx}
                  detected={detectedIdColumns}
                  compiled={compiledFilters}
                  issues={filterIssueMap}
                  check={filterCheck}
                  checkStale={checkStale}
                  onCheck={checkFilters}
                  preview={filterPreview}
                  previewStale={previewStale}
                  previewEnabled={previewEnabled}
                  profiles={profilesByAlias}
                  onPreview={startFilterPreview}
                  onCancelPreview={cancelFilterPreview}
                  download={previewDownload}
                  onDownload={startPreviewDownload}
                  onCancelDownload={cancelPreviewDownload}
                  blindSources={blindSources}
                  prefillProvenance={editJobId ? prefillProvenance : null}
                />
              )}
              {/* a rule that still needs work is said on the other tab too */}
              {tab === "join" && filterIssueMap.blocking && (
                <Note tone="fail">A filter needs work — open the Filters tab to finish or remove it.</Note>
              )}
              {tab === "filter" && !joinsOk && (
                <Note tone="fail">A join needs work — open the Joins tab to finish or remove it.</Note>
              )}
            </div>

            {/* UIFPE-441/490: Preview matches below the Rules card; the matched
                rows and their CSV appear once a preview has been asked for */}
            <FilterChecks
              clauses={filterClauses}
              ctx={filterCtx}
              issues={filterIssueMap}
              check={filterCheck}
              checkStale={checkStale}
              onCheck={checkFilters}
              preview={filterPreview}
              previewStale={previewStale}
              previewEnabled={previewEnabled}
              onPreview={startFilterPreview}
              onCancelPreview={cancelFilterPreview}
              download={previewDownload}
              onDownload={startPreviewDownload}
              onCancelDownload={cancelPreviewDownload}
              blindSources={blindSources}
            />
          </div>
        );
      })()}

      {/* --------------------------------------------------- 6 Job properties */}
      {/* UIFPE-702: where the file goes, what it is called and when it runs —
          three cards. Same state and rules as the old Output & delivery and
          Schedule steps; quality gates stay platform defaults (UIFPE-201). */}
      {!atMethod && step === 4 && (
        <div className="space-y-5">
          {/* UIFPE-599: register a destination here — email is chosen at once;
              sftp/s3 only once the test the dialog runs has passed */}
          {addDestOpen && (
            <AddDestinationDialog
              onClose={() => setAddDestOpen(false)}
              onCreated={(dest, { select }) => {
                setDestinations((ds) =>
                  ds.some((d) => d.id === dest.id) ? ds.map((d) => (d.id === dest.id ? dest : d)) : [...ds, dest]
                );
                if (select) setDestinationIds((ids) => (ids.includes(dest.id) ? ids : [...ids, dest.id]));
                // the server's view wins once it answers — but a list that does
                // not carry the record yet must not make it vanish
                api
                  .destinations()
                  .then((list) => setDestinations(list.some((d) => d.id === dest.id) ? list : [...list, dest]))
                  .catch(() => {});
              }}
            />
          )}

          <div className="rounded-card border border-white/70 bg-paper-card px-[26px] py-[22px] shadow-card">
            <CardTitle
              title="Destinations"
              info="Where the file goes. Every run delivers the same batch to each destination you pick; the managed partner-drop stands alone. Credentials are never stored here, only a reference."
            />
            {destinations.length > PICKER_THRESHOLD ? (
              // UIFPE-539: past a handful the searchable, paged picker — unchanged
              <div className="mt-4">
                <DestinationPicker
                  destinations={destinations}
                  selectedIds={destinationIds}
                  onToggle={toggleDestination}
                  targetFor={(d) => destinationTarget(d, jobSlug())}
                  currentUser={readShellSession()?.username || null}
                />
              </div>
            ) : (
              (() => {
                const types = [...new Set(["s3", "sftp", "email", ...destinations.map((d) => d.channel)])];
                const shown = destinations.filter((d) => !destTypesOff.includes(d.channel));
                return (
                  <div className="mt-4 flex flex-wrap items-end gap-3">
                    <div>
                      <span className="mb-1 block text-label font-semibold uppercase tracking-wide text-ink-mute">Type</span>
                      <MultiSelect
                        label="Destination type"
                        noun="types"
                        className="min-w-[190px]"
                        items={types.map((t) => ({ key: t, label: CHANNEL_WORD[t] || t.toUpperCase(), meta: String(destinations.filter((d) => d.channel === t).length) }))}
                        selected={types.filter((t) => !destTypesOff.includes(t))}
                        onChange={(on) => setDestTypesOff(types.filter((t) => !on.includes(t)))}
                      />
                    </div>
                    <div className="min-w-[320px] flex-1">
                      <span className="mb-1 block text-label font-semibold uppercase tracking-wide text-ink-mute">Deliver to</span>
                      <MultiSelect
                        label="Deliver to"
                        noun="destinations"
                        placeholder="Choose one or more destinations"
                        items={[
                          { key: MANAGED_KEY, label: "Managed S3 partner-drop", meta: "default" },
                          ...shown.map((d) => ({
                            key: d.id,
                            label: d.name,
                            meta: `${CHANNEL_WORD[d.channel] || d.channel} · ${isVerified(d) ? "verified" : "not tested"}`,
                          })),
                        ]}
                        selected={deliverToKeys(destinationIds)}
                        onChange={(keys) => setDestinationIds((ids) => deliverToIds(ids, keys))}
                      />
                    </div>
                  </div>
                );
              })()
            )}
            {/* the picked ones, in a table */}
            <div className="mt-4 overflow-hidden rounded-card border border-line">
              <Table head={["Name", "Type", "Address", "Status", ""]}>
                {(chosenDestinations.length ? chosenDestinations : [{ id: "", name: "Managed S3 partner-drop", channel: "s3", managed: true }]).map((d) => (
                  <tr key={d.id || "managed"} className="transition-colors hover:bg-paper-head">
                    <Td className="font-semibold text-ink">{d.name}</Td>
                    <Td className="text-ink-soft">{CHANNEL_WORD[d.channel] || d.channel}</Td>
                    <Td className="break-all font-mono text-meta text-ink-soft">{destinationTarget(d, jobSlug())}</Td>
                    <Td>
                      {d.managed ? (
                        <Pill tone="mute">default</Pill>
                      ) : (
                        // UIFPE-595: same words as the picker and the Destinations screen
                        <Pill tone={isVerified(d) ? "ok" : "warn"} title={isVerified(d) ? "" : statusText(d)}>
                          {isVerified(d) ? "verified" : "not tested"}
                        </Pill>
                      )}
                    </Td>
                    <Td className="text-right">
                      {!d.managed && (
                        <span className="inline-flex items-center gap-1.5">
                          {!isVerified(d) && (
                            <Button size="sm" onClick={() => testChosen(d)} busy={destTesting === d.id} disabled={Boolean(destTesting)}>
                              {d.channel === "email" ? "Send test email" : "Test"}
                            </Button>
                          )}
                          <Button variant="ghost" size="sm" onClick={() => toggleDestination(d.id)} title="Stop delivering here">
                            <X size={13} />
                          </Button>
                        </span>
                      )}
                    </Td>
                  </tr>
                ))}
              </Table>
            </div>
            {destTestMsg && <Note tone={destTestMsg.tone}>{destTestMsg.text}</Note>}
            <div className="mt-3 flex flex-wrap items-center gap-3">
              <Button onClick={() => setAddDestOpen(true)} title="Register a destination you own (D16) without leaving this export">
                <Plus size={14} /> New destination
              </Button>
              <span className="text-meta text-ink-mute">Created and tested here, selected at once when it passes.</span>
            </div>
            {destinations.length === 0 && (
              <Note>
                No author-owned destinations registered yet — the managed partner-drop is used, and files stay
                downloadable from the Exports tab either way.
              </Note>
            )}
            {/* UIFPE-595: said per channel */}
            {(() => {
              const untested = chosenDestinations.filter((d) => !isVerified(d));
              const mail = untested.filter((d) => d.channel === "email");
              const rest = untested.filter((d) => d.channel !== "email");
              const names = (list) => list.map((d) => d.name).join(", ");
              return (
                <>
                  {rest.length > 0 && (
                    <Note tone="warn">
                      <b>{names(rest)}</b> hasn't passed its connection test yet — the export can be published, but runs
                      will fail delivery there until it's verified (and one failing destination fails the batch).
                    </Note>
                  )}
                  {mail.length > 0 && (
                    <Note tone="warn">
                      <b>{names(mail)}</b> hasn't been proven to receive mail yet — publishing and running are fine, and
                      delivery will be attempted. To prove the address, send a test email from the Destinations screen.
                    </Note>
                  )}
                </>
              );
            })()}
          </div>

          <div className="rounded-card border border-white/70 bg-paper-card px-[26px] py-[22px] shadow-card">
            <CardTitle
              title="File name"
              info="What the partner sees. Beacon names every delivered file the same way, so two exports with the same name stay apart by their id."
            />
            <div className="mt-3 rounded-brand border border-line bg-paper-head px-3.5 py-2.5 font-mono text-body text-ink">
              {deliveredFileName({ jobId: editJobId, name: jobName, format: outputFormat, zip: zipOutput })}
            </div>
            <p className="mt-1.5 text-meta text-ink-mute">
              <code>{"{yyyyMMdd}"}</code> is the run's date and <code>{"{batch}"}</code> its batch id
              {editJobId ? "" : "; the export's id is given at publish"}. The extension follows the format and packaging.
            </p>
          </div>

          <div className="rounded-card border border-white/70 bg-paper-card px-[26px] py-[22px] shadow-card">
            <CardTitle
              title="Schedule"
              info={
                <>
                  Optional. The schedule always runs the export's <b>latest published version</b>; a run still going when
                  the next is due is skipped and recorded, never doubled. Pause, change or remove it any time from the
                  export's page without publishing a new version.
                </>
              }
            />
            <div className="mt-4 flex flex-wrap items-end gap-4">
              <label className="block w-[200px]">
                <span className="mb-1 block text-label font-semibold uppercase tracking-wide text-ink-mute">How often</span>
                <select className={inputClass} value={schedule.mode} onChange={(e) => setSchedule({ ...schedule, mode: e.target.value })}>
                  <option value="off">Manual only</option>
                  <option value="daily">Daily</option>
                  <option value="weekly">Weekly</option>
                  <option value="monthly">Monthly</option>
                </select>
              </label>
              {schedule.mode === "weekly" && (
                <label className="block w-[160px]">
                  <span className="mb-1 block text-label font-semibold uppercase tracking-wide text-ink-mute">Day of week</span>
                  <select className={inputClass} value={schedule.weekday} onChange={(e) => setSchedule({ ...schedule, weekday: e.target.value })}>
                    {WEEKDAYS.map(([id, name]) => (
                      <option key={id} value={id}>{name}</option>
                    ))}
                  </select>
                </label>
              )}
              {schedule.mode === "monthly" && (
                <label className="block w-[150px]" title="Months without this date are skipped">
                  <span className="mb-1 block text-label font-semibold uppercase tracking-wide text-ink-mute">Day of month</span>
                  <select className={inputClass} value={schedule.monthday} onChange={(e) => setSchedule({ ...schedule, monthday: +e.target.value })}>
                    {Array.from({ length: 28 }, (_, i) => i + 1).map((d) => (
                      <option key={d} value={d}>{d}</option>
                    ))}
                  </select>
                </label>
              )}
              {schedule.mode !== "off" && (
                <>
                  <label className="block w-[140px]">
                    <span className="mb-1 block text-label font-semibold uppercase tracking-wide text-ink-mute">Starting at</span>
                    <input type="time" className={inputClass} value={schedule.time} onChange={(e) => setSchedule({ ...schedule, time: e.target.value || "06:00" })} />
                  </label>
                  <label className="block w-[220px]">
                    <span className="mb-1 block text-label font-semibold uppercase tracking-wide text-ink-mute">Timezone</span>
                    <select className={inputClass} value={schedule.timezone} onChange={(e) => setSchedule({ ...schedule, timezone: e.target.value })}>
                      {[...new Set([BROWSER_TZ, ...TIMEZONES])].map((z) => (
                        <option key={z} value={z}>{z}</option>
                      ))}
                    </select>
                  </label>
                </>
              )}
              {/* UIFPE-168/674: how much each run delivers — the same two modes */}
              <div className="w-[260px]">
                <span className="mb-1 flex items-center gap-1 text-label font-semibold uppercase tracking-wide text-ink-mute">
                  Delivery type
                  <InfoTip
                    label="About delivery type"
                    text={
                      extractionMode === "incremental"
                        ? "Each run delivers only the rows changed since the last successful run; the first run is a full baseline. Deletions never appear — a removed row is simply absent from the next full load."
                        : "The whole result every run. Right for partner catalogs and when the feed joins tables."
                    }
                  />
                </span>
                <select className={inputClass} value={extractionMode} onChange={(e) => setExtractionMode(e.target.value)} aria-label="Delivery type">
                  <option value="full">Full load</option>
                  <option value="incremental" disabled={!sourceSupportsIncremental}>
                    {sourceSupportsIncremental ? "Incremental — changed rows" : isMultiSource ? "Incremental — needs a one-table feed" : "Incremental — needs a change timestamp"}
                  </option>
                </select>
              </div>
            </div>
            {schedule.mode !== "off" && (
              <Note tone="ok">
                {describeSchedule(pickerToCron(schedule), schedule.timezone)}
                {(() => {
                  const at = nextRun(pickerToCron(schedule), schedule.timezone);
                  return at ? <> · first run {fmtAbsolute(at.toISOString())}</> : null;
                })()}
                {schedule.enabled === false && <> · currently paused — publishing keeps it paused.</>}
              </Note>
            )}
            {schedule.mode === "off" && editJobId && hadSchedule && (
              <Note tone="warn">This export currently has a schedule — publishing with “Manual only” removes it.</Note>
            )}
            {/* UIFPE-237: why incremental is off, said once */}
            {isMultiSource ? (
              <p className="mt-2 text-meta text-ink-mute">
                Multi-source feeds run as full loads — the incremental window applies to a single source before the join,
                so changes in the other sources would never ship.
              </p>
            ) : (
              !sourceSupportsIncremental && (
                <p className="mt-2 text-meta text-ink-mute">
                  <b>{selected?.id ?? "This source"}</b> does not publish a change timestamp, so the feed runs as a full load.
                </p>
              )
            )}
          </div>
        </div>
      )}

      {/* -------------------------------------------------- 5 Review & publish */}
      {!atMethod && step === 5 && (() => {
        const filterLines = describeFilters(filterClauses);
        const blind = feedSources.filter((e) => attrsBySource[e.source.id]?.provenance !== "live").map((e) => e.source.id);
        const nextVersion = editJobId ? (editOf?.version ?? 0) + 1 : 1;
        const targets = mappingTargets(mappings);
        const xmlMissing = outputFormat === "xml" && schemaGuide?.paths ? missingRequired(schemaGuide.paths, targets) : [];
        const xmlUnknown = outputFormat === "xml" && schemaGuide?.paths ? unknownTargets(schemaGuide.declared, targets) : [];
        const leftOutPaths = outputFormat === "xml" && schemaGuide?.paths ? validLeftOut(schemaGuide, leftOut) : [];
        const deliverTo = chosenDestinations.length ? chosenDestinations.map((d) => d.name) : ["the managed S3 partner-drop"];
        const when = schedule.mode === "off" ? "When you run it" : describeSchedule(pickerToCron(schedule), schedule.timezone);
        const checks = [
          [feedSources.length > 0, "At least one source table"],
          [mappings.length > 0, `${mappings.length} field${mappings.length === 1 ? "" : "s"} mapped`],
          [unresolved.length === 0, unresolved.length ? `${unresolved.length} unresolved mapping item(s)` : "No unresolved mapping items"],
          ...(outputFormat === "xml" && schemaGuide?.paths
            ? [[xmlMissing.length === 0, xmlMissing.length ? "Schema-required elements unbound" : "Every schema-required element bound"]]
            : []),
          [Boolean(review?.valid), review?.valid ? "Engine validation passed" : "Validate before publishing"],
        ];
        const canPublish = feedSources.length > 0 && unresolved.length === 0 && mappings.length > 0 && review?.valid;
        // UIFPE-719: the prototype's Review — how the export was started, a
        // transform per field, the rules as rows, the destinations as a table,
        // the version it writes and what happens after
        const published = submitState?.ok ? submitState : null;
        const methodLabel = editJobId
          ? `An edit of v${editOf?.version ?? "…"}`
          : method === "sql"
            ? "Written as SQL, then read into the steps"
            : "Guided wizard";
        const fxLabelOf = (m) =>
          fxMeta[m.target] ? transformById(fxMeta[m.target].id)?.label : m.type === "derived" ? "expression" : m.type === "literal" ? "fixed value" : null;
        const joins = feedSources.slice(1);
        const previewDone = filterPreview?.status === "done";
        const author = readShellSession()?.username || null;
        const shownDestinations = chosenDestinations.length ? chosenDestinations : [{ id: "", name: "Managed S3 partner-drop", channel: "s3", managed: true }];
        return (
          <div className="space-y-5">
            {/* UIFPE-719: the intro card with the plain sentence */}
            <div className="rounded-card border border-white/70 bg-paper-card px-[26px] py-[22px] shadow-card">
              <CardTitle
                title="Review & publish"
                info="Everything you set, in one place. Change anything with its Edit button. Publishing writes an immutable version — a later change is a new version, and any earlier one can still be run."
              />
              <p className="mt-2 max-w-4xl text-[16px] leading-relaxed text-ink">
                <b className="text-pearson-amethyst">{when}</b>, Beacon reads{" "}
                <b className="text-pearson-amethyst">{feedSources.map((e) => tableOf(e.source.id)).join(" + ") || "—"}</b>
                {filterLines.length ? `, keeps the rows that match ${filterLines.length} filter${filterLines.length === 1 ? "" : "s"}` : ", keeps every row"}
                , writes <b className="text-pearson-amethyst">{mappings.length} field{mappings.length === 1 ? "" : "s"}</b> as{" "}
                <b className="text-pearson-amethyst">{outputFormat?.toUpperCase()}</b>
                {zipOutput ? " in a ZIP" : ""} and delivers it to <b className="text-pearson-amethyst">{deliverTo.join(", ")}</b>.
              </p>
            </div>

            <div className="grid items-stretch gap-5 lg:grid-cols-2">
              <ReviewCard title="Output" onEdit={() => jumpTo(0)}>
                <ReviewKv label="Method">{methodLabel}</ReviewKv>
                <ReviewKv label="Format">
                  {outputFormat?.toUpperCase()}
                  {delimiterChoices ? ` · ${chosenDelimiter}-delimited` : ""}
                  {/* UIFPE-586: the shape the partner actually receives */}
                  {outputFormat === "xml" ? ` · <${xmlOptions.root_tag || "rows"}><${xmlOptions.row_tag || "row"}>` : ""}
                </ReviewKv>
                {outputFormat === "xml" && <ReviewKv label="Partner schema">{partnerSchema?.filename || "none"}</ReviewKv>}
                <ReviewKv label="Packaging">{zipOutput ? "ZIP archive" : "Uncompressed"}</ReviewKv>
                <ReviewKv label="File name">
                  <span className="break-all font-mono text-meta">{deliveredFileName({ jobId: editJobId, name: jobName, format: outputFormat, zip: zipOutput })}</span>
                </ReviewKv>
              </ReviewCard>
              <ReviewCard title={`Source (${feedSources.length})`} onEdit={() => jumpTo(1)}>
                {/* UIFPE-748: a source read back without an alias keyed as undefined
                    and React warned — fall back to the table, then the position */}
                {feedSources.length ? (
                  feedSources.map((e, i) => (
                    <ReviewKv key={e.alias || e.source.id || i} label={i === 0 ? "Drives the feed" : "Joins"}>
                      {e.source.id}
                      {/* UIFPE-748: no empty "()" when a source has no alias */}
                      {e.alias && <span className="text-meta text-ink-mute"> ({e.alias})</span>}
                    </ReviewKv>
                  ))
                ) : (
                  <ReviewKv label="Tables">—</ReviewKv>
                )}
                {rowShape(feedSources) && <ReviewKv label="Rows">{rowShape(feedSources)}</ReviewKv>}
              </ReviewCard>
            </div>

            <ReviewCard title={`Fields (${mappings.length})`} onEdit={() => jumpTo(2)}>
              {mappings.length ? (
                <div className="overflow-hidden rounded-card border border-line">
                  <Table head={["#", "Target field", "Source", "Transform"]}>
                    {mappings.map((m, i) => {
                      const fx = fxLabelOf(m);
                      return (
                        <tr key={`${m.target}-${i}`}>
                          <Td className="tnum w-10 text-meta text-ink-mute">{i + 1}</Td>
                          <Td className="font-mono text-meta font-semibold text-ink">{m.target || (m.targets || []).join(", ")}</Td>
                          <Td className="max-w-[520px] truncate font-mono text-meta text-ink-soft" title={mappingText(m)}>{mappingText(m)}</Td>
                          <Td>
                            {fx ? (
                              <span className="inline-flex items-center gap-1 rounded-full border border-accent/35 bg-accent/10 px-2 py-0.5 text-label font-semibold text-accent">
                                <span className="font-mono italic">fx</span> {fx}
                              </span>
                            ) : (
                              <span className="text-meta text-ink-mute">straight copy</span>
                            )}
                          </Td>
                        </tr>
                      );
                    })}
                  </Table>
                </div>
              ) : (
                <p className="text-body text-signal-warn">No fields mapped yet.</p>
              )}
            </ReviewCard>

            <ReviewCard title={`Rules (${joins.length + filterClauses.length})`} onEdit={() => jumpTo(3)}>
              {/* UIFPE-719: the Rules step's rows, read-only */}
              {joins.length + filterClauses.length === 0 ? (
                <p className="text-body text-signal-warn">No rules — every joined row is exported.</p>
              ) : (
                <div className="overflow-hidden rounded-[12px] border border-line">
                  {joins.map((e, i) => (
                    <ReviewRule key={e.alias} kind="join" label={joinRuleLabel(e, i + 1)} definition={describeJoin(e)} />
                  ))}
                  {filterClauses.map((c, i) => (
                    <ReviewRule key={c.id} kind="filter" label={filterRuleLabel(c, i)} definition={filterRuleDefinition(c)} />
                  ))}
                </div>
              )}
              <p className="mt-2 text-meta text-ink-mute">
                {previewDone && !previewStale ? (
                  <>
                    <b className="text-ink">{fmtNum(filterPreview.matched)}</b> rows matched in your preview.
                  </>
                ) : previewDone ? (
                  <>The rules or fields changed since your preview — run <b>Preview matches</b> again on the Rules step.</>
                ) : (
                  <>
                    Run <b>Preview matches</b> on the Rules step to see how many rows pass.
                  </>
                )}
              </p>
            </ReviewCard>

            <ReviewCard title="Delivery & schedule" onEdit={() => jumpTo(4)}>
              {/* UIFPE-719: the destinations as the Job properties table shows them */}
              <div className="overflow-hidden rounded-card border border-line">
                <Table head={["Destination", "Type", "Address", "Status"]}>
                  {shownDestinations.map((d) => (
                    <tr key={d.id || "managed"}>
                      <Td className="font-semibold text-ink">{d.name}</Td>
                      <Td className="text-ink-soft">{CHANNEL_WORD[d.channel] || d.channel}</Td>
                      <Td className="break-all font-mono text-meta text-ink-soft">{destinationTarget(d, jobSlug())}</Td>
                      <Td>
                        {d.managed ? (
                          <Pill tone="mute">default</Pill>
                        ) : (
                          <Pill tone={isVerified(d) ? "ok" : "warn"} title={isVerified(d) ? "" : statusText(d)}>
                            {isVerified(d) ? "verified" : "not tested"}
                          </Pill>
                        )}
                      </Td>
                    </tr>
                  ))}
                </Table>
              </div>
              <ReviewKv label="Schedule">
                {schedule.mode === "off" ? "Manual only" : describeSchedule(pickerToCron(schedule), schedule.timezone)}
                {schedule.mode !== "off" &&
                  (() => {
                    const at = nextRun(pickerToCron(schedule), schedule.timezone);
                    return at ? <span className="text-meta text-ink-mute"> · first run {fmtAbsolute(at.toISOString())}</span> : null;
                  })()}
              </ReviewKv>
              <ReviewKv label="Delivery type">{extractionMode === "incremental" ? "Incremental (changed rows)" : "Full load"}</ReviewKv>
            </ReviewCard>

            {/* UIFPE-702: the Publish card — the checks, the version, Validate and
                Publish (same handlers and gate), and what happens next */}
            <div className="rounded-card border border-white/70 bg-paper-card px-[26px] py-[22px] shadow-card">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <CardTitle
                  title="Publish"
                  info="Validate runs the pipeline engine's own static validators plus a live schema check against the source — the same checks the batch would run. A draft that validates publishes as the next immutable version."
                />
                {/* UIFPE-719: draft until the version is written */}
                <Pill key={published ? "published" : "draft"} tone={published ? "ok" : "accent"} className={published ? "motion-pop-badge" : ""}>
                  {published ? `v${published.version} · published` : `v${nextVersion} · draft`}
                </Pill>
              </div>
              <div className="mt-3 space-y-2">
                <ReviewKv label="Checks">
                  <ul className="grid gap-1.5 sm:grid-cols-2">
                    {checks.map(([ok, text]) => (
                      <li key={text} className="flex items-center gap-2 text-body">
                        <span className={`grid h-5 w-5 place-items-center rounded-full text-[11px] font-bold text-white ${ok ? "bg-signal-ok" : "bg-pearson-amber text-ink"}`}>
                          {ok ? "✓" : "!"}
                        </span>
                        <span className={ok ? "text-ink" : "text-ink-soft"}>{text}</span>
                      </li>
                    ))}
                  </ul>
                </ReviewKv>
                <ReviewKv label="Version">
                  publishes <b>v{nextVersion}</b>
                  {author ? <> · author {author}</> : null} · a published version never changes
                </ReviewKv>
              </div>
              {/* UIFPE-237: the validator's column checks are all-or-nothing */}
              {blind.length > 0 && (
                <Note tone="warn">
                  Live schema unavailable for <b>{blind.join(", ")}</b> — the validator skips ALL column-reference checks when
                  any source can't be introspected, so column errors would surface at run time instead.
                </Note>
              )}
              {review?.valid && (
                <Note tone="ok">Engine validation passed — structure, expression allowlist and live column references are clean.</Note>
              )}
              {review && !review.valid &&
                review.issues.map((issue, i) => {
                  // UIFPE-750: a failed call shows as a plain sentence, raw text behind Details
                  const f = issue.where === "api" ? friendlyApiError(issue.message) : null;
                  return (
                    <Note key={i} tone="fail">
                      {f ? (
                        <>
                          {f.text}
                          {f.detail && (
                            <details className="mt-1">
                              <summary className="cursor-pointer text-meta font-semibold">Details</summary>
                              <code className="mt-1 block break-all font-mono text-meta">{f.detail}</code>
                            </details>
                          )}
                        </>
                      ) : (
                        <>
                          <b>{issue.where}</b>: {issue.message}
                        </>
                      )}
                    </Note>
                  );
                })}
              {unresolved.length > 0 && (
                <Note tone="warn">{unresolved.length} unresolved mapping item(s) remain — go back to Mappings.</Note>
              )}
              {/* UIFPE-558/587/616: both directions of the schema check, and the choices */}
              {xmlMissing.length > 0 && (
                <Note tone="fail">
                  The partner schema requires element(s) the mapping does not bind: <b className="font-mono">{xmlMissing.join(", ")}</b> — go
                  back to Mappings; publish will refuse until they are bound.
                </Note>
              )}
              {leftOutPaths.length > 0 && (
                <Note>
                  Left out (optional in the schema, not written to the file): <b className="font-mono">{leftOutPaths.join(", ")}</b>
                </Note>
              )}
              {xmlUnknown.length > 0 && (
                <Note tone="warn">
                  The mapping writes element(s) this schema does not declare: <b className="font-mono">{xmlUnknown.join(", ")}</b> — a
                  partner that validates strictly will reject the file. Publishing is allowed; the warning is recorded on the version.
                </Note>
              )}
              <div className="mt-4 flex flex-wrap items-center gap-3 border-t border-line pt-4">
                <Button onClick={validateDraft}>
                  <Check size={13} /> Validate
                </Button>
                <Button
                  variant="primary"
                  onClick={publishExport}
                  busy={publishing}
                  disabled={!canPublish}
                  title={feedSources.length === 0 ? "Select at least one source on the Source step" : !review?.valid ? "Validate first" : ""}
                >
                  {publishing ? "Publishing…" : `Publish v${nextVersion}`}
                </Button>
                <span className="ml-auto max-w-[52ch] text-right text-meta text-ink-mute">
                  Next: the version is written once and never changes.{" "}
                  {schedule.mode === "off" ? "Run it from the export's page whenever you like." : "The schedule then starts it on time; pause it from the export's page."}
                </span>
              </div>

              {/* UIFPE-627: the outcome — a success panel with the existing action, or the failure note */}
              {submitState &&
                (submitState.ok ? (
                  <div className="motion-pop-in mt-4 grid items-center gap-4 rounded-card border border-turq/50 bg-turq/10 px-5 py-4 sm:grid-cols-[40px_minmax(0,1fr)_auto]">
                    <span className="grid h-10 w-10 place-items-center rounded-full bg-turq text-ink">
                      <Check size={20} strokeWidth={2.2} aria-hidden="true" />
                    </span>
                    <div className="min-w-0">
                      <div className="text-[15px] font-semibold text-ink">Published</div>
                      <div className="text-body text-ink-soft">{submitState.text}</div>
                    </div>
                    <Button variant="primary" onClick={() => navigate(editJobId ? `/m/beacon/exports/${editJobId}` : "/m/beacon/exports")}>
                      {editJobId ? "Back to the export" : "Go to Exports"} <ArrowRight size={14} />
                    </Button>
                  </div>
                ) : (
                  <Note tone="fail">{submitState.text}</Note>
                ))}

              {/* UIFPE-719: the prototype's What happens next, once it is published */}
              {published && (
                <div className="motion-slide-down mt-4 border-t border-line pt-4">
                  <span className="mb-2 block text-label font-semibold uppercase tracking-wide text-ink-mute">What happens next</span>
                  <div className="space-y-2">
                    <ReviewKv label="1 · Published">
                      v{published.version} is frozen as{" "}
                      <code className="break-all font-mono text-meta">
                        configs/{published.exportId}/v{published.version}/{"{mapping,export}"}.json
                      </code>
                      . Nothing can change it.
                    </ReviewKv>
                    <ReviewKv label="2 · First run">
                      {schedule.mode === "off"
                        ? "Run it from the export's page whenever you like."
                        : "Run it now from the export's page, or wait for the schedule to start it."}
                    </ReviewKv>
                    <ReviewKv label="3 · Watch it">
                      The export's page shows each run, the delivered files and their row counts; Insights shows every feed.
                    </ReviewKv>
                  </div>
                </div>
              )}
            </div>
          </div>
        );
      })()}
      </div>
    </section>
  );
}

// UIFPE-425 v2: the fx popup. Every mapped row carries an fx button (the
// Power-Automate pattern); this modal picks, explains, and applies the
// field's transformation. Compilation stays pure in lib/transformBuilder.js —
// this renders the grouped searchable picker with plain-English info cards
// (the authors are non-technical), the chosen transformation's few inputs,
// and hands the finished mapping row up. Builder choices are remembered per
// target for THIS SESSION only — mapping.json keeps just the compiled
// expression (deliberate: no new config shape, nothing new for the engine).
// UIFPE-652: asked once per editing session before the first column move on
// a published export — the order is what the partner's reader sees.
function ReorderDialog({ version, onKeep, onChange }) {
  const keepRef = useRef(null);
  useEffect(() => {
    keepRef.current?.focus();
    const onKey = (e) => e.key === "Escape" && onKeep();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onKeep]);
  const v = version ?? null;
  return (
    <div className="motion-fade-in fixed inset-0 z-50 flex items-center justify-center bg-pearson-purple/45 p-4" onClick={onKeep}>
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="reorder-dialog-title"
        className="motion-pop-in w-full max-w-[460px] overflow-hidden rounded-card border border-line bg-paper-card shadow-[0_24px_48px_-20px_rgb(13_0_77_/_0.55)]"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="px-5 pb-2 pt-5">
          <h2 id="reorder-dialog-title" className="text-[15px] font-semibold tracking-tight text-ink">Change the column order?</h2>
          <p className="mt-1.5 text-body text-ink-soft">
            This export is published{v ? <> as <b>v{v}</b></> : null}. Partners who read the file by column
            position will see columns move. The new order ships with{v ? <> <b>v{v + 1}</b>,</> : null} the
            version you publish at the end of this edit.
          </p>
          <p className="mt-2 text-meta text-ink-mute">You will not be asked again while editing this export.</p>
        </div>
        <div className="mt-3 flex justify-end gap-2 border-t border-line bg-paper-head px-5 py-3">
          <Button ref={keepRef} onClick={onKeep}>Keep order</Button>
          <Button variant="primary" onClick={onChange}>Change order</Button>
        </div>
      </div>
    </div>
  );
}

// UIFPE-653: an input's label carries its small print in parentheses
// ("Text when not null (empty = keep value)") — shown under the box, not
// inside the label. Presentation only; the catalog's strings are untouched.
function splitLabel(label) {
  const m = /^(.*?)\s*\((.*)\)\s*$/.exec(label || "");
  if (!m) return { label, hint: null };
  const hint = m[2].trim();
  return { label: m[1].trim(), hint: hint.charAt(0).toUpperCase() + hint.slice(1) + (/[.!?]$/.test(hint) ? "" : ".") };
}

// UIFPE-658 drawer pieces. Module-level on purpose: defined inside the
// drawer they were new components on every render, so React remounted the
// inputs and focus was lost after each keystroke.
function FxLabelled({ label, hint, wide = false, children }) {
  const parts = splitLabel(label);
  return (
    <label className={`block ${wide ? "sm:col-span-2" : ""}`}>
      <span className="mb-1 block text-body font-semibold text-ink">{parts.label}</span>
      {children}
      {(hint || parts.hint) && <span className="mt-1 block text-meta text-ink-mute">{hint || parts.hint}</span>}
    </label>
  );
}

// a glyph per transformation for its card — the catalog carries none
const FX_GLYPH = {
  if_null: "?:", map_values: "A→B", default_when_empty: "∅→", date_format: "d/m", combine: "a+b",
  split_part: "a|b", change_case: "Aa", find_replace: "a⇄b", substring: "[..]", pad_to_width: "00a",
  round_number: ".00", mask_hash: "#", fixed_value: "\u201ca\u201d",
};
const fxSentences = (text) => String(text || "").split(/(?<=\.)\s+/);
const firstSentence = (text) => fxSentences(text)[0] || "";
const restSentences = (text) => fxSentences(text).slice(1).join(" ");

function FxStepHead({ n, title, right }) {
  return (
    <div className="mb-3 flex items-center gap-2.5">
      <span className="grid h-6 w-6 shrink-0 place-items-center rounded-full bg-accent text-label font-bold text-white">{n}</span>
      <h3 className="text-[16px] font-bold tracking-tight text-ink">{title}</h3>
      {right && <span className="ml-auto">{right}</span>}
    </div>
  );
}

function FxGlyph({ id, onCard }) {
  return (
    <span className={`grid h-[34px] w-[34px] shrink-0 place-items-center rounded-brand font-mono text-meta text-accent ${onCard ? "bg-paper-head" : "bg-paper-card"}`}>
      {FX_GLYPH[id] || "fx"}
    </span>
  );
}

function TransformPopup({ columns, describe = () => null, mapping, meta, takenTargets, position = null, onApply, onRevert, onClose, onEditAsExpression }) {
  // a row opened from a plain column starts pre-pointed at it; a row the
  // builder made earlier restores its remembered choices; anything else
  // (a hand-typed expression, or the "+ fx computed field" door) starts fresh
  const fromPlainColumn = mapping?.type === "one_to_one";
  const foreignExpression = Boolean(mapping) && !fromPlainColumn && !meta;
  const [transformId, setTransformId] = useState(meta?.id ?? "if_null");
  const [values, setValues] = useState(() =>
    meta?.values ?? (fromPlainColumn ? { column: mapping.source } : {})
  );
  const [target, setTarget] = useState(mapping?.target ?? "");
  const [targetTouched, setTargetTouched] = useState(Boolean(mapping));
  const [search, setSearch] = useState("");

  // a stale remembered id (catalog renamed between sessions) must not crash
  // the popup — fall back to the first entry
  const t = transformById(transformId) ?? TRANSFORMS[0];
  const preview = previewTransform(t.id, values);
  const result = buildTransform(t.id, values, target);
  const duplicate = takenTargets.includes(target.trim());
  const started = Object.keys(values).length > 0 || target.trim() !== "";
  // a transformed row can revert to its plain column; without one, "remove"
  // means removing the whole field
  const revertColumn = meta?.values?.column || (fromPlainColumn ? mapping.source : null);

  const setValue = (key, v) => {
    const next = { ...values, [key]: v };
    setValues(next);
    if (!targetTouched) setTarget(suggestTarget(next));
  };

  const pickTransform = (id) => {
    setTransformId(id);
    // a picked column survives the switch — everything else is per-transform
    setValues(values.column ? { column: values.column } : {});
  };

  const apply = () => {
    if (!result.ok || duplicate) return;
    onApply(result.row, { id: t.id, values });
  };

  const q = search.trim().toLowerCase();
  const visible = TRANSFORMS.filter(
    (tr) => !q || tr.label.toLowerCase().includes(q) || tr.info.what.toLowerCase().includes(q)
  );

  const selectClass = `${inputClass} font-mono`;

  // UIFPE-658: the popup is a full-height drawer from the right, in three
  // steps — choose (cards), set up, check. A built row starts with its
  // choice folded; a plain column or a new field starts on the cards.
  const [choosing, setChoosing] = useState(!meta);
  useEffect(() => {
    const onKey = (e) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const heading = mapping ? (fromPlainColumn ? mapping.source : mapping.target) : "New computed field";
  const valid = result.ok && !duplicate;

  return (
    <div className="motion-fade-in fixed inset-0 z-50 bg-pearson-purple/45" onClick={onClose}>
      <aside
        role="dialog"
        aria-modal="true"
        aria-labelledby="transform-dialog-title"
        className="motion-slide-in-right absolute inset-y-0 right-0 grid w-full max-w-[720px] grid-rows-[auto_minmax(0,1fr)_auto] border-l border-line bg-paper-card shadow-[-24px_0_48px_-24px_rgb(13_0_77_/_0.5)]"
        onClick={(e) => e.stopPropagation()}
      >
        {/* header: what is being transformed */}
        <div className="flex items-start gap-3 bg-pearson-purple px-6 pb-4 pt-5 text-white">
          <div className="min-w-0">
            <div className="text-label font-semibold uppercase tracking-[0.12em] text-pearson-light-purple">
              Transform{position ? ` · column ${position.index + 1} of ${position.total}` : " · new computed field"}
            </div>
            <div id="transform-dialog-title" className="mt-1 break-all font-mono text-[20px] leading-tight">{heading}</div>
          </div>
          <button
            type="button"
            aria-label="Close"
            onClick={onClose}
            className="ml-auto grid h-8 w-8 shrink-0 place-items-center rounded-brand border border-white/30 text-white transition-colors hover:bg-white/10"
          >
            <X size={15} />
          </button>
        </div>

        <div className="min-h-0 overflow-y-auto px-6">
          {foreignExpression && (
            <div className="pt-4">
              <Note tone="warn">
                This field is a hand-written expression — applying a transformation here replaces
                it. To fine-tune it instead, use "Edit as expression" below.
              </Note>
            </div>
          )}

          {/* step 1: choose, by card */}
          <section className="border-b border-line py-5">
            <FxStepHead
              n={1}
              title="What should happen to this value?"
              right={
                !choosing && (
                  <button type="button" onClick={() => setChoosing(true)} className="text-meta font-semibold text-accent hover:underline">
                    Change
                  </button>
                )
              }
            />
            {choosing ? (
              <>
                <input
                  className={inputClass}
                  placeholder="Search — try “date”, “blank”, “uppercase”"
                  aria-label="Search transformations"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                />
                {TRANSFORM_GROUPS.map((group) => {
                  const inGroup = visible.filter((tr) => tr.group === group);
                  if (inGroup.length === 0) return null;
                  return (
                    <div key={group}>
                      <div className="mb-1.5 mt-3.5 text-label font-semibold uppercase tracking-[0.12em] text-ink-mute">{group}</div>
                      <div className="grid gap-2 sm:grid-cols-2">
                        {inGroup.map((tr) => (
                          <button
                            key={tr.id}
                            type="button"
                            aria-pressed={tr.id === transformId}
                            onClick={() => {
                              pickTransform(tr.id);
                              setChoosing(false);
                            }}
                            className={`grid grid-cols-[34px_minmax(0,1fr)] items-start gap-x-2.5 gap-y-0.5 rounded-card border-[1.5px] px-3 py-2.5 text-left transition-colors ${
                              tr.id === transformId ? "border-accent bg-accent-soft" : "border-line bg-paper-card hover:border-paper-line"
                            }`}
                          >
                            <span className="row-span-3"><FxGlyph id={tr.id} onCard={tr.id !== transformId} /></span>
                            <span className="text-body font-semibold text-ink">{tr.label}</span>
                            <span className="text-meta text-ink-soft">{firstSentence(tr.info.what)}</span>
                            {tr.info.example[0] && (
                              <span className="mt-0.5 break-all font-mono text-label text-ink-mute">
                                <span className="text-ink">{tr.info.example[0][0]}</span> → {tr.info.example[0][1]}
                              </span>
                            )}
                          </button>
                        ))}
                      </div>
                    </div>
                  );
                })}
                {visible.length === 0 && (
                  <div className="pt-3 text-meta text-ink-mute">Nothing matches "{search}".</div>
                )}
              </>
            ) : (
              <div className="flex items-center gap-3 rounded-card border-[1.5px] border-accent bg-accent-soft px-3 py-2.5">
                <FxGlyph id={t.id} />
                <div className="min-w-0">
                  <div className="text-body font-semibold text-ink">{t.label}</div>
                  <div className="text-meta text-ink-soft">{firstSentence(t.info.what)}</div>
                </div>
              </div>
            )}
          </section>

          {!choosing && (
            <>
              {/* step 2: the transformation's inputs */}
              <section className="border-b border-line py-5">
                <FxStepHead n={2} title="Set it up" />
                {restSentences(t.info.what) && (
                  <p className="mb-3.5 ml-[34px] max-w-[62ch] text-body text-ink-soft">{restSentences(t.info.what)}</p>
                )}
                <div className="grid gap-3.5 sm:grid-cols-2">
                  {/* UIFPE-718: the prototype's order — the target, then the source */}
                  <FxLabelled label="Target field — the column name in the file" hint="A new name adds a column; the attribute's own name replaces it.">
                    <input
                      className={selectClass}
                      placeholder="ppidFlag"
                      value={target}
                      onChange={(e) => {
                        setTarget(e.target.value);
                        setTargetTouched(true);
                      }}
                    />
                  </FxLabelled>
                  {t.inputs.map((input) => {
                    if (input.kind === "column") {
                      // UIFPE-718: the source can be changed here, with its
                      // Gold-master meaning under it
                      const picked = values[input.key];
                      const meaning = picked ? describe(picked) : null;
                      return (
                        <FxLabelled
                          key={input.key}
                          label={input.label}
                          hint={picked ? (meaning ? <>Gold meaning: <i>{meaning}</i></> : "No description in the Gold master.") : null}
                        >
                          <select
                            className={selectClass}
                            value={values[input.key] || ""}
                            onChange={(e) => setValue(input.key, e.target.value)}
                          >
                            <option value="">— pick a column —</option>
                            {columns.map((c) => (
                              <option key={c} value={c}>
                                {c}
                              </option>
                            ))}
                            {picked && !columns.includes(picked) && <option value={picked}>{picked}</option>}
                          </select>
                        </FxLabelled>
                      );
                    }
                    if (input.kind === "select")
                      return (
                        <FxLabelled key={input.key} label={input.label}>
                          <select
                            className={inputClass}
                            value={values[input.key] || input.options[0].value}
                            onChange={(e) => setValue(input.key, e.target.value)}
                          >
                            {input.options.map((o) => (
                              <option key={o.value} value={o.value}>
                                {o.label}
                              </option>
                            ))}
                          </select>
                        </FxLabelled>
                      );
                    if (input.kind === "number" || input.kind === "text")
                      return (
                        <FxLabelled key={input.key} label={input.label}>
                          <input
                            className={input.kind === "text" ? selectClass : inputClass}
                            type={input.kind === "number" ? "number" : "text"}
                            min={input.min}
                            placeholder={input.placeholder || ""}
                            value={values[input.key] ?? ""}
                            onChange={(e) => setValue(input.key, e.target.value)}
                          />
                        </FxLabelled>
                      );
                    return null;
                  })}

                  {/* pick-many and pairs render full-width — they need the room */}
                  {t.inputs.map((input) => {
                    if (input.kind === "columns") {
                      const picked = values[input.key] || [];
                      return (
                        <FxLabelled key={input.key} label={input.label} hint="Click a column to add it, click again to remove it." wide>
                          <div className="flex max-h-40 flex-wrap gap-1.5 overflow-y-auto rounded-brand border border-line p-2">
                            {columns.map((c) => {
                              const i = picked.indexOf(c);
                              return (
                                <button
                                  key={c}
                                  type="button"
                                  onClick={() =>
                                    setValue(input.key, i >= 0 ? picked.filter((x) => x !== c) : [...picked, c])
                                  }
                                  className={`rounded-brand border px-2 py-1 font-mono text-meta transition-colors ${
                                    i >= 0
                                      ? "border-accent bg-accent/10 text-accent"
                                      : "border-line bg-paper-head text-ink hover:border-accent"
                                  }`}
                                >
                                  {i >= 0 ? `${i + 1}. ` : "+ "}
                                  {c}
                                </button>
                              );
                            })}
                          </div>
                        </FxLabelled>
                      );
                    }
                    if (input.kind === "pairs") {
                      const pairs = values[input.key] || [{ from: "", to: "" }];
                      const setPairs = (p) => setValue(input.key, p);
                      return (
                        <FxLabelled key={input.key} label={input.label} wide>
                          <div className="space-y-1.5">
                            {pairs.map((p, i) => (
                              <div key={i} className="flex items-center gap-2">
                                <input
                                  className={`${selectClass} max-w-[200px]`}
                                  placeholder="stored value"
                                  value={p.from}
                                  onChange={(e) =>
                                    setPairs(pairs.map((x, j) => (j === i ? { ...x, from: e.target.value } : x)))
                                  }
                                />
                                <span className="text-ink-mute">→</span>
                                <input
                                  className={`${selectClass} max-w-[200px]`}
                                  placeholder="export label"
                                  value={p.to}
                                  onChange={(e) =>
                                    setPairs(pairs.map((x, j) => (j === i ? { ...x, to: e.target.value } : x)))
                                  }
                                />
                                <Button variant="ghost" size="sm" onClick={() => setPairs(pairs.filter((_, j) => j !== i))}>
                                  <X size={13} />
                                </Button>
                              </div>
                            ))}
                            <Button variant="ghost" size="sm" onClick={() => setPairs([...pairs, { from: "", to: "" }])}>
                              <Plus size={13} /> Add pair
                            </Button>
                          </div>
                        </FxLabelled>
                      );
                    }
                    return null;
                  })}

                </div>
                {/* UIFPE-718: the SQL it renders, always in view */}
                <p className="mt-3.5 text-meta text-ink-mute">
                  Renders:{" "}
                  <code className="break-all font-mono text-ink">{preview || "— finish the fields above —"}</code>
                </p>
              </section>

              {/* step 3: the outcome — the catalog's examples, the checks, the SQL */}
              <section className="py-5">
                <FxStepHead
                  n={3}
                  title="Check the result"
                  right={<Pill tone={valid ? "ok" : "warn"}>{valid ? "valid" : "not yet valid"}</Pill>}
                />
                <div className="overflow-hidden rounded-card border border-line">
                  <table className="w-full text-body">
                    <thead>
                      <tr>
                        <th className="bg-paper-head px-3 py-2 text-left font-mono text-meta font-medium text-ink-soft">
                          {values.column || "before"}
                        </th>
                        <th className="bg-turq/15 px-3 py-2 text-left font-mono text-meta font-medium text-ink">
                          {target.trim() || "after"}
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {t.info.example.map(([before, after], i) => (
                        <tr key={i} className="border-t border-line">
                          <td className="px-3 py-2 font-mono text-meta text-ink-soft">{before}</td>
                          <td className="bg-turq/10 px-3 py-2 font-mono text-meta text-ink">{after}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  <div className="border-t border-line bg-paper-head px-3 py-1.5 text-meta text-ink-mute">
                    Example values for this transformation.
                  </div>
                </div>

                {started && !result.ok && <Note tone="warn">{result.error}</Note>}
                {duplicate && (
                  <Note tone="fail">
                    A mapped field already uses the name {target.trim()} — pick another, or remove
                    that row first.
                  </Note>
                )}

              </section>
            </>
          )}
        </div>

        {/* footer: the field's other exits on the left, the decision on the right */}
        <div className="flex flex-wrap items-center gap-2 border-t border-line bg-paper-head px-6 py-3.5">
          {mapping && (meta || !fromPlainColumn) && (
            <Button variant="ghost" size="sm" onClick={onRevert}>
              {revertColumn ? "Remove transformation" : "Remove field"}
            </Button>
          )}
          {(result.ok && result.row.type === "derived" ? result.row.expression : mapping?.expression) && (
            <Button
              variant="ghost"
              size="sm"
              onClick={() =>
                onEditAsExpression(
                  target.trim() || mapping?.target || "",
                  result.ok && result.row.type === "derived"
                    ? result.row.expression
                    : mapping.expression
                )
              }
              title="Put the expression into the expert row for hand-editing"
            >
              <Wand2 size={13} /> Edit as expression
            </Button>
          )}
          <span className="flex-1" />
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" onClick={apply} disabled={!valid}>
            <Check size={13} /> Apply{target.trim() ? ` to ${target.trim()}` : ""}
          </Button>
        </div>
      </aside>
    </div>
  );
}

function destinationTarget(d, jobSlug) {
  if (d.managed) return `s3://<beacon-bucket>/partner-drop/inbound/${jobSlug || "<job-id>"}`;
  if (d.channel === "s3") return d.destination_s3_prefix;
  if (d.channel === "email") return (d.recipients || []).join(", ");
  return `${d.host}:${d.port ?? 22}${d.remote_dir}`;
}

// Destination record -> the beacon export config's delivery block. Returns
// ---- UIFPE-441/490: polling one asynchronous Athena job ---------------------
// The filter preview and the CSV download share this: poll while `running`
// — the ExportDetail run-status idiom (a cancelled flag, a fixed interval,
// transient failures ignored) — with a UI cap that stops the executions
// rather than leaving them to Athena's own limit. A terminal result merges
// into the job's state, so `signature` and the rest survive it.
function useAthenaPolling(job, setJob, { status, cancel, capReason }) {
  useEffect(() => {
    if (!job || job.status !== "running" || !job.id) return undefined;
    const { id, startedAt } = job;
    let cancelled = false;
    const poll = async () => {
      let result;
      try {
        result = await status(id);
      } catch {
        return; // transient — keep polling
      }
      if (cancelled) return;
      if (result.status === "running") {
        if ((Date.now() - startedAt) / 1000 > PREVIEW_UI_CAP_S) {
          cancel(id).catch(() => {});
          setJob((p) => ({ ...p, status: "cancelled", id: undefined, reason: capReason }));
        } else {
          setJob((p) => ({ ...p, elapsed_s: result.elapsed_s }));
        }
        return;
      }
      setJob((p) => ({ ...p, ...result }));
    };
    poll();
    const timer = setInterval(poll, PREVIEW_POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [job?.id, job?.status]); // eslint-disable-line react-hooks/exhaustive-deps
}

// null for the managed partner-drop so the publisher keeps owning that path.
function deliveryFromDestination(d) {
  if (!d) return null;
  if (d.channel === "s3")
    return { channel: "s3", adapter: "s3_copy", destination_s3_prefix: d.destination_s3_prefix };
  if (d.channel === "email")
    // UIFPE-444: Pearson Notifications API (the publisher re-stamps this
    // server-side too — the client value is display-only in settings)
    return {
      channel: "email",
      adapter: "pearson_notifications",
      recipients: d.recipients,
    };
  return {
    channel: "sftp",
    adapter: "paramiko",
    host: d.host,
    port: d.port ?? 22,
    remote_dir: d.remote_dir,
    secret_ref: d.secret_ref,
    upload_pattern: "tmp-then-rename",
  };
}

// ---- UIFPE-237/167: the multi-source helpers live in lib/feedSources.js ------
// (pure functions, vitest-covered); only the React pieces stay here.

// UIFPE-237: the join builder row for one secondary source — dropdown-built
// equality pairs by default, a raw Spark SQL escape hatch for everything the
// dropdowns can't express (and forced when the source's columns are unknown).
// UIFPE-167: four join types (left, inner, full, cross); a cross join has no
// condition, so its pair/custom controls hide.
function JoinRow({ entry, index, feedSources, attrsBySource, onChange }) {
  const prior = feedSources.slice(0, index);
  const ownColumns = knownColumns(entry.source, attrsBySource);
  const join = entry.join ?? { type: "left", pairs: [{ leftRef: "", rightColumn: "" }], condition: null };
  const isCross = join.type === "cross";
  // UIFPE-581: a join prefilled from the declared relationships shows its
  // sentence instead of dropdowns; "Advanced" drops to the custom condition
  const relMode = Boolean(join.relationship) && !isCross;
  const forcedCustom = ownColumns.length === 0;
  const customMode = !isCross && !relMode && (forcedCustom || join.condition != null);
  // an author's edit is never upgraded again by the relationships (auto: false)
  const patch = (p) => onChange(index, { ...join, ...p, auto: false });
  const patchPair = (i, p) =>
    patch({ pairs: (join.pairs || []).map((pair, j) => (j === i ? { ...pair, ...p } : pair)) });
  // the attachment types the wizard offers; a converter/edit prefill carrying
  // another engine type renders it as an extra option so the prefill never lies
  const typeOptions = [...JOIN_TYPES];
  if (join.type && !typeOptions.includes(join.type)) typeOptions.push(join.type);
  const condition = joinConditionOf(entry);
  // leaving cross with no pairs yet (a converter-prefilled cross join) seeds
  // the default pair so the author has a condition to edit, not a blank row
  const setType = (type) => {
    const needsPairs = type !== "cross" && join.condition == null && !(join.pairs || []).length;
    patch(needsPairs ? { type, pairs: defaultJoinPairs(entry, prior, attrsBySource) } : { type });
  };

  return (
    <div className="rounded-card border border-white/70 bg-paper-card px-[26px] py-[22px] shadow-card">
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <span className="text-body font-semibold text-ink">{entry.source.id}</span>
        <Chip>{entry.alias}</Chip>
        <div className="ml-auto w-32">
          <select
            className={inputClass}
            value={join.type || "left"}
            onChange={(e) => setType(e.target.value)}
            aria-label={`Join type for ${entry.alias}`}
          >
            {typeOptions.map((t) => (
              <option key={t} value={t}>
                {t} join
              </option>
            ))}
          </select>
        </div>
      </div>
      {relMode && (
        <div className="space-y-1.5">
          <div className="text-[12.5px] text-ink">
            <b>{entry.alias}</b>: {join.relationship.summary}.
          </div>
          {join.relationship.narrow_by?.length > 0 && (
            <div className="text-[11.5px] text-ink-mute">
              One row per match. To keep fewer rows, narrow with a filter on the Rules step by{" "}
              {join.relationship.narrow_by.map((c, i) => (
                <span key={c}>
                  {i > 0 ? ", " : ""}
                  <code>{c}</code>
                </span>
              ))}
              .
            </div>
          )}
          <Button variant="ghost" size="sm" onClick={() => patch({ relationship: null })}>
            Advanced: edit the join
          </Button>
        </div>
      )}
      {!isCross && !customMode && !relMode && (
        <div className="space-y-2">
          {(join.pairs || []).map((pair, i) => (
            <div key={i} className="flex flex-wrap items-center gap-2">
              <div className="w-56">
                <select
                  className={`${inputClass} font-mono`}
                  value={pair.leftRef}
                  onChange={(e) => patchPair(i, { leftRef: e.target.value })}
                  aria-label="Left column"
                >
                  <option value="">— column —</option>
                  {prior.map((p) => (
                    <optgroup key={p.alias} label={`${p.alias} · ${p.source.id}`}>
                      {knownColumns(p.source, attrsBySource).map((c) => (
                        <option key={c} value={`${p.alias}.${c}`}>
                          {p.alias}.{c}
                        </option>
                      ))}
                    </optgroup>
                  ))}
                </select>
              </div>
              <span className="font-mono text-[12px] text-ink-mute">=</span>
              <div className="w-56">
                <select
                  className={`${inputClass} font-mono`}
                  value={pair.rightColumn}
                  onChange={(e) => patchPair(i, { rightColumn: e.target.value })}
                  aria-label={`${entry.alias} column`}
                >
                  <option value="">— column —</option>
                  {ownColumns.map((c) => (
                    <option key={c} value={c}>
                      {entry.alias}.{c}
                    </option>
                  ))}
                </select>
              </div>
              {(join.pairs || []).length > 1 && (
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => patch({ pairs: join.pairs.filter((_, j) => j !== i) })}
                  aria-label="Remove condition"
                >
                  <X size={13} />
                </Button>
              )}
            </div>
          ))}
          <Button
            size="sm"
            onClick={() => patch({ pairs: [...(join.pairs || []), { leftRef: "", rightColumn: "" }] })}
          >
            <Plus size={13} /> condition
          </Button>
        </div>
      )}
      {customMode && (
        <input
          className={`${inputClass} font-mono`}
          value={join.condition ?? ""}
          onChange={(e) => patch({ condition: e.target.value })}
          placeholder="product.ppid = price.ppid AND price.vatCountry = 'GB'"
        />
      )}
      {!isCross && forcedCustom && !relMode && (
        <Note>
          No column list is available for <b>{entry.source.id}</b> yet (introspection offline
          or still loading) — write the join condition as Spark SQL.
        </Note>
      )}
      {!isCross && !forcedCustom && !relMode && (
        <Button
          variant="ghost"
          size="sm"
          className="mt-2"
          onClick={() =>
            patch(
              customMode
                ? { condition: null, pairs: join.pairs?.length ? join.pairs : [{ leftRef: "", rightColumn: "" }] }
                : { condition: "" }
            )
          }
        >
          {customMode ? "Back to column dropdowns" : "Custom condition (Spark SQL)"}
        </Button>
      )}
      <div className="mt-2 text-meta text-ink-mute">
        {isCross ? (
          <span>no condition — every row pairs with every row</span>
        ) : (
          <>ON {condition ? <Chip>{condition}</Chip> : <span>— no join condition yet</span>}</>
        )}
      </div>
      {/* UIFPE-167: type-aware, naming the table this join actually attaches
          to (the config's `left`), which need not be the driving source */}
      <Note>{explainJoin(join.type || "left", joinLeftAlias({ ...entry, join }, prior), entry.alias)}</Note>
    </div>
  );
}

// UIFPE-673: a table's attributes, read-only, from the Source step; choosing
// attributes still happens on the Mapping step.
// UIFPE-729 (owner): shown in a popup behind an ⓘ — Attribute and Meaning
// (the Gold master's words, UIFPE-562), nothing else. Escape, ✕ or a click
// outside closes it.
function AttributesDialog({ table, data, onClose }) {
  const closeRef = useRef(null);
  useEffect(() => {
    closeRef.current?.focus();
    const onKey = (e) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  const columns = data?.columns || [];
  return (
    <div className="motion-fade-in fixed inset-0 z-50 flex items-start justify-center bg-pearson-purple/45 px-4 py-[4vh]" onClick={onClose}>
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="attributes-dialog-title"
        className="motion-pop-in flex max-h-[88vh] w-full max-w-[860px] flex-col overflow-hidden rounded-card bg-paper-card shadow-[0_24px_64px_rgb(13_0_77_/_0.35)]"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between gap-3 border-b border-line px-[26px] py-4">
          <h2 id="attributes-dialog-title" className="flex items-center gap-2 text-[18px] font-extrabold tracking-tight text-ink">
            <Table2 size={18} className="text-pearson-amethyst" aria-hidden="true" />
            {table}
            {columns.length > 0 && <span className="text-[14px] font-semibold text-ink-mute">· {columns.length} attributes</span>}
          </h2>
          <Button ref={closeRef} size="sm" onClick={onClose} aria-label="Close" className="w-7 px-0">
            <X size={14} />
          </Button>
        </div>
        <div className="overflow-y-auto px-[26px] pb-[22px] pt-3">
          {!data || data.loading ? (
            <p className="py-3 text-meta text-ink-mute">Reading the table’s attributes…</p>
          ) : data.error || !columns.length ? (
            <Note tone="warn">The attributes could not be read right now{data.error ? ` — ${data.error}` : ""}.</Note>
          ) : (
            <>
              {data.provenance && data.provenance !== "live" && (
                <Note tone="warn">Registry fallback ({data.reason || "offline"}): the live catalog could not be read.</Note>
              )}
              <table className="w-full border-collapse text-body">
                <thead className="sticky top-0 bg-paper-card">
                  <tr>
                    {["Attribute", "Meaning"].map((h) => (
                      <th key={h} className="border-b-2 border-ink px-2 py-2 text-left text-meta font-bold text-ink">
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {columns.map((c) => (
                    <tr key={c}>
                      <td className="w-[34%] border-b border-line/60 px-2 py-2 align-top font-mono font-semibold text-ink">{c}</td>
                      <td className="border-b border-line/60 px-2 py-2 text-ink-soft">{data.descriptions?.[c] || DASH}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

// UIFPE-700: a choice card — icon and title on one line, a line of text, an
// optional hint; pressed shows the tick. Used for Method and Format.
function MethodCard({ icon: Icon, title, children, hint, badge, pressed = false, disabled = false, onClick }) {
  return (
    <button
      type="button"
      aria-pressed={pressed}
      disabled={disabled}
      onClick={onClick}
      className={`relative grid grid-cols-[auto_minmax(0,1fr)] items-center gap-x-3 rounded-[14px] border-[1.5px] p-[18px] text-left transition-[border-color,box-shadow,background] disabled:cursor-not-allowed disabled:opacity-60 ${
        pressed
          ? "border-pearson-amethyst bg-paper-head outline outline-2 outline-pearson-light-purple"
          : "border-line bg-paper-card enabled:hover:border-pearson-amethyst enabled:hover:shadow-[0_4px_14px_rgb(81_46_171_/_0.14)]"
      }`}
    >
      <span className="grid h-10 w-10 place-items-center rounded-xl bg-accent-soft text-pearson-amethyst">
        <Icon size={20} strokeWidth={1.8} aria-hidden="true" />
      </span>
      <span className={`${badge ? "pr-24" : "pr-7"} text-[16px] font-bold text-ink`}>{title}</span>
      {badge && (
        <span className="absolute right-3 top-3">
          <Pill tone="warn">{badge}</Pill>
        </span>
      )}
      {children && <span className="col-span-2 mt-2 text-body leading-relaxed text-ink-soft">{children}</span>}
      {hint && <span className="col-span-2 mt-2 text-meta font-semibold text-pearson-amethyst">{hint}</span>}
      {pressed && (
        <span className="motion-pop-badge absolute right-3 top-3 grid h-[22px] w-[22px] place-items-center rounded-full bg-pearson-amethyst text-white">
          <Check size={13} strokeWidth={3} />
        </span>
      )}
    </button>
  );
}

// UIFPE-702: the Review cards — a title, an Edit that jumps to the step, and
// label / value rows (the export page's setup cards, in the wizard)
const CHANNEL_WORD = { s3: "AWS S3", sftp: "SFTP", email: "Email" };

function ReviewCard({ title, onEdit, children }) {
  return (
    <div className="flex flex-col rounded-card border border-white/70 bg-paper-card px-[26px] py-[22px] shadow-card">
      <div className="mb-3 flex items-center justify-between gap-3">
        <h2 className="text-[18px] font-extrabold tracking-tight text-ink">{title}</h2>
        {onEdit && (
          <Button size="sm" onClick={onEdit} title="Change this step">
            Edit
          </Button>
        )}
      </div>
      <div className="flex-1 space-y-2">{children}</div>
    </div>
  );
}

// UIFPE-719: one rule as the Rules step lists it, without Edit and remove
function ReviewRule({ kind, label, definition }) {
  return (
    <div className="border-b border-line px-3.5 py-2.5 last:border-b-0">
      <div className="flex flex-wrap items-center gap-2">
        <Pill tone={kind === "join" ? "live" : "accent"}>{kind}</Pill>
        <b className="text-body text-ink">{label}</b>
      </div>
      <div className="mt-0.5 break-all font-mono text-meta text-ink-soft">{definition}</div>
    </div>
  );
}

function ReviewKv({ label, children }) {
  return (
    <div className="grid gap-x-4 gap-y-0.5 text-body sm:grid-cols-[150px_minmax(0,1fr)]">
      <span className="text-meta font-semibold text-ink-mute">{label}</span>
      <span className="min-w-0 text-ink">{children}</span>
    </div>
  );
}

// UIFPE-628: a titled section on the open page — heading, one-line purpose,
// an optional control on the right. Replaces the per-step Cards.
function StepSection({ title, sub, right, className = "", children }) {
  return (
    <section className={className}>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
        <CardTitle title={title} info={sub} />
        {right}
      </div>
      {children}
    </section>
  );
}

// UIFPE-718: a card's heading with its description behind the ⓘ — the
// owner's rule for every wizard card (hover or focus shows it, a click pins it)
function CardTitle({ title, info }) {
  return (
    <div className="flex min-w-0 items-center gap-2">
      <h2 className="text-[18px] font-extrabold tracking-tight text-ink">{title}</h2>
      <InfoTip text={info} label={`About ${typeof title === "string" ? title : "this card"}`} />
    </div>
  );
}

// UIFPE-628: one selectable-card style for tables, extraction modes and
// destinations — an accent edge when chosen; the caller keeps the button
const choiceClass = (active, disabled = false) =>
  `flex w-full items-start gap-3 rounded-card border p-3 text-left transition-colors ${
    disabled
      ? "cursor-not-allowed border-line bg-paper-card opacity-50"
      : active
        ? "border-accent bg-paper-card shadow-[inset_3px_0_0_var(--color-accent)]"
        : "border-line bg-paper-card hover:border-paper-line hover:bg-paper-head"
  }`;

function ChoiceDisc({ active }) {
  return (
    <span
      aria-hidden="true"
      className={`mt-0.5 grid h-[18px] w-[18px] shrink-0 place-items-center rounded-full border-2 ${
        active ? "border-accent bg-accent text-white" : "border-paper-line bg-paper-card"
      }`}
    >
      {active && <Check size={11} strokeWidth={3} />}
    </span>
  );
}

function Summary({ label, children }) {
  return (
    <div className="flex justify-between gap-4 border-b border-line py-2">
      <dt className="shrink-0 text-ink-mute">{label}</dt>
      <dd className="min-w-0 text-right font-medium text-ink">{children}</dd>
    </div>
  );
}
