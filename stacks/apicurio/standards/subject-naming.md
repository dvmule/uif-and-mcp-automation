# Subject naming — the five schema types UIF registers per gold entity

**Status:** Normative for UIF. Decision of record: [ADR-0023](../../../architecture-design/decisions/0023-schema-subject-naming.md).
**Scope:** `mapping`, `gold`, and the three tier projections `api`, `event`, `message`.
**Machine-readable:** [`subject-naming.cases.json`](subject-naming.cases.json) — the pattern
plus every case both the BFF and the UI test against.

---

## What a subject is, and why this document exists

A **subject** is the address a contract lives at in the registry: the `group` a consumer
looks in, and the `artifact` they read. Get it wrong and a consumer cannot find the
contract; change it later and every consumer breaks.

Pearson's normative standard names three kinds of artifact — the event envelope, the
per-operation data schema, and the AsyncAPI document (§"What this does not cover"). It does
not name the five kinds UIF registers per gold entity. [ADR-0022](../../../architecture-design/decisions/0022-gold-schema-pipeline-repo-first-registry-next.md)
decision 4 assigned that standard to this stack. This is it.

## The grammar

```
group    = {domain}
artifact = {domain}.{entity}.{type}
```

Exactly three dot-separated segments. **No version segment** — the major is the artifact's
semver version, per the normative rules below.

| Slot | Rule | From |
|---|---|---|
| `domain` | a directory under `domains/`. Never `default`; `uif` is reserved for the envelope | the master's `metadata.domain` |
| `entity` | the gold table name, **kebab-cased** (`marketing_copy` → `marketing-copy`) | the master's `metadata.entity` |
| `type` | one of exactly five: `mapping` · `gold` · `api` · `event` · `message` | which artifact it is |

The domain repeats the group deliberately: a subject is constantly quoted without its group
— lineage references, CI logs, error messages — and it should still say who owns it.

`message`, not `messaging`. That is the live wire value: the `x-channels` tag, ADR-0022's
audience table, and the envelope's `channel` field that subscription rules filter on.

## One example of each

| Type | Subject | What it is |
|---|---|---|
| `mapping` | `product` : `product.product.mapping` | Silver→Gold attribute mapping, the authored rules |
| `gold` | `product` : `product.product.gold` | the gold master — full engineering description |
| `api` | `product` : `product.product.api` | tier projection for request/response callers |
| `event` | `product` : `product.product.event` | tier projection for queue subscribers |
| `message` | `product` : `product.product.message` | tier projection for the stream and export platform |

A projection exists only where attributes are tagged for that audience, so most entities
carry fewer than five. `product.marketing-copy.gold` and `price.price.mapping` show the same
grammar for a second entity and a second domain.

## What comes from the normative standard unchanged

Do not re-decide these here. They are Pearson's, not this stack's:

| Concern | Rule |
|---|---|
| Artifact version | semver `MAJOR.MINOR.PATCH`. **No `.vN` artifact suffix** for these five |
| Compatibility | `BACKWARD_TRANSITIVE` |
| Tier | every artifact declares `T1` / `T2` / `T3` at registration (D21) — but only two of D21's dimensions mean anything here; see below |
| Group | `<domain>` — already what this grammar uses |

Sources: "Event Envelope & Naming Standards" and "Versioning, Registry Workflow & Contract
Testing", both Confluence ITSOC — see [`README.md`](README.md) for links.

### The D21 tier is required here, but most of what it drives does not apply

D21 is written for **event types**, and holds the tier "as APICURIO artifact metadata on the
`data` schema". None of these five is an event type or a `data` schema, and none has a stream.
So the tier is declared — the step-2 lint fails an artifact that declares none — but five of
D21's seven operational dimensions are undefined for it. Do not read a tier on one of these
five as claiming the full matrix:

| D21 dimension | T1 / T2 / T3 | Applies to these five? |
|---|---|---|
| Stream retention | 7d / 3d / 24h | **No** — there is no stream |
| DLQ alerting | page / ticket / dashboard | **No** — there is no DLQ |
| Drift-detector sampling | 100% / 100% / sampled | **No** — there is no stream to copy |
| Per-event statusing | full / full / sampled | **No** — there are no occurrences |
| Kinesis capacity mode | provisioned / on-demand / on-demand | **No** |
| Parallel-run window at MAJOR | 90d / 90d / 30d | **Yes** |
| Generated contract-test depth | full + boundary / full / envelope only | **Yes** |

So for a gold master, a mapping or a tier projection, the tier means the parallel-run window
and the contract-test depth, and nothing else. Whether D21 should reach artifacts with no
stream at all is a question for the normative standard's owners, not something this stack
decides; until they answer, declaring a tier is the conservative reading, because the lint
rejects the alternative.

**All fourteen in-scope coordinates are `T2`, confirmed by the framework owner on 2026-09-24**
— the standard's own default for domain business events. Tier is registry metadata, so
re-tiering is a catalogue operation and needs no schema version bump (D21).

## What this does not cover

Every form below is **rejected** by the pattern, which is what lets an in-scope and an
out-of-scope subject share a group safely.

| Form | Kind | Governed by |
|---|---|---|
| `<entity>-<action>-data` | per-operation event payload | the normative standard |
| `uif` : `pearson-event-envelope` | CloudEvents envelope | the normative standard |
| `<domain>-events-asyncapi` | AsyncAPI document | the normative standard |
| `<domain>-<resource>-api` | OpenAPI/REST contract | [`../../../capabilities/api/guidelines.md`](../../../capabilities/api/guidelines.md) §1 |
| `vocabulary`, `knowledge-graph`, `a2a-routing` | capability artifacts | nothing yet — those capabilities' own decisions |

**`api` means two different things, and both are legitimate.** `product` : `product-api` is
the OpenAPI/REST contract; `product` : `product.product.api` is the api-audience tier
projection. They cannot collide, but know which you are looking at: three dot-separated
segments is this document's grammar, anything else is not.

## How it is enforced

| Where | Role |
|---|---|
| `src/app/services/metadata_validator.py` | **authority** — the only thing that can reject a publish. Checked before the schema-type gate, because a subject's shape does not depend on the body |
| `stacks/echo/src/apps/cli/generate_tier_subsets.py` | writes the coordinate into each generated projection |
| `stacks/echo/src/apps/cli/publish.sh` | registers under it |
| `ui/src/features/publish/…/SchemaPublishForm.tsx` | **advisory** — while-you-type feedback only |
| [`subject-naming.cases.json`](subject-naming.cases.json) | one corpus, loaded by pytest and vitest, so a drifting regex fails in whichever language drifted |

A coordinate is declared in the artifact itself — `registry` in JSON, `x-registry` in YAML —
per [`guidelines.md`](guidelines.md) §1. Both spellings are checked, anywhere under
`domains/**`.

## Changing this

Adding a sixth type, or changing a slot, needs an ADR superseding ADR-0023. The grammar
deviates from the normative `<entity>-<action>-data` by ARB grant; widening the deviation
is a new ARB conversation, not a repo decision.
