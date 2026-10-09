# Event — Streaming & Schema Tooling (the platform how-to)

This is the tool-specific companion to [`guidelines.md`](guidelines.md), the same way `../infrastructure/docker.md` and `../infrastructure/kubernetes.md` are the tool-specific companions to the Infrastructure standard. `guidelines.md` is broker-agnostic; this document tells an FDE exactly how the naming, contract, TTL, and reliable-publish rules map onto **AWS Kinesis Data Streams** and **AWS MSK** — UIF's two mandated choices (`../../CLAUDE.md`: AWS is the mandated cloud) — and, for context and portability, onto the wider industry landscape: **RabbitMQ**, **Google Cloud Pub/Sub**, **Apache Spark**, **AWS Glue**, and event-triggered **Anthropic/Claude-based agents**.

---

## 1. Choosing Kinesis vs. MSK

| | **Kinesis Data Streams** | **MSK (Managed Kafka)** |
|---|---|---|
| Model | Stream of shards | Topic of partitions (Apache Kafka) |
| Best fit | Simple pub/sub, AWS-native fan-out (Lambda, Firehose→S3), moderate consumer-group complexity | High-throughput, many consumer groups, compacted/latest-state topics, exactly-once transactions, teams already on the Kafka ecosystem |
| Ordering | Per-shard, by partition key | Per-partition, by partition key |
| Retention | 24h default, extendable to 8760h (365 days) | Per-topic `retention.ms`/`retention.bytes`; unbounded with log compaction |
| Scaling | Manual/auto shard split-merge; **the owning domain provisions and monitors shard capacity** | Broker/partition count set at cluster and topic creation; partitions can be added, never removed |
| Schema enforcement | Via **AWS Glue Schema Registry** SerDe libraries in producer/consumer (§4) | Via Glue Schema Registry, or a Kafka-native registry (Apicurio, `../schema/`) |
| Operational cost model | Pay per shard-hour + PUT/GET | Pay per broker-hour (cluster is always-on) |

Default to **Kinesis** for a domain's first event type (lower operational surface, fits the "reference-quality then production-quality" principle) and move to **MSK** when a domain needs compacted topics, exactly-once semantics, or many independent consumer groups replaying from arbitrary offsets.

---

## 2. Kinesis Data Streams

**Provision as code (Terraform, under `domains/<domain>/infrastructure/terraform/`):**
```hcl
resource "aws_kinesis_stream" "developer_registered" {
  name             = "product.developer.registered"   # matches the event type, guidelines.md §1
  shard_count      = 2
  retention_period = 168   # hours = 7 days; 24-8760 valid
  stream_mode_details { stream_mode = "PROVISIONED" }  # or ON_DEMAND for unpredictable load
  encryption_type  = "KMS"
  kms_key_id       = aws_kms_key.events.arn
  tags = { domain = "product", env = var.env, owner = "product-fde", managed-by = "terraform" }
}
```
- **Shard/partition key = the aggregate id** (`developerId`), never a random UUID by default (`guidelines.md` §1) — this keeps all events for one aggregate on one shard, processed in order by one consumer.
- **The producing domain owns and monitors its own shard capacity.** Set a CloudWatch alarm on `WriteProvisionedThroughputExceeded`/`ReadProvisionedThroughputExceeded` and scale shards *before* it fires, not after — a stream that starts rejecting writes because it was under-provisioned is the producing domain's outage, not the platform's.
- **TTL = `retention_period`.** Default 24h; raise it (up to 8760h/365 days) only for topics a consumer genuinely needs to replay from further back — every extra day of retention is extra storage cost across every shard.
- **Consume** with the Kinesis Client Library (KCL) for a long-running consumer group with checkpointing, or a Lambda event-source mapping for a lightweight, per-record consumer (a natural fit for triggering an agent — §5).

---

## 3. MSK (Managed Kafka)

**Provision as code:**
```hcl
resource "aws_msk_cluster" "product" {
  cluster_name           = "product"
  kafka_version           = "3.7.x"
  number_of_broker_nodes  = 3
  broker_node_group_info {
    instance_type   = "kafka.m7g.large"
    client_subnets  = var.private_subnet_ids
    security_groups = [aws_security_group.msk.id]
  }
  encryption_info { encryption_in_transit { client_broker = "TLS" } }
}
```
**Topic per event type** (`guidelines.md` §1), created declaratively (Terraform `kafka` provider, or a GitOps-managed topic operator — never `kafka-topics.sh` run by hand):
```hcl
resource "kafka_topic" "developer_registered" {
  name               = "product.developer.registered"
  partitions         = 6
  replication_factor = 3
  config = {
    "retention.ms"   = "604800000"   # 7 days
    "cleanup.policy" = "delete"       # "compact" for a latest-state/event-sourcing topic
  }
}
```
- **Partition key = the aggregate id**, same rule as Kinesis's shard key.
- **TTL = `retention.ms`/`retention.bytes`.** Use `cleanup.policy=compact` instead of a time-based TTL for a topic that represents current state per key (e.g. "latest developer profile") rather than a historical log of facts.
- **Exactly-once** where genuinely required: enable the idempotent producer and use Kafka transactions to atomically write to the topic and commit a consumer offset — reserve this for cases where at-least-once + idempotent-consumer (`guidelines.md` §4) isn't sufficient, since transactions cost throughput.
- **Consumer groups** give you independent replay per consumer — each group tracks its own offset, so a new consumer can be added later and replay from the retention window without affecting existing consumers.

---

## 4. Schema enforcement on AWS: Glue Schema Registry

**AWS Glue Schema Registry** is the AWS-native implementation of Schema Registry First (`../schema/`) for both Kinesis and MSK: producers and consumers link the Glue SerDe library, which validates every message against the registered schema (JSON Schema, Avro, or Protobuf) at serialize/deserialize time — the compatibility mode (BACKWARD/FORWARD/FULL, `guidelines.md` §3) is enforced by the registry itself, not by convention. Register the same `components/schemas` definitions from `asyncapi.yaml` as the registry's schema, exactly as an OpenAPI schema is what a contract test validates against.

**Glue beyond the registry — the cold-storage/archival layer (`guidelines.md` §4 TTL, layer 3).** A Glue crawler runs against the S3 location events are archived to (from a Kinesis Firehose delivery stream or an MSK S3 sink connector) and catalogs it into the **Glue Data Catalog**, making the full historical event archive queryable via Athena — this is how "loss of a message from the live stream" is distinguished from "loss of the fact forever": the live stream's short TTL is an operational window, the Glue-catalogued archive is the long-term, queryable record.

---

## 5. Event-triggered Anthropic/Claude-based agents

An **Agent** module (`../agent/`) consuming an event is the concrete case of "event-triggered agent invocation" in `guidelines.md`'s Concepts table. The wiring is the same regardless of transport:
1. A lightweight consumer (a Lambda on a Kinesis event-source mapping, or a Kafka consumer group member on MSK) pulls the message, validates it against the registered schema (never hands unvalidated payload to the model), and invokes the agent runtime with the event's `data` as context.
2. The agent's own `agent-manifest.yaml` (`../agent/guidelines.md` §2) declares **which event types trigger it**, the same way it declares `allowedTools` — an agent's triggers are governed and reviewable, not wired ad hoc in consumer code.
3. Any event the agent's task itself produces on completion/failure is published back through the normal producer path (§2/§3), following the "agent-emitted" source pattern in `guidelines.md`'s Concepts section — the agent is a peer producer/consumer, not a special case.
4. Treat the model call inside the consumer as the slow, costly step: size the consumer's concurrency/shard count for the model's latency and rate limits, not for the broker's raw throughput — this is usually the true bottleneck for an agent-triggered pipeline.

---

## 6. Wider industry landscape (context and portability, not a UIF choice)

UIF mandates AWS; the rules in `guidelines.md` (naming, envelope, compatibility, idempotency, TTL layering) are written to be broker-agnostic so the same contract discipline holds if a domain's dependency, a partner integration, or a future migration puts one of these in the path.

| Tool | Category | How it maps onto this standard |
|---|---|---|
| **RabbitMQ** (AMQP 0-9-1) | Message broker (queue-oriented, not a log) | Exchange+routing key ≈ topic+partition key; a **dead-letter exchange** is a first-class, built-in mapping of the DLQ requirement (`guidelines.md` §4); per-message/per-queue `x-message-ttl` maps directly to the retention/TTL rule. Better fit than Kinesis/MSK for task-queue-style work distribution than for a replayable event log. |
| **Google Cloud Pub/Sub** | Managed pub/sub (comparable to Kinesis) | Topic+subscription ≈ topic+consumer group; **ordering keys** ≈ partition key; retention up to 31 days ≈ TTL layer 1; attribute-based message filtering ≈ CloudEvents extension-based routing. Illustrative only — not a UIF target, since AWS is mandated. |
| **Apache Spark (Structured Streaming)** | Stream processing, not a broker | The natural **consumer** for Activity/Telemetry streams (`guidelines.md` Concepts) rather than Domain events: reads from Kinesis/MSK, applies windowed aggregation, writes results to the data lake — used when the goal is an aggregate over many messages, not a per-message reaction. |
| **AWS Glue** | Schema registry + catalog/ETL | Covered in §4 above — the concrete AWS mechanism for both Schema Registry First enforcement and cold-storage cataloguing. |
| **Anthropic / Claude-based agents** | Event consumer (application layer, not infrastructure) | Covered in §5 above. |

---

## 7. Deploy permissions — one shared grant per environment, never one per module

Terraform that creates a queue, a topic, an event bus, a connection or a
stream runs as the environment's GitHub Actions deploy role, so that role
needs write access to those services. The grant is scoped by **environment,
not by module**, and it is not event-specific — the same document also
carries the bucket, alarm and role permissions a stack needs. It therefore
lives with the Infrastructure capability — the messaging grants are in
[`../infrastructure/echo-dev-deploy-1.policy.json`](../infrastructure/echo-dev-deploy-1.policy.json)
(its `README.md` explains the split).
**Do not add a per-module policy** — ask for that one to be extended.

Why the rule exists: the first messaging grants on the role were written per
module and scoped to that module's name prefix. The next module's Terraform
then failed with `AccessDenied` on actions the role visibly already had,
because the resource ARNs did not match the other module's prefix — a
confusing failure, and one that invites a near-duplicate policy every time it
happens.

Two things worth knowing when a new event flow is added:

- **An EventBridge connection creates a Secrets Manager secret** for its
  credentials (`events!connection/<name>/<id>`) and needs the
  `events.amazonaws.com` service-linked role. Granting only
  `events:CreateConnection` fails on the next step; both are in the policy.
- **Permissions are enumerated from the Terraform, not discovered one apply
  at a time.** A resource costs more actions than its create call: the
  provider reads every attribute back afterwards, so an S3 bucket needs
  ~18 `Get*` calls it never writes, and tags imply their own pair. When
  adding a resource type, read the `.tf`, list create + read-back + update +
  delete + tag, and check each against the live role with
  `aws iam simulate-principal-policy` before merging.

---

Back to the broker-agnostic standard: [`guidelines.md`](guidelines.md). Schema Registry: `../schema/`. Agents: `../agent/`. The cluster/network substrate these run on: `../infrastructure/`.
