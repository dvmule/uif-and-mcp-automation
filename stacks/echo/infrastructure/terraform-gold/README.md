# terraform-gold — the gold layer's Terraform root

The infrastructure behind the silver→gold pipeline, owned by Echo per
[ADR-0018](../../../../architecture-design/decisions/0018-gold-runtime-is-a-stack-concern.md):
the two Glue jobs and their scripts, the dispatcher and its schedule rules, the
Transform UI's four Lambdas, the gold pipeline config table, the gold data and
artifact buckets, and the grants the shared Glue role needs on them. Every
resource carries the UIF standard name, `uif-echo-{env}-…`.

It is a separate root from `../terraform` (Echo's own stack) so the gold layer
deploys on its own cadence. Two things stay the platform stack's and are looked
up by name here: the shared Glue role `echo-{env}-glue-role` and the scripts
bucket `echo-{env}-glue-scripts`, where our scripts live under `gold/`.

State: `s3://pearson-ingestion-framework-tfstate-283556840805/uif-echo-gold/{env}/`.
Deploy: `.github/workflows/deploy-echo-gold-dev.yml`, driven by the repo
variable `GOLD_INFRA` (skip | plan | apply). The deploy also builds the
Transform UI, reading the four Lambda URLs from this root's outputs.

Three things this root cannot create and expects to exist: the Lake Formation
tags `uif-echo-gold-layer` and `uif-echo-silver-layer` (values are the
environment names), and the Secrets Manager secret
`uif/echo/{env}/submit/github`, whose value is a GitHub token set by hand.

A tag key needs `lakeformation:CreateLFTag`, which the deploy role does not
hold, so both tags are created once by an LF admin — see the one-time setup
in `capabilities/data/gold-control-plane.md`.

## Daily statistics (UIFPE-544)

`ops_stats.tf` holds the nightly statistics: a Glue database
`uif_echo_{env}_gold_ops`, its Iceberg table `daily_stats` (one row per gold
entity per day, partitioned by month, under `s3://uif-echo-{env}-gold/daily_stats/`),
the job `uif-echo-{env}-gold-summarize`, and a **Glue trigger** that runs it at
04:30 UTC — an hour after the nightly reconcile, so the day being summarised has
all of its rows. EventBridge has no native StartJobRun target and the deploy
role holds no `scheduler:*`, which is why the schedule is a Glue trigger rather
than a rule.

The arithmetic is in `src/libs/gold_stats.py` and is pure and unit-tested; the
job only fetches (metrics rows from DynamoDB, each entity's `_changes` table
from the catalog), calls it and writes. Each day is deleted and rewritten, so a
re-run is safe and a back-fill can be repeated.

**The one-off back-fill** (the metrics table holds 90 days):

```
aws glue start-job-run --region eu-west-1   --job-name uif-echo-dev-gold-summarize   --arguments '{"--backfill_days":"90"}'
```

A single day can be rewritten the same way with `{"--day":"2026-09-23"}`. The
nightly run needs no arguments: it summarises yesterday.

**Never give a Glue argument an empty default.** `"--day" = ""` cost this job
every one of its first runs: Glue drops an empty value instead of passing it as
an argv token, so the flag was left beside the orphaned value of a
Glue-consumed `--enable-*` directive and argparse bound that. The argument is
absent from `default_arguments`, not empty, and a test forbids an empty default
on any argument. The job also needs `dynamodb:Scan` on the config table, which
`gold_executor_access.tf` grants: it is the only gold job that reads the table
whole rather than one row at a time.

## The monthly leadership pack (UIFPE-544)

`ops_report.tf`: `uif-echo-{env}-gold-export` rolls `daily_stats` up into one
CSV per month and writes it to the artifacts bucket, at
`reports/monthly/{YYYY-MM}.csv` plus a stable `reports/monthly/latest.csv`. A
Glue trigger runs it at **06:00 UTC on the 1st**, an hour and a half after the
nightly statistics run, so the month's last day is already in the table.

The file carries **two grains in one object**, marked by a `grain` column:
domain rows for leadership, entity rows for whoever has to find out which table
moved a number. Domain rows are accumulated from the same daily rows as the
entity rows, so they cannot disagree, and the job asserts that before writing.

Rules worth knowing before quoting a figure from it:

- `run_success_pct` divides by **terminal** runs, so a gate-skip is not a
  failure. A month with nothing terminal has no rate at all, rendered as an
  empty cell rather than 0%.
- `freshness_attainment_pct` divides by the **judged** entity-days.
  `freshness_unknown` is published beside it, because the metrics rows expire
  after 90 days and 100% over two judged days of thirty is not a good month.
- `entities` and `rows_written` are separate figures. A count of tables is not
  a count of rows, and a paused table must not read like a busy one, which is
  what `entities_with_runs` is for.
- **`compute_seconds` is the additive figure; `compute_minutes` is for
  reading.** A rounded per-row float cannot be summed: the first real pack had
  a domain row of 272.6 minutes over entity rows of 253.9 and 18.6, because
  15,236 s and 1,118 s each round down while their exact total of 16,354 s does
  not. Total the seconds, not the minutes. The job reconciles on every column
  `gold_report.ADDITIVE_COLUMNS` declares and refuses to report success if any
  domain row disagrees with its entity rows.
- **No events published or delivered**, because no durable record of them
  exists (UIFPE-572), and **no DPU-hours or cost**, because capacity varies per
  run and lives only in Glue's own run history. Compute time is reported
  instead, from the durations already in the table.

**Re-running a month** (idempotent, one key per month):

```
aws glue start-job-run --region eu-west-1   --job-name uif-echo-dev-gold-export   --arguments '{"--month":"2026-09"}'
```

With no arguments it reports the month that just ended.

## Alerting and the dashboard (UIFPE-543)

`alerts.tf` holds every alarm that pages someone, the `uif-echo-{env}-gold`
dashboard, and the notifier: alarms invoke `uif-echo-{env}-gold-alert-notify`
directly (no SNS topic — the deploy role's SNS grant covers `echo-dev-*` only,
and fan-out earns a topic when a second channel arrives), and the function
mails every enabled row of `uif-echo-{env}-alert-subscribers` through the
Pearson Notifications API with beacon's credential
(`uif/beacon/{env}/notifications/api`, reused by owner decision). The metrics
behind the alarms are the `UIF/Gold` EMF documents `src/libs/gold_telemetry.py`
emits; `../observability/slo.yaml` states the targets they were tuned to.
`alert_seed_subscribers` in the tfvars seeds the table once; after that the
rows belong to the console's Notifications page. The notifier's zip copies
`platform/python/pearson_notifications/` in (see `platform/python/README.md`).
The notifier runs inside the account VPC's private subnets with its own
egress security group and a seat on the Secrets Manager endpoint's group, for
the same reason beacon's deliver Lambda does: the Pearson endpoints are on
private addresses behind the corporate gateway route, and outside the VPC
every send timed out (found live 2026-09-23). `vpc_id`, `private_subnet_ids`
and `secretsmanager_endpoint_sg_id` in the tfvars mirror beacon's.

## Commands

Run from this directory.

| Task | Command |
|---|---|
| Init | `make init ENV=dev` |
| Plan | `make plan ENV=dev` (stages the preview Lambda bundle first) |
| Apply | `make apply ENV=dev` — in dev the workflow does this on merge |
| Format / validate | `make fmt` · `make validate ENV=dev` |
