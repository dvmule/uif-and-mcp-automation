# Design Spec: UIF naming standards refresh — rules and hook

**Date:** 2026-09-08
**Author:** Vaibhav Yenorkar
**Status:** Proposed — awaiting review before implementation

---

## Purpose

Bring `architecture-design/standards/` back in line with the current **UIF Resource Name Lookup**, which is now the official naming standard. The rule files and the `PreToolUse` hook were derived from an earlier convention and have drifted: three of their assertions are now the opposite of the standard, roughly twenty-five resource types have no coverage at all, and one hook check actively denies edits that the new standard requires.

Scope is the standards package only — the five rule files, the hook, the installer metadata, the setup guide, and the governance record. No deployed resource is renamed and no domain or stack code is touched.

---

## Decisions taken

| # | Decision |
|---|---|
| D1 | Rule files are re-shaped to mirror the standard's own six groups, so a table row maps to a rule file. |
| D2 | The hook's scattered constants become one `PATTERNS` table covering every resource type, with a test asserting rules and hook agree. No generator, no build step. |
| D3 | BigQuery and GCS rules are **preserved**, marked as not-yet-tabulated rather than authoritative. |
| D4 | Banned tokens, the closed cadence set, zero-padding and the Appendix A length limits are **kept**. |

D3 and D4 cover ground the lookup table does not address. Neither contradicts it; deleting either would leave real resources ungoverned or let the hook approve names AWS rejects.

---

## What changed

### Direct conflicts — the rules currently assert the opposite

| Subject | Rules today | Standard now | Where |
|---|---|---|---|
| S3 bucket form | account id first: `283556840805-s3-uif-echo-dev-ui` | `uif-echo-dev-ui` — no account prefix | `00-core.md:19`, hook `check_hyphen_name` |
| Secrets path | `uif/echo/dev/gip/db/tep` | `uif/echo/dev/db/tep` — no `gip` segment | `secrets.md`, `00-core.md:57` |
| DynamoDB table | `uif-event-dev-glue-job-metrics`; `glue-job` a valid subject | `uif-echo-dev-pipeline-metrics` — name the concept, not the engine | `00-core.md:17,43` |
| Silver Glue database | keyed by domain: `uif_echo_dev_silver_product` | keyed by source system: `uif_echo_dev_silver_tep` | `catalog.md:20,28` |
| Data-lake prefixes | `{bucket}/{domain}/{table}/` | three distinct shapes, see below | `data-lake.md` |

### The data-lake prefix change

The old rules had one two-segment shape per cloud. The standard now has three shapes, distinguished by layer:

```
Bronze   {bucket}/{source_system}/{data_domain}/{table}/_extract_date=YYYY-MM-DD/
Silver   {bucket}/{source_system}/{data_domain}/{table}/
Gold     {bucket}/{entity}/
```

Bronze gains a leading `{source_system}` segment and an explicit Hive-style partition segment; Gold drops the domain entirely.

### The blocking defect

`validate_naming.py` `check_paths()` rejects any S3 path segment containing an underscore:

```python
bad = [s for s in segs if "_" in s or s != s.lower()]
```

The Bronze partition segment `_extract_date=2026-09-04` trips it, so **the hook denies a correctly-named new Bronze path today.** This is the highest-priority item in the plan — it blocks compliant work, not merely permits stale work.

### Resource types with no rule or hook coverage

Compute and ETL: Glue connection (network, per-source), Lambda layer, Lambda controller form, ECS cluster / service / task definition / container, ECR repository, Step Functions state machine.

Eventing: EventBridge bus, EventBridge event-subscription rule, SQS queue and `.fifo` variant, SQS subscription queue, SQS dead-letter queue as a `-dlq` suffix, SNS topic.

Networking and edge: ALB, ALB target group, API Gateway HTTP API, CloudFront distribution, CloudFront function, security group, VPC endpoint.

Identity: IAM role and policy forms (the `-role` / `-policy` pairing).

Observability: CloudWatch log group, dashboard, alarm.

Storage: the three S3 bucket sub-forms, and **DynamoDB attribute naming** — an entirely new category, described below.

### DynamoDB attribute conventions — a new kind of rule

The standard now governs attribute names inside items, not just resource names:

| Kind | Form | Examples |
|---|---|---|
| scalar | `{concept}_{type}` with type from a closed set — `_id _name _arn _path _col _at _date _count _mode` | `pipeline_id`, `last_processed_at`, `watermark_col` |
| map | `{concept}`, singular, no suffix | `extract`, `silver`, `reconcile` |
| list | `{concept}_{type}s` — plural marks the list, the suffix names the element | `primary_key_cols`, `date_cols`, `tags` |
| boolean | bare predicate, no `is_` or `has_` | `enabled`, `dry_run` |

This is data-model guidance, not resource naming, and it has no check surface today.

---

## Target structure — rule files

Nine files. The standard's six groups, plus core, plus the two that need their own path scoping.

| File | Covers | Loads for |
|---|---|---|
| `00-core.md` | stem, stack and env vocabularies, delimiter table, token order, banned tokens, cadence set, zero-padding, length limits, legacy freeze | any UIF file |
| `compute-etl.md` | Glue jobs and connections, Lambda functions and layers, ECS family, ECR, Step Functions | `**/infra/**/*.tf`, `stacks/*/infrastructure/**/*.tf`, `domains/*/infrastructure/**/*.tf` |
| `storage.md` | S3 buckets (data lake, supporting, UI), the three key-prefix shapes, DynamoDB tables | `*.tf`, `*.tfvars`, `**/drafts/**/*.json`, `stacks/*/domains/**/*.json` |
| `catalog.md` | Glue catalog databases (Silver by source system, Gold by domain), Glue and Iceberg tables, `api_{entity}_view`, preserved BigQuery forms | `**/glue_jobs/**/*.py`, `**/schemas/**`, `**/scripts/register_*.py`, `stacks/*/src/**/*.py` |
| `eventing.md` | EventBridge bus and the three rule kinds, SQS and its three forms, SNS | `*.tf` |
| `networking.md` | ALB, target group, API Gateway, CloudFront distribution and function, security group, VPC endpoint | `*.tf` |
| `identity-secrets.md` | IAM role and policy forms, Secrets Manager slash form | `**/infra/iam*.tf`, `stacks/*/infrastructure/**/iam*.tf`, `**/scripts/*secret*.py`, `**/lambda/**/*.py` |
| `observability.md` | CloudWatch log group, dashboard, alarm | `*.tf` |
| `dynamodb-attributes.md` | the four attribute conventions above | `**/drafts/**/*.json`, `stacks/*/domains/**/*.json`, `**/schemas/**` |

**Why `catalog.md` stays separate rather than folding into `compute-etl.md`,** which is where the standard groups it: the two have different file surfaces. Catalog and dataset names appear in Python and schema files; ECS and Glue-job names appear in Terraform. Merging them would load Terraform guidance for a Glue job author and table-naming guidance for a Terraform author. This is a deliberate deviation from strict 1:1 with the standard's grouping, made for path-scoping, and it is the only one.

`aws-resources.md` and `data-lake.md` are **removed** — their content redistributes into `compute-etl.md`, `storage.md`, `eventing.md`, `networking.md` and `observability.md`. Because the installer replaces `rules/naming/` rather than merging, deleted files disappear from `~/.claude/` on the next install with no manual cleanup.

---

## Target structure — hook

### One `PATTERNS` table

Today the hook holds four constants (`STACKS`, `ENVS`, `CADENCES`, `LIMITS`) plus per-check logic. Replace with one table keyed by Terraform resource type, each entry declaring: delimiter form, required suffix if any, length limit, whether a rename is destructive, and the rule file to cite in the denial message.

Resource types to cover: `aws_glue_job`, `aws_glue_connection`, `aws_glue_catalog_database`, `aws_glue_catalog_table`, `aws_lambda_function`, `aws_lambda_layer_version`, `aws_ecs_cluster`, `aws_ecs_service`, `aws_ecs_task_definition`, `aws_ecr_repository`, `aws_sfn_state_machine`, `aws_s3_bucket`, `aws_dynamodb_table`, `aws_cloudwatch_event_bus`, `aws_cloudwatch_event_rule`, `aws_sqs_queue`, `aws_sns_topic`, `aws_lb`, `aws_lb_target_group`, `aws_apigatewayv2_api`, `aws_cloudfront_function`, `aws_security_group`, `aws_vpc_endpoint`, `aws_iam_role`, `aws_iam_policy`, `aws_secretsmanager_secret`, `aws_cloudwatch_log_group`, `aws_cloudwatch_dashboard`, `aws_cloudwatch_metric_alarm`.

### Length limits to add

The current table has ten entries. Missing, and the two tightest are new:

| Resource | Limit |
|---|---|
| `aws_lb` | **32** — newly revealed by the standard's own note |
| `aws_lb_target_group` | **32** |
| `aws_sfn_state_machine` | 80 |
| `aws_cloudfront_function` | 64 |
| `aws_sns_topic`, `aws_ecr_repository`, `aws_cloudwatch_event_bus` | 256 |
| `aws_ecs_cluster`, `aws_ecs_service`, `aws_security_group`, `aws_glue_connection`, `aws_cloudwatch_dashboard`, `aws_cloudwatch_metric_alarm` | 255 |
| `aws_cloudwatch_log_group` | 512 |

### Rename guard — expand from two types to eight

`RENAME_GUARDED` currently covers `aws_s3_bucket` and `aws_glue_job`. Rename is equally destructive or replacement-forcing for `aws_dynamodb_table` (data loss), `aws_glue_catalog_database` (metadata loss), `aws_secretsmanager_secret` (breaks every IAM statement and stored ARN), `aws_sqs_queue` (in-flight messages), `aws_ecr_repository` (images), and `aws_lb` (replacement). Each gets its own denial reason rather than one shared message.

### Path checks — rewrite

Replace the single kebab/underscore test with a layer-aware grammar:

1. Determine layer from the field (`target.s3_path` → Bronze, `silver.s3_path` → Silver) or from the bucket's own `{layer}` token.
2. Validate segment count and shape against that layer's form.
3. **Allow Hive-style partition segments** matching `^_[a-z_]+=.+$` — this is the fix for the blocking defect.
4. Keep the GCS snake_case / Oracle-schema check under D3, marked as preserved rather than tabulated.

### DynamoDB attribute names — rule only, no hook check yet

Enforcing the four attribute conventions against arbitrary JSON keys would produce false positives on every draft: a hook that denies legitimate edits is worse than one that misses. The conventions ship as `dynamodb-attributes.md` context so Claude generates conforming names; mechanical enforcement is a follow-up once the false-positive rate is understood on real drafts. Recorded as out of scope below, not forgotten.

---

## Anti-drift test

`architecture-design/standards/hooks/test_validate_naming.py`. Three assertions:

1. Every resource type in `PATTERNS` is named in at least one rule file.
2. Every pattern string in a rule file's fenced blocks appears in `PATTERNS`.
3. `STACKS`, `ENVS`, `CADENCES` and the suffix set each match the vocabularies written in `00-core.md`.

Plus behavioural cases for the defect being fixed: a compliant Bronze path with a partition segment must pass; the three conflicting old forms must now fail.

This is what converts the drift we found from a discovery into a test failure. Note that `CLAUDE.md` bars executable code from `architecture-design/`; `validate_naming.py` is the standing exception, and a test beside it is consistent with that exception rather than a new one.

**How it actually gets run is an open item.** `pytest.ini` sets only `--import-mode=importlib` — it declares no `testpaths`, and the repo's convention is explicit per-suite invocation (`py -m pytest domains/product/qe domains/program/qe`). So nothing collects this test automatically. Worse, no existing workflow would fire on it: `standards-review.yml` is scoped to `domains/**`, is advisory-only, and is currently inert — its `ANTHROPIC_API_KEY` is absent and per the framework owner (2026-08-28) the org cannot provision it for the foreseeable future, so the step skips entirely.

The plan therefore needs one of: a path filter for `architecture-design/standards/**` added to `domain-ci.yml` or `stacks-ci.yml`, or a small dedicated job that runs this one test file. Without that, the anti-drift guarantee is only as strong as someone remembering to run it — which is the same failure mode that produced the current drift. **This choice belongs in the implementation plan, not deferred.**

---

## Version, distribution, governance

**Version.** `VERSION` goes `1.0.0` → `2.0.0`. Breaking by any reading: names that were compliant are no longer, and the hook's verdicts change.

**Setup-Guide.md.** The contents table gains four rows and loses two. Troubleshooting examples reference `aws-resources.md` by name and must be re-pointed. The "what the hook refuses" list needs the expanded rename guard.

**Distribution.** Install is manual (`git pull && ./installers/install.sh`), with no CI enforcement, so every engineer keeps 1.0.0 until they re-run it. The installer already writes `.installed-version`; the plan should add an announcement step and a note in the guide that 1.x rules will assert the wrong S3 and secrets forms until updated.

**Governance.** `standards/README.md` requires an ADR for any change to a standard here. `0013` is taken, so this is **ADR-0014**, recording that the lookup table is now the standard, the three superseded assertions, and the D3/D4 preservation decisions.

---

## Out of scope

- Renaming any deployed resource. The standard's own footer freezes `echo-{env}-*` names; the legacy prohibition stays.
- Migrating existing non-compliant names. That is a planned migration, not this change.
- Hook enforcement of DynamoDB attribute names — rule-only for now, per the reasoning above.
- Reconciling the source `.docx` files. The artifact is the standard; the documents behind it are not in this repository.
- BigQuery and GCS becoming authoritative. They are preserved as-is pending an owner decision.

---

## Risks and open questions

**S3 bucket global uniqueness — resolved 2026-09-08.** Dropping the account-id prefix means a bucket name must be globally unique across all of AWS, not merely within the account, and the account-id prefix in the old rule is the conventional remedy for exactly that. Raised with the standard's owner and **confirmed deliberate**: the `uif-{stack}-{env}-` stem carries the uniqueness. No account segment, and no collision suffix if one ever occurs — escalate instead.

**ALB and target-group names have 14 characters of headroom.** `uif-{stack}-{env}-{purpose}-alb` against a 32-char cap leaves little for `{purpose}` once the stack is `beacon` and the env is `prod`. Worth a note in `networking.md` rather than a surprise at apply time.

**Preserved BigQuery and GCS rules are now unsourced.** They govern real resources but appear in no current standard. They need an owner decision, and until then the rules should say plainly that they are retained rather than ratified.

**Engineers on 1.0.0 get actively wrong guidance.** Not merely stale: the old rules assert the account-id S3 form and the `gip` secrets segment, both now incorrect. The announcement matters more than it usually would.

**The anti-drift test has no automatic runner today.** See the note under Anti-drift test. A test nobody runs prevents nothing, and the drift being fixed here is precisely what an unrun check allows.

**The hook cannot see Terraform interpolation.** `check_hyphen_name` already skips any value containing `${`, and much of the new estate is built from `local.name` or `var.name_prefix` rather than literals. The expanded coverage therefore reaches fewer real names than the pattern count suggests. Not a regression, but it should temper expectations about how much the hook actually catches — the rule files remain the primary mechanism, the hook a backstop for literals.

---

## Success criteria

1. A compliant new Bronze path with a `_extract_date=` partition segment passes the hook.
2. Each of the three superseded forms — account-prefixed bucket, `gip` secrets path, `glue-job-metrics` table — is rejected for a new resource and still tolerated where it already exists on disk.
3. A new name for each of the ~25 previously uncovered resource types is checked against its pattern and its length limit.
4. The anti-drift test passes, and fails if a pattern is added to the hook without a rule file, or vice versa.
5. `VERSION` is `2.0.0`, `Setup-Guide.md` describes the nine files accurately, and ADR-0014 records the change.
6. No deployed resource name changes anywhere in the repository.
