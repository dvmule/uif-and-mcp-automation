// What happens to an attribute when this task is submitted — AUTO (all four
// artifacts, SQL included, are edited by the compiler) or PLAN (the structured
// artifacts are edited and the SQL change is handed to a data engineer as an
// anchored patch plan).
//
// A MIRROR of compile_intake_delta.py, not a second opinion. The compiler
// already prints this verdict, but only when it actually runs — which is after
// submission. Surfacing it while authoring is the whole point: "publishes on the
// next run" and "waits for an engineer" are different promises, and the author
// is the person who needs to know which one they are making.
//
// The subtlety that makes a naive mirror dangerous: the verdict is NOT a
// property of the column alone. compile_delta overrides classify() twice, and
// both overrides only fire on the MODIFY path — the wizard's most-travelled
// route, deep-linked straight from the home page's attribute search:
//
//   · editing a driving-table scalar is plan:driving-modify, not AUTO — and
//     since UIFPE-106 the same posture covers conditionals
//     (plan:conditional-modify) and lookup-joins (plan:lookup-join-modify),
//     because the shipped pick/CTE may be hand-tuned. On the registered
//     product entity that is 35 of the 48 add-path AUTO columns, so a mirror
//     of classify() alone would promise "no engineer needed" for 35
//     attributes that land in the queue.
//   · changing a registered column's KIND is plan:kind-change-<old>-to-<new>,
//     because an array becoming a scalar rewrites a CTE rather than editing it.
//
// Parity with the Python is pinned by a shared fixture asserted from both sides:
//   __fixtures__/classificationVerdicts.json
//   ← domains/product/qe/test_ui_classification_parity.py (the compiler's output)
//   ← __tests__/classify.test.ts                          (this mirror)

import type { ConditionalBranch, MappingColumn, TaskIntent } from "./types";

/** The compiler's class string. AUTO classes are `driving`, `eav:<group>`,
 *  `conditional` and `lookup-join` (UIFPE-106); everything else is a
 *  `plan:*`. Kept as the compiler's own vocabulary so the two can be
 *  compared literally. */
export type ClassifyClass =
  | "driving"
  | `eav:${string}`
  | "conditional"
  | "lookup-join"
  | "derived-sql"
  | "plan:conditional"
  | "plan:nested"
  | "plan:lookup-join"
  | "plan:unrecognized-source"
  | "plan:driving-modify"
  | "plan:conditional-modify"
  | "plan:lookup-join-modify"
  | "plan:derived-sql-modify"
  | `plan:kind-change-${string}`;

export interface Verdict {
  /** AUTO: the compiler edits the SQL too. PLAN: an engineer does. */
  verdict: "AUTO" | "PLAN";
  /** The compiler's class — shown as the reason code, never invented here. */
  reason: ClassifyClass;
  /** True when the verdict came from an intent override rather than the
   *  column's own shape, i.e. this exact column would be AUTO on an add. */
  fromIntent: boolean;
}

const lastSegment = (t: string) => (t || "").split(".").pop() ?? "";
// BARE EAV shape: the filter is nothing but the ATTR_GROUP_ID equality — a
// filter carrying any other conjunct is not AUTO-EAV, because the compiler's
// CTE writer rebuilds the WHERE from the group number and would silently
// drop the extra predicate.
const BARE_ATTR_GROUP = /^ATTR_GROUP_ID\s*=\s*(\d+)$/;
// The UIFPE-153 notes convention: a column whose derivation needs a hop the
// intake cannot record is marked MULTI-HOP and stays PLAN, SQL authoritative.
const MULTIHOP = /multi-?hop/i;

const bareEavGroup = (src?: { table: string; column: string;
                              filter?: string | null } | null): string | null => {
  const table = lastSegment(src?.table ?? "").toUpperCase();
  const m = BARE_ATTR_GROUP.exec((src?.filter ?? "").trim());
  return table.endsWith("EXT_B") && m ? m[1] : null;
};

const branchEavGroup = (b: ConditionalBranch): string | null =>
  bareEavGroup(b.source);

/**
 * Mirror of the Python `classify(col, driving)`.
 *
 * A conditional stays PLAN when the SQL craft is the contract (D5): a struct
 * MEMBER (dotted name — the named_struct + all-null gate are hand-written),
 * a declared MULTI-HOP derivation, a fallbackToElse branch (no deterministic
 * strict-pick rendering), a branch carrying its own transform (a
 * null-reviving transform outside the CTE would break the strict pick), or
 * any branch that is not bare-EAV-shaped.
 *
 * A lookup-join is AUTO only when every on-pair anchors on the DRIVING table
 * and the join is LEFT — chained lookups (A -> B -> C) and other join types
 * have no dimension-CTE rendering and stay plan:lookup-join.
 *
 * Note the EAV test: the AUTO fast path requires the source table's name to end
 * in `EXT_B` *and* the filter to be exactly the ATTR_GROUP_ID equality. The
 * suffix is an Oracle PDH naming convention, so a structurally identical
 * attribute store in another domain falls through to lookup-join. That is the
 * compiler's rule today, reproduced faithfully — see the note in
 * classify.test.ts, it is a genericity limit worth raising rather than a bug
 * to paper over here.
 */
export function classifyColumn(col: MappingColumn, drivingTable: string): ClassifyClass {
  // ADR-0008 (the query is the contract): the author supplied the query, so
  // an ADD splices it mechanically — an AUTO class guarded by the compiler's
  // battery refusals. Checked FIRST so it wins over every kind dispatch.
  // `!= null` mirrors the Python `is not None`: an EMPTY block still
  // classifies derived-sql on both sides and is refused by the battery.
  if (col.derivation != null) return "derived-sql";
  if (col.kind === "conditional") {
    const branches = col.branches ?? [];
    if (col.goldColumnName.includes(".")
        || MULTIHOP.test(col.notes ?? "")
        || branches.length < 2
        || branches.some((b) => b.fallback === "fallbackToElse")
        || branches.some((b) => b.transform != null)
        || branches.some((b) => branchEavGroup(b) === null))
      return "plan:conditional";
    return "conditional";
  }
  if (col.kind !== "scalar") return `plan:${col.kind}` as ClassifyClass;
  const table = lastSegment(col.source?.table ?? "").toUpperCase();
  const group = bareEavGroup(col.source);
  if (group) return `eav:${group}`;
  const drv = lastSegment(drivingTable).toUpperCase();
  if (table && table === drv) return "driving";
  const j = col.source?.join;
  if (j) {
    const on = j.on ?? [];
    const anchored = on.length > 0 && on.every(
      (o) => lastSegment(o.leftTable || drivingTable).toUpperCase() === drv);
    if (anchored && (j.type ?? "left") === "left") return "lookup-join";
    return "plan:lookup-join";
  }
  return "plan:unrecognized-source";
}

export interface VerdictContext {
  drivingTable: string;
  intent: TaskIntent | null;
  /** goldColumnName -> kind, from the REGISTERED mapping. Needed for the
   *  kind-change override; an empty map simply means no column is registered. */
  registeredKinds?: Record<string, MappingColumn["kind"]>;
}

/** The verdict compile_delta would reach for this column, in this task. */
export function verdictFor(col: MappingColumn, ctx: VerdictContext): Verdict {
  const base = classifyColumn(col, ctx.drivingTable);
  const name = col.goldColumnName;
  const registered = ctx.registeredKinds?.[name];

  if (ctx.intent === "modify") {
    // Order matters: the kind-change override is applied before the
    // eav/driving dispatch, so it wins over an otherwise-AUTO class.
    if (registered !== undefined && registered !== col.kind)
      return { verdict: "PLAN", fromIntent: true,
               reason: `plan:kind-change-${registered}-to-${col.kind}` as ClassifyClass };
    if (base === "driving")
      return { verdict: "PLAN", reason: "plan:driving-modify", fromIntent: true };
    // UIFPE-106: the shipped pick/CTE may be hand-tuned, so restructures
    // mirror the driving-modify posture.
    if (base === "conditional")
      return { verdict: "PLAN", reason: "plan:conditional-modify", fromIntent: true };
    if (base === "lookup-join")
      return { verdict: "PLAN", reason: "plan:lookup-join-modify", fromIntent: true };
    // UIFPE-217: the migrated derived columns' shipped SQL predates the
    // d_<name> wrap shape, and machine-editing hand-shaped CTEs is the
    // silent-damage class the platform refuses — a modify is an engineer's
    // plan; adding the column new splices the query automatically.
    if (base === "derived-sql")
      return { verdict: "PLAN", reason: "plan:derived-sql-modify", fromIntent: true };
  }
  const auto = base === "driving" || base === "conditional"
    || base === "lookup-join" || base === "derived-sql"
    || base.startsWith("eav:");
  return { verdict: auto ? "AUTO" : "PLAN", reason: base, fromIntent: false };
}

/** Plain-language reading of a verdict, for people who do not read class
 *  strings. Deliberately says what happens next rather than naming the tier. */
export function verdictSentence(v: Verdict): string {
  if (v.verdict === "AUTO") {
    // The two UIFPE-106 classes carry submit-time refusals classification
    // cannot pre-screen (source registration, branch overlap, malformed
    // condition strings) — the promise is honest about that: a refusal is
    // loud and atomic, never wrong SQL.
    if (v.reason === "lookup-join")
      return "Compiles on its own when the lookup's table is a registered "
        + "source — an unregistered one is refused at submit, nothing is "
        + "written.";
    if (v.reason === "conditional")
      return "Compiles on its own — branch shapes the compiler cannot render "
        + "safely are refused at submit, nothing is written.";
    if (v.reason === "derived-sql")
      return "Your query is built into the final gold query exactly as "
        + "written — checked at submit (read-only, registered sources, "
        + "stable results) and reviewed before it deploys.";
    return "Compiles on its own — the value appears on the next scheduled run.";
  }
  switch (v.reason) {
    case "plan:conditional":
      return "A branching shape the compiler refuses by design — a struct "
        + "member, a multi-hop derivation, a fall-back-to-ELSE branch, a "
        + "branch-level transform, or a branch read from outside the EAV "
        + "store — so a data engineer writes the query change.";
    case "plan:lookup-join":
      return "A join the compiler does not generate — chained through "
        + "another lookup, or not a LEFT join — so a data engineer writes "
        + "the query change.";
    case "plan:derived-sql-modify":
      return "Changing a derived column's query: the shipped SQL predates "
        + "the spliced shape, so a data engineer places the new query — "
        + "adding it new would have compiled on its own.";
    case "plan:nested":
      return "A repeating group: assembling it is a query rewrite, so a data "
        + "engineer does that part. This shape stays manual by design.";
    case "plan:driving-modify":
      return "Editing a column on the entity's own spine table rewrites part of "
        + "the final query, so a data engineer reviews it — adding a new one "
        + "would have compiled on its own.";
    case "plan:conditional-modify":
      return "Restructuring a branching attribute can touch hand-tuned query "
        + "lines, so a data engineer reviews it — adding it new would have "
        + "compiled on its own.";
    case "plan:lookup-join-modify":
      return "Repointing a looked-up attribute can touch a hand-tuned join, "
        + "so a data engineer reviews it — adding it new would have compiled "
        + "on its own.";
    case "plan:unrecognized-source":
      return "The source does not match a shape the compiler can generate, and "
        + "states no join — a data engineer has to place it.";
    default:
      if (v.reason.startsWith("plan:kind-change-")) {
        const [was, now] = v.reason.slice("plan:kind-change-".length).split("-to-");
        return `Changing this from ${was} to ${now} restructures the query rather `
          + "than editing it, so a data engineer applies that change.";
      }
      return "A data engineer applies the query change for this attribute.";
  }
}

export interface VerdictTally {
  auto: number;
  plan: number;
  /** plan reason -> count, for "why" without listing every attribute. */
  byReason: Record<string, number>;
  /** PLAN only because the task is a modify — would be AUTO as an add. */
  intentDriven: number;
}

export function tally(verdicts: Verdict[]): VerdictTally {
  const t: VerdictTally = { auto: 0, plan: 0, byReason: {}, intentDriven: 0 };
  for (const v of verdicts) {
    if (v.verdict === "AUTO") { t.auto++; continue; }
    t.plan++;
    t.byReason[v.reason] = (t.byReason[v.reason] ?? 0) + 1;
    if (v.fromIntent) t.intentDriven++;
  }
  return t;
}
