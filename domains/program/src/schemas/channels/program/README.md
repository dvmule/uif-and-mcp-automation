# Per-channel program data-payload schemas

**Generated — do not hand-edit.** Each file is the Gold master schema
([`../../gold/program/v1/master.json`](../../gold/program/v1/master.json))
projected onto one delivery channel, driven entirely by the master's
`x-channels` tags (top-level **and** nested — object properties and array
items are filtered recursively). Regenerate after any master change:

```bash
python3 stacks/echo/src/apps/cli/generate_tier_subsets.py \
  --master domains/program/src/schemas/gold/program/v1/master.json \
  --entity program --version v1 --out-dir /tmp/subsets
```

> The `api` tier lands here too, with the event and message tiers: a
> projection is a schema whichever channel carries it (ADR-0022 decision 4).
> The OpenAPI contract stays in the API capability's folder at
> `../../../api/program/openapi.yaml` and references its projection across the
> two trees.

## Channel → transport

| `x-channels` value | operation | Schema | Delivery |
|---|---|---|---|
| `event` | `created` | [`program-data-event-created.json`](program-data-event-created.json) | **SQS** (EventBridge rule → subscriber queue) |
| `event` | `updated` | [`program-data-event-updated.json`](program-data-event-updated.json) | **SQS** (EventBridge rule → subscriber queue) |
| `message` | `created` | [`program-data-message-created.json`](program-data-message-created.json) | **Kinesis** (EventBridge rule → stream) |
| `message` | `updated` | [`program-data-message-updated.json`](program-data-message-updated.json) | **Kinesis** (EventBridge rule → stream) |
| `api` | `created` | [`program-data-api-created.json`](program-data-api-created.json) | **webhook** (EventBridge rule → API destination) |
| `api` | `updated` | [`program-data-api-updated.json`](program-data-api-updated.json) | **webhook** (EventBridge rule → API destination) |

These are schemas for the **`data` block** of the event envelope, not the
envelope itself.

## What the projection removes (as of master @ 2026-09-25)

- **Everywhere** (empty `x-channels`): `inventoryItemId` and `organizationId`
  — the Oracle surrogates, which never leave the platform. The same exclusion
  product makes, for the same reason. 13 top-level properties become 11.
- **No per-channel difference.** `event`, `message` and `api` are identical:
  12 leaf fields each. Every attribute tagged for one audience is tagged for
  all three.
- All three share `required: ["programId"]`.

Because the three tiers are the same document, the three registry coordinates
this entity produces all carry identical content. Whether that should collapse
to fewer coordinates is recorded on UIFPE-621, not decided here.

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
