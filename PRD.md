# PRD.md — Product Requirements Document (agent workflow template)

A PRD breaks a **Jira Story** into **small, sequential, verifiable tasks** so an AI agent (or FDE) implements work **one step at a time** instead of generating large unverified diffs. Each task must be completable, testable, and committable before the next begins.

**When to use:** every UIFPE Story that touches `domains/<domain>/src/`.  
**Where to store:** copy this template to `domains/<domain>/PRD-UIFPE-<n>.md` (preferred) or keep one active `PRD.md` at repo root during a session.  
**Agent rule:** read [`AGENTS.md`](AGENTS.md) + this PRD before writing code.

---

## How agents must use a PRD

1. Read the full PRD once for context.
2. Execute **only the next unchecked task**.
3. Run that task's **Verification** commands.
4. Mark the task `[x]` in the PRD (same branch).
5. Commit with Smart Commit referencing UIFPE-\<n\>.
6. Repeat until all tasks are `[x]`, then open PR.

**Do not** start Task 3 while Task 2 is unchecked. **Do not** add scope not listed in the PRD without updating the PRD first.

---

## Template (copy below this line for each story)

---

# PRD — UIFPE-\<n\>: \<Story title\>

| Field | Value |
|---|---|
| **Jira** | [UIFPE-\<n\>](https://pearson.atlassian.net/browse/UIFPE-\<n\>) |
| **Domain epic (parent)** | UIFPE-\<51-57\> · `domain:\<name\>` |
| **Capabilities (labels)** | `capability:\<api\>`, … |
| **GitHub path** | `domains/\<domain\>/src/` |
| **Branch** | `feature/UIFPE-\<n\>-\<slug\>` |
| **PRD file** | `domains/\<domain\>/PRD-UIFPE-\<n\>.md` |
| **Standards to read first** | `capabilities/\<c\>/guidelines.md`, … |

## Problem

\<One paragraph — what the user/system needs and why it matters.\>

## Out of scope

- \<Explicit exclusions so the agent does not expand scope.\>

## Acceptance criteria (story level)

- [ ] AC1: \<Observable outcome\>
- [ ] AC2: \<Observable outcome\>
- [ ] AC3: CI / DoD gates pass for `domains/\<domain\>/`

---

## Implementation tasks (sequential — complete in order)

### Task 1 — Design & contracts

**Goal:** \<e.g. Register schema and OpenAPI before handlers.\>

**Steps:**
1. …
2. …

**Files:** `domains/\<domain\>/src/schemas/`, …

**Verification:**
```bash
# e.g. schema validates, openapi lint
```

- [ ] Task 1 complete

---

### Task 2 — Core implementation

**Goal:** \<e.g. Implement handler with factory pattern.\>

**Steps:**
1. …
2. …

**Files:** `domains/\<domain\>/src/api/`, …

**Verification:**
```bash
pytest ../qe/tests/test_<feature>.py
```

- [ ] Task 2 complete

---

### Task 3 — Tests (QE)

**Goal:** \<Unit + contract coverage for ACs.\>

**Steps:**
1. …

**Files:** `domains/\<domain>/qe/tests/`

**Verification:**
```bash
pytest ../qe/tests/
```

- [ ] Task 3 complete

---

### Task 4 — Container & DoD

**Goal:** Dockerfile builds; DOMAIN-CHECKLIST items satisfied.

**Steps:**
1. …

**Verification:**
```bash
docker build -f domains/\<domain\>/src/Dockerfile -t \<domain\>:local .
```

- [ ] Task 4 complete

---

### Task 5 — Trace & PR

**Goal:** Jira and GitHub aligned.

**Steps:**
1. Confirm branch name and Smart Commits on all commits.
2. Open PR: `UIFPE-\<n\> · \<title\>`.
3. Link PR in Jira Development panel (automatic if key present).

**Verification:**
- [ ] PR opened; last commit `UIFPE-\<n\> #comment Ready for review #code-review`

- [ ] Task 5 complete

---

## Example — abbreviated (UIFPE-39 style)

| Task | Goal |
|---|---|
| 1 | Register product API schema in `src/schemas/` |
| 2 | Author `openapi.yaml` (API First) |
| 3 | Implement FastAPI handlers + mapping gate |
| 4 | Contract tests in `qe/tests/` |
| 5 | Dockerfile + PR |

Full story context: [`jira/backlog-seed.md`](jira/backlog-seed.md) · worked example: [`capability-domain-jira-map.md`](capabilities/docs/capability-domain-jira-map.md).

---

## Related

- Agent rules: [`AGENTS.md`](AGENTS.md)
- Domain DoD: `domains/<domain>/DOMAIN-CHECKLIST.md`
- FDE workflow: `architecture-design/operating-model/fde-build-deploy-workflow.md`

*Template version: 2026-08-06*
