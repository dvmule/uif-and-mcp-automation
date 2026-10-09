# Echo promotion to UAT — review

**Date:** 2026-09-28 · **Reviewer:** read-only inspection of live AWS resources in
`283556840805` / `eu-west-1`, plus the `echo/v0.10.0` tag manifest and the promotion run.

## Answer

**The echo stack itself is fully promoted. The gold layer is provisioned but not
operational, and there is a single root cause.**

Every compute and storage component reaches parity with dev. What is missing is not
infrastructure — it is the **gold pipeline configuration**, which is data rather than
Terraform, and everything else that looks absent follows from it.

## What is present, at parity with dev

| Component | dev | uat | |
|---|---|---|---|
| Lambda functions | 14 | 14 | ✅ |
| Lambda layers | 4 | 4 | ✅ |
| Glue jobs | 14 | 14 | ✅ |
| Glue triggers | 2 | 2 | ✅ |
| DynamoDB tables | 7 | 7 | ✅ |
| S3 buckets | 7 | 7 | ✅ |
| EventBridge rules | 34 | 34 | ✅ |
| CloudWatch alarms | 24 | 24 | ✅ |
| Gold IAM roles | 7 | 7 | ✅ |

Specifically confirmed for the gold layer:

- All five gold Lambdas are present: `gold_announce`, `gold_dispatcher`,
  `gold_alert_notify`, `gold_serving_sync`, `gold_serving_api`.
- All four gold Glue jobs are present, and their scripts are served from
  **`uif-echo-uat-release-artifacts`** — UAT's own bucket, not dev's. The promotion
  staged artifacts correctly; there is no cross-environment reference.
- All four gold EventBridge rules exist and are **ENABLED**:
  `schedule-gold-dispatch-tick`, `schedule-gold-dispatch-reconcile`,
  `on-gold-succeeded-announce`, `on-gold-succeeded-serve`.
- Gold jobs run as `uif-echo-uat-glue-role`, the standard name — not a legacy role.

## The root cause

**`uif-echo-uat-gold-pipeline-config` holds 0 rows.** dev holds 5 — product, price,
program, stock, marketing_copy.

Everything else follows from that one fact:

```
0 config rows
  -> create_gold_pipeline() never ran for any entity in uat
  -> ensure_gold_database() never ran, so no per-domain gold Glue database exists
  -> the dispatch tick fires on schedule but has nothing to dispatch
  -> gold-transform / gold-reconcile / gold-export have NEVER RUN
  -> uif-echo-uat-gold-serving holds 0 rows (dev holds ~140,257)
```

The per-domain gold databases are **not** created by Terraform. The gold root creates
only `aws_glue_catalog_database.gold_ops`, which is present in UAT as
`uif_echo_uat_gold_ops`. The rest are created by the entity's **first transform run**;
`sync_pipelines` only applies the Lake Formation tag, and on a later pass once the
database exists. (`ensure_gold_database` in `pipeline_controller.py` is the API's
`create_gold_pipeline` path, not the sync's.) Either way no Terraform change is needed.

## Observed gaps, in order of consequence

| # | Gap | Evidence |
|---|---|---|
| 1 | Gold pipeline config empty | `gold-pipeline-config`: 0 rows vs 5 in dev |
| 2 | No per-domain gold Glue databases | dev has `uif_echo_dev_gold_{price,product,program,stock}`; uat has none of them |
| 3 | Gold has never served | `gold-serving`: 0 rows vs ~140,257 |
| 4 | Gold pipelines have never run | `gold-transform`, `gold-reconcile`, `gold-export`: NEVER RUN |
| 5 | Most of UAT is unexercised | 11 of 14 Glue jobs have never run |
| 6 | Two IAM roles absent | `marketing-copy-api-role`, `marketing-copy-sync-role` |

Gaps 2–4 are **symptoms of gap 1**, not independent work.

`gold-summarize` did run, at 2026-09-28 10:00, and reported SUCCEEDED — it is on a Glue
trigger rather than the dispatcher, which is why it alone fired. Treat that success
cautiously: the job has two known blockers (an empty `--day` poisons argparse, and the
Glue role has no `dynamodb:Scan`), so a success can be a silent no-op with `_changes`
failures counted as zeros.

## Legacy naming still present in UAT

UAT carries gold databases under superseded names that dev no longer uses:

- `echo_uat_gold_product` — the pre-`uif-` convention
- `eds_uat_gold_product` — older still
- plus legacy roles `echo-uat-glue-role`, `echo-uat-lambda-role`

Per ADR-0014 these are non-compliant **by design** until an explicit migration, so they
are not a promotion defect. They matter here only because they make UAT *look* like it
has a gold product database when the standard-named one the pipeline expects is absent.

## A separate finding, outside echo

**Domain-level promotion is wired for one domain out of four.** The workflows are:

```
deploy-product-dev.yml   deploy-product-uat.yml   deploy-product-prod.yml
deploy-price-dev.yml     deploy-program-dev.yml   deploy-stock-dev.yml
```

Price, program and stock have **no uat workflow at all**. Whatever is decided about
seeding gold config, those three domains have no promotion path today.

## What the promotion run itself reported

`promote-echo.yml` run 36347802572 on `echo/v0.10.0` succeeded and cut
`release/uat/2026-09-27.1`. Its own notices:

```
the gold root is already at these artifacts — nothing to apply.
the main root is already at these artifacts — nothing to apply.
```

So the promotion applied **no** infrastructure — it found both Terraform roots already
at those artifact SHAs. That is consistent with the estate having been converged by hand
beforehand. It also means the tag alone is not evidence of what is deployed, which is why
this review inspected live resources instead.

Note `echo/v0.9.0`'s promotion **failed** (run 36345696040) immediately before
`v0.10.0` succeeded. Worth a glance if anything looks half-applied.

## Recommendation

**One action closes gaps 1–4: seed the gold pipeline config in UAT.** The databases
the dispatcher then has entities to dispatch, the first transform run creates the Glue
database, and serving populates from that run. No Terraform change, no redeploy.

Before running it, two things need a decision that this review cannot make:

1. **Which entities should UAT carry** — all five as in dev, or a subset? Seeding is what
   makes a gold pipeline live, so this is a deliberate choice about what UAT runs.
2. **Whether UAT should run on a schedule or on demand.** The dispatch rules are already
   ENABLED, so seeding a config row with a cadence will cause UAT gold to start running
   at its due slots without further action.

## Limits of this review

Stated so the green rows are not read as broader than they are.

- Compared by resource-name **suffix** after the `uif-echo-{env}-` prefix. A component
  deliberately named differently in UAT would not have matched — which is how the legacy
  gold database names surfaced.
- **Not compared:** Lake Formation grants, S3 bucket contents and key prefixes, IAM
  policy *contents* (only role existence), Secrets Manager, CloudWatch dashboards and log
  groups, and API Gateway.
- Lake Formation is worth its own check before UAT gold is exercised: gold table access
  in dev runs through the `uif-echo-gold-layer` LF tag, and tag-based grants do not appear
  when listing permissions on a concrete resource.
