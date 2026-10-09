# ADR-0014 — Apicurio Registry vs AWS Glue Schema Registry

**Status:** Proposed
**Date:** 2026-08-13
**Authors:** UIF Architecture Team
**Scope:** Which registry UIF adopts as its contract registry of record — Apicurio Registry or AWS Glue Schema Registry — and the reasons for choosing Apicurio.

**Related:**

- [ADR-0011](0011-apicurio-schema-governance.md) — Apicurio as guardrail and governance engine, including the Dual-Sync Bridge that keeps Glue Schema Registry in step as a runtime mirror
- [ADR-0013](0013-schema-versioning-and-consumer-migration.md) — schema versioning, artifact states, and consumer migration in Apicurio

---

# Context

UIF Eventing is designed as an enterprise event platform that transforms business changes from Gold-layer data into consumable events delivered through:

- Amazon SQS
- Amazon Kinesis
- API Gateway / API Destinations

Current Eventing Flow:

```text
Source Systems
      ↓
Bronze
      ↓
Silver
      ↓
Gold
      ↓
Event Processing Glue
      ↓
EventBridge
      ↓
SQS / Kinesis / APIs
```

The current implementation requires:

- Schema validation
- Schema versioning
- Schema drift detection
- Event generation
- Consumer onboarding
- Contract governance (Future)

Two registry technologies were evaluated:

1. AWS Glue Schema Registry
2. Apicurio Registry

---

# Decision

UIF will use **Apicurio Registry** as the authoritative contract repository and governance platform.

---

# Why Not AWS Glue Schema Registry

AWS Glue Schema Registry is optimized for:

- Schema storage
- Schema versioning
- Compatibility checking
- Serialization / deserialization
- Integration with:
  - AWS Glue
  - Kinesis
  - Lambda
  - MSK / Kafka
  - Flink

It primarily manages:

```text
Avro
JSON Schema
Protobuf
```

and is focused on runtime validation of producer/consumer payloads.

While this satisfies UIF's immediate schema validation requirements, it does not address the broader platform vision.

---

# Why Apicurio

Apicurio provides schema management plus enterprise contract governance.

Supported artifacts include:

- OpenAPI
- AsyncAPI
- JSON Schema
- Avro
- Protobuf
- GraphQL
- WSDL
- XML Schema

This enables UIF to manage both API and Event contracts from a single platform.

---

# Benefit 1: Enterprise Contract Catalog

Apicurio becomes the central catalog for all integration contracts.

Example:

```text
ProductChanged Event

CustomerChanged Event

PriceChanged Event

ProgramChanged Event

Product API

Customer API

Partner APIs
```

Consumers do not need to know where a contract resides.

Instead they discover everything through a single contract platform.

---

# Benefit 2: AsyncAPI Registry

Schemas describe:

```text
Fields
Data Types
Required Attributes
```

They do not describe:

```text
What event is published

Who publishes it

How it is delivered

How consumers subscribe

Examples

Delivery semantics
```

AsyncAPI fills this gap.

Example:

```yaml
asyncapi: 3.0.0

channels:

  product.changed:

    messages:

      ProductChanged:

        payload:

          $ref: ProductSchema_v1
```

This allows UIF to publish complete event contracts instead of only field definitions.

---

# Benefit 3: API Contract Repository

UIF currently owns or integrates with multiple API platforms including MuleSoft.

Apicurio allows storage of:

```text
REST APIs

OpenAPI specifications

Partner APIs

Mule APIs

Event Contracts
```

inside one contract catalog.

Example:

```text
Product REST API

ProductChanged Event

Customer REST API

CustomerChanged Event
```

This provides a unified integration inventory.

---

# Benefit 4: MCP / Agent Discovery

Future UIF agents should be capable of contract discovery.

Example:

User:

```text
Show me available Product events
```

Agent:

```text
Search Apicurio
```

Result:

```text
ProductCreated

ProductUpdated

ProductRetired
```

The agent can then retrieve:

- Schema
- Examples
- Version history
- Documentation
- Subscription guidance

This enables:

```text
Contract Discovery

Code Generation

Documentation Generation

Consumer Generation
```

through MCP-enabled agents.

---

# Benefit 5: Consumer Self-Service Onboarding

Current onboarding model:

```text
Consumer
   ↓
Raise Ticket
   ↓
UIF Team
   ↓
Configure Subscription
   ↓
Share Contract
```

Future onboarding model:

```text
Consumer
   ↓
Open Catalog
   ↓
Discover Contract
   ↓
Select Delivery Pattern
   ↓
Subscribe
```

No platform team involvement is required.

Supported delivery patterns:

```text
SQS

Kinesis

API Delivery

Change Feed
```

This reduces operational load on UIF teams.

---

# Benefit 6: Cross-Platform Contract Governance

Today UIF primarily targets AWS.

Future state may include:

```text
AWS

Kafka

Autobahn

REST APIs

MuleSoft

Partner Platforms
```

Governance requirement:

```text
One Product Contract

One Customer Contract

One Price Contract
```

regardless of transport technology.

Apicurio enables:

- Version management
- Compatibility rules
- Ownership metadata
- Contract lifecycle management

independent of implementation platform.

---

# Eventing Integration

## Gold Merge Event

```json
{
  "Source": "echo.gold.transform",
  "DetailType": "GoldMergeCompleted",
  "EventBusName": "uif-event-dev",
  "Detail": "{\"goldRunId\":\"jr_12345\",\"domain\":\"product\",\"status\":\"Success\"}"
}
```

---

## Schema Validation

Event Processing Glue:

```text
Retrieve Delta Records

Fetch Schema From Apicurio

Compare Gold Schema

Detect Schema Drift

Validate All Records
```

---

## Phase-1 Governance

When schema drift occurs:

```text
New Field Added

Field Removed

Datatype Changed
```

Glue does **not** fail the run. It fails that record closed and carries on, writing the failure to `uif-event-{env}-publish-metrics`:

```text
status = PUBLISH_FAILED

reason = SCHEMA_DRIFT
```

The record stays recoverable from that row — see [ADR-0007](0007-five-dynamodb-tables-for-eventing.md) Table 3.

No automatic schema version creation will occur in Phase‑1.

---

# Architecture Summary

```text
Gold Merge Glue
       │
       ▼
EventBridge
       │
       ▼
Domain Controller Lambda
       │
       ├── Idempotency check
       ├── Config lookup
       ├── Delta row count
       └── Job sizing
       │
       ▼
Event Processing Glue
       │
       ├── Fetch Contract From Apicurio
       ├── Validate Schema
       ├── Detect Drift
       ├── Validate Records
       ├── Generate Events
       └── Publish Events
       │
       ▼
EventBridge
       │
       ├── UIF SQS
       ├── Client SQS
       ├── Kinesis
       └── API Gateway
```

---

# Consequences

## Positive

- Single enterprise contract catalog
- Event and API contract management
- AsyncAPI support
- MCP / Agent discovery
- Consumer self-service onboarding
- Cross-platform governance
- Centralized contract lifecycle management

## Negative

- Additional platform to manage
- More complex than AWS Glue Schema Registry
- Governance processes required
- Higher upfront setup cost

---

# Final Decision

UIF is building an enterprise integration platform, not merely a schema validation platform.

AWS Glue Schema Registry is sufficient for:

```text
Schema Validation
Schema Compatibility
```

Apicurio is chosen because it enables:

```text
Enterprise Contract Catalog

AsyncAPI Registry

API Contracts

MCP / Agent Discovery

Consumer Self-Service Onboarding

Cross-Platform Contract Governance
```

and aligns with UIF's long-term eventing and integration strategy.
