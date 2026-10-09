# ADR-0023 — A subject grammar for the five registered schema types UIF has no convention for

**Status:** **Accepted** — the Decision 7 deviation was granted by ARB
**Revised:** 2026-09-25, on the repository owner's instruction: the three channel types
are now registered **per operation**, so the closed vocabulary is `mapping`, `gold`, and
`{channel}-created` / `{channel}-updated` for each channel — eight tokens, not five.
Decisions 1, 1.1, 1.2 and 6 below are amended; the rest stand. **The original acceptance
rested on an ARB deviation granted by Rajnivas Karunakaran acting as approver, and this
ADR states it "cannot be Accepted by the repository alone" — so this revision needs the
same sign-off recorded in this block, and does not have it yet.** The vocabulary is
already enforced in the validator, the shared corpus and the UI, and 18 artifacts are
registered under it in dev and uat, so the position is live while the record is
incomplete; that gap is deliberate and visible rather than glossed.
**Date:** 2026-09-23 (revised 2026-09-24 · accepted 2026-09-24)
**Decider:** Rajnivas Karunakaran (repository owner). **The Decision 7 deviation was granted
by ARB on 2026-09-24; the approver is Rajnivas Karunakaran, acting in the ARB approver
role, with no conditions attached.** Approver and decider are therefore the same person —
recorded plainly here so a reader can weigh that rather than have to infer it, since this
ADR states below that it "cannot be Accepted by the repository alone". No separate ARB
minute or Jira decision record exists; this line is the record.
**Scope:** The registry coordinate (`group` + `artifact`) for the five schema types UIF
registers per gold entity — `mapping`, `gold`, and the `api`/`event`/`message` tier
projections. These five have **no** convention in any current Pearson standard.
**Fulfils:** [ADR-0022](0022-gold-schema-pipeline-repo-first-registry-next.md) decision 4,
which assigns `stacks/apicurio` *"the standard for coordinates, which masters cite for the
coordinate they declare"* — and names it as something the registry stack owns but which does
not yet exist. This ADR is that standard.
**Conforms to:** the normative APICURIO artifact naming, versioning, compatibility and
tiering rules in "Event Envelope & Naming Standards" (Confluence ITSOC 1277100355) and
"Versioning, Registry Workflow & Contract Testing" (ITSOC 1276903795) — **except** the one
deviation in Decision 7.
**Supersedes in practice:** the transitional `{entity}.gold-{tier}-value` tier coordinate
introduced by UIFPE-523 (see Decision 5).
**Relates to:** [ADR-0012](0012-schema-registry-moves-to-stacks-apicurio.md) (where the
Schema standards live), [ADR-0011](0011-apicurio-schema-governance.md) (the governance
layer), [ADR-0013](0013-schema-versioning-and-consumer-migration.md) (the per-operation
event lane — see Decision 2), [ADR-0018](0018-gold-runtime-is-a-stack-concern.md) (the
ownership test ADR-0022 applies), [ADR-0014](0014-naming-standard-refresh.md) (the
precedent for "one table, plus a test that the docs and the code agree").
**Numbering:** 0023. Follows ADR-0022.
**Design:** [`docs/superpowers/specs/2026-09-23-schema-subject-naming-design.md`](../../docs/superpowers/specs/2026-09-23-schema-subject-naming-design.md)

---

## Context

**UIF registers five kinds of schema artifact that no Pearson standard names.** The
normative APICURIO artifact naming table covers exactly three kinds — the event envelope
(`uif/pearson-event-envelope`), the per-operation data schema (`<entity>-<action>-data`),
and the per-domain AsyncAPI document (`<domain>-events-asyncapi`). The Schema Standards
"Gold" page governs mapping-file contents, column naming and table naming, but no registry
artifact naming. So the Silver→Gold mapping, the gold master, and the three tier
projections are ungoverned.

**ADR-0022 already identified this gap and assigned it an owner.** Its decision 4 gives
`stacks/apicurio` both halves of registration — the service, and *"the standard for
coordinates, which masters cite for the coordinate they declare"*. The service exists; the
standard did not. This ADR supplies it, which is why it is a fulfilment of ADR-0022 rather
than a competing decision.

**What the 29 coordinates on disk look like without a standard.** Three separate forms have
grown in the vacuum: `product-gold-master` and `product-gold-attribute-mapping` (a kebab
form bent from `<entity>-<action>-data`, whose `<action>` slot is meaningless for an
artifact that is not an occurrence), `product.gold-api-value` for the tier projections, and
bare nouns like `legal:vocabulary` and `hr:a2a-routing` for artifact kinds outside this
scope entirely.

**Two things made the problem hard to see, and both are worth recording.**

First, **the governing standard is in Confluence and nothing in this repository links to
it.** The first draft of the design document treated the on-disk kebab names as drift to be
collapsed, when `product-created-data` is the repository's correct implementation of an
ARB-governed standard. Decision 8 addresses the cause.

Second, **ADR-0013 §9 condemns the normative form.** It calls `<entity>-<action>-data` and
tier-qualified names *"legacy … to be migrated or retired"* and asserts a dotted
`{domain}.{entity}.{operation}` form with `.vN` sibling artifacts. Both diverge from the
normative standard, and ADR-0013 is still `Status: Proposed`. Anyone reading only the ADRs
would conclude the opposite of what the standard says.

**A third failure is live today.** `SchemaPublishForm.tsx:13` validates
`^[a-z0-9]+(\.[a-z0-9-]+)+-(value|key)$` — the Kafka subject shape. It matches the demo
seed data and the transitional tier coordinates, and warns on every other real name in the
estate, including the normative form and ADR-0013's canonical form.

**The cost of acting now is low.** No in-scope artifact has a live registration this
standard would break; all gold and mapping blocks are `lifecycle: draft` per ADR-0022
decision 3, and the live dev and uat registries hold only demo seeds.

## Decision

**Eight decisions. Seven conform to the normative standard; one is a deviation.**

### 1. The coordinate grammar

```
group    = {domain}
artifact = {domain}.{entity}.{type}
```

Exactly three dot-separated segments; no version segment.

- `domain` — a directory under `domains/`. Never `default`. The `uif` group stays reserved
  for the envelope artifact.
- `entity` — the gold table name, kebab-cased (`marketing_copy` → `marketing-copy`), so the
  subject is derivable from the master's path mechanically rather than by judgement.
- `type` — a **closed vocabulary of eight** (revised 2026-09-25): `mapping` and `gold`
  unsuffixed, plus each of ADR-0022's three *audiences* crossed with each published
  operation — `api-created`, `api-updated`, `event-created`, `event-updated`,
  `message-created`, `message-updated`. A channel is registered once per operation so the
  two contracts can version independently.

  Both sets stay **closed**, and the cross product is built from them in one place
  (`_SUBJECT_CHANNELS` and `_SUBJECT_OPERATIONS` in `metadata_validator.py`): a new
  channel is not a valid type until it is added there, the same gate the naming rules put
  on the `{stack}` token. Only `created` and `updated` exist, because a delete is detected
  and counted but never published — §1.2 records that — so a `deleted` type would name a
  contract nothing serves.

  The **bare channel tokens are retained as superseded**, not deleted. The claim test is
  "the third segment is one of the type tokens", so removing `api` outright would make
  `product.product.api` unclaimed — passing validation as though governed by some other
  standard. A superseded name going quiet is the one outcome a migration cannot afford, so
  they stay claimed and are rejected with a message naming the replacement.

The domain repeats the group deliberately: a subject is constantly quoted without its group
(lineage references, CI logs, error messages), and it should still say who owns it.

### 1.1 One example of each type

All five exist today only for the `product` domain's `product` entity, so that is the one
complete worked set. Paths are relative to `domains/product/`.

| Type | Subject (`group` : `artifact`) | What it is | Name today | File |
|---|---|---|---|---|
| `mapping` | `product` : `product.product.mapping` | Silver→Gold attribute mapping — the authored rules | `product-gold-attribute-mapping` | `src/schemas/mapping/product/v1/product.mapping.json` |
| `gold` | `product` : `product.product.gold` | The gold master — full engineering description, `x-channels` per attribute | `product-gold-master` | `src/schemas/gold/product/v1/master.json` |
| `api-created` | `product` : `product.product.api-created` | Tier projection for request/response callers, created occurrences | `product.gold-api-value` | `src/schemas/channels/product/product-data-api-created.json` |
| `api-updated` | `product` : `product.product.api-updated` | Tier projection for request/response callers, updated occurrences | `product.gold-api-value` | `src/schemas/channels/product/product-data-api-updated.json` |
| `event-created` | `product` : `product.product.event-created` | Tier projection for queue subscribers, created occurrences | `product.gold-event-value` | `src/schemas/channels/product/product-data-event-created.json` |
| `event-updated` | `product` : `product.product.event-updated` | Tier projection for queue subscribers, updated occurrences | `product.gold-event-value` | `src/schemas/channels/product/product-data-event-updated.json` |
| `message-created` | `product` : `product.product.message-created` | Tier projection for the stream and the export platform, created occurrences | `product.gold-message-value` | `src/schemas/channels/product/product-data-message-created.json` |
| `message-updated` | `product` : `product.product.message-updated` | Tier projection for the stream and the export platform, updated occurrences | `product.gold-message-value` | `src/schemas/channels/product/product-data-message-updated.json` |

The last three are generated from the `gold` master by `generate_tier_subsets.py`, filtered
on `x-channels` (ADR-0022 step ③), so regenerating **is** the rename.

**How it generalises.** A projection exists only where attributes are tagged for that
audience, which is why most entities carry fewer than five.

| Subject | Note |
|---|---|
| `product` : `product.marketing-copy.gold` | Second entity in one domain. `marketing_copy` → `marketing-copy` per the kebab rule |
| `product` : `product.marketing-copy.mapping` | — |
| `price` : `price.price.gold` | Entity name equals domain name, so the repeat is literal |
| `price` : `price.price.mapping` | **Created by this change** — the file carries no `registry` block today |
| `program` : `program.program.gold` | — |
| `program` : `program.program.mapping` | — |
| `stock` : `stock.stock.gold` | — |
| `stock` : `stock.stock.api` | The only tier projection outside `product` |
| `stock` : `stock.stock.mapping` | **Created by this change** — no `registry` block today |

**What these are not.** Every row below is rejected by the design's §3.2 pattern, which is
what lets an in-scope subject and an out-of-scope one share a group safely.

| Subject | Kind | Governed by |
|---|---|---|
| `product` : `product-created-data`, `product-updated-data` | Per-operation event payload | Normative `<entity>-<action>-data` — unchanged, and expanded in §1.2 |
| `uif` : `pearson-event-envelope` | CloudEvents envelope | Normative — unchanged |
| `product` : `product-events` | Per-domain AsyncAPI document | Normative `<domain>-events-asyncapi`, which this **does not match** — see Consequences |
| `product` : `product-api`, `marketing-copy-api` · `stock` : `stock-api` | OpenAPI/REST contract | `capabilities/api/guidelines.md` §1 (`<domain>-<resource>-api`) |
| `product` : `product-master-data-contract` | Data contract | Nothing |
| `hr` : `employee-classification` | Field-classification policy | Nothing |
| `legal` : `vocabulary` · `legal` : `knowledge-graph` · `hr` : `a2a-routing` | Capability artifacts | Nothing — those capabilities' own decisions |

**One readability hazard this makes concrete.** `product` : `product-api` (the OpenAPI
contract) and `product` : `product.product.api` (the api-audience tier projection) are
different artifacts in one group, and the word `api` means something different in each. They
do not collide — the pattern separates them — but a reader must know which kind they are
looking at. The alternative was to rename one of them, which would mean overriding either
the API capability's own naming rule or the audience vocabulary ADR-0022 fixed; neither is
this ADR's to take.

### 1.2 The per-operation event lane, for contrast

This is the lane most easily confused with the `event` type above, and the confusion is
reasonable: **both describe the envelope's `data` block.** The channels README says so
directly — the tier projections are schemas *"for the `data` block of the event envelope
(the same position `product-created-data.json` describes per-operation)"*. They differ in
what they slice by. A tier projection is the gold master filtered by **audience**, one per
entity per audience. A per-operation payload is the `data` block for **one operation**, one
per operation.

Nothing here is governed by this ADR. The table exists so a reader can tell at a glance
which lane a subject belongs to.

| Operation | Artifact (normative `<entity>-<action>-data`) | CloudEvents `type` | Bus DetailType | State |
|---|---|---|---|---|
| created | `product` : `product-created-data` | `com.pearson.product.product.created.v1` | `product.created.v1` | live |
| updated | `product` : `product-updated-data` | `com.pearson.product.product.updated.v1` | `product.updated.v1` | live |
| deleted | **none** | — | — | **not published** |

**There is no `deleted` artifact, and that is deliberate rather than an omission.** The
product AsyncAPI records that *"DELETE verdicts are counted but not yet published"*, and
that *"INSERT publishes created, every other non-delete verdict publishes updated"* — so a
delete is detected and counted, and no event carries it. A `product-deleted-data` subject
would therefore be fictional, and is not listed as though it were pending.

`price`, `program` and `stock` each carry the same `created` + `updated` pair, so the lane is
eight artifacts across four domains. None has a `deleted`.

**The two lanes side by side**, for one domain:

| Subject | Lane | Sliced by | Count |
|---|---|---|---|
| `product.product.event` | this ADR | audience (`x-channels: event`) | one per entity |
| `product.product.message` · `product.product.api` | this ADR | audience | one per entity per audience |
| `product-created-data` · `product-updated-data` | normative, out of scope | operation | one per operation |

A subject with **three dot-separated segments** is this ADR's. A subject in
`<entity>-<action>-data` form is the normative lane's. The design's §3.2 pattern accepts the
first and rejects the second, so the two cannot be confused by any gate — only by a reader,
which is what this subsection is for.

### 2. This ADR does not touch the per-operation event lane

`<entity>-<action>-data` remains the normative form for the per-operation data schema, and
this ADR asserts nothing about it. §1.2 tabulates that lane for contrast, including that no
`deleted` artifact exists. **ADR-0013's conflict with that form is not resolved
here** — it is recorded as an open item and left to its own ratification.

**Amended 2026-09-25.** Both lanes now slice by operation, so "created/updated belongs to
the other lane" is no longer what separates them. What separates them is what the subject
*is*: this ADR's is a **tier projection** — the master filtered by audience, three dot
segments — and the normative lane's is a **per-operation data schema** in
`<entity>-<action>-data` form. A consumer therefore sees two subjects describing the same
envelope `data` block for the same operation, `product.product.event-created` and
`product-created-data`, and the honest answer is that the first is the audience-filtered
view this repository generates and governs, while the second is the ARB-governed
per-operation payload. Which one a consumer should bind to is **not settled here** and is
the open item this revision adds rather than resolves.

The two grammars are still mutually exclusive by construction — the design's §3.2 pattern rejects
`<entity>-<action>-data`, `uif/pearson-event-envelope` and `<domain>-events-asyncapi` — so
neither gate can mistake one lane for the other.

### 3. The type is a segment of the artifact id

Not the group, and not metadata-only. One group per domain, so a domain stays one ownership
unit and the normative `group = <domain>` rule is unchanged. The accepted cost is that
compatibility mode is set per artifact rather than once per group.

### 4. Versioning is the normative semver model, unchanged

Majors are the artifact's semver MAJOR; minors and patches follow the ITSOC semver mapping.
**No `.vN` artifact suffix for these five types.** ADR-0013's case for `.vN` is that a major
must be visible in the DetailType for EventBridge routing — and none of these five is routed
by DetailType, so the argument is unavailable for them.

### 5. The transitional tier coordinate is replaced, not contradicted

UIFPE-523 set the tier projections to `{entity}.gold-{tier}-value`. That name was **not a
naming decision**: `publish.sh` was already passing it as `--artifact-id`, and UIFPE-523's
fix was to make the generated file *declare* what was actually being registered, because
*"all three tiers claimed to be the master while publish.sh registered them per tier — three
names for one artifact, and the file agreed with none of them."* It closed an agreement bug
and inherited the name.

This ADR supplies the standard that name was standing in for. Two consequences:

- `generate_tier_subsets.py` emits `{domain}.{entity}.{type}` instead.
- `publish.sh --artifact-id` stops being the source of the name. ADR-0022 decision 4 moves
  registration off `stacks/echo` to `stacks/apicurio` anyway, so this removes a coordinate
  decision from a stack that ADR-0022 says should not be making one.

### 6. The channel types are read in the audience sense, and the token is `message`

`api`, `event` and `message` are ADR-0022's three audiences, produced by filtering the
master on `x-channels`. Since the 2026-09-25 revision each appears in the vocabulary only
with an operation suffix, but the audience reading is unchanged — the suffix says which
occurrence the contract describes, not which attributes it carries. The projection is
**identical for every operation**, which is why the generator emits the same body twice
under two coordinates. `message` — not `messaging` — is the live wire value: the
`x-channels` tag, ADR-0022's own audience table, and the envelope's `channel` field, which
subscription rules filter on.

### 7. **The deviation: one grammar, five artifact kinds, ARB approval required**

The normative form is `<entity>-<action>-data`. It has an `<action>` slot, and a gold
master, a mapping and a tier projection have no action — they are not occurrences. Forcing
them into it means inventing a pseudo-action, which is how `product-gold-master` and
`product.gold-api-value` both arose: **each already deviates in practice, differently,
because the normative form has no shape for them.**

This deviation is additive — the normative grammar keeps every artifact kind it covers —
and it requires one amendment: the step-2 lint rule *"artifact name does not match
`<entity>-<action>-data`"* must apply to the data-schema kind rather than to every
artifact.

**Granted by ARB on 2026-09-24, approver Rajnivas Karunakaran, no conditions.** The
remaining co-dependency is real and unchanged: the BFF rule and the step-2 lint must be
amended **together**, because a subject satisfying one currently fails the other. Shipping
either alone makes these types unregisterable.

**Where the lint actually stands (2026-09-24).** Three things, and only the first is done:

- *In this repository,* the step-2 **naming** rule is now executable and running in CI —
  `stacks/apicurio/standards/test_step2_naming.py`, wired through `subject-naming-ci.yml`.
  It derives the expected `<entity>-<action>-data` name from the reverse-DNS event type and
  compares it, closing a gap where an artifact and its type could describe different
  operations with every prior gate passing.
- *The normative specification* on Confluence is **unamended**. The amendment scoping the
  D5/D7 row to data schemas, plus a row for these five types, was published to ITSOC
  1276903795 as v3 on 2026-09-24 and **reverted to v4 the same day** by owner decision,
  because an ARB grant with no independent record should not be asserted on a normative
  page. The drafted amendment is preserved but unpublished.
- *The Eventing standard's own CI* — the enforcement the row describes — has no
  implementation that could be located in this repository or the `pearson-dnt-coe`
  organisation. It is owned outside this repository.

**Superseded 2026-09-25.** Registration went ahead: 18 artifacts are live in the dev and
uat registries under the revised vocabulary, having been registered directly against the
BFF rather than through the standard's own CI. So this is no longer a blocker on
registration; it is an outstanding gap in the *enforcement* the row describes — the
amendment is still unpublished and the Eventing standard's CI still has no implementation
in this organisation. Recording the change of state rather than leaving a paragraph that
says the estate is blocked when it is not.

### 8. Enforcement is server-authoritative, client-advisory, with three writers aligned

The rule is normative in `stacks/apicurio/src/app/services/metadata_validator.py` — the only
component that can reject a publish, and the layer Schema Standards names as the
cross-cutting enforcement point. Three other places currently decide or assert a
coordinate and must move with it: `generate_tier_subsets.py` (writes it into the file),
`publish.sh --artifact-id` (registers under it), and `stacks/echo/qe/gold/test_publish_sh.py`
(asserts it). `SchemaPublishForm.tsx` keeps a regex for while-you-type feedback, marked
advisory.

A shared example corpus, `stacks/apicurio/standards/subject-naming.cases.json`, is loaded by
both `pytest` and `vitest`, so a regex that drifts in either language fails that language's
tests. No codegen step in either toolchain.

## Consequences

**Becomes easier.** Five previously ungoverned artifact kinds get a convention, derivable
from a master's path with no judgement calls. ADR-0022's step ⑤ gains the standard it cites.
The type is readable from the coordinate, so browsing, CI path filters and per-type
reasoning need no metadata round trip. One coordinate decision leaves `stacks/echo`, which
ADR-0022 decision 4 wants. And a convention cannot regrow silently: a repo test asserts
every `registry.artifact` anywhere under `domains/` is either compliant or on an explicit
out-of-scope list — widened from `src/schemas/**` after that narrower scan missed five
coordinates, including both `src/api/` tier projections.

**Becomes harder.** Two subject grammars coexist in one registry — this one for the five
types, the normative one for the envelope, data schema and AsyncAPI document — so an
engineer must know which kind an artifact is before naming it. Mutual exclusivity
(Decision 2) makes that a build failure rather than a silent mistake, but it is still a
thing to know. Compatibility mode is set per artifact.

**Accepted: this ADR cannot be Accepted by the repository alone.** Decision 7 is ARB's to
grant. If ARB refuses, Decisions 1 and 3 fall and the fallback is to agree pseudo-action
tokens for four artifact kinds within the kebab form — a larger governance ask, which is
part of the case in Decision 7.

**Accepted: the compliance dashboard number will drop sharply on deploy.**
`validate_all()` sweeps every live artifact, and the live registries hold demo seeds in the
`-value` shape under non-UIF groups. Every one flips to non-compliant. That is the intended
result of leaving the seeds alone and flagging them, stated here so it is not later read as
a regression. The verdict is server-cached, so the rollout must force a re-validation.

**Accepted: three artifact kinds are named by no standard at all, including this one.**
`legal:vocabulary`, `legal:knowledge-graph` and `hr:a2a-routing` are bare nouns under a
domain group. They belong to the Semantics, Knowledge Graph and A2A capabilities, and
naming them is those capabilities' decision, not this one's. They are on the out-of-scope
list so the regrowth test passes, which makes them visible rather than governed.

**Accepted: the AsyncAPI document's name diverges from the normative form, and this ADR
does not fix it.** The normative artifact is `<domain>-events-asyncapi`; the repository
declares `product` : `product-events`. AsyncAPI documents are normatively governed and out
of scope here, so correcting it belongs with the `group` divergence below rather than in this
decision — but it was found by this inventory and is recorded so it is not lost.

**Accepted: a `group` vocabulary divergence is left unresolved.** The normative domain
vocabulary is `catalog`, `customer`, `finance`, `pricing`, `contract`, `order`, `employee`;
this repository's groups are the thirteen `domains/` directories — `product` where the
standard writes `catalog`, `orders` for `order`. This affects artifacts **already** in
normative scope, independent of this ADR, so it is raised to ARB alongside the deviation.

**Accepted: ADR-0013's conflict with the normative standard stays open.** Two counts — the
dotted form and the `.vN` model — plus a §9 that condemns the normative kebab form. It is
still Proposed. Nothing here depends on its resolution.

## Found during implementation

Three things the design did not anticipate, each of which changed it.

**The validator returns early for anything non-AVRO**, with `schema_type_unsupported` — and
all five of these types are JSON Schema. A rule placed where the design said (beside the
domain-prefix check) would never have fired for a single artifact it governs. The rule sits
**before** that gate instead, which is also correct on its own terms: a subject's shape is a
property of the coordinate, not of the body, so it needs neither the body parsed nor the
type supported.

**The rule cannot judge every subject, and must not try.** The registry holds artifacts
governed by other standards, so flagging a non-matching subject indiscriminately would make
the compliance verdict lie about artifacts this ADR has no authority over. The rule
therefore fires only when the third segment is one of the five type tokens — the evidence of
claiming this grammar. The consequence is deliberate and worth stating: a typo'd token like
`product.product.messaging` is **not** caught by the server. The repository test is the
strict half; it knows the full out-of-scope allowlist and can afford to be.

**The mapping meta-schema forbade the values the standard requires.**
`gold-attribute-mapping.schema.json` had `additionalProperties: false` on its registry block
with no `tier` property, and a `compatibility` enum of `BACKWARD|FORWARD|FULL|NONE` — so a
conformant `BACKWARD_TRANSITIVE` could not be declared at all. Both were fixed. This is the
kind of thing that turns a documented standard into one nothing can comply with.

**A latent collision was fixed in passing.** The master scaffold built
`f"{domain}-gold-master"`, keyed on *domain* — so a second entity in one domain would have
collided on one coordinate. `product` and `marketing_copy` were already the case in point;
only an explicit `intake.registry` kept them apart.

## Confirmed during review

**Three pre-existing violations were found while taking the inventory** and are fixed as
part of the implementation: `price.mapping.json` and `stock.mapping.json` carry no
`registry` block at all (already a violation of `stacks/apicurio/standards/guidelines.md`
§1); five blocks carry no `compatibility` field (`hr:employee-classification`,
`hr:a2a-routing`, `legal:knowledge-graph`, `legal:vocabulary`,
`product:product-master-data-contract`); and no block anywhere declares the D21 tier the
normative standard requires at registration.

**The inventory was undercounted twice, and both misses were scope errors.** Scanning
`domains/*/src/schemas/` for a JSON `registry` block found 21 coordinates; widening to
`domains/` found 25 — the five added being both `src/api/` tier projections and the three
capability artifacts above. Widening again to YAML, where the coordinate is spelled
`x-registry`, found 29: three OpenAPI contracts (`product-api`, `marketing-copy-api`,
`stock-api`) and one AsyncAPI document (`product-events`). All four are out of scope, but a
regrowth test that cannot see them is not a regrowth test, so it reads **both** spellings in
**both** formats anywhere under `domains/**`.

**Nothing in this repository links to the governing Confluence standards.** Neither the
Schema Standards (UIF space) nor the ITSOC Eventing standards are referenced from
`stacks/apicurio/standards/`, which is where ADR-0012 put the Schema capability's standards
and where a reader would look. That gap caused this decision's first draft to be built on a
wrong premise. Adding the links is part of the implementation.

## Rejected alternatives

| Alternative | Why rejected |
|---|---|
| Adopt UIFPE-523's `{entity}.gold-{tier}-value` as the standard | It is already on disk for all four tier projections, so adopting it would be free. Rejected because it names only the tier projections and leaves gold and mapping ungoverned; its `-value` suffix is the Kafka shape ADR-0013 §9 explicitly retires; its first slot is the *entity* where every other coordinate in the estate carries the *domain*; and it was inherited from a script argument rather than decided. |
| Extend the kebab form to five kinds (`<entity>-gold-master`, `<entity>-data-<channel>`) | Needs no ARB deviation and is close to what is on disk — the strongest alternative. Rejected because it requires agreeing four new pseudo-action tokens with no vocabulary to draw them from, and the `<action>` slot is semantically wrong for artifacts that are not occurrences. |
| Type in the group (`product-gold`, `product-event`) | Would let one Apicurio rule cover a type per domain. But it breaks the normative `group = <domain>` rule, the one part of the coordinate this ADR does not have to touch. |
| Type as metadata only | Conforms with no deviation at all, but a gold master and a mapping become indistinguishable by subject, so browsing, CI path filters and per-type rules all need a metadata lookup. |
| Type as artifact prefix (`gold.product`) | A sorted listing would cluster by type, but it reads back-to-front against every other UIF name, which puts domain first. |
| `.vN` majors for these types | Consistent with ADR-0013 across both lanes, but a second ARB deviation resting on consistency alone — the routing justification does not apply to artifacts nothing routes on. |
| Co-versioned majors across the five | Mapping pins a versioned gold path and the projections are generated from that master, so they cannot legitimately drift. Rejected as over-coupled: a mapping-only rewrite would force four spurious majors. Replaced with the CI drift guard. |
| Canonical spec file plus codegen for the regex | Mirrors ADR-0014's one-table approach, but a codegen step in two toolchains is heavy for a single pattern. |
| Ratify ADR-0013 first | Cleaner sequencing, but it blocks five ungoverned artifact kinds on an unrelated ratification that must itself be reconciled with the normative standard first. |
