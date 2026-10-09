# 0026. A Gold Entity's Read API Is the Shared Serving Copy

- Status: Accepted (2026-10-01)
- Date: 2026-10-01
- Author: reyasahamed.habibrahman@pearson.com
- Deciders: framework owner
- Scope: How a consumer reads **one Gold entity record, synchronously, by an identifier it already holds** — for every Gold domain. Out of scope and unchanged: bulk export, analytical and arbitrary-column query, event and message delivery, and any API a domain serves over something that is not a Gold entity.
- Related: [ADR-0018](0018-gold-runtime-is-a-stack-concern.md) — the foundation-versus-domain test applied again here. [ADR-0022](0022-gold-schema-pipeline-repo-first-registry-next.md) — the projection pipeline this decision's contracts derive from. [ADR-0024](0024-domain-eventing-is-config-not-terraform.md) — the same line drawn for the eventing plane; the reasoning is deliberately parallel.

---

## Context

**A consumer wants one record, now, by the identifier it already holds.** A catalogue page asks for one item; a fulfilment check asks for one position. That is a point read, it happens while somebody waits, and it is the commonest thing anyone asks of a Gold entity.

**Gold cannot answer it.** Gold is columnar files on object storage, queried through a distributed engine. That shape is right for scanning millions of rows and wrong for fetching one: a query costs seconds and scans far more than the row asked for. Answering a point read synchronously therefore needs a copy of the entity in a key-value store, refreshed as Gold changes.

**Two answers to that were built, and one domain ended up carrying both.**

The first was a service per domain. Each domain would own a container that queried the analytical store on request, with its own deployment chart, its own identity, its own image repository and its own promotion pipeline. The domain's API contract described that service.

The second was one shared copy and one shared reader. A single key-value table holds every entity's records; a single retrieval function serves them; an entity joins by adding a line to its pipeline configuration, and no infrastructure changes. Its contract references the projection the schema pipeline already generates from the domain's master.

**The evidence that accumulated while both existed is one-sided.** The per-domain service was scaffolded and never ran:

- its cloud substrate was gated off by default and switched on in no environment, so its identity, image repository and deployment role were never created;
- with the gate open it **could not produce a plan at all** — two composed modules iterate over a value that is unknown until apply — so it could not have been deployed even deliberately;
- its runtime access policy named a storage bucket that does not exist, and so granted nothing;
- its promotion workflow has never run once.

Meanwhile the shared copy serves three entities in the same environment, and a fourth was filled and verified against its analytical source in a single day. Three domains already read through it and document it; the fourth documented the service that was never deployed and left the one that works undocumented.

**So the choice has effectively been made by which one works.** What has been missing is a decision saying so, which is why a domain could still acquire a second implementation by following the older pattern. Three separate open questions have been waiting on it: whether container identity belongs in the Gold data plane's infrastructure state, whether the one-image-per-domain build rule applies to a domain whose workload is serverless, and whether the gated substrate should ever be switched on.

---

## Decision

**A Gold entity's synchronous read API is the shared serving copy. A domain does not build, ship or operate a service of its own to read its Gold entity, and its contract for that read derives from the domain's master schema rather than restating it.**

One entity on the estate does not follow this today. That is recorded below rather than left implicit — see *The one exception on the estate today*.

The line sits here:

| What | Owned by | Created how often |
|---|---|---|
| The shared table, the retrieval function, the business-key index, the synchronisation job, their alarms and their access grants | The stack, in Terraform | Once per estate |
| An entity's opt-in, the key a caller addresses it by, and its API contract | The domain, as configuration and one contract file | Once per entity, as a configuration change |

A domain that wants its entity readable adds a line to that entity's pipeline configuration and a contract beside it. It does not acquire a container image, a deployment chart, a service identity, an image repository or a promotion pipeline.

---

## Mechanics

Only what is not already settled by ADR-0022 for the contract or by the shared copy's own design.

**Opting in is one line.** An entity declares that it is served in its pipeline configuration. The shared table and function already exist, so nothing is provisioned and no deployment changes.

**The read surface follows the entity's key shape**, not a convention chosen per domain. An entity keyed on one business column is read at one path segment; an entity keyed on two is read at two, and its collection — every sub-grain under the first column — is a second, paged operation. Both are bounded: a read never returns an unbounded list.

**The key a caller uses is not necessarily the key the table merges on.** An entity's analytical table may be keyed on identifiers from the source system that no channel publishes, because change detection cannot express the business key on every source. Where the two differ the entity declares both: the merge key stays the table's, and the business key becomes the read surface through a secondary index on the copy. A caller therefore never needs, and never receives, an identifier the platform does not publish.

**One contract per entity, and it references rather than restates.** The response schema is the projection the schema pipeline generates from the master. A hand-authored copy of a generated schema is a restatement, and the two drift silently — which is exactly what happened while this was undecided.

**The promise is the shared function's, not the entity's.** One function serves every entity, so its latency and availability alarms are on the function. Every contract therefore publishes the same service-level figures, and a contract publishing different ones is promising something nothing watches.

---

## Migration

**Three of the four Gold domains already match this decision** and need no change: each reads through the shared copy and each contract already references its generated projection.

**The fourth removes its second implementation.** Because that implementation was never deployed, removal is a code change and not an operational one:

1. Its API contract is rewritten to the read surface the shared copy actually serves, referencing the generated projection.
2. The contract-derivation guard, which named one entity, becomes one assertion over every entity declared as served — so no contract can restate its schema again.
3. The service, its tests, its deployment chart and its gated infrastructure are removed. The infrastructure plan shows no change, because none of it was ever created.
4. Its container image definition becomes a comment-only placeholder, because the shared domain build gate requires the file to exist regardless of whether a domain ships an image. Two domains already carry such a placeholder for the same reason.

Nothing needs to be migrated between stores, and no consumer is affected: the removed service had no deployment and therefore no callers.

---

## The one exception on the estate today, and why it is written down

One domain carries a **second** Gold entity whose read surface predates this decision and does not follow it: it has a copy of its own, a synchronisation job of its own and a reader of its own. It was built before the shared copy existed, and the shared copy was generalised **from** it — so it is the pattern's origin rather than a departure from it, and it simply never moved onto the thing it inspired.

It is written down rather than left implicit, because a decision that states a rule while a live service contradicts it teaches that the rule is optional. Two things had already followed from the silence. A change to the read surface's security had to be implemented, configured, deployed and tested twice. And the duplication that caused was nobody's decision — it was just what the estate happened to contain.

**The intent is migration, not permanence.** Three things might have made the exception necessary. Each was checked, and none does:

| Possible reason to keep it separate | What was found |
|---|---|
| No master schema for the shared engine to derive a read set from | it has one, and every property it publishes is on the API channel |
| A composite key the shared index cannot address | its key is a single column |
| A payload too large for an item in the key-value store | its largest is within the store's per-item limit |

What remains is one real difference: the payload is stored **compressed**, and the shared engine has no notion of a compressed column. That is a storage detail rather than a contract one, so it is a feature the engine gains — not a reason for a second service.

Two costs of folding it in, so the migration is not mistaken for free: the first fill is large enough to want planning rather than a merge, and the shared store's housekeeping sweep would cover roughly three times as many records. Reads are unaffected, because they are addressed per key; what degrades is how precisely a store-level alarm points at a cause.

Until that lands the exception is **declared and bounded**: one entity, in one domain, and no new ones. A domain may not cite it as precedent, because what made it an exception was its date.

---

## Consequences

**What this buys.** Making an entity readable is a configuration change reviewed in a pull request, not an infrastructure exercise. There is one read path to secure, to alarm, to tune and to reason about, rather than one per domain. A contract cannot drift from its master, because a single guard covers every served entity rather than whichever one somebody remembered to name. And a domain team ships no service — no image to build, no chart to maintain, no identity to rotate.

**What it costs, stated plainly.**

- **One function is one failure domain.** A fault in the shared reader affects every entity at once, where per-domain services would have failed independently. That is the trade: a single path is easier to watch and worse to lose.
- **A domain cannot vary its read.** No per-entity latency budget, no per-entity authentication model, no per-entity paging rule. An entity whose needs genuinely differ has no room inside this decision, and would need a new one.
- **The copy is stale by design.** It is refreshed on the entity's own pipeline cadence, so a read can return data measurably older than the analytical store. Each contract publishes the freshness figure its alarm enforces, so the staleness is declared rather than discovered.
- **A first fill is slow and is not free.** Enabling a wide entity means writing every one of its rows into the copy and its index, which for a large entity takes hours and costs real write capacity. That falls on whoever enables it, and it wants planning rather than a merge.
- **Arbitrary column selection and full-table query are not served by this at all.** The removed service could answer them; the shared copy cannot. See below.

---

## What this does not decide

- **Bulk export.** Moving a whole entity to a partner or a downstream platform is a different capability with a different delivery model, and is unaffected.
- **Analytical and arbitrary-column query.** The removed service could take a caller's column list and query the analytical store directly. This decision removes that capability along with the service, and does **not** decide where it should live if a named consumer asks for it. Returning a chosen subset of fields from the shared read is a separate, smaller question.
- **Whether a domain may ship a container at all.** This decides only that it does not do so to read a Gold entity. A domain with a genuinely different workload is not addressed here, and the one-image-per-domain build rule is still unresolved for serverless data-plane domains.
- **The shared function's sizing and the copy's store.** How much compute the reader and the synchronisation job are given, and whether a key-value store remains the right one, are operational questions inside this decision rather than changes to it.
