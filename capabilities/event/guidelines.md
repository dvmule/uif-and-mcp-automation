# Event — Guidelines (the worked standard)

This is the detailed, do-this companion to the Event capability `README.md`. It tells a Forward Deployed Engineer what an event is, how to register its contract, which of several event shapes to use, how to name and route it, how to test and build it, and how to run it reliably on AWS — using globally recognised standards, informed by real production lessons from Pearson's own event platforms, so the result is identifiable and readable to any engineer, inside or outside UIF.

This document follows the same **template shape** as every capability's `guidelines.md` (see `../api/guidelines.md`): five sections — Naming, Contract/YAML documentation, Testing → automation → build, Standards & guidelines, Definition of done. A **Concepts** section comes first because "event" is used loosely in the industry and needs a precise definition before naming or contracts make sense.

Grounding standards (all public): **AsyncAPI 3.0** (the contract format), **CloudEvents 1.0** (CNCF — the message envelope), **xAPI 1.0.3 / Activity Streams 2.0 (W3C)** (actor-verb-object activity events), JSON Schema 2020-12 (the `data` payload), the **Transactional Outbox** pattern (Richardson, microservices.io), Confluent/Apicurio **schema compatibility modes**, SemVer 2.0.0, and the CloudEvents distributed tracing extension (W3C Trace Context). Tool-specific companion for AWS: [`streaming.md`](streaming.md) (Kinesis Data Streams and MSK).

---

## Concepts: what is an event, and what isn't one

**An event is an immutable statement that something happened**, published once, past tense, with no expectation of a reply. It is not a request (an API call expects a response), not a command (an imperative instruction addressed to one specific handler — modelled as an API call or an MCP tool call, not an event), and not a query. If the producer needs an answer, it isn't an event.

**Three shapes cover almost everything UIF publishes** — pick the narrowest one that fits:

| Shape | What it captures | Delivery guarantee needed | Grounding | Example |
|---|---|---|---|---|
| **Domain event** | A state change in one aggregate/resource, owned by one domain | At-least-once, schema-validated, replayable | CloudEvents 1.0 | `product.developer.registered` |
| **Activity** | An actor did something to an object (a fact about behaviour, not a state change) | At-least-once, high volume, order not required | xAPI 1.0.3 / Activity Streams 2.0 | `product.developer.viewedDocs` |
| **Telemetry** | A raw system/user signal at very high volume, feeding analytics | Best-effort; loss of an individual message is acceptable | — (treat as a metric stream, `../observability/`) | UI click stream, page-view pings |

**Why the distinction matters.** A domain event must never be dropped (a consumer's state depends on it) and must be schema-validated at publish time. Telemetry can tolerate loss and doesn't justify the cost of exactly-that rigor. Conflating the three — publishing millions of telemetry pings on the same topic and SLA as a domain event that a billing system depends on — is a real, observed anti-pattern: it forces every consumer to filter noise out of a topic instead of subscribing to what it actually needs. This guide is written for **domain events**; §4 gives explicit rules for when to reach for Activity/Telemetry instead.

### Event sources — an event does not have to come from an API

Treat "an API handler publishes an event" as the common case, not the only one. Four other sources are equally first-class:

- **Agent-emitted.** A long-running or agentic task (`../agent/`) raises a domain event on completion or failure exactly like a service does — e.g. an extraction pipeline publishes `<domain>.<task>.completed` / `.failed` when it finishes. Declare the events an agent is allowed to publish in its `agent-manifest.yaml` (`../agent/guidelines.md` §2), the same way its allowed tools are declared — an agent's write side is governed identically to its read side.
- **File/object-triggered.** A file landing in object storage is a fact, not a request — treat the S3 `ObjectCreated` notification (via EventBridge) as the trigger and publish a domain event (`<domain>.<resource>.ingested`) once the file is validated, rather than having every downstream consumer poll or parse S3 notifications directly.
- **Change-data-capture (CDC) triggered.** The most reliable way to publish a domain event when the state change lives in a database: write the event to an **outbox table in the same transaction** as the business write (the Transactional Outbox pattern), then have a CDC connector (e.g. Debezium) tail that table and publish to the topic. This avoids the dual-write problem — publishing directly from application code after a commit can fail between the two steps and silently lose the event; the outbox+CDC path is atomic with the state change.
- **Event-triggered (choreography).** A consumer's reaction to one event may itself raise a new event. This is normal and encouraged for decoupled workflows, but every derived event **must carry the originating event's `id` as a `causationid` extension attribute** (alongside its own new `id`) so a chain can be traced and a choreography loop can be detected and broken. Never let a consumer re-publish the same event type it consumes without changing the causation chain — that is an infinite-loop risk, not a pattern.

### When to use what (event vs. API vs. agent trigger)

| Need | Use |
|---|---|
| Caller needs a result now | API (`../api/`) or an MCP tool call (`../mcp/`) — never an event |
| A fact happened that other domains/consumers should react to, asynchronously, and it's fine if they react seconds later | Domain event |
| Cross-domain state change (one domain's write must be reflected in another) | Domain event — a synchronous cross-domain write is a coupling anti-pattern; publish an event and let the other domain's consumer apply it |
| An agent should react to a fact after it happens, without the producer knowing or caring which agents care | Event-triggered agent invocation — the agent subscribes to the topic (`../agent/`) |
| An agent's action is needed as part of a caller's synchronous flow, with a result the caller waits for | Direct MCP tool call, not an event |
| High-volume behavioural signal with no per-message delivery guarantee | Activity or Telemetry (Concepts, above) — not a domain event topic |

---

## 1. Naming

**Event type (the CloudEvents `type` attribute).** Reverse-DNS-prefixed, dot-delimited, domain-scoped, versioned: `com.pearson.<domain>.<entity>.<action>.v<major>`. This is the one thing a consumer subscribes to — treat it as part of the public contract.
- Good: `com.pearson.product.developer.registered.v1`, `com.pearson.finance.invoice.paid.v1`, `com.pearson.sku.attribute.updated.v1`.
- Bad: `DeveloperRegisteredEvent`, `product-events`, `RegisterDeveloper` (present tense — that's a command, not an event), a numeric or opaque event code with no semantic meaning on its own (undiscoverable and impossible to grep or diff in a PR), `product.developer.registered` (no `com.pearson` prefix, no version suffix — the old three-segment format).

**Channel / topic: one topic per event type, not one topic per aggregate.** Name the topic after the event type it carries: `com.pearson.product.developer.registered.v1`. **Do not multiplex several event types onto one shared topic** (e.g. a single `product-events` topic carrying registration, login, and password-reset events) — this is a real, observed anti-pattern: it forces every consumer to filter the topic for the one type it cares about, couples unrelated schemas to the same partition/retention/ACL policy, and makes a breaking change to one event type block or complicate every other type sharing the topic. If a use case genuinely needs several event types to preserve relative order (they share a partition key and ordering across types matters), that is the one legitimate exception — document the reason in `asyncapi.yaml`, and still give each type its own schema.

**Message / schema components.** `PascalCase`, matching the event without the domain prefix or tense change: `DeveloperRegistered`. Defined once under `components/schemas` in the AsyncAPI document (`../schema/`).

**Payload (`data`) fields.** `camelCase`, ISO 8601 / RFC 3339 UTC timestamps, opaque string IDs — identical convention to API JSON (`../api/guidelines.md` §1). A consumer should not have to learn a second convention to read an event versus a response body.

**Partition / shard key.** Chosen deliberately, not left as a random UUID by default. Use the id of the aggregate the event is about (`developerId`, `invoiceId`) so that all events for one aggregate land on the same partition/shard and are processed in relative order by one consumer instance; fall back to a random key only when no per-aggregate ordering is needed and you want to spread load evenly. Document the key in `asyncapi.yaml` — a partition key chosen implicitly and never revisited is how topics end up needing a shard/partition re-plan under load later.

**CloudEvents required attributes** (`specversion`, `id`, `source`, `type`, `time`) are set by the producer library, never hand-rolled per event. The Pearson-mandated `source` format is `urn:pearson:svc:<service-name>` (e.g. `urn:pearson:svc:catalog-service`).

---

## 2. Contract / YAML documentation (Event-Driven + Schema Registry First)

The contract is written and registered **before** the first message is published (`../schema/`). The contract is the single source of truth; the producer and every consumer code against it, never against each other's memory of the payload shape.

**Format:** AsyncAPI **3.0** in `asyncapi.yaml`, kept in `domains/<domain>/src/events/` (or the reference module). AsyncAPI reuses JSON Schema for `payload`, so — exactly as with OpenAPI — the same schema the registry validates is what the contract test asserts against.

**Every operation must carry** (AsyncAPI 3.0 `send`/`receive` operations, replacing the old pub/sub terms):
- `channel` bound to exactly one event type/topic (§1).
- A `message` with a `payload` schema and a **CloudEvents envelope**: the producer emits CloudEvents in structured mode (JSON), with the domain's data under `data` and the standard attributes (`id`, `source`, `type`, `time`, `datacontenttype`, `dataschema`) set at the envelope level, plus `causationid` where the event is derived from another (Concepts, above).
- `summary`/`description` stating **delivery semantics** for that event — at-least-once (default) or exactly-once — so a consumer knows whether it must dedupe.
- At least one realistic `example` message.

**The envelope/data split is what makes the schema modular.** CloudEvents' fixed envelope never changes; only `data` varies per event type. This means a consumer library that only needs routing/correlation (dashboards, audit logs, the outbox relay itself) can depend on the envelope schema alone and stay stable across every domain event ever added — it never needs to know about `data` shapes it doesn't consume.

**Polymorphic events on one topic (only for the documented ordering exception in §1).** Where several event types deliberately share a topic, discriminate `data` with a JSON Schema `oneOf` keyed by the envelope's `type` attribute (or, if using Avro instead of JSON Schema, a registry `TopicRecordNameStrategy` so multiple record types can be registered against one subject/topic). Never infer the shape from the payload's fields — always discriminate on `type`.

**Components over repetition.** Define the `data` schema once under `components/schemas` and `$ref` it; the same schema object is what the Schema Registry validates on every publish (Schema Registry First, `../schema/`).

**Documentation is generated, not hand-kept.** Human-readable event docs are rendered from `asyncapi.yaml` in CI (e.g. the AsyncAPI Generator) and published — never written twice.

**Registration is a promotion, not a single act.** A schema moves through the same environments as the code that produces it: registered first in a dev/test registry, promoted to staging once contract tests pass, promoted to production only as part of a release. **A schema version already promoted to production is never edited or deleted** — even a deprecated event type's schema stays in the registry indefinitely, because a consumer replaying historical messages (§4, TTL) must still be able to resolve the schema those old messages were written against.

**Versioning (SemVer 2.0.0 applied to the schema).**
- **Additive/backward compatible** (new optional field, new consumer) → same event type, same topic. The registry enforces this with a **BACKWARD** compatibility check (§3) — a new schema must be readable by consumers using the previous schema.
- **Breaking** (remove/rename a field, tighten validation, change meaning) → a **new event type** (e.g. `product.developer.registered.v2`), published alongside the old one for a documented deprecation window while consumers migrate — an event type can't carry a `Sunset` header, so the window and migration date live in the AsyncAPI `description` and the domain's changelog.

---

## 3. Testing → automation → build

Follow the test pyramid; every layer is automated in CI and gates the build. Nothing merges red; nothing publishes an incompatible schema.

1. **Unit tests** — producer domain logic and message construction, no broker. Fast, run on every commit.
2. **Contract tests** — the constructed message's `data` is validated against the **registered** `asyncapi.yaml` schema (Ajv 2020 / `jsonschema` Draft 2020-12 — the same libraries and technique as the API contract test, `../api/guidelines.md` §3). This is the load-bearing test: it proves the producer and the contract have not drifted.
3. **Schema compatibility check** — on every PR touching the schema, the registry runs a compatibility check against the previously registered version, in the mode declared for that event type (**BACKWARD** by default; **FULL** if both old and new consumers must run simultaneously during a rollout; see `../schema/` for the full mode reference). An incompatible change without a new event type fails the PR.
4. **Integration tests** — producer and consumer against a real, ephemeral broker (e.g. Testcontainers for Kafka; LocalStack for Kinesis, `streaming.md`) — proves the message actually serializes, publishes, and is received.
5. **Consumer-driven contract tests** — where an event has known consumers, each consumer's expectations are verified (e.g. Pact) so a producer change can't silently break a subscriber.
6. **End-to-end / smoke** — after CD, publish one message against a deployed environment and assert the expected downstream effect.

**Automation & build gate (GitHub Actions, at repo root — see `../delivery/`).** On every PR touching `domains/<domain>/**`:
```
lint  →  unit  →  contract (vs registered schema)  →  schema compatibility check  →  build container (src/Dockerfile)  →  security scan  →  publish rendered event docs
```
- The build artifact is still the **one container image** (`../infrastructure/docker.md`) — a domain that produces or consumes events does not get a second image.
- The compatibility check and the contract test both run here; a breaking, unversioned schema change fails the PR exactly like a breaking OpenAPI change fails an API PR.

---

## 4. Standards & guidelines (non-negotiable design inputs)

- **Security by default (`../security/`).** Producers and consumers authenticate to the broker (mTLS or SASL/OAuth, never plaintext credentials); topic/stream ACLs are least-privilege per domain. Never trust the wire — validate every inbound event against its registered schema before processing, exactly as an API validates its request body.
- **Observability by default (`../observability/`).** Emit the RED signals per consumer (rate, error, processing duration); structured JSON logs keyed by the CloudEvents `id`; propagate distributed trace context via the **CloudEvents distributed tracing extension** (`traceparent`, W3C Trace Context) so an event can be followed across the domains it crosses. Monitor consumer lag/`ProvisionedThroughputExceededException` as a first-class signal, not an afterthought — a consumer falling behind is silent until someone alerts on it.
- **Reliable publish: prefer outbox + CDC over a direct publish-after-commit** (Concepts, above) for any event whose loss would leave a domain's own state inconsistent with what it told the world. A direct publish from application code is acceptable only for events where an occasional lost message is tolerable (Activity/Telemetry).
- **Delivery semantics are explicit, not assumed.** State at-least-once or exactly-once per event in the contract (§2). At-least-once is the default and the safer, cheaper choice; it requires idempotent consumers. Do not build for "exactly ordered" delivery unless the transport and cost genuinely justify it — most consumers should be built to tolerate out-of-order, duplicated delivery.
- **Idempotent consumers.** Dedupe by the CloudEvents `id` (or a domain-specific idempotency key in `data`); a redelivered message must never double-apply an effect.
- **Ordering is opt-in, scoped to the partition key.** Global ordering across a topic is not guaranteed by default, and shouldn't be assumed even within a topic unless the partition/shard key (§1) is the same for the two events being compared. Don't assume ordering otherwise.
- **Dead-letter, never silent-drop.** A message that repeatedly fails processing goes to a documented dead-letter queue/topic with enough context to replay it — it is never dropped. Cap the number of unacknowledged/unprocessed messages a consumer is allowed to hold (a backpressure limit) so a stuck consumer degrades visibly rather than silently accumulating unbounded lag.
- **TTL / retention is a deliberate choice across three layers, not one setting:**
  1. **Broker/stream retention** — how long an *unconsumed* message stays available to be read at all. This is transport-specific (Kinesis default 24h, extendable to 8760h/365 days; MSK/Kafka set per-topic `retention.ms`/`retention.bytes`, commonly days-to-weeks, or indefinite with log compaction for latest-state topics) — see `streaming.md` for exact configuration.
  2. **Schema retention** — indefinite. A schema, once promoted to production, is never deleted, even after its event type is deprecated (§2) — replay and audit depend on being able to resolve it.
  3. **Cold/archival retention** — many domain events are also archived to durable, cheap storage (S3) for analytics and long-term replay, on a retention schedule set by data governance (`../data/`), independent of the broker's short-term retention window.
- **Backward compatibility is a promise (§2).** Additive within an event type; breaking only via a new event type with a published deprecation window.

---

## 5. Definition of done (Event)

- [ ] Name follows §1 (event type, one topic per type unless a documented ordering exception, partition key deliberately chosen, schema components, `camelCase` payload).
- [ ] Contract in `asyncapi.yaml` follows §2 and is **registered before the first publish**, promoted dev→staging→prod alongside the code; delivery semantics stated.
- [ ] Unit + contract tests pass; contract test validates the message `data` against the registered schema (§3).
- [ ] Schema compatibility check passes in CI in the declared mode; any breaking change ships as a new event type (§2, §3).
- [ ] Security: broker auth + least-privilege ACLs in place; every inbound event validated against schema (§4).
- [ ] Observability: RED signals, CloudEvents `id` correlation, `traceparent` propagation, consumer-lag alerting, one SLO defined (§4).
- [ ] Idempotent consumer behaviour and a dead-letter policy documented; backpressure limit set (§4).
- [ ] Retention set deliberately at all three layers — broker TTL, schema (indefinite), and any cold-storage archive — per `streaming.md` (§4).
- [ ] If the event's loss would leave domain state inconsistent, it publishes via outbox+CDC, not a direct post-commit call (§4).
- [ ] CI green: lint → unit → contract → compatibility check → container build → scan → docs published (§3).
- [ ] Maps to a Jira Story (`capability:event` + its `domain:*`) per `../docs/capability-domain-jira-map.md`.

---

**This standard is broker-agnostic in its contract, naming, and testing rules** — they hold regardless of transport. AWS is UIF's mandated cloud (`../../CLAUDE.md`), and the two native choices are Kinesis Data Streams and MSK; the tool-specific how-to, including exactly how TTL, partitioning, and reliable publish map onto each, is [`streaming.md`](streaming.md). The same discipline that makes an API's contract the source of truth (`../api/guidelines.md`) makes an event's `asyncapi.yaml` the source of truth for everything that flows through it.
