// Turning a finished wizard task into a reviewable pull request (UIFPE-218).
//
// ADR-0006 D4: the UI opens a PULL REQUEST, never touches live state. This
// module is the deterministic half of that — envelope in, the exact branch
// name, commit message, PR title, PR body and file content out. No network,
// no credentials, no AWS: the service (lambda_submit) does the talking, and
// everything it says is decided here, where it can be tested.
//
// Three rules from the repository shape every string below, and getting any
// of them wrong means CI rejects the PR the user just submitted:
//
//   1. The PR TITLE needs a UIFPE key plus a real description
//      (.githooks/lint-pr.sh: >=15 chars, >=3 words, >=2 non-filler words
//      after the key is removed).
//   2. The BODY needs a Summary section and a Verification section, each
//      prose, measured after markdown is stripped.
//   3. Every COMMIT is authored by the engineer who submitted, at their
//      @pearson.com address — never a bot, never a shared address
//      (.githooks/lint-identity.sh, enforced on every PR by
//      commit-identity.yml). The service is credited in a trailer, exactly
//      as an AI-assisted commit credits its agent.
//
// The PR carries the INTAKE ENVELOPE only. A workflow on the branch runs the
// delta compiler and pushes the artifact edits, so the compiler stays the
// single writer of mapping/master/manifest/SQL and the reviewer sees both the
// intent and the generated diff.

import type { WizardState } from "./types";
import { isWeekly } from "./pipelines";

export const TICKET_RE = /^UIFPE-\d+$/;

/** Where a submitted task's envelope lands on the branch. The compile
 *  workflow watches this path. */
export const INTAKE_DIR = "domains/product/src/schemas/intake/submitted";

export interface SubmissionAuthor {
  /** Exactly as it appears in Jira — the commit's author. */
  email: string;
  name: string;
}

export interface SubmissionPlan {
  branch: string;
  path: string;
  content: string;
  commitMessage: string;
  prTitle: string;
  prBody: string;
}

export interface SubmissionProblem {
  field: "ticket" | "author" | "columns" | "table";
  message: string;
}

const slug = (s: string) =>
  s.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 40);

/** What is still missing before a task can become a pull request. Checked
 *  here rather than at the service, so the wizard can say so while the user
 *  is still looking at the form. */
export function submissionProblems(state: WizardState,
                                   ticket: string,
                                   author: SubmissionAuthor): SubmissionProblem[] {
  const out: SubmissionProblem[] = [];
  // case-tolerant: the plan uppercases the key anyway, so uifpe-218 is fine
  if (!TICKET_RE.test(ticket.trim().toUpperCase()))
    out.push({ field: "ticket",
               message: "A ticket like UIFPE-218 is required — the pull "
                 + "request is rejected without one, and it is how anyone "
                 + "searching Jira finds what shipped." });
  const email = author.email.trim().toLowerCase();
  if (!email.endsWith("@pearson.com") || email.includes("+"))
    out.push({ field: "author",
               message: "Your @pearson.com address is required, with no "
                 + "+alias — every commit is authored by the person who made "
                 + "the change, and Jira matches the address exactly." });
  if (!author.name.trim())
    out.push({ field: "author",
               message: "Your name is required (Requested by) — it authors "
                 + "the submission commits." });
  if (!state.columns.length)
    out.push({ field: "columns", message: "Nothing to submit — this task has no columns." });
  if (!state.destinationTable)
    out.push({ field: "table", message: "No gold table chosen." });
  return out;
}

/** One human sentence per column — the same register the Review summary uses,
 *  because a reviewer reads this in the PR before opening the diff. */
function columnLine(c: WizardState["columns"][number], intent: string): string {
  const verb = intent === "modify" ? "changes" : "adds";
  const feeds = (["event", "message", "api"] as const)
    .filter((k) => c.channels?.[k]).join(", ") || "none (stored only)";
  const shape = c.kind === "scalar" ? "one value per row"
    : c.kind === "conditional" ? "value depends on a condition"
    : "a list per row";
  const derived = c.derivation ? ", from its own query" : "";
  return `- ${verb} \`${c.goldColumnName}\` — ${c.businessDescription || "no description"}`
    + ` (${shape}${derived}) · consumer feeds: ${feeds}`;
}

export function buildSubmissionPlan(state: WizardState, ticket: string,
                                    author: SubmissionAuthor,
                                    envelope: unknown): SubmissionPlan {
  const key = ticket.trim().toUpperCase();
  const entity = (state.destinationTable ?? "table").split(".").pop() ?? "table";
  const action = state.isNewTable ? "new table"
    : state.intent === "modify" ? "change" : "add";
  const what = state.isNewTable
    ? `create the ${entity} gold table`
    : state.intent === "modify"
      ? `change ${state.columns.length === 1
          ? state.columns[0].goldColumnName
          : `${state.columns.length} attributes`} on ${entity}`
      : `add ${state.columns.length === 1
          ? state.columns[0].goldColumnName
          : `${state.columns.length} attributes`} to ${entity}`;

  // The task tail keeps two DIFFERENT tasks under one ticket from colliding
  // on one branch (the timestamp in the default task name); entity identity
  // never rides on the branch — the slug is lossy (underscores become
  // dashes), so the PR body carries the entity verbatim in a marker instead.
  const tail = slug(state.taskName).replace(/[^0-9]/g, "").slice(-12);
  const branch = `intake/${key}-${slug(`${action}-${entity}`)}`
    + (tail ? `-${tail}` : "");
  const path = `${INTAKE_DIR}/${key}-${slug(state.taskName)}.json`;
  const content = JSON.stringify(envelope, null, 2) + "\n";

  // Smart Commit: the #comment command posts to the ticket, and it only works
  // when the author email matches a Jira user — hence the identity rule.
  const commitMessage = [
    `${key} #comment ${what} — submitted from the Transform wizard`,
    "",
    `Intake envelope only: the compile workflow on this branch runs the delta`,
    `compiler and pushes the mapping, schema, manifest and query edits, so the`,
    `compiler stays the single writer of those artifacts.`,
    "",
    "Co-Authored-By: UIF Transform wizard <noreply@pearson.com>",
  ].join("\n");

  const prTitle = `${key} ${what.charAt(0).toUpperCase()}${what.slice(1)}`
    + ` (Transform wizard submission)`;

  const derived = state.columns.filter((c) => c.derivation);
  const prBody = [
    // machine-readable identity for the My submissions ladder: the entity
    // exactly as written, because the branch slug cannot round-trip it
    `<!-- uif:entity=${entity} -->`,
    "## Summary",
    "",
    `Submitted from the Transform wizard by ${author.name} `
      + `(${author.email}) — this pull request carries the intake envelope for`
      + ` **${state.destinationTable}**; the compile workflow on this branch`
      + ` produces the artifact edits, so the delta compiler remains the only`
      + ` writer of the mapping, schema, manifest and query.`,
    "",
    `**Intent:** ${state.isNewTable ? "create a new gold table"
      : state.intent === "modify" ? "change existing attributes"
      : "add new attributes"} · **columns:** ${state.columns.length}`
      + ` · **schedule:** ${state.frequency}`
      + (isWeekly(state.frequency) ? ` on ${state.weeklyDays.join(", ")}` : "")
      + ` from ${state.startTime} UTC`,
    "",
    "In plain terms, this change:",
    "",
    ...state.columns.map((c) => columnLine(c, state.intent ?? "add")),
    ...(state.intent === "modify"
      ? [`- ${state.refreshHistorical
          ? `recalculates the changed attributes for all existing rows after deploy`
            + `${state.refreshMode === "emit"
                ? ", notifying every consumer feed whose values changed"
                : ", quietly — consumer feeds are not notified"}`
          : "applies the new logic to future changes only; existing rows keep "
            + "their current values"}`]
      : []),
    ...(derived.length
      ? ["",
         `**${derived.length} column${derived.length === 1 ? " carries" : "s carry"}`
           + ` its own query** (ADR-0008). The query is spliced into the gold`
           + ` query exactly as written here — read it as the change it is:`,
         "",
         ...derived.flatMap((c) => [
           `<details><summary><code>${c.goldColumnName}</code></summary>`,
           "",
           "```sql",
           (c.derivation?.sql ?? "").trim(),
           "```",
           "",
           "</details>"])]
      : []),
    "",
    "## Verification",
    "",
    `The wizard refuses to submit a task that fails its own checks, so at`
      + ` submission time: every column passed the intake contract's`
      + ` validation, each attribute's query preview rendered, and the`
      + ` schedule resolved to a real cadence.`,
    "",
    "What the reviewer should confirm, because the wizard cannot:",
    "",
    "- the compile workflow's artifact diff on this branch matches the intent above",
    "- the domain suites pass on the compiled result (the workflow runs them)",
    ...(derived.length
      ? ["- each embedded query reads the right rows and returns one row per "
         + "entity (the wizard's test run checks the grain; the values are "
         + "yours to judge)"]
      : []),
    ...(state.isNewTable
      ? ["- the generated `master.json` is registered before anything runs — "
         + "the compiler refuses an unregistered entity by design"]
      : []),
  ].join("\n");

  return { branch, path, content, commitMessage, prTitle, prBody };
}
