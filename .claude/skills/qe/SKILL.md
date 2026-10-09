---
name: qe
description: Use this skill whenever the user (a QE Lead) wants to generate a contract test from a registered schema, check test coverage against the UIF baseline, or draft a Jira Bug from a failing CI run. Trigger on "write a contract test", "generate tests from the schema", "check coverage", "file a bug for this failure", "is this domain's CI enforcing coverage", or any request to scaffold quality-gate automation for a domain.
---

# qe — mechanical test-gate scaffolding for UIF QE Leads

Test strategy (what to cover) and the Definition-of-Done sign-off (is it good enough to trust) are QE judgment this skill never overrides. What it automates is scaffolding the two proven, repeatable artifacts — the contract test and the coverage gate — and drafting (never auto-filing) a Bug from a CI failure.

## Contract-test generator

The registered schema is already the source of truth — generate the test directly from it rather than hand-writing assertions, following the pattern proven twice in `delivery-lab/reference-implementations/api/`:

- **TypeScript:** Ajv 2020 (`ajv/dist/2020` + `ajv-formats` + `yaml`) validating the response body against the contract's own component schema. Do not use `jest-openapi` — it only supports OpenAPI 3.0 and chokes on 3.1 constructs like `type: [string, null]`.
- **Python:** `jsonschema.Draft202012Validator` + `referencing`, with the dialect explicitly pinned (`Resource(contents=doc, specification=DRAFT202012)`) — an OpenAPI document isn't self-describing, so without this you get `CannotDetermineSpecification`.
- Point the generated test at the domain's own `src/schemas/` or `src/api/openapi.yaml` component schemas — never redefine the shape inline in the test.
- **Generate boundary/negative cases directly from the schema's own constraints** — `minLength`/`maxLength`, `minimum`/`maximum`, `pattern`, `enum`, `required` — asserting the API rejects the out-of-bounds input (RFC 7807 Problem response, matching status code). This is mechanical, the same way the happy-path assertions are: the schema already states the boundary, the test just proves the API enforces it. It is not "test strategy" (which scenario matters) — it's "does the contract hold at its own stated edges," derivable without judgment from the schema alone.
- Boundary and coverage checks are always run **per domain** — each domain owns its own schemas and its own CI job, so a coverage gap in `domains/product/` says nothing about `domains/customer/`.

## Coverage gate

Check whether the domain's CI (`domains/<domain>/infrastructure` build step in `.github/workflows/domain-ci.yml`) enforces a coverage threshold. The reference implementations hit 95–98%; if a domain's CI is still the "no build steps yet" placeholder, say so explicitly rather than assuming coverage is enforced. **Scope note:** "automation" here means these two mechanical artifacts — the contract test (incl. boundary cases) and the coverage threshold. Scaffolding a broader e2e/regression suite is Test Strategy (what scenarios matter) and stays with the QE Lead; this skill won't propose one uninvited.

## Performance/load test execution wiring

The skill can wire the harness, never choose what to load-test. Given a domain's registered `openapi.yaml`, scaffold a load-test script (k6 or Locust, whichever the domain's `qe/` already uses) that hits the domain's own registered endpoint(s) with placeholder virtual-user counts, duration, and pass/fail thresholds clearly marked `# QE Lead: set target load profile here`. Wire it as an opt-in CI job, not a required gate — until the QE Lead sets real numbers, an unset placeholder should not fail a build.

## CI-failure → Bug draft

Given a failing CI run's output:
1. **Extract the detail automatically — don't make QE retype it.** Pull the failing gate (structural / Entra-Keeper / contract test / coverage), the exact failing assertion/error line, the run URL, and the commit SHA straight from the run output. This is mechanical extraction, not judgment.
2. Draft a Jira Bug: summary (`<domain> CI: <gate> failed — <one-line reason>`), description (gate, exact error text, run link, commit SHA), `labels: ["capability:<name>"]`, **parented to the owning Domain epic** — not the Story. Bugs follow the same single-owning-parent rule as everything else (`jira/backlog-seed.md` §5); capability stays a label.
3. **Link the Bug to the regressed Story** via `createIssueLink` (relation: "relates to" / "is caused by") so it's traceable to the work that introduced it, without giving it a second parent.
4. **Show the draft and get explicit confirmation before calling `createJiraIssue`** — filing to live Jira is a shared-system action, same rule as `pm`.
5. **Make sure someone is actually told**, not just that the record exists: file the Bug with the full detail already in its description (step 2) so no one has to chase QE for repro steps, and confirm a Jira Automation rule exists (trigger: Bug created and linked to a Story → notify the Story's assignee). If none exists, say so and offer to draft it — Jira admin config, not code (see "Notifying QE and reviewers" in `capabilities/delivery/jira-github-integration.md`).

## Related: how dev progress reaches you without asking

Developers commit with `UIFPE-<n> #comment <what changed> #<transition>` (`capabilities/delivery/jira-github-integration.md`). The `#comment` half is what actually posts visible progress on the issue — a commit carrying only `#<transition>` changes the status but leaves no record of *why*. `Ready for QA` is the intended hand-off point to you; it should be paired with a Jira Automation rule (trigger: → `Ready for QA`, action: notify/assign the QE Lead) so the hand-off is a real notification, not a status you'd have to go check for.

## What this skill does NOT do (stays with the QE Lead)

- **Test strategy** — which scenarios matter is a judgment call, not derivable from the schema alone.
- **Performance/load test scenario design** — the skill can help wire execution, not decide what to load-test.
- **Definition-of-Done sign-off** — "is this good enough to trust" is never automated, even when every gate is green.
