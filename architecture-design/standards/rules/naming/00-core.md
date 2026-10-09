---
paths:
  - "**/infra/**/*.tf"
  - "**/infra/**/*.tfvars"
  - "**/infrastructure/**/*.tf"
  - "**/infrastructure/**/*.tfvars"
  - "**/drafts/**/*.json"
  - "**/glue_jobs/**/*.py"
  - "**/lambda/**/*.py"
  - "**/schemas/**"
  - "**/scripts/*.py"
  - "stacks/*/infrastructure/**"
  - "stacks/*/src/**"
  - "stacks/*/domains/**"
  - "domains/*/infrastructure/**"
---
# UIF naming — core vocabulary

Name every **newly created** resource `uif-{stack}-{env}-{operation}`: `uif-`, then the
stack that owns it, then the environment, then what the resource does. Per-resource
forms live in the sibling rule files; this file is the vocabulary they all share.

✅ `uif-echo-dev-bronze-ingest`, `uif-beacon-dev-export`, `uif-echo-dev-pipeline-config`
❌ `echo-dev-bronze-ingest` (no framework prefix) · `uif-echo-bronze-ingest` (no env)

## Domains do not get a `{stack}` token — they name through the stack that runs them

Decided 2026-09-16, unblocking UIFPE-427/428/429 (Price, Program, Stock — each currently
zero infrastructure). **A domain's Gold-layer resources use the `{stack}` token of
whichever stack's Glue jobs actually run them, with the domain appearing as a qualifier
inside the name — never as the `{stack}` slot itself.**

Confirmed by the one domain that already has real infrastructure. `domains/product/infrastructure/terraform/locals.tf`:

```
uif_prefix = "uif-echo-${var.environment}"   # the standard's stem, for resources we own
```

That is Product's own comment about its **own** resources — not a lookup into Echo's
shared platform. Product's Gold pipeline runs as Echo's Glue jobs, so even resources
Product owns outright take the `echo` stem. `catalog.md`'s Glue-database form already
encodes exactly this: `uif_{stack}_{env}_{layer}_{domain}` → `uif_echo_dev_gold_product`.
`{stack}` is `echo`; `product` is a `{domain}` qualifier, one level in.

**For Price, Program and Stock:** if their new Terraform provisions a Gold pipeline that
Echo's Glue jobs run — which is the case today, and what UIFPE-427/428/429 model on
Product's Terraform — their resources take `uif-echo-{env}-...-{price|program|stock}`,
the same shape as `uif_echo_dev_gold_product`. **`price`, `program` and `stock` are not
added to the `{stack}` set below.** They are not stacks; they are domains whose Gold
layer is operated by one.

This only changes if a domain's infrastructure stops being run by any existing stack —
that is a new architecture decision, not a naming one, and is not what these three
tickets are building today.

## Legacy names — do not rename

Existing `echo-{env}-*` resources predate this convention. **Never change the `name`
of a deployed resource in Terraform** — it forces replacement, and for S3 buckets,
DynamoDB tables, SQS queues, ECR repositories and Secrets Manager secrets that means
data loss. Apply this convention to newly created resources only. Renames happen
through an explicit, planned migration.

Non-compliant live names are non-compliant **by design** until migrated. Do not offer
to fix them.

## Vocabularies

| Slot | Values |
|---|---|
| `stack` | `apicurio, beacon, echo, event, harbor, orchestrator` — the module that owns the resource |
| `env` | `dev, prod, uat` |
| `layer` | `bronze, silver, gold` — medallion layers only |
| `operation` | `ingest, transform, reconcile, quality-check, analyze, replicate, dispatch, deliver, control, export` — illustrative, not closed like `cadence`; name what the resource does even if the exact word isn't listed (e.g. `probe`, `strip-api-prefix`) |
| `service` | the workload that assumes or uses the identity — the thing being granted access, not the engine it runs on. Where a stack has exactly one identity for an AWS service, the service name *is* the workload (`glue`). Illustrative, not closed, like `operation`. |
| `purpose` | what the resource holds, fronts or attaches to — a noun, not a verb: `ui`, `api`, `glue-scripts`, `oracle`. Illustrative, not closed. |
| `target` | `s3, gcs, bq` — always explicit, no unmarked default |
| `cadence` | `30min, 4hr, daily, hourly` — closed. Flag `weekly`, `nightly`, `2hr` as outside the vocabulary rather than inventing a token. |
| `instance` | `slot-NN` — zero-padded to two digits |

✅ `uif-echo-dev-schedule-bronze-ingest-daily-slot-00`
❌ `uif-echo-dev-schedule-bronze-ingest-nightly` · `...-slot-0`

`apicurio` joined the `stack` slot on 2026-09-08 by owner decision: `stacks/apicurio/` is a
stack by ADR-0012, and `stacks-ci.yml` already detects it from the folder name. Note the
route it took — **a new stack directory is not a valid `{stack}` token until it is added to
this list.** That gate is deliberate, not an oversight: adding a directory and adding a token
are two decisions, which is why `event` sits in the slot list with no stack directory at all.

`orchestrator` joined the `stack` slot on 2026-10-03 with `stacks/uif-orchestrator/` (the UIF A2A
orchestrator, ADR pending). The directory is `uif-orchestrator` because that is the deployable's name;
the token is `orchestrator` because the pattern is `uif-{stack}-{env}-…` and a hyphenated token would
put a second `uif` in every name (`uif-uif-orchestrator-dev-cluster`) and never parse as a stack slot.
Resources: `uif-orchestrator-dev-cluster`, `uif-orchestrator-dev-ecr-repo`, `uif-orchestrator-dev-alb`.

Adding a token here is also only half the change. `hooks/validate_naming.py` enforces this
slot from its own `STACKS` set: the 2026-09-08 decision updated this file alone, so for six
days the guard denied every `uif-apicurio-*` name as an unknown stack while the standard said
it was sanctioned. `standards-ci.yml` did check the pair, but in one direction only — that
every token the hook enforces is documented here — which this case passed. Both directions are
now asserted, so the same drift fails CI. Update both files together, and use
`installers/install-project.sh --check` to see whether a given repo's *installed* copies agree
with the package.

## Throwaway namespaces — labs and POCs

A lab or proof-of-concept under `delivery-lab/` creates real cloud resources it intends to
throw away. Those resources must be impossible to mistake for the estate — a person (or a
cleanup script) looking at one has to know at a glance that deleting it breaks nothing.

The rule is one reserved token, in the `{env}` slot, both ways:

- **Inside `delivery-lab/`**, the env slot is always `poc`. The real environment tokens
  (`dev`, `uat`, `prod`) are banned there — a lab that names itself into a real environment
  is a collision waiting to happen.
- **Everywhere else**, `poc` is rejected like any unknown env. It is deliberately absent
  from the `env` row in the slot table above: it is not an environment, it is a declaration
  that nothing may depend on the resource.

✅ `uif-event-poc-orders-bus` (in a lab) · ❌ `uif-event-dev-orders-bus` (in a lab) ·
❌ `uif-echo-poc-dispatch` (in the estate)

What `poc` declares: the resource is out of the estate, referenced by no estate code, carries
no data anyone will miss, and may be deleted without notice. A lab that outgrows that
declaration is no longer a lab — it moves into a stack or domain and takes a real name.

Decision record: UIFPE-473 (2026-09-21). This turns a convention that lived in one Terraform
variable description into an enforced rule; the guard inspects `delivery-lab/` for exactly
this check and no other.

## Three slots that are easy to confuse

`operation`, `service` and `purpose` occupy the same position in different forms, so the
wrong one produces a name that reads fine and means something else. One question each:

| Slot | Question it answers | Shape |
|---|---|---|
| `operation` | what does it do? | a verb — `transform`, `dispatch`, `ingest` |
| `service` | whose identity is it? | the workload being granted access — `glue`, `deliver` |
| `purpose` | what does it hold or front? | a noun — `ui`, `api`, `glue-scripts`, `oracle` |

A load balancer in front of an API is `uif-echo-dev-api-alb`, not `uif-echo-dev-serve-alb`
— a balancer holds no verb. A role a delivery function assumes is
`uif-beacon-dev-deliver-role`, not `uif-beacon-dev-lambda-role` — the engine is not the
workload.

The same word legitimately appears in more than one slot, because the slots ask different
questions about different resources. A function named for what it does is
`uif-beacon-dev-deliver` (`operation`); the role it assumes is
`uif-beacon-dev-deliver-role` (`service`, naming that workload). That is coherence, not
collision.

## Banned tokens — no phase, ticket, status or version words

Never use: `batch2`, `fix12`, `new`, `old`, `phase0`, `spike`, `tmp`, `v2`, or ticket
keys such as `UIFPE-254`.

✅ `uif-echo-dev-probe-gcs-iceberg` ❌ `uif-echo-dev-phase0-iceberg-gcp-probe`

Exception: EventBridge and SQS subscription names carry a `{version}` token to match the
registry — see `eventing.md`.

## Delimiters — the platform decides

| Delimiter | Form | Used by |
|---|---|---|
| hyphen | `uif-echo-dev-silver-transform-bq` | most AWS resources |
| underscore | `uif_echo_dev_silver_tep` | Glue catalog databases and tables |
| slash | `uif/echo/dev/db/tep` | Secrets Manager, S3 key prefixes |
| platform-prefixed | `/aws/lambda/uif-echo-dev-dispatch` | CloudWatch log groups |
| upper-underscore | `UIF_ECHO_DEV_SILVER_IPM` | BigQuery datasets — retained, not tabulated (see `catalog.md`) |

## Length limits

AWS platform caps, not convention choices. The 32-char ALB and target-group limits and
the 63-char bucket limit bite first — count before committing to a name, and shorten the
`operation`, never drop `env`.

| Artefact | Limit | Artefact | Limit |
|---|---|---|---|
| ALB, ALB target group | 32 | Glue job, Glue connection, Glue catalog table, ECS, DynamoDB, Glue database, Security group, VPC endpoint, CloudWatch dashboard, CloudWatch alarm | 255 |
| S3 bucket, **RDS instance identifier** | 63 | ECR repo, SNS topic, EventBridge bus | 256 |
| EventBridge rule, IAM role, Lambda, CloudFront function, **CloudFront origin access control** | 64 | Secrets Manager secret, CloudWatch log group | 512 |
| Step Functions, SQS queue | 80 | API Gateway, IAM policy — managed and inline | 128 |

Three names have **no cap documented by AWS**: DB subnet group, API Gateway VPC link and API
Gateway authorizer. None is asserted here or in the hook, on purpose — an invented cap either
rejects a legal name or enforces nothing, and both are worse than an honest blank. The stem
and suffix are still enforced on all three.

An RDS instance identifier carries four constraints the convention does not express: the
first character must be a letter, no trailing hyphen, no two consecutive hyphens, and the
value is stored lower-case. Every compliant `uif-` name satisfies them.
