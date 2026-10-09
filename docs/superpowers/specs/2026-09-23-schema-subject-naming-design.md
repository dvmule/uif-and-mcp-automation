# Schema subject naming standard — design

**Date:** 2026-09-23 (revised 2026-09-24, twice — see §1)
**Status:** **Implemented** — the ARB deviation was granted 2026-09-24. Registration remains
blocked on the step-2 lint amendment (§8), which is not in this repository.
**Decider:** Rajnivas Karunakaran (repository owner)
**Capabilities touched:** Schema (primary) · Data · Event · API · Delivery
**Companion decision record:** [ADR-0023](../../../architecture-design/decisions/0023-schema-subject-naming.md)
**Fulfils:** [ADR-0022](../../../architecture-design/decisions/0022-gold-schema-pipeline-repo-first-registry-next.md) decision 4

---

## What this is

A schema **subject** is the address a contract lives at in the registry — the thing a
publisher writes to and a consumer reads from. This document defines the subject grammar
for the five kinds of schema UIF registers per gold entity: the Silver→Gold **mapping**,
the **gold** master, and the three tier projections (**api**, **event**, **message**).

It exists because ADR-0022 decision 4 assigns `stacks/apicurio` *"the standard for
coordinates, which masters cite for the coordinate they declare"* — and that standard did
not exist. Three different forms had grown in its absence.

Two things it is **not**. It does not govern the per-operation event payload, which has a
normative Pearson convention already (§2). And it does not govern the elements *inside* a
contract — that is Schema Standards N1–N12 (Confluence, UIF space), untouched here.

A PM or UX reader needs only §3. §8 is the governance gate.

---

## 1. Revision notes — two corrections, both recorded rather than edited out

**Revision 1 (premise was wrong).** The first draft claimed five subject conventions were
live and all equally drifted. In fact **one of them is a normative, ARB-governed Pearson
standard** the repository already implements correctly: "Event Envelope & Naming Standards"
(Confluence ITSOC 1277100355), whose parent states *"Deviation requires ARB approval"*. So
`product-created-data` is compliant, not drift; the dotted grammar proposed here is a
**deviation** needing ARB approval (§8); **ADR-0013 is the deviation, not the baseline** —
its §9 condemns the normative form; and majors are **semver artifact versions**, not `.vN`
suffixes. The cause: the standard is in Confluence and nothing in this repository links to
it (§10 item 3).

**Revision 2 (inventory was stale and undercounted).** The first draft was written against
a feature branch **138 commits behind `develop`**, and scanned only
`domains/*/src/schemas/`. Both were wrong:

- **UIFPE-523 had already renamed the tier projections** to `{entity}.gold-{tier}-value`
  and **moved the api tier to `src/api/`**. Three of the original eleven renames pointed at
  files that had moved or been renamed.
- **The scan undercounted twice. 21 found, then 25, and 29 actually exist.** Scanning
  `domains/*/src/schemas/` for a JSON `registry` block found 21; widening to `domains/`
  found 25 — adding both `src/api/` tier projections and three capability artifacts
  (`legal:vocabulary`, `legal:knowledge-graph`, `hr:a2a-routing`). Widening again to YAML,
  where the coordinate is spelled **`x-registry`**, found 29: three OpenAPI contracts and
  one AsyncAPI document. All four are out of scope, but a regrowth test that cannot see
  them is not a regrowth test — §7 now reads both spellings in both formats.
- **Two enforcement points were missing from §5** — `generate_tier_subsets.py`, which
  writes the coordinate into the file, and `publish.sh --artifact-id`, which registers
  under it.

The lesson worth keeping: an inventory is only as good as its scope and its base commit, and
this one was wrong on both.

---

## 2. The two baselines

### 2.1 Normative (Pearson, ARB-governed)

From "Event Envelope & Naming Standards" (ITSOC 1277100355):

| Item | Convention | Example |
|---|---|---|
| Group | `<domain>` | `catalog` |
| Envelope artifact | `pearson-event-envelope`, group `uif` | `uif/pearson-event-envelope` |
| Data schema artifact | `<entity>-<action>-data` | `catalog/product-created-data` |
| AsyncAPI document artifact | `<domain>-events-asyncapi` | `catalog/catalog-events-asyncapi` |
| Artifact version | Semver `MAJOR.MINOR.PATCH` | `1.2.0` |

Plus, from "Versioning, Registry Workflow & Contract Testing" (ITSOC 1276903795):
**`BACKWARD_TRANSITIVE`** on data schema artifacts (`FULL_TRANSITIVE` on the envelope); a
**D21 tier** (T1/T2/T3) required at registration; and a **step-2 CI lint** whose failure
condition is *"artifact name does not match `<entity>-<action>-data`"* — enforced, not
merely documented. That lint is the gate §8's deviation must clear.

**The gap:** this table covers three artifact kinds. `mapping`, `gold` and the three tier
projections are not among them. The Schema Standards "Gold" page (UIF 1493795302) governs
mapping-file contents and column/table naming, but no registry artifact naming.

### 2.2 Repo-internal (ADR-0022, Proposed)

ADR-0022 defines the gold schema pipeline as five steps and, in decision 4, assigns step ⑤
to `stacks/apicurio` because it owns *"both halves of what registration needs — the
**standard for coordinates**, which masters cite for the coordinate they declare, and the
service that registers them."* The service exists; the standard did not. **This document is
that standard**, which is why it fulfils ADR-0022 rather than competing with it.

Three further ADR-0022 decisions this design inherits rather than restates: the master is
the configuration (decision 2), `metadata.lifecycle` is the publish gate (decision 3), and
the three audiences are `event`, `message`, `api` — the names used verbatim in §3's type
vocabulary.

---

## 3. The grammar

```
group    = {domain}
artifact = {domain}.{entity}.{type}
```

Exactly three dot-separated segments. No version segment: the major lives in the artifact's
semver version, per §2.1.

```
group: product                     group: stock
  product.product.mapping            stock.stock.mapping
  product.product.gold               stock.stock.gold
  product.product.api                stock.stock.api
  product.product.event
  product.product.message
  product.marketing-copy.mapping
  product.marketing-copy.gold
```

### 3.1 Slot rules

- **`domain`** — one of the `domains/` directories. Never `default`. The `uif` group is
  reserved for the envelope artifact per §2.1 and is not a domain.
- **`entity`** — the **gold table name, kebab-cased**. On disk these are snake case
  (`marketing_copy`, the Athena/Glue convention, itself normative per the Gold page), so the
  transform is stated once: `_` → `-`. This makes `product.marketing-copy.gold` derivable
  from `gold/marketing_copy/v1/master.json` mechanically rather than by judgement.
- **`type`** — a closed vocabulary of exactly five: `mapping`, `gold`, `api`, `event`,
  `message`. The last three are ADR-0022's three audiences, named identically. Adding a
  sixth requires an ADR.

### 3.2 The pattern

```
^(?P<domain>[a-z][a-z0-9]*)
\.(?P<entity>[a-z][a-z0-9]*(?:-[a-z0-9]+)*)
\.(?P<type>mapping|gold|api|event|message)$
```

Verified against 26 cases, 0 failures. It correctly rejects the normative
`<entity>-<action>-data`, `uif/pearson-event-envelope` and `<domain>-events-asyncapi` forms,
and the transitional `product.gold-api-value` — so the grammars cannot be confused by
either gate.

| Check | Enforced by | Why there |
|---|---|---|
| Shape and type vocabulary | the regex | pure syntax, same answer everywhere |
| `domain` ∈ `ls domains/` | a repo test | `create-domain.sh` must never require a validator edit |
| `domain` == `metadata.domain` | the BFF, already, at `metadata_validator.py:126` | only the registry knows the live metadata block |

---

## 4. Per-type rules

| Type | Registers | Source of truth | Compatibility | Tier (D21) |
|---|---|---|---|---|
| `mapping` | one per gold entity | authored `*.mapping.json` | `BACKWARD_TRANSITIVE` | required |
| `gold` | one per gold entity | `gold/{entity}/v{n}/master.json` | `BACKWARD_TRANSITIVE` | required |
| `api` | tier projection | master, `x-channels: api` | `BACKWARD_TRANSITIVE` | required |
| `event` | tier projection | master, `x-channels: event` | `BACKWARD_TRANSITIVE` | required |
| `message` | tier projection | master, `x-channels: message` | `BACKWARD_TRANSITIVE` | required |

**Compatibility is `BACKWARD_TRANSITIVE`**, per §2.1 — not the `BACKWARD` the 20 blocks that
declare one currently carry. Corrected in §6.

**A D21 tier is required at registration.** No block anywhere declares one today, so §6 adds
them. Tier is registry metadata, changeable without a schema version bump, so an initial
assignment is not a long-term commitment.

**Versioning is the normative semver model, unchanged.** The `.vN` artifact-suffix model
stays where its justification holds — ADR-0013's per-operation event lane, out of scope
here. ADR-0013's case for `.vN` is DetailType visibility for EventBridge routing, and
**none of these five is routed by DetailType**.

**The three tier projections are generated**, by `generate_tier_subsets.py` from the master's
`x-channels` tags (ADR-0022 step ③). Per ADR-0022 decision 1 no step may have a second
implementation, so the generator is the only writer and regenerating **is** the rename.

---

## 5. Enforcement

### 5.1 Authority — the BFF

`stacks/apicurio/src/app/services/metadata_validator.py` gains one rule beside the existing
domain-prefix check at `:126`: `artifact_id` matches §3.2, and `type` is in the closed
vocabulary. This is the only component that can reject a publish; Schema Standards names the
*"APICURIO Governance BFF"* as the cross-cutting enforcement point; and ADR-0022 decision 4
puts the coordinate standard in this stack.

**Hard prerequisite.** The step-2 lint currently **fails** any artifact name not matching
`<entity>-<action>-data` (§2.1). Until it is amended per §8, the BFF rule and the lint
contradict each other. **Neither may be deployed before the other is amended** — shipping
one alone makes the five types unregisterable.

### 5.2 The three other writers of a coordinate

Missed in the first draft, and the reason a rename would otherwise be reverted:

| Where | What it does | Change |
|---|---|---|
| `stacks/echo/src/apps/cli/generate_tier_subsets.py:250` | writes `"artifact": f"{args.entity}.gold-{tier}-value"` into each projection | emit `{domain}.{entity}.{type}` |
| `stacks/echo/src/apps/cli/publish.sh` | registers under its `--artifact-id` | callers pass the new coordinate; ADR-0022 decision 4 moves registration to `stacks/apicurio` anyway |
| `stacks/echo/qe/gold/test_publish_sh.py` | asserts the coordinate | update with the above |

UIFPE-523's `{entity}.gold-{tier}-value` was not a naming decision — `publish.sh` was
already passing it, and UIFPE-523 made the file agree with what was being registered, fixing
*"three names for one artifact."* This standard supplies what that name was standing in for.

### 5.3 Advisory — the publish form

`SchemaPublishForm.tsx:13` replaces `SUBJECT_SHAPE` with the §3.2 pattern and rewrites the
message. It stays a **warning, not a block**: the server is the authority, and the form's job
is while-you-type feedback, which a per-keystroke round trip would not give.

The current regex matches only the demo seeds and the transitional tier coordinates, and
fires on every other real name in the estate — the normative form included.

### 5.4 Shared corpus — the anti-drift mechanism

`stacks/apicurio/standards/subject-naming.cases.json` holds valid and invalid subjects, each
invalid case annotated with why it fails — including the normative out-of-scope forms and the
transitional tier form, so the boundary is itself tested. Both `pytest` (BFF) and `vitest`
(UI) load it. No codegen step in either toolchain.

---

## 6. Migration

**29 coordinates exist under `domains/`** — 25 as a JSON `registry` block, 4 as a YAML
`x-registry` block. 12 are in scope and renamed, 2 are created, 17 are out of scope (§6.4).

**Everything here is gated on §8.** No coordinate is registered before ARB approves; the file
edits can land behind the gate because no in-scope artifact has a live registration.

### 6.1 Renames (12)

| From (`group`:`artifact`) | To | File |
|---|---|---|
| `product`:`product-gold-master` | `product.product.gold` | `product/src/schemas/gold/product/v1/master.json` |
| `product`:`marketing-copy-gold-master` | `product.marketing-copy.gold` | `product/src/schemas/gold/marketing_copy/v1/master.json` |
| `price`:`price-gold-master` | `price.price.gold` | `price/src/schemas/gold/price/v1/master.json` |
| `program`:`program-gold-master` | `program.program.gold` | `program/src/schemas/gold/program/v1/master.json` |
| `stock`:`stock-gold-master` | `stock.stock.gold` | `stock/src/schemas/gold/stock/v1/master.json` |
| `product`:`product-gold-attribute-mapping` | `product.product.mapping` | `product/src/schemas/mapping/product/v1/product.mapping.json` |
| `product`:`marketing-copy-gold-attribute-mapping` | `product.marketing-copy.mapping` | `product/src/schemas/mapping/marketing_copy/v1/product.mapping.json` |
| `program`:`program-gold-attribute-mapping` | `program.program.mapping` | `program/src/schemas/mapping/program/v1/program.mapping.json` |
| `product`:`product.gold-api-value` | `product.product.api-created` / `-updated` | `product/src/api/product-data-api-{created,updated}.json` |
| `product`:`product.gold-event-value` | `product.product.event-created` / `-updated` | `product/src/schemas/events/channels/product-data-event-{created,updated}.json` |
| `product`:`product.gold-message-value` | `product.product.message-created` / `-updated` | `product/src/schemas/events/channels/product-data-message-{created,updated}.json` |
| `stock`:`stock.gold-api-value` | `stock.stock.api-created` / `-updated` | `stock/src/api/stock-data-api-{created,updated}.json` |

The last four are **generated** — the change is to `generate_tier_subsets.py` (§5.2), and
regenerating produces the rename. Hand-editing them would be reverted by the next run.

All 12 also change `compatibility` to `BACKWARD_TRANSITIVE` and gain a D21 tier (§4).

### 6.2 `registry` blocks created (2)

Both files currently carry **no `registry` block at all**, already violating
`stacks/apicurio/standards/guidelines.md` §1:

| New coordinate | File |
|---|---|
| `price`:`price.price.mapping` | `price/src/schemas/mapping/price/v1/price.mapping.json` |
| `stock`:`stock.stock.mapping` | `stock/src/schemas/mapping/stock/v1/stock.mapping.json` |

### 6.3 Code changes (4)

`generate_tier_subsets.py`, `publish.sh` callers, `test_publish_sh.py` (§5.2), and the
step-2 lint rule (§8, not in this repository).

### 6.4 Out of scope — listed so it is a decision, not an oversight (17)

| Coordinate | Why out |
|---|---|
| `price`:`price-created-data`, `price-updated-data` | per-operation event payload — normative `<entity>-<action>-data`, §2.1. Tabulated in ADR-0023 §1.2 |
| `product`:`product-created-data`, `product-updated-data` | same |
| `program`:`program-created-data`, `program-updated-data` | same |
| `stock`:`stock-created-data`, `stock-updated-data` | same |
| `product`:`product-master-data-contract` | a data contract; not one of the five types |
| `hr`:`employee-classification` | a field-classification policy; not one of the five |
| `legal`:`vocabulary` | Semantics capability's artifact — its naming is that capability's decision |
| `legal`:`knowledge-graph` | Knowledge Graph capability's |
| `hr`:`a2a-routing` | A2A capability's |
| `product`:`product-api`, `product`:`marketing-copy-api`, `stock`:`stock-api` | OpenAPI/REST contracts — `capabilities/api/guidelines.md` §1 (`<domain>-<resource>-api`). Declared as YAML `x-registry` |
| `product`:`product-events` | Per-domain AsyncAPI document. Normatively `<domain>-events-asyncapi`, which this **does not match** — recorded in §10 |

`legal:vocabulary`, `legal:knowledge-graph` and `hr:a2a-routing` are bare nouns governed by
no standard, this one included. They are listed so the §7 regrowth test passes, which makes
them **visible rather than governed** — a deliberate distinction.

**One readability hazard worth stating.** `product`:`product-api` (the OpenAPI contract) and
`product`:`product.product.api` (the api-audience tier projection) are different artifacts in
one group, and `api` means something different in each. They do not collide — §3.2 separates
them — but a reader must know which kind they are looking at. Renaming either would mean
overriding the API capability's naming rule or ADR-0022's audience vocabulary, neither of
which is this document's to take.

The demo seed artifacts in dev and uat are also left alone; they will report non-compliant
(§5.1).

---

## 7. Testing

| Test | Asserts | Lives in |
|---|---|---|
| Regex cases | every entry in `subject-naming.cases.json`, both languages — including that the normative and transitional out-of-scope forms are rejected | BFF `qe/`, UI `src/**/*.test.ts` |
| Domain vocabulary | the `domain` slot of every in-scope coordinate is a directory under `domains/` | repo test |
| No convention regrowth | every coordinate **anywhere under `domains/**`** — JSON `registry` *and* YAML `x-registry` — is either §3-compliant or on the §6.4 list | repo test |
| Compatibility mode | every in-scope block declares `BACKWARD_TRANSITIVE` | repo test |
| Tier presence | every in-scope block declares a D21 tier | repo test |
| Projection drift guard | the major parsed from each projection's `metadata.source` path equals the `gold` artifact's semver MAJOR | repo test |
| Generator parity | a fresh `generate_tier_subsets.py` run reproduces each committed projection byte-identically, coordinate included | `stacks/echo/qe/gold/` |
| Docs match code | the five type tokens in the standard document equal the vocabulary in the validator | BFF `qe/` |

**The regrowth test reads both spellings in both formats, anywhere under `domains/**`.** Each
narrowing cost real coverage: `domains/*/src/schemas/**` missed five coordinates including
both `src/api/` tier projections — the exact artifacts this standard governs — and
JSON-only missed four more declared as YAML `x-registry`.

**The drift guard** covers a real hole: nothing structurally prevents a projection at major 1
being generated off a master at major 2. Projections record
`metadata.source: "schemas/gold/product/v1/master.json"`, so the major is parseable from a
file the generator writes.

**Generator parity** already exists in spirit — UIFPE-523 confirmed the projections
byte-identical to a fresh run. This makes the coordinate part of that assertion.

---

## 8. The ARB submission

**One deviation is requested.** Everything else conforms to §2.1.

| | |
|---|---|
| **Deviation** | Subject grammar for five artifact kinds: `{domain}.{entity}.{type}` instead of `<entity>-<action>-data` |
| **Scope** | `mapping`, `gold`, `api`, `event`, `message` only. The per-operation data schema, the envelope and the AsyncAPI document keep the normative forms untouched |
| **Not requested** | Versioning (normative semver retained), compatibility (`BACKWARD_TRANSITIVE` adopted), tier (D21 adopted), group = `<domain>` (already matches) |
| **Requires** | Amendment to the step-2 lint rule so it applies to the data-schema kind rather than every artifact |
| **Blocks** | Registration of all five types. File edits may land; nothing publishes |

**The case for it.**

1. **`<entity>-<action>-data` does not fit four of the five.** It has an `<action>` slot, and
   a gold master, a mapping and a tier projection have no action — they are not occurrences.
   Forcing them into it means inventing a pseudo-action, and the estate proves the point:
   `product-gold-master` and `product.gold-api-value` **already deviate, differently**,
   because the normative form has no shape for them.
2. **The type must be readable from the coordinate.** Five artifact kinds share one group per
   domain. Browsing, CI path filters and per-type compatibility reasoning all need to
   distinguish a gold master from a mapping without a metadata round trip.
3. **It is additive.** The normative grammar keeps every kind it covers; this fills a gap it
   does not address. The two are mutually exclusive by construction (§3.2).
4. **ADR-0022 decision 4 already called for it** — a repo-internal decision assigning
   `stacks/apicurio` the coordinate standard. This is not a new ambition.
5. **The alternative is worse-defined.** Extending the kebab form means agreeing four new
   pseudo-action tokens with no vocabulary to draw them from.

**An open question to put to ARB alongside it.** The normative domain vocabulary is
`catalog`, `customer`, `finance`, `pricing`, `contract`, `order`, `employee`; this
repository's groups are the thirteen `domains/` directories — `product` where the standard
writes `catalog`, `orders` for `order`. **The `group` slot therefore diverges today, for
artifacts already in normative scope**, independent of this deviation.

---

## 9. Out of scope

- The per-operation event payload — normative per §2.1, unchanged. It is `created` and
  `updated` only: **there is no `deleted` artifact**, because the product AsyncAPI records
  that *"DELETE verdicts are counted but not yet published"* and that every non-delete
  verdict publishes `updated`. Eight artifacts across four domains. Tabulated with their
  CloudEvents types and DetailTypes in ADR-0023 §1.2, which also sets out why this lane and
  the `event` type are easily confused — both describe the envelope's `data` block, one
  sliced by audience and the other by operation.
- The envelope artifact and the AsyncAPI document artifact — normative per §2.1, unchanged.
- Schema Standards N1–N12 — the elements *inside* a contract. Its own scope statement excludes *"resource and infrastructure naming"*, which is why no N-rule governs subjects.
- The OpenAPI/REST contract naming rule (`<domain>-<resource>-api`, `capabilities/api/guidelines.md` §1).
- The CloudEvents `type` attribute and ADR-0013's `.vN` family model.
- **`legal:vocabulary`, `legal:knowledge-graph`, `hr:a2a-routing`** — the Semantics, Knowledge Graph and A2A capabilities' own naming decisions.
- Retiring or re-seeding the demo artifacts in dev/uat.
- Ratifying ADR-0013, and reconciling it with §2.1 (§10 item 1).
- The two `$id` grammars. `$id` is not the registry subject.

## 10. Known open items

1. **ADR-0013 contradicts the normative standard on two counts** — its dotted form and its
   `.vN` model — and its §9 condemns the normative kebab form as legacy. Still `Proposed`.
   Nothing here depends on its resolution.
2. **The `group` vocabulary divergence** (§8) affects artifacts already in normative scope.
3. **Nothing in this repository links to the governing Confluence standards** from anywhere a
   subject-naming reader would look. That is why revision 1 was needed. Adding them to
   `stacks/apicurio/standards/README.md` is cheap and prevents a recurrence.
4. **Three artifact kinds are governed by no standard** — `legal:vocabulary`,
   `legal:knowledge-graph`, `hr:a2a-routing`.
5. **The AsyncAPI document's name diverges from the normative form.** `product`:`product-events`
   against a normative `<domain>-events-asyncapi`. Out of scope here (AsyncAPI documents are
   normatively governed), found by this inventory, recorded so it is not lost.
6. **Group-level compatibility rules** remain unavailable on the BFF (ADR-0013 open item #1),
   so §4's mode is set per artifact.
7. **RESOLVED 2026-09-24 — the ARB approver is recorded.** The approver is Rajnivas
   Karunakaran, acting in the ARB approver role, with no conditions attached; it is now in
   ADR-0023's header. Approver and decider are the same person and the header states that
   plainly, because no separate ARB minute or Jira decision record exists — that line is
   the record.
8. **RESOLVED 2026-09-24 — the D21 tiers are confirmed, and what they mean is now written
   down.** All 14 in-scope artifacts are `T2`, confirmed by the framework owner; it is the
   standard's own default for domain business events. Investigating this surfaced a scope
   problem worth keeping: D21 is written for **event types** and holds the tier "on the
   `data` schema", while none of these five is an event type, a `data` schema, or has a
   stream — so five of D21's seven dimensions (stream retention, DLQ alerting, drift
   sampling, per-event statusing, Kinesis capacity) are undefined for them, and only the
   parallel-run window and contract-test depth transfer. `subject-naming.md` now tabulates
   that, so a tier on one of these five is not read as claiming the full matrix. Whether
   D21 should reach streamless artifacts at all remains the normative owners' question;
   declaring a tier is the conservative reading because the step-2 lint rejects declaring
   none. Re-tiering stays a catalogue operation with no version bump.
9. **PARTLY ADDRESSED 2026-09-24 — the step-2 naming rule is enforced here; the standard
   and its own CI are not.** What landed: the rule is executable and running in this
   repository (`stacks/apicurio/standards/test_step2_naming.py`, via
   `subject-naming-ci.yml`), deriving the expected `<entity>-<action>-data` name from the
   reverse-DNS event type and comparing the two — a gap where an artifact and its type
   could describe different operations with every prior gate passing. What did not: the
   normative specification on ITSOC 1276903795 is **unamended** (the amendment was
   published as v3 and reverted to v4 the same day, pending an ARB record independent of
   the ADR asserting it), and the Eventing standard's own CI has no implementation locatable
   in this repository or the `pearson-dnt-coe` organisation. The five types still cannot
   register.
7. **`capabilities/README.md:35`** links to `capabilities/schema/`, which ADR-0012 deleted.
