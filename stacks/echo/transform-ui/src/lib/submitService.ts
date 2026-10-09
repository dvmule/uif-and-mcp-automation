// Client for the wizard submission service (UIFPE-218) — lambda_submit.
//
// POST turns the finished task into a pull request (ADR-0006 D4: the UI
// writes PRs, never live state); GET is My submissions. All the strings the
// service sends to GitHub are built by submission.ts, where the repository's
// own gates pin them; this module only talks.
//
// Same wiring convention as the other services: the deploy passes
// Terraform's `submit_url` output to the build. While it is empty the wizard
// falls back to the download-and-hand-over flow — submission is unavailable,
// never broken.

import type { WizardState } from "./types";
import {
  buildSubmissionPlan, submissionProblems, SubmissionAuthor,
} from "./submission";
import { newTableArtifacts } from "./newTableArtifacts";

export const SUBMIT_URL: string = import.meta.env.VITE_SUBMIT_URL ?? "";

export const submitConfigured = () => SUBMIT_URL.length > 0;

export interface SubmissionReceipt {
  number: number;
  url: string;
}

export interface SubmissionRow {
  number: number;
  url: string;
  title: string;
  ticket: string | null;
  status: string;
  approved: boolean;
  firstRun?: "succeeded" | "failed" | "running";
  submittedAt: string;
  comments: { author: string; when: string; text: string; verdict?: string }[];
}

/** Everything the submission commit carries: the envelope, plus the schema
 *  a new table registers first (registry-first — the compiler refuses an
 *  unregistered entity, so the file that unblocks it rides along). */
export function submissionFiles(state: WizardState, envelopePath: string,
                                envelope: unknown): { path: string; content: string }[] {
  const files = [{ path: envelopePath,
                   content: JSON.stringify(envelope, null, 2) + "\n" }];
  if (state.isNewTable) {
    const artifacts = newTableArtifacts(state);
    files.push({ path: artifacts.paths.master, content: artifacts.masterJson });
  }
  return files;
}

export async function submitTask(state: WizardState, envelope: unknown,
                                 ticket: string, author: SubmissionAuthor):
  Promise<SubmissionReceipt> {
  const problems = submissionProblems(state, ticket, author);
  if (problems.length)
    throw new Error(problems.map((p) => p.message).join(" "));

  const plan = buildSubmissionPlan(state, ticket, author, envelope);
  const r = await fetch(SUBMIT_URL, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      ticket: ticket.trim().toUpperCase(),
      author,
      branch: plan.branch,
      files: submissionFiles(state, plan.path, envelope),
      commitMessage: plan.commitMessage,
      prTitle: plan.prTitle,
      prBody: plan.prBody,
    }),
  });
  const body = await r.json().catch(() => ({}));
  if (!r.ok)
    throw new Error((body.problems ?? [`submission failed (${r.status})`]).join(" "));
  return body as SubmissionReceipt;
}

export async function listSubmissions(authorEmail?: string):
  Promise<SubmissionRow[]> {
  const qs = authorEmail ? `?author=${encodeURIComponent(authorEmail)}` : "";
  const r = await fetch(`${SUBMIT_URL}${qs}`);
  const body = await r.json().catch(() => ({}));
  if (!r.ok)
    throw new Error((body.problems ?? [`listing failed (${r.status})`]).join(" "));
  return (body.submissions ?? []) as SubmissionRow[];
}
