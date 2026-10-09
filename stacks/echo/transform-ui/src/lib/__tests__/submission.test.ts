// Submission plan (UIFPE-218): the exact branch, commit and pull request a
// finished task becomes. Every string here is checked by a repository gate in
// CI, so the assertions mirror those gates — and the generated title/body are
// written to __fixtures__/submissionPlan.json, where the Python side runs the
// REAL .githooks/lint-pr.sh over them (qe/test_submission_plan.py). A wizard
// that produced an unmergeable PR would be worse than one that produced none.
import { describe, it, expect } from "vitest";
import { writeFileSync, readFileSync, existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import {
  buildSubmissionPlan, submissionProblems, TICKET_RE, INTAKE_DIR,
} from "../submission";
import { emptyWizard } from "../types";
import type { WizardState } from "../types";

const HERE = dirname(fileURLToPath(import.meta.url));
const FIXTURE = join(HERE, "..", "__fixtures__", "submissionPlan.json");
const REGEN = !!process.env.REGEN_SUBMISSION_PLAN;

const AUTHOR = { email: "reyasahamed.habibrahman@pearson.com",
                 name: "Reyas Ahamed Habib Rahuman" };

const scalar = (name: string, over: Record<string, unknown> = {}) => ({
  goldColumnName: name, businessDescription: `The ${name}`,
  kind: "scalar" as const, dataType: "string" as const, nullable: true,
  transform: null, notes: null,
  source: { table: "EGO_MTL_SY_ITEMS_EXT_B", column: "C_EXT_ATTR7", filter: null },
  channels: { event: true, message: true, api: true },
  ...over,
});

const task = (over: Partial<WizardState> = {}): WizardState => ({
  ...emptyWizard(),
  taskName: "Transformation_Task_20260828120000",
  method: "guided", intent: "add", domain: "product",
  destinationTable: "gold_product.product",
  drivingTable: "MTL_SYSTEM_ITEMS_B",
  columns: [scalar("returnsWindow") as never],
  ...over,
});

describe("submissionProblems", () => {
  it("demands a ticket, because a PR without one is rejected by CI", () => {
    const p = submissionProblems(task(), "", AUTHOR);
    expect(p.map((x) => x.field)).toContain("ticket");
    expect(submissionProblems(task(), "UIFPE-218", AUTHOR)).toEqual([]);
    expect(submissionProblems(task(), "UIFPE9", AUTHOR).length).toBe(1);
    expect(submissionProblems(task(), "  uifpe-9 ", AUTHOR)).toEqual([]);
  });

  it("demands the author's own pearson address, with no +alias", () => {
    // .githooks/lint-identity.sh: a plus-addressed author makes Jira discard
    // the smart commit silently, and a non-pearson address matches no user
    for (const email of ["someone@gmail.com", "first.last+uif@pearson.com", ""]) {
      const p = submissionProblems(task(), "UIFPE-218", { ...AUTHOR, email });
      expect(p.map((x) => x.field), email).toContain("author");
    }
  });

  it("refuses an empty task", () => {
    expect(submissionProblems(task({ columns: [] }), "UIFPE-218", AUTHOR)
      .map((x) => x.field)).toContain("columns");
  });

  it("demands the author's name — the service refuses a nameless commit", () => {
    const p = submissionProblems(task(), "UIFPE-218", { ...AUTHOR, name: " " });
    expect(p.map((x) => x.field)).toContain("author");
  });

  it("tolerates a lowercase ticket — the plan uppercases it anyway", () => {
    expect(submissionProblems(task(), "uifpe-218", AUTHOR)).toEqual([]);
  });
});

describe("buildSubmissionPlan", () => {
  const plan = buildSubmissionPlan(task(), "UIFPE-218", AUTHOR, { intake: {} });

  it("names a branch and a path that the compile workflow watches", () => {
    expect(plan.branch).toMatch(/^intake\/UIFPE-218-/);
    expect(plan.path.startsWith(INTAKE_DIR)).toBe(true);
    expect(plan.path.endsWith(".json")).toBe(true);
  });

  it("keeps two tasks under one ticket on two branches", () => {
    // the task-name timestamp is the discriminator — without it, a second
    // add on the same entity collides and reads as already-submitted
    const other = buildSubmissionPlan(
      task({ taskName: "Transformation_Task_20260829090000" }),
      "UIFPE-218", AUTHOR, {});
    expect(other.branch).not.toBe(plan.branch);
    expect(other.branch).toMatch(/^intake\/UIFPE-218-add-product-\d+$/);
  });

  it("carries the entity verbatim in a body marker — the branch slug is lossy", () => {
    const p = buildSubmissionPlan(
      task({ isNewTable: true, destinationTable: "gold_orders.order_history" }),
      "UIFPE-300", AUTHOR, {});
    expect(p.prBody).toContain("<!-- uif:entity=order_history -->");
    expect(p.branch).toContain("order-history");   // dashes: cannot round-trip
  });

  it("writes a Smart Commit whose #comment reaches the ticket", () => {
    expect(plan.commitMessage.startsWith("UIFPE-218 #comment ")).toBe(true);
    // the service is credited the way an AI-assisted commit credits its agent;
    // the AUTHOR (set by the service) is the human — never a bot
    expect(plan.commitMessage).toContain("Co-Authored-By: UIF Transform wizard");
    expect(plan.commitMessage).not.toMatch(/bot@|noreply@github/);
  });

  it("titles the PR with the key and a real description", () => {
    // lint-pr.sh strips the key, then needs >=15 chars, >=3 words and >=2
    // non-filler words from what is left
    expect(plan.prTitle).toMatch(/^UIFPE-218 /);
    const rest = plan.prTitle.replace(/UIFPE-\d+/, "").trim();
    expect(rest.length).toBeGreaterThanOrEqual(15);
    expect(rest.split(/\s+/).length).toBeGreaterThanOrEqual(3);
  });

  it("bodies the PR with the two sections CI requires, as prose", () => {
    expect(plan.prBody).toContain("## Summary");
    expect(plan.prBody).toContain("## Verification");
    const summary = plan.prBody.split("## Summary")[1].split("## Verification")[0];
    expect(summary.replace(/[^a-z ]/gi, "").split(/\s+/).filter(Boolean).length)
      .toBeGreaterThan(8);
  });

  it("describes each column in the reviewer's language", () => {
    const p = buildSubmissionPlan(
      task({ columns: [scalar("a") as never, scalar("b") as never] }),
      "UIFPE-218", AUTHOR, {});
    expect(p.prBody).toContain("`a`");
    expect(p.prBody).toContain("`b`");
    expect(p.prBody).toContain("consumer feeds: event, message, api");
    expect(p.prTitle).toContain("2 attributes");
  });

  it("puts a derived column's query in the body, where it is reviewed", () => {
    const derived = scalar("parentProduct", {
      derivation: { sql: "SELECT k, min(v) AS parentProduct FROM t GROUP BY 1",
                    keyedBy: [{ queryColumn: "k", entityColumn: "SEGMENT1" }] },
    });
    const p = buildSubmissionPlan(task({ columns: [derived as never] }),
                                  "UIFPE-218", AUTHOR, {});
    expect(p.prBody).toContain("carries its own query");
    expect(p.prBody).toContain("SELECT k, min(v) AS parentProduct");
    expect(p.prBody).toContain("```sql");
  });

  it("states the refresh decision for a modify, both ways", () => {
    const emit = buildSubmissionPlan(
      task({ intent: "modify", refreshHistorical: true, refreshMode: "emit" }),
      "UIFPE-218", AUTHOR, {});
    expect(emit.prBody).toContain("notifying every consumer feed");
    const none = buildSubmissionPlan(
      task({ intent: "modify", refreshHistorical: false }),
      "UIFPE-218", AUTHOR, {});
    expect(none.prBody).toContain("future changes only");
  });

  it("asks the reviewer to register the schema for a new table", () => {
    const p = buildSubmissionPlan(
      task({ isNewTable: true, destinationTable: "gold_orders.order" }),
      "UIFPE-300", AUTHOR, {});
    expect(p.prTitle).toContain("Create the order gold table");   // sentence-cased
    expect(p.prBody).toContain("master.json` is registered before anything runs");
  });

  it("hands the Python side the real text to run lint-pr.sh over", () => {
    const cases = {
      add: buildSubmissionPlan(task(), "UIFPE-218", AUTHOR, {}),
      addMany: buildSubmissionPlan(
        task({ columns: [scalar("a") as never, scalar("b") as never,
                         scalar("c") as never] }), "UIFPE-218", AUTHOR, {}),
      modify: buildSubmissionPlan(task({ intent: "modify" }), "UIFPE-218", AUTHOR, {}),
      newTable: buildSubmissionPlan(
        task({ isNewTable: true, destinationTable: "gold_orders.order" }),
        "UIFPE-300", AUTHOR, {}),
      derived: buildSubmissionPlan(task({
        columns: [scalar("parentProduct", {
          derivation: { sql: "SELECT k, min(v) AS parentProduct FROM t GROUP BY 1",
                        keyedBy: [{ queryColumn: "k", entityColumn: "SEGMENT1" }] },
        }) as never] }), "UIFPE-218", AUTHOR, {}),
    };
    const doc = {
      $comment: [
        "Generated PR text for every submission shape (UIFPE-218).",
        "Written by src/lib/__tests__/submission.test.ts; asserted by",
        "qe/test_submission_plan.py, which pipes each title/body through the",
        "REAL .githooks/lint-pr.sh. Regenerate with REGEN_SUBMISSION_PLAN=1.",
      ],
      cases: Object.fromEntries(Object.entries(cases).map(([k, v]) =>
        [k, { branch: v.branch, prTitle: v.prTitle, prBody: v.prBody,
              commitMessage: v.commitMessage }])),
    };
    if (REGEN) {
      writeFileSync(FIXTURE, JSON.stringify(doc, null, 2) + "\n");
      return;
    }
    expect(existsSync(FIXTURE), "regenerate with REGEN_SUBMISSION_PLAN=1").toBe(true);
    expect(JSON.parse(readFileSync(FIXTURE, "utf8")).cases).toEqual(doc.cases);
  });
});

describe("TICKET_RE", () => {
  it("accepts a key and rejects everything shaped like one but not", () => {
    expect(TICKET_RE.test("UIFPE-218")).toBe(true);
    for (const bad of ["UIFPE218", "uifpe-218", "UIFPE-", "PROJ-1", "UIFPE-1a"])
      expect(TICKET_RE.test(bad), bad).toBe(false);
  });
});
