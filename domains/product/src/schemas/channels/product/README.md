# Per-channel product data-payload schemas

**Generated — do not hand-edit.** Each file is the Gold master schema
([`../../gold/product/v1/master.json`](../../gold/product/v1/master.json))
projected onto one delivery channel, driven entirely by the master's
`x-channels` tags (top-level **and** nested — object properties and array
items are filtered recursively). Regenerate after any master change:

```bash
python3 stacks/echo/src/apps/cli/generate_tier_subsets.py \n  --master domains/product/src/schemas/gold/product/v1/master.json \n  --entity product --version v1 --out-dir /tmp/subsets
```

> The `api` tier lands here too, with the event and message tiers: a
> projection is a schema whichever channel carries it (ADR-0022 decision 4).
> The OpenAPI contract stays in the API capability's folder at
> `../../../api/product/openapi.yaml` and references its projection across the
> two trees.

## Channel → transport

| `x-channels` value | operation | Schema | Delivery |
|---|---|---|---|
| `event` | `created` | [`product-data-event-created.json`](product-data-event-created.json) | **SQS** (EventBridge rule → subscriber queue) |
| `event` | `updated` | [`product-data-event-updated.json`](product-data-event-updated.json) | **SQS** (EventBridge rule → subscriber queue) |
| `message` | `created` | [`product-data-message-created.json`](product-data-message-created.json) | **Kinesis** (EventBridge rule → stream) |
| `message` | `updated` | [`product-data-message-updated.json`](product-data-message-updated.json) | **Kinesis** (EventBridge rule → stream) |
| `api` | `created` | [`product-data-api-created.json`](product-data-api-created.json) | **webhook** (EventBridge rule → API destination) |
| `api` | `updated` | [`product-data-api-updated.json`](product-data-api-updated.json) | **webhook** (EventBridge rule → API destination) |

These are schemas for the **`data` block** of the event envelope (the same
position `product-created-data.json` describes per-operation) — the
envelope itself (`specversion`, `id`, `subject`, …) is identical across
channels except its `channel` field, which names the channel the copy was
published for and is what subscription rules filter on (the DetailType is
per-operation: `{domain}.{operation}.{version}`).

## What the projection removes (as of master @ 2026-09-17)

- **Everywhere** (empty `x-channels`): the Oracle surrogates
  `inventoryItemId`, `organizationId`, `itemCatalogGroupId`, plus nested
  `contributors[].contributorId` and `categories[].categoryId` — internal
  keys never leave the platform.
- **SQS (`event`) only**: `contributors[].affiliationType2/affiliation2/
  affiliationType3/affiliation3` are tagged `message`+`api` only, so the
  SQS payload carries just the first affiliation; Kinesis and webhook carry
  all three.
- `message` and `api` are currently identical (119 leaf fields vs SQS's
  115); all three share `required: ["productId"]`.

## Why six files and not three

A channel is registered **per operation** — `{channel}-created` and
`{channel}-updated` are separate registry coordinates (owner instruction
2026-09-25). A projection file must declare the coordinate it is registered
under (UIFPE-523: three tiers once claimed to be the master while `publish.sh`
registered them per tier, and the file agreed with none of them), and one file
cannot declare two coordinates — so each channel is emitted twice.

**The two files in a pair carry the same projection.** The projection slices by
audience; the operation slices by what happened. They are separate artifacts so
the contracts can version independently: a field added to the created contract
need not widen the updated one. Only `created` and `updated` exist — a delete is
detected and counted but never published, so a `deleted` projection would
describe a contract nothing serves.
