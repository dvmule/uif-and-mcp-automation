# Gold entity APIs — the read pattern for product, price, program and stock

Marketing copy already has an API (UIFPE-254/255). This document designs the same
capability for the other four gold entities.

**Status: decided. [ADR-0026](../../architecture-design/decisions/0026-gold-entity-read-api-is-the-shared-serving-copy.md)
(2026-10-01) settled it for all four domains — a gold entity's read API is the shared
serving copy, and a domain ships no service of its own to read it.** The per-domain
FastAPI application this document assesses below has been removed; that assessment is
kept because it is the evidence the decision rests on, not because the code still
exists. The surface was agreed with the owner 2026-09-23 and the decisions are recorded
in "Decisions taken" below; the rest of the document is the reasoning behind them and
the facts they rest on.

Read this first if you only read one thing: **the serving architecture was already
decided, and product already has an API.** Neither needed designing. What needed
designing was the resource surface, and that turned out to be constrained by a handful
of facts about the data that are easy to miss.

## What was already settled — do not redesign it

From `domains/product/docs/design-marketing-copy-content-service.md`:

- Gold is Iceberg on S3: an analytical store with **no point lookup**, so a synchronous
  GET needs a serving copy at any traffic level. Athena-per-request is ruled out on
  latency, cost and concurrency.
- **The serving store is DynamoDB**, one shape for every gold API — stated there as
  *"one serving approach for every future gold API (product next)"*.
- **Materialization is change-log driven** — each transform run's change rows name
  exactly the keys to upsert or delete, never a full-table rewrite.
- **Authentication is two hops, and the contract only describes the first.** A consumer
  presents EntraID to the Mule gateway, which owns that check (2026-09-03). The gateway
  then calls the function with a Pearson Identity (IES) token it mints for itself, which
  the backend verifies against the issuer's JWKS and pins to an **allow-listed subject**
  (2026-10-01) — so that subject names the gateway, not an end user. Those tokens carry
  no `aud` and no `scope` claim, so the allow-list is the whole authorization decision on
  that hop. The second hop is verified in the backend rather than assumed, because every
  function url is reachable directly and a check only in front of the gateway would be
  decorative. The gateway-injected shared header is a weaker, static check of the same
  hop, and is still unarmed because its secret has never been created.
- **`ETag` is a hash of the content, never a timestamp.** Silent maintenance repairs
  values *without* moving `_gold_updated_at`, so a timestamp ETag would let a
  revalidating cache 304 past a real change forever. Platform invariant, all entities.

That document anticipates product as the next consumer of the same serving pattern. It
does **not** commit to generalizing the materializer — the only generalization it
discusses is of the delta compiler's SQL *writers*, which it explicitly defers
(*"generalizing the writers is real work that waits for demand"*). Making the
materializer reusable is new scope introduced by this document, not an inherited
decision.

## Product already has an API, and that changes the shape of the work

`domains/product/src/api/` holds a **Product Data API v2.0.0** — contract-first,
Apicurio coordinate `product/product-api`, `compatibility: BACKWARD`,
`application/problem+json` errors, Entra scope `api://product-api/product.read`, a
FastAPI app, and contract tests at `qe/tests/contract/`. It already provides
`GET /products` (cursor-paginated) and `POST /products/query` (column projection, with
an api-eligibility guard).

The contract declares a registry coordinate, but **nothing has been registered** — see
"What actually enforces this" below. Treat v2.0.0 as an authored contract, not a
governed one.

What is wrong with it is not the design:

- it reads gold **via Athena**, which the 2026-09-03 decision ruled out for synchronous reads
- `AthenaProductStore` is **untested** by its own docstring (*"connectivity is deferred,
  so this class is untested"*); the default store is in-memory sample data
- its cursor is a literal offset — `ORDER BY ppid LIMIT n OFFSET m`, with the cursor being
  the integer offset. At ~2.14M rows deep-OFFSET is slow, and it skips or repeats rows
  when writes land between pages.

**Its infrastructure is worse than undeployed — it cannot be applied.** `main.tf` records
this in its own header:

- `enable_api_substrate` is absent from every `envs/*.tfvars` and defaults to `false`.
- With it set to `true`, **the plan fails**: `Invalid for_each argument` ×2, because
  `platform/terraform/modules/{irsa,github-oidc-deploy-role}` iterate `policy_arns` with
  `for_each` over an ARN unknown at plan time. Fixing that is a design choice inside those
  modules. So the substrate *"could not have been applied in any case."*
- Its runtime policy grants read on `uif-product-gold-${env}`, while this domain's actual
  gold bucket is `uif-echo-{env}-gold` — one of the two names is wrong, and a policy
  naming a non-existent bucket grants nothing.
- It serves `domain-cd.yml`, which is not present in `.github/workflows/`.
- It places container IAM, an ECR repo and a CD role in the **gold data plane's** Terraform
  state (`echo-gold/{env}`), which is applied by the gold pipeline. Whether the API
  substrate belongs in that state is UIFPE-116 open decision 1.

So product is a **port** onto the settled serving store, keeping the contract, the error
shape, the scope and the tests — but its Terraform is a rewrite, not a reuse, and the two
`platform/` module defects are a prerequisite, not a detail. The other three are new
builds following it.

## The four entities, as measured

The api-tier surface is the generated `api` channel projection of each gold master.
Counted three ways, because they answer different questions: top-level properties size
the response envelope, property paths at all depths size the `?fields=` allow-list, and
distinct names are what the naming checker judges.

| Entity | Gold primary key | Top-level | Paths (all depths) | Leaves | Distinct names | Checker |
|---|---|---|---|---|---|---|
| product | `inventoryItemId` + `organizationId` | 60 | 129 | 119 | 125 | **36 errors**, 109 warnings |
| price | `productId` | 3 | 17 | 16 | 17 | 1 error, 16 warnings |
| program | `inventoryItemId` + `organizationId` | 11 (of 13) | 13 | 12 | 13 | 5 errors, 15 warnings |
| stock | `productId` + `organizationCode` | 6 | 6 | 6 | 6 | **0 errors**, 5 warnings |

Checker figures are `check_name.py` over every API-visible property name at every depth —
containers included, because a container name (`priceSpecifications`, `contributors`) is a
JSON property a consumer sees. Judging top-level names only understates product by
20 errors.

**price's 3 top-level properties are misleading.** `priceSpecifications` is an array whose
elements carry 14 fields, so the real filterable surface is 17 names, not 3. That matters
for `?fields=` and for projection.

All four run `gold-24h` — a daily pipeline. One platform prerequisite: **UIFPE-523**,
consolidating the two generators and emitting each tier to its owning capability folder.
**UIFPE-524** is also a prerequisite, rescoped: make product's contract `$ref` that
projection instead of mirroring the mapping.

## Facts that shaped the design

**1. Only stock's gold key is exposable.** product and program are keyed on
`inventoryItemId` + `organizationId` — Oracle source-system surrogates carrying an
explicit `x-channels: []`, so they appear in no channel. That is correct: rule **N7**
forbids a source-system surrogate key on an API, and the generator strips them everywhere
along with `itemCatalogGroupId`, `contributors[].contributorId` and
`categories[].categoryId`.

| Entity | Gold key exposable on the API? |
|---|---|
| stock | **yes** — `productId` + `organizationCode`, both in the api tier |
| price | yes — `productId`, and price has no organization grain |
| product | **no** — must key on `productId` + `organizationCode` instead |
| program | **no** — and it has neither `organizationCode` nor `productId` in its api tier |

So for product and program the API must key on a business key that is *not* the gold key,
and **nothing in the repo proves that business key is unique at the gold grain.** Each
ticket carries the query as a blocking prerequisite.

Program is sharpest. Its api tier is `programId`, `parentProductId`, `relatedProgramId`
and ten attributes — no `organizationCode`, so a single column would stand in for a
two-column grain, and no `productId`, so it cannot be joined to product by product
identifier on the API surface at all. That is why program goes last.

**2. The naming debt lands on the API surface.** One logical name renders per layer, so a
gold master's name *becomes* the API property name.

| Entity | Errors | Breakdown |
|---|---|---|
| product | 36 | 24 × N2 no class word · **11 × N4 numbered siblings** · 1 × N3 (`fulfillmentOrgDetails` abbreviation) |
| program | 5 | N2, including `associatedProducts` and `relatedPrograms` |
| price | 1 | N2 on `priceSpecifications` |
| stock | 0 | clean |

Plus N5 across every entity (no `*IdType` sibling for any identifier) and N1 nearly
everywhere — only **33 of product's 125** api-visible names are registered, against a
47-element seed vocabulary.

**There are five numbered-sibling groups, not two**, and the vocabulary already prescribes
the fix for the first two:

| Group | Prescribed fix |
|---|---|
| `contributors.affiliation1/2/3` | `contributorAffiliations` — registered, with `replaces` naming all six |
| `contributors.affiliationType1/2/3` | same entry |
| `enablingTechnology.enablingTechnologyLevel1/2` | not yet prescribed |
| `languageLevel1/2` | not yet prescribed |
| `learnerStageLevel1/2` | not yet prescribed |

The `contributorAffiliations` entry also states the target shape — *"an array of objects
carrying `affiliationName`, `affiliationTypeCode` and `affiliationSortSequence`"* — so
that correction is a vocabulary decision already taken, not a new proposal. Six of
product's eleven N4 errors are the individual `affiliation*` names being flagged against
it.

Route taken: **register exemptions for the established N2 names, fix the numbered-sibling
groups.** A numbered sibling is a shape bug, not a naming preference, and it is cheapest
to correct before consumers depend on it. Fixing every N2 name wholesale would be a
breaking change to live gold *and* to the event contracts that share the names.

Registration is a **Confluence action, not a repo edit** — there is no
`canonical-vocabulary.json` in this repository, so the checker resolves against the
skill's 47-element seed snapshot. Any ticket that says "register X" means the Schema
Standards page.

One N4 *warning* to dismiss on the record: `unitWeightQuantity` and `unitHeightQuantity`
differ by one character, which the checker reads as one concept spelled two ways. They
are different measurements.

**3. Product's api tier carries PII and confidential data.** Eight api-visible names are
classified `confidential` and six are marked PII in the vocabulary:

| Names | Classification |
|---|---|
| `contributors`, `contributorGivenName`, `contributorFamilyName`, `contributorAdditionalName`, `contributorPreNominalTitle`, `contributorPostNominalTitle` | confidential · PII quasi-identifier |
| `discountCode`, `sellingRestrictionCode` | confidential |

A single `product.read` scope over a payload containing contributor names is a security
posture decision, not an implementation detail. Options are a separate scope for the
contributor block, field-level redaction by scope, or accepting one scope and recording
why. **This needs an owner decision before product's contract is written** — it is the
one open question in this document.

price, program and stock have no PII or confidential names in their api tiers.

**4. stock is the right pilot.** Zero naming errors, six properties, and — the stronger
reason — the only entity whose gold primary key is exposable, so it proves the
materializer and serving-table pattern without also needing the uniqueness prerequisite
resolved. product then inherits a proven path.

## Decisions taken (owner, 2026-09-23)

**Two resources, not one.** A canonical singular resource for the single product, and a
*filtered* plural collection for lookups that can legitimately return more than one:

```
GET  /product/{productId}                                    canonical single product
GET  /product/{productId}/organization/{organizationCode}    narrowed to one org
GET  /products?isbn13=|sourceItemNumber=|organizationCode=   filtered, cursor-paginated
POST /products/query                                         column projection over a page
```

This resolves the path-segment rule cleanly rather than by exemption: **plural for a
collection resource, singular where the resource only ever resolves to one item.** Both
the schema rules and the shipped `/marketing-copy/{contentId}` are then consistent, and
the rule should be amended to say so.

Applied across all four, so the tickets and this document cannot drift:

| Entity | Paths | Ticket |
|---|---|---|
| product | `GET /product/{productId}` · `GET /product/{productId}/organization/{organizationCode}` · `GET /products?…` · `POST /products/query` | UIFPE-519 |
| stock | `GET /stock/{productId}` · `GET /stock/{productId}/organization/{organizationCode}` | UIFPE-518 |
| price | `GET /price/{productId}` | UIFPE-520 |
| program | `GET /program/{programId}` — pending the uniqueness query | UIFPE-521 |

Only product gets a collection endpoint, because only product has a non-unique lookup
(`sourceItemNumber`) and an existing `/products` contract to keep. The other three are
point lookups until a named consumer brings a filter.

Keeping `/products` means the *routing* change is additive — new paths plus new query
parameters break no existing caller.

**The version is still v3.0.0, not v2.1.0.** The surface is additive, but the port also
replaces the hand-authored flat `Product` schema with the generated nested projection,
and that changes the response body shape. Flat dotted keys becoming a nested object is a
breaking change, so the additive-routing argument does not carry the whole release. This
reverses the v2.1.0 position taken earlier in this document; the shape mismatch was found
after it. The cost of the break is nil — the API has never been deployed and has no
consumers — and taking it now is much cheaper than carrying two shapes. See "The
eligibility sources agree. The *shapes* do not." below.

`/products` is a **filtered** collection, not a bulk exporter. Paginating ~2.14M rows
through a synchronous API is the wrong mechanism; the event stream and Athena already
exist for that, and the contract should say so.

**Exact match only** on `productId`, `isbn13` and `sourceItemNumber`. No partial or fuzzy
matching — that would need OpenSearch, which is a different cost and operational burden.
`productId` and `isbn13` are unique; **`sourceItemNumber` is not** (rare), which is
exactly why that lookup lives on `/products`, where a list response represents the truth
natively instead of needing a 409 or a 300.

**Read model:** base table keyed `(businessKey, organizationCode)`, so `GetItem` serves
one organization and `Query` on the partition serves all of them — **no GSI** for either.
Two `KEYS_ONLY` GSIs on `isbn13` and `sourceItemNumber` keep the alternate-identifier
lookups near-free: the index carries keys only, then `GetItem` on the base table. Cursors
are DynamoDB `LastEvaluatedKey`, which replaces the OFFSET problem rather than porting it.

**Every collection read is one page, and the cursor is opaque at the boundary** — added
2026-09-27, implementing rather than revising the above. Two parts, and the second is the
one that is easy to skip:

- **One request is one `Query` with a `Limit`**, never a loop that drains the partition.
  `guidelines.md` is unconditional — *never return an unbounded list* — and a shared engine
  cannot make that conditional on which entity is asking. The envelope is the one product's
  contract already declares: `{items, nextCursor}`, `?pageSize=` 1..100 default 50,
  `?cursor=`. `nextCursor` is **required and explicitly null** on the last page, per N12.
- **`LastEvaluatedKey` never reaches a consumer.** It is base64url-encoded, so nothing in
  the contract names the store. That is what makes a second store a two-method change
  instead of a breaking API change — the point of the DynamoDB-or-document-store decision
  below. The token is untrusted input on the way back in: strict decoding, a key-shape
  check, and **the partition checked against the path**, so a cursor issued for one
  collection cannot be replayed against another. Without that last check DynamoDB answers
  a caller's error with a `ValidationException`, which is our 5xx and an SLA event.

A page can hold **fewer items than `pageSize` and still have a cursor**, because DynamoDB
also stops at 1 MB per page. Nothing tops a page up — a consumer follows `nextCursor` and
never an item count. Each contract has to say so in those words, or consumers will
silently truncate on a short page.

Two honest notes on what this buys today. **stock's partition is bounded by grain** —
measured at 9 sub-grains at most across 87,157 partitions — so for stock the bound is slack
and the cursor is never issued. It is load-bearing for an entity whose partition grows with
history. And **the four pagination names fail N2** (`items`, `nextCursor`, `pageSize`,
`cursor` carry no class word). They are protocol fields rather than business elements, and
they match product's contract exactly, so the route is the one this document already takes
for established names: **register an exemption**, not rename. Renaming would produce
`nextCursorText` and diverge the two contracts.

**Attribute filtering** via `?fields=` on the single reads (dot notation, over 129 property
paths for product) and the existing `POST /products/query` for the collection. Both
validate against the generated api projection, which *is* the allow-list — that is what
stops a consumer requesting `inventoryItemId` and the API leaking a surrogate key. Honest
note: DynamoDB bills on the full item size **read**, not what is returned, so `?fields=`
saves the consumer's bandwidth and parse time, not our read cost.

**Composite reads** via opt-in `?include=price,stock`, composed in the product Lambda —
parallel `GetItem` against three read models in the same region, one hop of single-digit
milliseconds. No composite is materialised: a three-way join would go stale in three
different ways and need three change-log triggers.

The response shape mirrors the real grain, so nothing is repeated that does not vary:

```json
{ "productId": "…",
  "price": { "…": "…" },
  "organizations": [
    { "organizationCode": "82", "…": "…", "stock": { "…": "…" } }
  ] }
```

This is confirmed by the api tiers, not assumed: **price's api tier has no
`organizationCode`**, so price sits outside the organizations array — repeating it per
organization would imply org-specific pricing that does not exist. **stock's api tier has
`organizationCode`** and is keyed on it, so stock sits inside each organization, exactly
as the product attributes are. program cannot be composited this way at all: its api tier
carries no `productId`.

**Partial failure populates `null`.** A price or stock outage must not break a product
read. Stated in the contract, not left emergent.

A missing price is also a normal steady state, not only an outage: **256,526 of price's
1,492,904 rows carry no price in domain.** Those return an empty `priceSpecifications`
array, per rule N12 — `[]`, never `null` and never a 404.

**Caching:** a composite's `Cache-Control` is the **minimum** of the included parts and
its `ETag` covers every included part. Product refreshes 06:30 UTC, stock 08:15, price
08:45 — caching a composite on product's TTL would serve a stale price for two hours.

**`productId` everywhere from gold onward — owner decision, 2026-09-23.** The product
identifier is `productId` on the gold layer and on every surface downstream of it: gold
columns, events, messages, APIs, MCP tools and documentation. `ppid` is not an alternative
spelling to be tolerated; it is wrong from gold onward.

**`PPID` stays on the silver side**, because it is the name of the actual Oracle source
column. A mapping's `drivingColumn`, `source.column` and `drivingFilter`, and the
`SELECT` / `WHERE` / `GROUP BY` clauses that read the source table, all keep `PPID` — you
cannot rename another system's column. The boundary is the `AS` in the mapping SQL, and
it is already drawn correctly: `stock.sql` reads `PPID AS productId`.

The decision is also the cheap direction. Every generated channel schema already declares
`required: ["productId"]`, and **no gold master has a `ppid` property at all** — so `ppid`
would mean changing the generator and every event contract. (This reverses an earlier
recommendation in this document, made before the generator was found.)

The consequence is that **the canonical vocabulary is wrong**: it registers `ppid` and not
`productId`. Registering `productId` and retiring `ppid` is a Confluence action and a
prerequisite for these contracts, not an optional tidy-up.

**`productId` is the logical name; `product_id` is its physical rendering.** Schema
Standards N8 renders a gold column in snake_case, and `mapping.py`'s `physical()` does
exactly that: `productId` → `product_id`. So a gold Athena column, an MCP parameter and an
MCP result correctly read `product_id`, while the master, the event `data` block, the
message payload and the API body read `productId`. **A sweep must not rewrite `product_id`
to `productId` in physical contexts** — that would break every Athena query. The rule is
that the identifier is `productId`, not that the string `product_id` is wrong.

**Two code sites still read a gold column literally named `ppid`, which does not exist** —
see "Where `ppid` still leaks past gold" below.

**Schema authoring follows the event module.** The api-tier payload schema is
**generated, never hand-authored** — the gold master projected onto the `api` channel,
filtered recursively through nested objects and array items. Each REST contract `$ref`s
that projection, so the pull API and the webhook payload cannot drift: they are the same
projection by construction.

**That is the target, not the present state.** Today the Product Data API's `Product`
schema is hand-authored and **flat**, and it does not match the generated payload — see
below. Making product's contract `$ref` the projection is the substance of the port, not
a formality.

**One generator, one contract source** (UIFPE-523, UIFPE-524). Today there are **two**
generators, and the contract test reads neither of them — see below.

## The generator situation, stated accurately

Two scripts project a gold master onto channel/tier subsets:

| Script | Writes | Notes |
|---|---|---|
| `stacks/event/scripts/generate_channel_schemas.py` | `domains/{domain}/src/schemas/channels/{entity}/{entity}-data-{channel}-{operation}.json` | the committed artifacts came from here |
| `stacks/echo/src/apps/cli/generate_tier_subsets.py` | `{out-dir}/{entity}/{tier}/{version}/schema.json` | takes `--master/--entity/--out-dir`; has its own test suite |

**Verified: they produce the same api projection.** Run head-to-head on product's master,
both give 60 top-level properties and 119 leaves, with identical leaf sets. So this is
duplication, not an active disagreement — and the committed artifacts are not wrong.

They differ in exactly one rule, and it is **latent**:

- a property with **no** `x-channels` key at top level — `generate_channel_schemas.py`
  keeps it on *all three* channels; `generate_tier_subsets.py` defaults it to `api` only.
- nested untagged fields inherit the parent in both, so nested behaviour agrees.
- an explicit `x-channels: []` is excluded by both.

No master currently has an untagged top-level property, which is why the outputs match.
There are 16 untagged *nested* properties across the masters (13 under
`price.priceSpecifications`, 3 under `product.fulfillmentOrgDetails.marketingInitiative`),
and those inherit identically. **The first untagged top-level property added to any master
would silently leak into the event payload under one generator and not the other.**
Consolidating before that happens is UIFPE-523's real content.

`generate_channel_schemas.py` is also **not** fully domain-parameterised. `DOMAIN` drives
the master path, output path, `$id` and registry artifact, but four `product` literals are
hardcoded into the *generated output* — `$comment` (line 73), `title` (81), `description`
(82) and `metadata.source` (92) — so another domain's generated schema would claim it was
derived from product's master. It also assumes `entity == domain`
(`gold/{DOMAIN}/{DOMAIN}/v1/master.json`), which does not hold for `marketing_copy`.

Moving the artifacts is safe: **no executable code reads them.** The only references are
the generator itself, two READMEs, and the JSON files' own `$id`/`artifact` fields.
`contract-governance.yml` triggers on both `domains/*/src/schemas/**` and
`domains/*/src/api/**`, so relocating the `api` projection to each domain's `src/api/`
keeps it in the gate's path filter.

**Recommendation for UIFPE-523:**

> keep `generate_tier_subsets.py` (it is already `--master/--entity/--out-dir`
> parameterised and has a test suite), delete `generate_channel_schemas.py`, decide the
> untagged-top-level rule explicitly and test it, and emit each tier to the owning
> capability folder. **Where that is, is now a stated rule** — see
> [ADR-0022](../../architecture-design/decisions/0022-gold-schema-pipeline-repo-first-registry-next.md)
> decision 4, which is the single definition; this page deliberately does not restate it.

Choosing `generate_tier_subsets.py` also fixes the four hardcoded `product` literals and
the `entity == domain` assumption for free, since it takes both as arguments.

UIFPE-524 keeps its own scope, but not the one it was raised with — see the next section.

## The eligibility sources agree. The *shapes* do not.

UIFPE-524 was raised on the belief that the gold master's `x-channels` and the mapping's
`channels.api` disagree about one field. **They do not disagree at all.** Measured at
matching granularity:

| Set | Count | Composition |
|---|---|---|
| `product.mapping.json` where `channels.api == true` | 71 | 59 scalars + **12 dotted** `fulfillmentOrgDetails.*` members |
| generated api projection, top level | 60 | 59 scalars + **1 container** `fulfillmentOrgDetails` |

The container's 12 children are exactly the 12 dotted member names — verified as a set
comparison, not by eye. `fulfillmentOrgDetails.marketingInitiative` is present in **both**,
and is already tagged `["event","message","api"]` in the master. Nothing needs tagging and
nothing needs regenerating.

**The real drift is the response shape.** The OpenAPI `Product` schema is hand-authored
and flat: 12 of its 71 properties are **literal dotted keys** such as
`fulfillmentOrgDetails.discountCode`, typed `["string","null"]`. The generated payload
nests them. So for the same data today:

```json
// GET /products — what the OpenAPI contract declares
{ "fulfillmentOrgDetails.discountCode": "…" }

// webhook payload — what the generated api projection declares
{ "fulfillmentOrgDetails": { "discountCode": "…" } }
```

Two different JSON documents for one entity. Three consequences:

1. The "push and pull cannot drift" property does not hold yet. Product's port must make
   the contract `$ref` the projection, which **changes the response shape** — flat dotted
   keys become a nested object. That is a **breaking** change, and it is why the port is
   v3.0.0. Since the API has never been deployed there are no consumers, so take the break
   now and say so in the ticket rather than shipping the flat shape forward.
2. `?fields=` dot notation collides with the dotted property names: `?fields=` on
   `fulfillmentOrgDetails.discountCode` is ambiguous between a flat key and a path. Nesting
   the shape removes the ambiguity; keeping it flat means the allow-list and the path
   syntax fight.
3. The contract test asserts against the **mapping**, not the generated projection —
   *"the contract exposes exactly the `channels.api` columns of `product.mapping.json` —
   no more, no fewer"*. It passes today (71 == 71) precisely **because** both sides are
   flat and dotted. Re-pointing it at the projection is therefore not a no-op refactor:
   it is what forces the shape fix.

**One more defect in that test.** `test_list_products_never_leaks_ineligible_columns`
asserts `"organizationid" not in item` — lowercase `i`, while the column is
`organizationId`. The assertion can never fail, so the N7 leak guard really only covers
`inventoryItemId`. Fix the spelling and add the other three stripped surrogates
(`itemCatalogGroupId`, `contributors[].contributorId`, `categories[].categoryId`).

**So UIFPE-524 becomes the contract-truth ticket:** nest the `Product` schema onto the
generated projection, re-point the contract test at that projection, fix the vacuous
assertion, and write down the platform rule — *a REST contract `$ref`s the generated
projection; no consumer-facing artifact re-derives eligibility from a mapping.* That is
separate from UIFPE-523, which consolidates the generators, and it is the piece that turns
"push and pull cannot drift" from an aspiration into a tested property.

## Where `ppid` still leaks past gold

**No gold table has a column named `ppid`.** The logical identifier is `productId` and its
physical rendering is `product_id`. The beacon team reached this conclusion independently
on **2026-09-22** and recorded it in code:

> *the PPID is physically `product_id` — no Gold table has a column named `ppid`
> (owner decision 2026-09-22); `parent_product_id` is not the PPID*

That sweep fixed `rowFilter.js` (which now matches `^product_id$` as well as `^ppid$`) and
widened `preview.py`'s `IDENTIFIER_COLUMN_RE` to cover `product_id`,
`parent_product_id`, `inventory_item_id`, `source_item_number`, `program_id` and
`content_id`. Both are correct today. `SAMPLE_IDENTIFIER_RE` deliberately keeps the older,
narrower list, documented in place and pinned by UIFPE-490's assertions — that is a choice,
not an oversight.

**Two sites were missed:**

| Site | What it does | Consequence |
|---|---|---|
| `domains/product/src/api/app/store.py:203` | `ORDER BY ppid LIMIT n OFFSET m` in Athena SQL against gold | the query names a column that does not exist. Latent only because `AthenaProductStore` is untested and the substrate cannot be applied — it would fail on first real use. |
| `stacks/beacon/ui/src/lib/feedSources.js:107` | `shared.includes("ppid") ? "ppid" : shared[0]` — prefer `ppid` as *"the gold layer's cross-table key"* | the intended smart default is unreachable, so the join auto-prefill silently falls back to the first shared column, which may be the wrong join key. Comments at `:101` and `:159` carry the same stale assumption. |

`feedSources.js` is the sharper of the two, because it sits in the **same stack and the
same decision** as the two files that were corrected — `knownColumns()` returns the live
gold columns, which `preview.py` itself treats as snake_case, so `"ppid"` can never match.
One file in a three-file sweep was left behind.

Neither is a naming-tidiness item. Both are code reading a column that is not there.

## What actually enforces this

Do not lean on governance that is not running.

| Step | Status |
|---|---|
| `contract-governance.yml` → `lint` | **runs** on any touched contract, ungated |
| `contract-governance.yml` → `compatibility-check` | **no-op green** — the check step is `if: vars.APICURIO_REGISTRY_URL != ''`, and the workflow's own header says the variable *"is not provisioned in this repo yet"* |
| `contract-governance.yml` → `register-and-sync` | needs the registry URL **and** `SCHEMA_GOVERNANCE_DEPLOY_ENABLED == 'true'` |
| Apicurio registration | **nothing has ever been registered** |
| Required status checks | none configured on this repository |

Consequence for this design: **no gate will catch a breaking change to any of these
contracts today.** For product that happens not to matter — the port is knowingly
breaking, at v3.0.0, against zero consumers. It matters for everything after: once these
APIs have consumers, compatibility rests entirely on review. Provisioning the registry is
the honest prerequisite for the second release, not the first.

## What a stock API serves, stated honestly

stock runs `gold-24h`. An on-hand quantity read from this API is **up to 24 hours stale**,
and `availableToPromiseIndicator` with it. Fine for a catalog display, wrong for an
order-commitment check. Either the contract says so plainly and the cadence is raised
where a consumer needs it, or an availability check goes to the system of record.

## The SLO — measured 2026-09-28, and the numbers were not what was predicted

`guidelines.md` requires an SLO before shipping. Here it is, with what enforces each line,
because an objective nothing watches is not an objective:

| | Budget | Enforced by |
|---|---|---|
| latency p50 | **20 ms** | `uif-echo-{env}-gold-serving-api-slow-warm` |
| latency p99 | **1,300 ms** | `uif-echo-{env}-gold-serving-api-slo-breached` |
| availability | **99.9%** monthly | the retrieval function's `Errors` and `Throttles` alarms |
| freshness | **36 hours** | the per-entity `FreshnessBreached` alarm |

Every figure is a measurement, not an aspiration. 300 samples per path against dev:

```
warm p50   5.2 ms      warm p99   12.4 ms
cold p50   1,091 ms    cold max   1,211 ms
```

**The section below claimed "reads are single-digit milliseconds" and that was true of
DynamoDB and false of the API.** The first measurement found a warm median of **317 ms**,
because the handler built three boto3 clients per request and re-read the pipeline config
and the entity's master schema on every request — the read itself was 5 ms of it. Two
shipped paths proved the split without instrumentation, since they share all the overhead
and differ only in the read. Caching those, and raising memory from 256 MB to 1024 MB
because the cost was CPU-bound, took warm p50 to 5.2 ms and made it **13.5x cheaper** per
million requests as well as 61x faster. The lesson is cheap to state and was not cheap to
find: **measure the API, not the datastore.**

**Cold starts are accepted, by owner decision.** They are the entire tail — 1,091 ms
against 5 ms warm — and that is why the p99 budget is 1,300 ms rather than 50. Provisioned
concurrency would fix it and is a standing charge whether requests arrive or not, which
would dominate the bill at today's traffic of none. A consumer needing a tight tail should
say so; the fix exists and costs money.

**The freshness figure is 1.5x the daily cadence, which is what the alarm already fires
on** — not a number chosen for the contract. Publishing a tighter one would be an
objective nothing enforces. Consequence worth stating to consumers: an on-hand quantity can
be a day and a half old.

**These bound the backend only.** The Mule gateway adds its own share, unmeasured because
it does not exist yet, so a consumer-facing **SLA** must add to these rather than quote
them.

## Cost and performance

Reads are single-digit milliseconds **at the datastore** — see the SLO above for what the
API costs around them. Per the marketing-copy measurement, 100 req/s against
a DynamoDB serving table costs a few tens of dollars a month before gateway caching, and
these four entities carry a few KB per row — no compression needed. The one-time fill is
one write per row: product ≈2.14M, price ≈1.49M, stock and program smaller; single-digit
dollars each on-demand.

The recurring cost driver is not reads, it is **capacity for the daily materialization
burst** — a full run can touch every row. On-demand absorbs it; if it ever moves to
provisioned, the burst is what sizes it.

## Verification

Per entity, before its contract is written:

- **The uniqueness prerequisite** — prove the exposable key identifies one gold row.
  Required for product and program, whose gold keys are not exposable; not needed for
  stock or price, whose keys are:
  ```sql
  SELECT productId, organizationCode, COUNT(*) c
  FROM   echo_dev_gold_product.product
  GROUP  BY productId, organizationCode
  HAVING COUNT(*) > 1
  ```
  Any row returned means the design does not hold on that key. Run the program equivalent
  on `programId` alone.
- `check_name.py` over **every API-visible name at every depth**, containers included.
  A top-level-only set understates product by 20 errors. Every error resolved, every `N1`
  recorded as a Confluence registration task. Run a known-bad control first — the checker
  is right, but a harness that feeds it CRLF-terminated input produces false N3 failures.
- Contract lints clean under `contract-governance.yml`. Compatibility **cannot** be
  verified until `APICURIO_REGISTRY_URL` is set; while it is unset, each ticket records
  that its compatibility claim rests on review.
- For product specifically: the `Product` schema `$ref`s the generated api projection, and
  **no property name contains a dot**. Assert that — it is the check that proves the flat
  shape is gone and that push and pull are one document.
- The N7 guard tested with a known-bad control: `?fields=inventoryItemId` is rejected
  with a 400 naming the field.
- `?include=price,stock` with price deliberately absent returns 200 and `"price": null`;
  a product with no price in domain returns `"priceSpecifications": []`, not null.
- A composite response's `Cache-Control` equals the minimum of the included parts.
- First-load counts against the measured bars:

  | Entity | Bar | Source |
  |---|---|---|
  | price | **1,492,904** rows at `productId` grain · **12,267,949** elements · 126/52/8.2 per product (max/p99/mean) · 256,526 rows with no price | measured over dev Silver 2026-09-18 |
  | product | **~2.14M** rows, of a previous 6.83M dev rows | gold README; not recorded to the row |

  price's older **1,352,350** bar is superseded but is *what the live table currently
  holds* — verify a fresh load against 1,492,904, and expect the live table to read low
  until it is rebuilt.
- `GetItem`, `Query` and GSI latency measured, not assumed.
- The deny path: a request that bypasses the gateway is refused by the shared-header check.

## Sequencing

```
UIFPE-523  one generator, relocated
   │
   ├─► UIFPE-518  stock — the pilot: the only entity with an exposable gold key,
   │                     and it carries the materializer + serving-table module
   │        │
   │        ├─► UIFPE-519  product — the port
   │        │        ▲
   │        │        └── UIFPE-524  contract $refs the projection, shape nested
   │        │                       (product-specific work; sets the platform rule)
   │        │
   │        └─► UIFPE-521  program — last: no organizationCode and no productId
   │                                 on its api tier
   │
   └─► UIFPE-520  price — independent of product; no organization grain
```

stock goes first: it is the only entity whose gold primary key is fully exposable, so it
proves the materializer without also needing the uniqueness prerequisite answered. product
follows as the port. price is independent and can run in parallel. Program goes last,
because its key question is the one three simpler entities will not have answered.

## Open questions for the owner

1. **Contributor PII on the product API** — one `product.read` scope, a separate scope for
   the contributor block, or scope-driven field redaction? Blocks product's contract.
2. **Registry provisioning** — is `APICURIO_REGISTRY_URL` set before these contracts get
   consumers, or is compatibility asserted by review for now? Product's own port does not
   need it; the release after it does.
3. **Confirm v3.0.0 for the product port.** The response shape has to change for push and
   pull to be one document, and no consumer exists to break. Recommended, but it is a
   version decision and should be explicit.
4. **Where the API substrate's Terraform lives** — its own state, or the gold data plane's
   as today? This is UIFPE-116 open decision 1 and the API is the second workload to hit
   it. Answering it is cheaper than porting twice.

## Prerequisites outside these tickets

| Blocker | Why it blocks |
|---|---|
| `for_each` over unknown ARNs in `platform/terraform/modules/{irsa,github-oidc-deploy-role}` | the API substrate cannot reach a clean plan in any environment |
| `uif-product-gold-${env}` vs `uif-echo-{env}-gold` in product's runtime policy | the serving Lambda's gold read grant names a bucket that does not exist |
| `APICURIO_REGISTRY_URL` unset | no compatibility enforcement for any contract change |
| Uniqueness of `productId` + `organizationCode`, and of `programId` | the whole keying decision for two of four entities |
| Vocabulary registers `ppid`, not `productId` | every one of these contracts names `productId`, so N1 fails platform-wide until `productId` is registered and `ppid` retired (Confluence) |
| Two code sites read a gold `ppid` column that does not exist | breaks product's Athena cursor, and leaves beacon's join auto-prefill falling back to the wrong column |
