# ADR-0008 — Derived Columns: The Query Is the Contract

**Status:** Accepted (2026-08-26)
**Date:** 2026-08-26 (a first draft with a different approach was proposed 2026-08-23; earlier drafts live in this file's git history)
**Author:** reyasahamed.habibrahman@pearson.com
**Deciders:** Reyas Ahamed Habib Rahuman (module owner, gold data plane & control plane); framework owners (Rajnivas Karunakaran, Satyanarayana Gunavanthan) informed via this document's PR
**Scope:** How any gold domain's intake contract (`gold-attribute-mapping.schema.json` and the domain's `*.mapping.json`) records a column whose value takes more than one step to work out, how the Transform UI captures it, and what the compiler does with it. This is a platform decision: it applies to every gold domain the same way and contains no domain-specific logic — each domain's actual queries live in that domain's own mapping files.
**Supersedes:** none. Builds on ADR-0005 (gold layer shape) and ADR-0006 D-decisions (the UI writes pull requests, never live state).
**Numbering:** 0008. 0006 is claimed by the control-plane ADR on `feature/UIFPE-97-adr-0006-control-plane` and 0007 by the oxigraph ADR on the `feature/UIFPE-36-*` branches — both in review, neither merged; this document depends on neither and yields both numbers. **Filename note:** the file keeps its original name (`0008-derivation-chains.md`) so existing links keep working; the mapping block keeps the name `derivation` for the same reason.
**Capabilities spanned:** **Schema** (the intake contract carries the block), **Data** (the gold SQL is what the block contains and the compiler assembles), **Delivery** (the checks are CI gates). No API/Event surface changes.

**In plain terms:** most gold columns can be described by pointing at one source table and column. Some cannot — their value takes several steps to work out. For those columns, the person setting up the pipeline gives us **the query itself**, written against the source tables they already know. The platform checks the query, a reviewer approves it, and the compiler builds it into the final gold query **unchanged**. The mapping file then holds the real logic — not a summary of it — so it can never go stale.

---

## Context

The intake contract records **one source table, column, filter, and join per column**, plus an optional transform. That covers the vast majority of columns. A small number do not fit: their value comes from several steps — following a link row, applying a qualifying test, translating a code through a lookup, then picking one result by a stated rule. Today there are five such columns across the two live gold domains, and any new domain can bring more. Their actual logic is deliberately **not** repeated in this document — it lives in each domain's own mapping files, where it cannot drift from this ADR.

Before this decision, the mapping recorded only the first step of such a column and put the rest in a free-text note. Nobody could trust the mapping alone: a reviewer could not see where the value really came from, and the UI could not show or capture it. UIF's purpose is that pipelines are set up **through the UI** — no templates, no waiting on an engineer — so complex columns needed a way in that the people who own the mappings can actually use. They know their **source structure**; they should never have to learn a framework-invented language on top of it.

## What the design must satisfy

1. **The mapping tells the whole truth.** Anyone reading it sees a derived column's full logic, not a pointer and a note.
2. **No wrong SQL published by accident.** The worst outcome on this platform is SQL that looks right, passes silently, and produces wrong data.
3. **Setup through the UI.** The author is the same person who fills the intake workbook today — they know the source tables; they are not framework engineers.
4. **SQL is welcome when governed.** What stays banned is *ungoverned* SQL: anything unparsed, unreviewed, reading unregistered tables, or producing unstable results.
5. **Same result every run.** A derived value must not wobble between identical runs — unstable values ring every downstream consumer for no reason.

## Decision

**A derived column's contract is the query itself, governed.** The column carries the author's actual SELECT in the mapping, plus a small declared contract (which output column identifies the entity, which carries the value). The compiler forms the final gold query from it mechanically. Governance comes from a validation battery plus the pull-request review every submission already gets (ADR-0006: the UI writes PRs, never live state).

We considered and rejected three other paths, in earlier drafts and the UIFPE-153 spike: keeping free-text notes (the mapping stays untrustworthy); describing each derived column in a framework-invented step language while engineers keep writing the SQL (unwritable by pipeline authors, and a second copy of the truth that a drift check must police forever); and generating all SQL from structured steps (the spike showed subtle omissions compile silently wrong). Carrying the query has one source of truth, nothing to drift, and an author who already knows how to write it.

## The contract

### Block shape

`derivation` is an optional block on a column of any kind (kind = the value's shape; derivation = how the value is reached). The example is deliberately generic — placeholder names, no real domain:

```jsonc
"derivation": {
  // the author's own query — one read-only SELECT, in the source's own names
  "sql": "SELECT l.ENTITY_REF AS entityRef, min(m.NAME) AS parentName
          FROM link_table l JOIN master_table m ON ...
          WHERE ... GROUP BY 1",

  // which output column identifies the entity each row belongs to
  "keyedBy": [ { "queryColumn": "entityRef", "entityColumn": "ENTITY_KEY" } ],

  // which output column carries the value (defaults to the gold column's name)
  "valueColumn": "parentName",

  // which tables and columns the query reads — written by the validator from
  // the parsed query and re-checked in CI; never typed by hand
  "reads": { "link_table": ["ENTITY_REF", "…"], "master_table": ["NAME", "…"] }
}
```

Contract rules:

- **One SELECT, read-only, Spark SQL dialect.** No data or table changes, no multiple statements.
- **Registered sources only.** Every table the query references must be a registered source for that entity. A query needing an unregistered table is refused, with the registration path named.
- **At most one row per entity.** Lists and structures are built *inside* the query — the query's output row is always the final value.
- **Same result every run.** Functions that change between runs (random values, current time) are refused; anything that picks one value from many, or builds a list, must state its ordering.
- **`reads` is computed, never authored** — the parser writes it, CI re-checks it, so the lineage record can never be wrong or stale.

### Assembly (what "form the final query" means)

The compiler wraps the query under the column's name, adds it to the entity's gold query, and joins it on the declared entity key — always a LEFT join, so an entity with no derived result keeps its row and shows an empty value rather than disappearing. The wrap is the **only** transformation: the SQL the reviewer approved is the SQL that runs.

### Validation battery (checked as the author types, again at submit, again in CI)

| # | Refusal | What it stops |
|---|---|---|
| 1 | Does not parse, or is not a single read-only SELECT | malformed or unsafe input |
| 2 | Reads a table that is not a registered source | going around the registry |
| 3 | Uses an unstable function, or builds an unordered list | values that wobble between identical runs |
| 4 | The declared key/value columns are not in the query's output | a broken contract with the compiler |
| 5 | Test run (dev data): more than one row for the same entity | one entity getting an ambiguous value |

The UI runs 1–4 live and offers 5 as a one-click test run (row count, one-row-per-entity check, sample values) before submit. The capture experience is specified in `capabilities/data/gold-pipeline-wizard.md`.

### Classification

New class **`derived-sql`**, replacing `plan:derived`. Before: a derived column waited for an engineer to *write* the change. Now: the compiler writes all four artifacts itself from the supplied query, and the change waits only for a reviewer to *approve* it. The pinned counts in the test suites move in the implementing story's PR.

### Hygiene gate

The structural checks that spot a complex-shaped column run in CI for every domain with a mapping; a flagged column must carry a `derivation.sql` contract. Complex logic can never hide in a free-text note.

## Transition from the earlier approach

A first draft of this decision (2026-08-23) took the describe-in-a-step-language path, and its first phase shipped as UIFPE-210 before this design replaced it. What happens to that machinery:

| Shipped artifact (UIFPE-210) | Disposition |
|---|---|
| `derivation` block in the shared schema | **Re-shaped** to the contract above (step definitions removed; sql / keyedBy / valueColumn / reads added) |
| The derivation blocks registered today (four, across the two live domains) | **Migrated** — the SQL is lifted verbatim from the existing hand-written queries; the retired drift check had already proven the description and the SQL matched, so the lift is mechanical |
| Step renderers, drift check, and their shared test fixture (Python + TypeScript) | **Retired** — with one source of truth there is nothing to drift; the wrap is covered by ordinary compiler tests |
| Hygiene gate (structural checks, auto-enrolment of every mapped domain) | **Kept**, re-based on the contract above |
| `plan:derived` class (both sides) | **Replaced** by `derived-sql`; shared test fixtures regenerated |
| The read-only step display in the UI | **Replaced** by the query display plus the computed `reads` list |
| Code comments citing earlier section labels of this ADR | Cleaned in the same implementing story |

Zero behavioral change to any deployed pipeline: the migrated queries are the ones already running.

## Consequences

- The mapping tells the whole truth **by construction** — it carries the logic, not a description of it.
- Complex columns become writable through the UI by the people who own the mappings.
- Derived columns move from the engineer's queue to the reviewer's queue: self-service with a human gate, not self-service instead of one.
- Reviewers of these pull requests read the exact SQL that will run.
- Two platform lessons are baked into the rules above: numeric keys must be compared with consistent casts, and anything that picks one value from many — or builds a list — must state its ordering, or identical runs produce different change signals and ring every consumer for nothing.

## Non-goals (hard boundaries)

- **No templates.** Direct query input — typed, pasted, or AI-drafted into the same box — is the only complex-column path.
- **No framework vocabulary in the UI.** Kinds keep their established names (Scalar / Conditional / Nested / Derived (SQL)); everything else shown is source-native names or plain outcomes.
- **No ungoverned SQL.** A query failing any refusal is not stored, not previewed as accepted, not submittable.
- **No multi-statement or data-changing SQL, ever.**
- **No domain-specific logic in this document or the shared schema** — each domain's queries live in that domain's mapping files.
