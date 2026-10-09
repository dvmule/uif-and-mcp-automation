---
paths:
  - "**/infra/**/*.tf"
  - "**/infrastructure/**/*.tf"
  - "stacks/*/infrastructure/**/*.tf"
  - "domains/*/infrastructure/**/*.tf"
---
# Observability names

Vocabulary and length limits live in `00-core.md`.

| Resource | Terraform type | Form | Example |
|---|---|---|---|
| CloudWatch log group | `aws_cloudwatch_log_group` | `/{aws-path}/uif-{stack}-{env}[-{component}]` | `/aws/lambda/uif-echo-dev-dispatch`, `/ecs/uif-beacon-dev-pipeline`, `/aws/apigateway/uif-echo-dev` |
| CloudWatch dashboard | `aws_cloudwatch_dashboard` | `uif-{stack}-{env}-{subject}` | `uif-echo-dev-silver-bq-replication` |
| CloudWatch alarm | `aws_cloudwatch_metric_alarm` | `uif-{stack}-{env}-{subject}-{condition}` | `uif-echo-dev-silver-bq-replicator-failed` |

## AWS owns the log-group prefix

A log group's leading path is dictated by the service that writes to it — `/aws/lambda/`,
`/aws/apigateway/`, `/ecs/`. The UIF stem follows it rather than replacing it. Do not
invent a prefix, and do not omit one to make the name look like the others.

## An alarm names the condition, not the severity

The `{condition}` token says what happened, so the alarm reads as a sentence in a
notification.

✅ `uif-echo-dev-silver-bq-replicator-failed` ❌ `uif-echo-dev-silver-bq-critical` —
severity belongs in the alarm's configuration, not its name.
