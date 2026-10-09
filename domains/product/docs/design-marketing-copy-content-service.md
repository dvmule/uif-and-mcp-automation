# Marketing copy as a content service — gold library + on-demand retrieval

Product rows carry a `marketingCopy` list that tells a consumer *which* copy
exists for a product — but not the copy itself, which can run to hundreds of
kilobytes per piece. This design brings the copy library to Gold as its own
entity and puts a small retrieval API in front of it, so every item in the
product's `marketingCopy` list gains a clickable `contentUrl` and consumers
fetch the full text only when they actually need it. The heavyweight content
never rides inside product rows, events, or messages — only the pointer does
(the claim-check pattern).

Decided with the module owner 2026-09-03. Delivery is sliced as
**UIFPE-252** (the gold entity), **UIFPE-253** (the `contentUrl` field),
**UIFPE-254** (the serving copy + API), **UIFPE-255** (the Mule gateway
route + security). Owner-set parameters: ~**100 requests/second** retrieval
traffic; security enforced at the Mule gateway; the `attachment` column and
geo-visibility are out of scope.

## What the data actually says (all figures live from dev, 2026-09-03)

The design was shaped by measuring the source, not by the intake as first
written. Source: `uif_echo_dev_silver_mdm.xxpso_ego_market_copy_data`
(already ingested — no platform dependency).

| Fact | Measured | Consequence |
|---|---|---|
| Rows at `version='C'` | 6,561,622 | the library's working set |
| Distinct `content_id` | 6,561,618 | **contentId alone is the key** |
| Distinct (content_id, type, owning_market, target_audience) | 6,561,618 | the composite adds **zero** uniqueness — rejected as PK and as URL shape |
| Full-key duplicate rows | 4 | dedupe latest-by-`last_update_date` in the mapping SQL |
| Rows with a null key part | 35 (across the 4 originally proposed parts) | the mapping filter excludes null `contentId` — the only part that is the key |
| Rows with NULL `content` | 27,701 | kept in gold (metadata is valid); the API answers 404 |
| `content` length | max **348,438 bytes** (single-byte text: bytes == chars) · p50 856 B · p99 17 KB · 715 rows over 100 KB · **0 rows over 350 KB** | too big for events; **fits a DynamoDB item** (400 KB limit) — and gzipped the worst row is 12.6 KB (the largest ten compress 5.4×–50.7×, worst compressed 36.8 KB), so headroom is ~10× |
| `disabled_flag = 'Y'` | 2 | excluded alongside the version filter |
| Product `marketingCopy` contentIds resolving in the library | 2,491,459 of 2,491,621 (99.993%) | 162 dangling ids 404 — correct behavior, tracked as a reconcile note |
| geo-visibility column in the source | **does not exist** | it is a product-side assignment attribute (EAV group 1181), not a property of the content — out of the key, out of the entity |

## The gold entity (UIFPE-252)

`gold_product.marketing_copy` — a second entity in the product domain,
config-driven like every gold table (ADR-0001/0003).

- **Key:** `contentId` (one column). The four artifacts are scaffolded by
  hand (the delta compiler serves registered entities; the new-table
  scaffolder remains a pending platform decision).
- **Columns:** `contentId, type, owningMarket, audience, description,
  content` — from `content_id, type, owning_market, target_audience,
  copy_desc, content`.
- **Filter:** `version = 'C' AND (disabled_flag IS NULL OR disabled_flag <>
  'Y')`, plus the null-key exclusion and the latest-row dedupe above.
- **Channels: `api` only.** No event or message feed — pushing content
  through feeds is exactly what this design exists to avoid. The entity
  still writes its change log like every gold table; the serving copy below
  is its first consumer.
- **CDC:** `key_mapping` is the identity mapping on `content_id`; watermark
  `_silver_updated_at`; cadence `gold-24h` to start (one config line to
  change). Lake Formation access for consuming stacks is automatic — the
  gold-layer tag applies at sync (UIFPE-229).

## The pointer on the product table (UIFPE-253)

Each `marketingCopy` item gains **`contentUrl`**:

```
{base}/marketing-copy/{contentId}
```

- **One path segment.** The dash-composite URL in the original intake was
  rejected on the evidence: it adds no uniqueness (table above), dashes
  inside values make it ambiguous, null key parts break it, and re-marketing
  or re-audiencing content would break every previously shared link. The
  assignment attributes (type, audience, geoVisibility, owningMarket) stay
  where they already are — as metadata fields *inside* each item.
- **The base is per-environment configuration** (`pipelines/overlays/{env}`),
  never a literal in shared code or SQL — gold rows must not carry dev URLs
  into other environments: dev `https://api1-dev.pearson.com/uif/v1`, uat
  `https://api1-stg.pearson.com/uif/v1`, prod
  `https://api1-us1.pearson.com/uif/v1` — `/v1` per the API guidelines'
  major-version rule; the registered contract, the dev overlay and the Mule
  route (UIFPE-255) all carry it.
- **Mechanics:** additive struct widening (supported since UIFPE-157),
  backfill **silent** — the URL is derivable from `contentId`, so adding it
  is maintenance, not a change consumers need pinging about.

## Retrieval at 100 req/s (UIFPE-254 + UIFPE-255)

Gold is Iceberg on S3 — an analytical store with no point lookup, so a
synchronous GET needs a serving copy at any traffic level; the owner-stated
100 req/s rules out Athena-per-request outright (seconds of latency, cost,
concurrency quotas). **The serving store is DynamoDB** — the owner's call
(2026-09-03): one serving approach for every future gold API (product next),
not an S3 store for this entity and a different store for the rest. The
byte-accurate measurement made that possible: every row fits a DynamoDB item
today (max 348,438 bytes vs the 400 KB limit), and `content` is stored
**gzipped** (a Binary attribute), which turns the thin 13% raw headroom into
~10× (worst compressed row: 36.8 KB) and cuts read cost — an item-size cliff
from future content growth is no longer one oversized row away.

```
transform run ──► change log ──► materializer (delta-only)
                                      │  upserts/deletes one item
                                      ▼  per changed contentId
                  DynamoDB uif-{env}-marketing-copy-serving
                  (pk contentId; content gzipped, Binary)
                                      ▲
   consumer ─► Mule gateway ─► Lambda ┘   GET /uif/v1/marketing-copy/{id}
              (auth, caching)  (gunzip + JSON envelope, ETag, Cache-Control)
```

- **Materialization is change-log-driven**: each run's change rows name
  exactly the contentIds to upsert or delete — never a 6.5M-item rewrite.
  The one-time fill is ~6.5M writes (single-digit dollars, on-demand).
- **The API** gunzips and returns a JSON envelope (`contentId, type,
  owningMarket, audience, description, content`); `ETag` is a **hash of the
  content itself**, never a timestamp — the platform's silent-maintenance
  invariant repairs values WITHOUT moving `_gold_updated_at`, so a timestamp
  ETag would let a revalidating cache 304 past a real content change
  forever; `Cache-Control` sized to the pipeline cadence so the
  gateway's cache absorbs hot content. **404** for unknown ids and for
  null-content rows. Reads are single-digit-ms; at p50 ≈ 300 B compressed,
  100 req/s costs a few tens of dollars a month before gateway caching.
- **The materializer guards the cliff anyway**: any item that would exceed
  ~380 KB *after* compression is refused loudly (logged, alarmed) rather
  than written truncated — measured headroom says this never fires; the
  guard is what makes that an invariant instead of an observation.
- **Security is enforced at the Mule gateway** (owner decision). Defense in
  depth: the gateway injects a shared header the Lambda validates, so the
  backend cannot be invoked around the gateway. The registered OpenAPI
  contract still *declares* the OAuth2/Entra scheme — contract-first, the
  same Apicurio governance gate every registered contract clears.

**Why not one S3 object per contentId** (the earlier draft of this design):
it works, and it is the only shape with literally no size ceiling — but it
would make marketing_copy's serving store a one-off. The owner chose one
uniform pattern over the marginal simplicity: with gzip, DynamoDB serves
this entity's blobs AND next quarter's product-table API (a few KB per row,
no compression even needed) from the same store shape, the same
change-log-driven materializer, and the same freshness check.

## Known limitations, stated up front (adversarial round, 2026-09-03)

- **Wizard adds to this entity refuse at compile, loudly.** The delta
  compiler's automatic SQL writers anchor on the product entity's file shape
  (`FROM items`); this entity's final SELECT reads from its dedupe step, so a
  wizard-submitted new column classifies as "updates automatically" but the
  compile refuses with a clear anchor error and nothing is written — the
  column then lands as an engineer edit in the same PR. Acceptable for a
  six-column library that changes rarely; generalizing the writers is real
  work that waits for demand.
- **The dedupe's tie-break assumes `RECORD_ID` is a unique staging sequence.**
  Nothing in the repo can prove it. If two duplicate rows ever tied on BOTH
  `LAST_UPDATE_DATE` and `RECORD_ID` while differing in a value, the pick
  would be nondeterministic. A one-time live assertion (record_id non-null
  and unique per contentId at version 'C') is part of the first-load checks.

## What we would revisit

- **Latency-sensitive consumers or 10× traffic:** deepen the gateway cache
  (or add DAX in front of the table); the read path is already single-digit
  milliseconds, so scale is a caching question, not a redesign.
- **A future item over the 400 KB limit even compressed:** the materializer's
  size guard makes this loud, not silent. If it ever fires, that one content
  class moves to an S3 pointer (`contentRef`) while everything else stays in
  the table — a per-item escape hatch, not a second architecture.
- **The 162 dangling assignments:** if the rate grows, a reconcile check
  comparing assigned contentIds against the library belongs in the nightly
  reconcile rather than a ticket note.
- **`attachment` and richer content types:** excluded now by owner decision;
  the envelope leaves room for an `attachmentUrl` field later without
  breaking the contract.
- **Events for the library itself:** the entity starts feed-less. If a
  consumer ever needs "copy changed" notifications, the change log already
  exists — turning on a channel is configuration, not redesign.
