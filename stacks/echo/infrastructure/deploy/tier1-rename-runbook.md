# Tier 1 rename — pre-apply runbook

Applying the `uif-` rename to dev. **This is a maintenance window, not a routine
apply:** it destroys and recreates all 33 EventBridge rules, so Bronze ingestion
stops for the duration.

Everything below is measured from a real `terraform plan` run against
`echo/dev/terraform.tfstate` on 2026-09-09, not estimated.

## What the plan actually says

```
Plan: 178 to add, 12 to change, 178 to destroy      (176 replacements)
```

47 resources were renamed. They cascade to 176 replacements, because dependents
must be recreated when a parent's name changes:

| Renamed | Cascades to |
|---|---|
| 33 EventBridge rules | 33 targets + 35 Lambda permissions |
| 3 IAM roles + 3 policies | 11 Lake Formation grants |
| 1 security group | 5 SG rules |
| 1 bucket (address also renamed) | 22 S3 objects |
| 13 Glue jobs, 5 Lambdas, 4 layers, 1 connection, 2 dashboards, 1 alarm, 1 log group | — |

**Updated in place, NOT replaced** — worth knowing because an earlier draft of
this plan claimed otherwise:

- **`aws_apigatewayv2_api.ingestion`** — id `dqtw191jq9` survives, so
  **the invoke URL does not change** and the UI does not need rebuilding for it.
- **`aws_cloudfront_distribution.ui`** — only the comment changes.
- The three account-wide `/aws-glue/jobs/*` log groups, and the gold buckets.

## Before you start

- [ ] **Credentials.** `aws-runas dev` → account `283556840805`, `PCMPowerUser`.
      Note this config is `aws-runas`-shaped: plain `aws --profile dev` cannot
      resolve `source_profile=saml`.
- [ ] **The four layer zips are staged** in `deploy/layers/`. `filemd5()` runs at
      plan time, so a missing zip aborts before anything happens — but a *stale*
      one silently overwrites the known-good S3 copy on apply. Verify against S3
      by byte size, not ETag (three are multipart, so their ETag is not an MD5).
- [ ] **Nobody else is applying.** `use_lockfile = true`, so the lock is an S3
      object; a concurrent workstation apply will collide.
- [ ] **Decide the Lake Formation drift first — see below.**

## The Lake Formation drift — DECIDED: accept the narrowing

**Decision, 2026-09-09 (owner): accept it.** No config change; the apply narrows
those three grants to what `lakeformation.tf` declares. Nothing to do before the
window — this section is kept so the reduction is a recorded choice rather than a
surprise in the plan output.

The detail, for whoever reads the plan:

Three grants show as replaced *before any migration*. Verified by planning the
original `uif-relay-echo` tree against the same state: it produces the identical
3 replacements.

```
aws_lakeformation_permissions.analyst_gold_product_db[0]
aws_lakeformation_permissions.analyst_silver_fipm_db[0]
aws_lakeformation_permissions.analyst_silver_product_db[0]
```

AWS currently holds `ALTER` and `DROP` on those databases for `PCMPowerUser`, plus
several `permissions_with_grant_option`, which the Terraform config does not
declare. Someone granted them out of band.

**Applying will narrow them to what the config says.** That is probably correct,
but it is a real permission reduction for the analyst role and it is unrelated to
this rename. **Decide deliberately:** either accept the narrowing, or add the
grants to `lakeformation.tf` first so the apply is a no-op for them. Do not
discover this mid-window.

## The ingestion pause — and the window, from the actual config

All 33 EventBridge rules are destroyed and recreated. Between destroy and create
no schedule exists, so nothing fires.

An earlier version of this runbook said "avoid 05:30–12:00 UTC". That was
guesswork from the cron expressions. What matters is which frequencies have
**enabled pipelines**, and the config table says far fewer than the rules imply.

Read from `echo-dev-glue-job-config` on 2026-09-09 — **20 of 75 configs are
enabled**:

| Enabled | `schedule.frequency` | Fires |
|---|---|---|
| 14 | `4hr` | 00:00, 04:00, 08:00, 12:00, 16:00, 20:00 UTC |
| 4 | `hourly` | every hour at :00 |
| 1 | `daily` slot 11 | 11:00 UTC |
| 1 | `12 hr` | **never — see below** |

So **24 of the 33 rules currently dispatch nothing**: the `30min` rule, eleven of
the twelve Bronze daily slots, and all twelve reconcile slots. Renaming those is
operationally inert, which makes this window much less risky than the raw
replacement count suggests.

**Recommended window: start at about :05 past an hour that is not a 4-hour
multiple and is not 11:00.** So 13:05, 14:05, 15:05, 17:05, 18:05, 19:05 UTC.
That buys roughly 55 minutes before the next `hourly` tick and avoids the 14
`4hr` pipelines entirely.

If the apply overruns and misses a tick, that is **not a lost run**: watermarks
are untouched and the next tick resumes exactly where it left off. The cost is a
freshness gap, plus the confusion of a scheduled run that never started.

### Two things the config revealed, worth fixing separately

**One enabled pipeline has `frequency = "12 hr"`, which matches no rule.** There
is no 12-hour EventBridge rule, and the string carries a space. That pipeline is
enabled and has never been dispatched. Not caused by the rename, and not fixed by
it.

**All 5 enabled Gold pipelines use `frequency = "gold-24h"`**, which matches none
of Echo's three Gold rules (`gold`, `gold-hourly`, `gold-reconcile`). That is
consistent with **ADR-0004 CP-2**, which moved Gold's dispatcher into
`uif-dal-gold` — and with **CP-5**, whose cutover runbook says the platform team
should *disable their three Gold EventBridge rules and remove Gold from their
Terraform state*. Those three rules are still here, dispatching nothing. This
rename is a good moment to ask whether they should exist at all rather than be
renamed.

### Optional: capture rule state first

Recreated rules take their `state` from config, so any manual disabling is lost.

```bash
aws-runas dev aws events list-rules --region eu-west-1 --name-prefix echo-dev- \
  --query 'Rules[].{name:Name,state:State}' --output table
```

## Apply

```bash
cd stacks/echo/infrastructure/terraform
aws-runas dev terraform init -backend-config=backends/dev.hcl
aws-runas dev terraform plan -var-file=envs/dev.tfvars -out=tier1.tfplan

# READ IT. Confirm ~176 replacements and that nothing unexpected is in there -
# in particular no aws_dynamodb_table and no bronze/silver/gold bucket. Those
# hold data and are pinned to legacy_name_prefix; if they appear, stop.
aws-runas dev terraform show tier1.tfplan | grep -E 'must be replaced|will be destroyed'

aws-runas dev terraform apply tier1.tfplan
```

Applying a saved plan file rather than re-planning is deliberate: it guarantees
you apply exactly what you read.

## After

- [ ] **All 33 rules exist and are ENABLED.**
      `aws events list-rules --name-prefix uif-echo-dev-schedule-`
      Expect 8 fixed-cadence + 12 bronze slots + 12 reconcile slots + 2 `on-` event rules.
- [ ] **The Lambdas carry the new prefix.** This is the one that breaks silently:
      `echo_resources.py` now reads `RESOURCE_PREFIX` from the environment, so a
      Lambda that kept its old env vars will look for resources that no longer
      exist.
      ```bash
      for f in dispatch pipeline-controller replicate-gcs replicate-bq dq-check-controller; do
        aws-runas dev aws lambda get-function-configuration \
          --function-name "uif-echo-dev-$f" \
          --query 'Environment.Variables.[RESOURCE_PREFIX,LEGACY_RESOURCE_PREFIX]' --output text
      done
      ```
      Expect `uif-echo-dev   echo-dev` for all five.
- [ ] **The DynamoDB tables are untouched** and still hold their rows — they are
      pinned to `legacy_name_prefix` precisely so this rename cannot reach them.
      ```bash
      aws-runas dev aws dynamodb describe-table --table-name echo-dev-glue-job-config \
        --query 'Table.ItemCount'
      ```
- [ ] **Trigger one pipeline end to end** before declaring it done. A rename that
      leaves every resource present but mis-wired looks identical to success from
      the console.
      ```bash
      py src/apps/cli/adhoc_run.py --pipeline-id <one> --env dev
      ```
- [ ] **Re-sync the Glue scripts.** Terraform tracks them with `filemd5()` but
      does not upload them, and the bucket is new and therefore empty of them:
      ```bash
      aws-runas dev aws s3 sync src/apps/pipeline/ s3://uif-echo-dev-artifacts/glue_jobs/ \
        --exclude "*" --include "*.py" --delete
      ```
      **Do this before triggering anything** — the Glue jobs point at
      `s3://uif-echo-dev-artifacts/glue_jobs/…` and will fail on a missing script.

## If it goes wrong

There is no clean rollback: the old names are gone from state once destroyed.
Recovery is to revert the code and apply again, which recreates the
`echo-dev-*` names. That works because Tier 1 holds no data — which is the whole
reason it went first.

The two things that would genuinely hurt, and neither is in this plan: a
DynamoDB table (pinned) or a lake bucket (untouched). If either appears in a plan
you are about to apply, something has gone wrong with the `legacy_name_prefix`
wiring — stop and re-read `locals.tf`.
