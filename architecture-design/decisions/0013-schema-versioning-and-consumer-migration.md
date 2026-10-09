# ADR-0013 — Schema versioning, artifact states, and consumer migration in the Apicurio registry

**Status:** Proposed
**Date:** 2026-09-02
**Owners:** Event Fabric platform team
**Consulted:** Schema Registry BFF team (open items listed in §9)
**Context:** Event Fabric eventing layer — Apicurio Registry (fronted by the Schema Registry BFF), Event Fabric Processor (Glue), Subscription Lambda, EventBridge domain buses.

---

## 1. Decision summary

1. **Addressing model — every dimension has exactly one home:**
   environment → registry instance (endpoint config) · domain → `group_id` · entity + operation → `artifact_id` (`{domain}.{entity}.{operation}`, e.g. `product.product.created`) · **major version → `.vN` artifact suffix** (base artifact = major 1, no suffix) · **minor version → inner Apicurio version** within an artifact.
2. **A schema family** = the base artifact plus its `.vN` siblings within one group, matched only with the anchored pattern `^{base}(\.v\d+)?$` — never substring matching, never across groups.
3. **DetailType carries the family major only:** `{domain}.{operation}.v{major}` (e.g. `product.created.v2`). Inner minors never appear in DetailTypes and never affect routing.
4. **`bump=minor`** = new inner version inside the same artifact; requires backward compatibility; invisible to routing and consumers. **`bump=major`** = new sibling artifact derived by the BFF; new DetailType; triggers the consumer-migration lifecycle.
5. **Artifact state drives everything; inner-version state drives nothing.**
   - Subscription Lambda (enrollment): offer only the highest-suffix sibling with `state == ENABLED`.
   - Processor (emission): emit one event per sibling with `state != DISABLED`.
6. **Deprecation is not a delivery cutoff.** DEPRECATED closes new enrollment only; DISABLED (and only DISABLED) stops emission. DISABLED may be applied to a major only after the subscriptions table shows zero ACTIVE subscriptions on its DetailType.

## 2. The two version lanes

### 2.1 Minor — evolution in place (worked example, live in the dev registry)

Registered 2026-09-01: artifact `product/product.product.created`, inner version 1 (fields incl. `ppid`, `description`, `inventoryItemStatus`, `legacyColA/B/C`). On 2026-09-02, published with `bump=minor`: inner version 2 = identical schema **plus optional `data.coverImageThumbnail`**.

Resulting artifact:

| Artifact | Inner versions | Backs DetailType |
|---|---|---|
| `product.product.created` | 1, 2 (both ENABLED) | `product.created.v1` |

Both minors remain stored and individually fetchable (`?version=1` pins the pre-thumbnail document) — they are different documents on the same contract line, where minor 2 accepts every payload minor 1 accepts.

**No operational difference between minors 1 and 2 — example.** Consumer A subscribed to `product.created.v1` before the minor. After the minor: A's EventBridge rule is unchanged (rules match DetailType; the DetailType did not change); the Processor validates new events against the head (minor 2) and stamps the same `detail-type=product.created.v1`; A's payloads now may carry `coverImageThumbnail`, and A's parser is unaffected because the field is optional — the backward-compatibility gate is precisely the guarantee that head-validated events remain parseable by all earlier-minor consumers. No subscription change, no rule change, no migration, no notification. The only observable differences from a minor are: the registry history grows; pinned fetches of older minors remain possible; and (recommended) the in-payload `schema_version` stamp records which minor shaped each event, for diagnostics only.

Corollaries: the Processor always validates against the head minor (a superset of all earlier minors — validating against a stale minor would wrongly reject new payloads); consumers must treat all minor-added fields as optional forever within a major (replayed/redriven events may predate the minor); disabling an *inner* version has no operational effect and is reserved for redaction (it only breaks pinned fetches of that document).

### 2.2 Major — a new stream (worked example, live in the dev registry)

Published 2026-09-01 with `bump=major` against the base artifact. The BFF derives a **new sibling artifact** — it is not a version inside the base:

| Artifact | Inner versions | State (at creation) | Backs DetailType |
|---|---|---|---|
| `product.product.created` | 1, 2 | ENABLED | `product.created.v1` |
| `product.product.created.v2` | 1 | ENABLED | `product.created.v2` |

Properties: the base artifact is untouched by the major (its history, state, and content are frozen as they were); each sibling has an independent inner-minor counter (both contain "version 1", meaning different things); nothing in the registry links the siblings — the family relationship exists only in the naming convention, hence the anchored-regex rule; `latest_version` on an artifact is its latest **minor** and must never be read as the family's latest major.

Breaking changes (field removal, rename, re-type, new required field, semantic change) are the only legitimate reason for a major. The compatibility check (`POST /publish/{g}/{a}/compatibility`) recommends the bump; a configured BACKWARD rule (§10, currently absent) enforces it at publish time.

## 3. Artifact states — ENABLED vs DEPRECATED vs DISABLED

State is read by two systems with deliberately different predicates:

| State | Subscription Lambda (enrollment: `== ENABLED`) | Processor (emission: `!= DISABLED`) | Meaning |
|---|---|---|---|
| **ENABLED** | offered in catalog; valid for new registration and as migration target | emitted | fully live |
| **DEPRECATED** | **not offered; new registrations rejected** | **still emitted** | closed to new subscribers; existing subscribers grandfathered — the migration-window state |
| **DISABLED** | not offered | **not emitted** | end of life — the only delivery cutoff |

Rules of use: nothing flips states automatically (no timer, no cascade — every transition is a deliberate operator action); DEPRECATED costs existing subscribers nothing; DISABLED is applied only after the zero-ACTIVE-subscriptions check (§4 step 6); state applies at **artifact level** for all emission/enrollment decisions — inner-version states are publish history.

## 4. Consumer migration lifecycle — worked example (5 subscribers)

Setup: 5 consumers subscribed to `product.created.v1`. A breaking change (2 columns added, 3 removed) requires a major. 3 consumers can migrate now; 2 need until Oct 31.

1. **Publish the major** (`bump=major`) → sibling `product.product.created.v2` exists, DetailType `product.created.v2` becomes mintable. Nothing changes for any of the 5.
2. **Immediately DEPRECATE the base artifact.** New subscribers can no longer enroll on v1 (the v1 population can only shrink). All 5 existing subscribers continue to receive v1 events — deprecation is invisible to the Processor's emit predicate, to their EventBridge rules, and to their subscription rows.
3. **Dual-publish window.** Every Glue run resolves the family and emits each business event twice: `product.created.v1` (validated against the base head) and `product.created.v2` (validated against the sibling head). Each consumer's rule matches exactly one DetailType — no duplicates. Precondition: the gold source must still supply the fields the v1 shape requires, for the entire window.
4. **The 3 ready consumers migrate** via the Subscription Lambda update flow (`PUT /event-subscriptions/{id}`): validation confirms the target is ENABLED and newer; `PutRule` rewrites their rule's detail-type list; their next event is v2-shaped. Each migrates independently and instantly.
5. **The 2 holdouts do nothing** and keep receiving v1 until Oct 31 — this is the state the model exists to make safe. The commitment must carry a consequence: v1 emission stops Nov 1; unmigrated subscriptions are force-migrated or suspended with notice (never left as live rules on a permanently silent DetailType).
6. **Sunset check, then DISABLE.** Verify zero ACTIVE subscription rows contain `product.created.v1` (DynamoDB query; corroborate with per-rule CloudWatch `Invocations`). Then flip the base artifact to DISABLED.
7. **Emission stops mechanically.** The next Glue run's family resolution drops the base artifact (`state == DISABLED` fails the emit predicate) and emits `product.created.v2` only. No deploy, no config change — the state flip is the cutoff. The registry retains the DISABLED artifact and its full minor history for audit.

## 5. Glue ↔ registry API contract (the Processor's two calls)

The Processor's entire registry surface is **two read-only calls** through the Schema Registry BFF (the native Apicurio API is not reachable through the facade). Both are made **once per run** (`apicurio_fetch_once_per_run: true`) before any row processing; the resulting emit plan is cached and reused for the whole batch.

### 5.1 Call 1 — list artifacts (family resolution input)

```
GET {BFF}/api/v1/schemas/?limit=200&offset=0        # paginate: offset += page size until offset >= total
```

The endpoint has no name/group/state filters, so Glue applies three filters client-side, in order:

1. `group_id == "{domain}"` (e.g. `product`) — families never span groups
2. `artifact_id` matches the anchored family pattern `^{base}(\.v(\d+))?$` (e.g. `^product\.product\.created(\.v(\d+))?$`) — never substring matching
3. `state != "DISABLED"` — the emit predicate (ENABLED and DEPRECATED both emit)

Fields consumed per item: `artifact_id` (family match; **major = suffix digits, absent suffix = major 1**), `group_id`, `state`. Explicitly ignored: `latest_version` — it is the inner *minor* head and must never inform routing or major derivation.

### 5.2 Call 2 — fetch head content (one per surviving sibling)

```
GET {BFF}/api/v1/schemas/{group}/{artifact_id}      # NO ?version param → returns the head minor
```

Fields consumed: `content` (the JSON Schema the Processor validates payloads against — delivered as an escaped string; parse it), `content_version` (inner minor, used only for the in-payload `schema_version` stamp and the run-start log — never for routing).

### 5.3 Worked example — one run in each lifecycle phase

Family `product.product.created`, group `product`, config pattern `apicurio_artifact_pattern: "product.product.{operation}"` (the config knows only base names; majors are discovered, never configured).

**Phase A — before the major.** Listing returns (after filters): `product.product.created` (ENABLED). Detail fetch: 1 call → head minor 2 (includes optional `coverImageThumbnail`). Emit plan for `INSERT`:

```
emit product.created.v1   validate-against product/product.product.created (head minor 2)
```

Registry traffic: 1 listing + 1 detail. Every INSERT row → 1 PutEvents.

**Phase B — after `bump=major` + base DEPRECATED (dual-publish window).** Listing returns: `product.product.created` (DEPRECATED — passes `!= DISABLED`), `product.product.created.v2` (ENABLED). Detail fetches: 2. Emit plan:

```
emit product.created.v1   validate-against product/product.product.created     (DEPRECATED, head minor 2)
emit product.created.v2   validate-against product/product.product.created.v2  (ENABLED,   head minor 1)
```

Registry traffic: 1 listing + 2 details. Every INSERT row → **2 PutEvents** (one per major; each subscriber's rule matches exactly one DetailType — no duplicates). Note: the transition into Phase B required **no Glue change** — the same query found a second family member.

**Phase C — after base artifact DISABLED (sunset complete).** Listing returns after filters: `product.product.created.v2` only (the base fails filter 3). Emit plan:

```
emit product.created.v2   validate-against product/product.product.created.v2 (head minor 1)
```

Every INSERT row → 1 PutEvents. The state flip **is** the cutoff mechanism: emission of v1 stopped because the next run's resolution dropped the base artifact — no deploy, no config change, nothing notified Glue.

### 5.4 Mandatory operational posture

- **Log the resolved emit plan at every run start** (artifact, state, DetailType, head minor) — e.g. `emitting: product.created.v1 [DEPRECATED, minor 2], product.created.v2 [ENABLED, minor 1]` — so any state confusion is answerable from the job log.
- **Fail fast, never emit-nothing-green:** listing unreachable, or any `publish_ops` operation resolving to zero majors, aborts the run loudly. A registry hiccup must never produce a successful job that silently emitted nothing.
- Reference implementation: `glue_emit_planner.py` (`_list_all_artifacts()`, `_fetch_head()`, `build_emit_plan()`).
- Native-API note: Apicurio itself supports server-side group/name filtering (`GET /apis/registry/v2/search/artifacts?group=…`); the BFF does not expose it — re-exposing that filter is open item §10.10 and becomes important as artifact count grows past a few hundred.

## 6. Consequences for each component

- **Processor (Glue):** resolve families once per run from the pipeline config's base-name patterns (config knows base names only; majors are discovered, never configured); emit per sibling `!= DISABLED`, payload validated against that sibling's head; log the resolved emit plan (artifact, state, DetailType) at every run start; fail fast if a publishable operation resolves to zero majors.
- **Subscription Lambda:** catalog and registration validate against highest-suffix ENABLED sibling; migration targets must be ENABLED and newer than current (forward-only).
- **Consumers (published guidance):** code against the head; treat all minor-added fields as optional forever; a required field is by definition a next-major concern.
- **Publishers:** run the compatibility check in CI to compute the bump — never hand-pick it; a `major` outcome requires steward sign-off (it commits the platform to a migration campaign); semantic breaks (meaning changes with identical shape) are majors too and are caught only by human review; publishing a minor to a superseded sibling is a deliberate backport, not routine.
- **Policy:** at most two live (non-DISABLED) majors per family at any time — a new major cannot ship until the oldest line is DISABLED.

## 7. Rejected alternatives

- **Majors as inner Apicurio versions of one artifact** — conflates routing-relevant (major) and routing-invisible (minor) changes in one counter; every additive change would force a new DetailType and a customer migration, or version numbers become meaningless for routing.
- **DetailType carries the minor** (`product.created.v1.2`) — re-routes consumers on compatible changes; defeats the purpose of compatibility.
- **`version`/latest-only emission pinned in pipeline config** — the emit set must derive from registry state; a config pin reintroduces silent starvation of non-latest subscribers.
- **ENABLED-only emit predicate** — makes deprecation a delivery cutoff, starving grandfathered subscribers the moment governance closes enrollment.
- **Environment in `group_id`** — forks contracts per environment; environments are separated by registry instance instead.

## 8. Known failure modes this ADR is designed against

- **Minor/major conflation** (deriving DetailTypes from `latest_version`, or subscribing to a DetailType no major backs) → consumer receives zero events with no error anywhere. Mitigations: family-derived DetailTypes only; subscribe-time validation; per-rule zero-`Invocations` alarms.
- **Deprecation-as-cutoff** (ENABLED-only emission) → holdouts starve mid-window. Mitigation: `!= DISABLED` predicate, stated in §3 and encoded in the Processor.
- **Substring family matching** → foreign artifacts (e.g. `product.product.created-bulk`) absorbed into a family. Mitigation: anchored regex, group-scoped; family-namespace reservation (§10).
- **Silent fork via backport** → a minor published to a superseded sibling diverges from the current major with green checks. Mitigation: backport review rule (§6); BFF warning requested (§10).

## 9. Naming addendum

The `{domain}.{entity}.{operation}` form (no `-value` suffix, no tier qualifiers) passes the BFF's metadata validation as of 2026-09-01 and is canonical for this platform. Legacy `default`-group artifacts using Kafka-subject shapes (`*-value`, tier-qualified) are outside the family convention and are to be migrated or retired; they must never share a group with standard-form families.

## 10. Open items (owned by the Schema Registry BFF team)

1. **Compatibility rule (blocker):** configure BACKWARD at group level (or expose a rule-management endpoint). Until then `compatibility: null` — the minor lane is honor-system and the check endpoint returns assumptions.
2. **State management (blocker):** no BFF route exists to set DEPRECATED/DISABLED at artifact (or version) level; the sunset lifecycle (§4) is not executable through the facade today.
3. Auto-DEPRECATE the superseded sibling on `bump=major` (belt-and-suspenders for §4 step 2).
4. Family-namespace reservation in the metadata validator (reject new artifacts colliding with `{base}.vN` of an existing family).
5. Live-major cap enforcement (§6 policy) at publish time.
6. Backport warning on `bump=minor` targeting a non-highest sibling.
7. `content`-as-object variant on publish/compatibility endpoints (JSON-in-JSON escaping is a recurring integration failure).
8. UI: stop hiding `-value` suffixes (distinct contracts render identically); label inner minors as "version N", reserving `vN` styling for family majors.
9. Auth scheme + read-only scoped access for the Subscription Lambda and Processor.
10. Expose the registry's server-side `group`/`name` filters on the listing endpoint (fetch-everything + client-side filtering does not scale past a few hundred artifacts).
