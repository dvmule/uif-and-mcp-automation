# Event

## Purpose
Event-driven, asynchronous integration — how capabilities publish and subscribe to events with registered schemas, so systems stay decoupled and react to change rather than polling for it.

## Standards
- **Event Driven** is preferred where latency and coupling matter; batch is a deliberate exception, not the default.
- Every event has a registered schema (`asyncapi.yaml` / schema in the registry) before it is published (`../schema/`).
- Events are versioned; consumers are never broken silently.

## Governance
- **Central:** event schema standards, the broker/topology, naming and versioning rules.
- **Local (FDE):** which events a module publishes and how it reacts.

## Patterns
- Publish/subscribe with schema validation at the boundary.
- Event choreography for cross-module reactions; orchestrate centrally only when state and compensation demand it.

## Guidance
- **The worked standard:** [`guidelines.md`](guidelines.md) — what an event is, which of three shapes (Domain event, Activity, Telemetry) to use, how an event can originate from an API, an agent, a file, a CDC outbox, or another event, how to name and register its contract, test it, and build it, on globally recognised standards (AsyncAPI 3.0, CloudEvents 1.0, xAPI/Activity Streams, JSON Schema 2020-12, SemVer 2.0.0). Same five-section shape as `../api/guidelines.md`, with a Concepts section first.
- **Platform how-to:** [`streaming.md`](streaming.md) — how the standard maps onto AWS Kinesis Data Streams and MSK (UIF's mandated choices), AWS Glue Schema Registry and Data Catalog, event-triggered Anthropic/Claude agents, and — for context and portability — RabbitMQ, Google Cloud Pub/Sub, and Apache Spark.
- **Golden path:** `../../delivery-lab/blueprints/` (event-playbook, next after Sprint 0). Design events during step 8 of `../../architecture-design/operating-model/fde-build-deploy-workflow.md`.
