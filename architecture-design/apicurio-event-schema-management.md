# Event Schema Management with Apicurio Registry

This is the working guide for anyone changing an event schema. It answers one question — **is my change a minor or a major, and what do I owe the platform because of it** — and tells you what belongs in the pull request.

It deliberately does not describe how versions are stored, resolved, or retired. Those are decided in [ADR-0013](decisions/0013-schema-versioning-and-consumer-migration.md), and repeating them here would create a second copy to drift. Where this guide and ADR-0013 disagree, **ADR-0013 wins.**

---

## Where the mechanics are defined

| Question | Defined in |
|---|---|
| How a **major** is stored | [ADR-0013 §1, §2.2](decisions/0013-schema-versioning-and-consumer-migration.md) — a `.vN` **sibling artifact** (base artifact = major 1, no suffix) |
| How a **minor** is stored | ADR-0013 §1, §2.1 — an **inner Apicurio version** inside one artifact |
| Artifact and group naming | ADR-0013 §1, §9 — group = domain, artifact = `{domain}.{entity}.{operation}` (e.g. `product.product.created`) |
| DetailType derivation | ADR-0013 §1 — `{domain}.{operation}.v{major}`; minors never appear in a DetailType |
| How the processor decides what to emit | ADR-0013 §5 — two read calls through the BFF, anchored family regex, emit every sibling whose `state != DISABLED` |
| What ENABLED / DEPRECATED / DISABLED mean | ADR-0013 §3 |
| How consumers migrate off a major | ADR-0013 §4 — the seven-step lifecycle |
| Known failure modes and their mitigations | ADR-0013 §8 |

---

## 1. Choosing the bump

Choose based on **what happens to an existing consumer that does not change its code.**

### `bump=minor` — backward-compatible change

The existing consumer keeps working without modification. Use for:

- Adding a **new optional field** (has a default or is nullable)
- Adding a new value to an enum **when consumers are known to handle unknowns** (see caveats)
- Widening a constraint (e.g. `maxLength` 100 → 255)
- Adding a new nested optional object (e.g. a new `pricing` block)
- Adding a new element type to an array of unions

**Effect:** a new inner version inside the same artifact. The DetailType does not change, EventBridge rules do not change, no subscription changes, no migration, no notification. Consumers may start seeing the new field; because it is optional, their parsers are unaffected. See ADR-0013 §2.1.

### `bump=major` — breaking change

An existing consumer would fail, misbehave, or lose data. Use for:

- **Removing** or **renaming** a field
- Changing a field's **type** (`string` → `integer`, scalar → object)
- Making an optional field **required**
- Removing an enum value, or changing enum semantics
- Tightening a constraint that existing valid payloads would now violate
- Changing the meaning of a field without changing its name (semantic break)
- Restructuring nesting (moving `isbn13` under `identifiers.isbn13`)

**Effect:** a new sibling artifact `{base}.vN`, a new DetailType, and a consumer-migration campaign — dual publication while both majors are live, then deprecation and sunset. See ADR-0013 §2.2 and §4.

A major commits the platform to running two lines at once and to chasing every subscriber off the old one. It needs **steward sign-off**, not just a passing compatibility check.

### Decision flow

```
Does an existing consumer break without a code change?
│
├── Yes ──► bump=major
│
└── No ───► bump=minor
```

ADR-0013 defines **two lanes only.** A documentation-only change — descriptions, examples, formatting — goes through the **minor** lane; there is no `patch` bump in this model.

### Caveats that catch people

- **Enum additions are minor only if consumers tolerate unknown values.** If a downstream system switches on the enum and errors on unmatched cases, treat it as a major. When in doubt, ask the consuming teams — this is the single most common source of accidental breakage.
- **Adding a required field is major**, even if the producer always populates it. Consumers with strict validation will reject replayed events that predate the field.
- **Minor-added fields are optional forever within a major.** Replayed and redriven events may predate the minor, so a consumer can never assume the field is present. See ADR-0013 §2.1.
- **The compatibility rule is not yet enforced.** ADR-0013 §10 item 1 records BACKWARD compatibility as an open blocker: no rule is configured, so the check endpoint returns assumptions and the minor lane is honour-system today. Do not rely on the registry to catch a mislabelled minor — a human has to.
- **Never hand-pick the bump.** Run the compatibility check to compute it (ADR-0013 §6, Publishers).

---

## 2. Where schema files live

Event schemas sit inside the owning domain, one pair per event type:

```
domains/product/src/schemas/events/
  product-created-data.json       the JSON Schema
  product-created-example.json    a worked example event
domains/price/src/schemas/events/
  price-created-data.json
  price-created-example.json
```

Every schema change is a pull request against these files. The registry is populated from them — never edited by hand in the Apicurio UI.

> **Not yet in place:** no `CHANGELOG.md` exists alongside any schema directory today. Requiring a changelog entry per schema change is a proposal, not current practice — adopt it deliberately or drop this note.

---

## 3. Failure behaviour the processor must keep

These are the guarantees a schema change must not undermine.

| Situation | Required behaviour |
|---|---|
| The artifact listing call fails | **Abort the run loudly.** A registry hiccup must never produce a green run that silently emitted nothing (ADR-0013 §5.4) |
| A publishable operation resolves to **zero** majors | **Abort the run loudly** — same reason |
| A payload fails validation against the head minor | Fail that record closed with reason `SCHEMA_DRIFT` and carry on; the record is recoverable from `uif-event-{env}-publish-metrics` ([ADR-0007 Table 3](decisions/0007-five-dynamodb-tables-for-eventing.md)) |
| A consumer is subscribed to a DetailType no live major backs | Caught by subscribe-time validation and per-rule zero-invocation alarms (ADR-0013 §8) |

Emitting an event that does not match its registered schema is worse than emitting nothing — consumers have no way to detect it.

---

## 4. Checklist for a schema pull request

- [ ] The bump lane is stated in the PR body, with the reason
- [ ] The compatibility check was run, and the lane matches its outcome — not hand-picked
- [ ] No field removed, renamed, or retyped in a minor
- [ ] The schema validates against the `-example.json` sample beside it
- [ ] **If major:** steward sign-off recorded; the new DetailType named; migration plan per ADR-0013 §4, including who the current subscribers are
- [ ] **If major:** the superseded sibling is DEPRECATED as part of the same change (ADR-0013 §4 step 2) — otherwise new subscribers keep enrolling on a line you intend to retire

---

## Related

| Document | What it covers |
|---|---|
| [`decisions/0013-schema-versioning-and-consumer-migration.md`](decisions/0013-schema-versioning-and-consumer-migration.md) | The versioning model, artifact states, migration lifecycle, and the processor's registry contract |
| [`decisions/0011-apicurio-schema-governance.md`](decisions/0011-apicurio-schema-governance.md) | Apicurio as guardrail and governance engine |
| [`event-centric-publish-reference.md`](event-centric-publish-reference.md) | The end-to-end publish path from Gold merge to subscriber queue |
| [`../stacks/apicurio/standards/guidelines.md`](../stacks/apicurio/standards/guidelines.md) | The Schema capability standard and definition of done |
