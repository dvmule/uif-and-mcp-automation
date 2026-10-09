---
paths:
  - "**/infra/**/*.tf"
  - "**/infrastructure/**/*.tf"
  - "stacks/*/infrastructure/**/*.tf"
  - "domains/*/infrastructure/**/*.tf"
---
# Compute and ETL resource names

Vocabulary, length limits and the legacy-rename prohibition live in `00-core.md`.

| Resource | Terraform type | Form | Example |
|---|---|---|---|
| Glue job | `aws_glue_job` | `uif-{stack}-{env}-[layer]-{operation}[-{target}]` | `uif-echo-dev-silver-transform-bq` |
| Glue connection — network | `aws_glue_connection` | `uif-{stack}-{env}-{type}` | `uif-echo-dev-vpc-network` |
| Glue connection — per source | `aws_glue_connection` | `uif-{stack}-{env}-{system-type}-{system}` | `uif-echo-dev-db-tep` |
| Lambda function | `aws_lambda_function` | `uif-{stack}-{env}-{operation}` or `uif-{stack}-{env}-{subject}-controller` | `uif-echo-dev-dispatch`, `uif-echo-dev-pipeline-controller` |
| Lambda layer | `aws_lambda_layer_version` | `uif-{stack}-{env}-{purpose}-layer` | `uif-echo-dev-oracle-layer` |
| ECS cluster | `aws_ecs_cluster` | `uif-{stack}-{env}-cluster` | `uif-beacon-dev-cluster` |
| ECS service | `aws_ecs_service` | `uif-{stack}-{env}-[{component}-]service` | `uif-beacon-dev-pipeline-service` |
| ECS task definition | `aws_ecs_task_definition` | `uif-{stack}-{env}-{component}-task-definition` | `uif-beacon-dev-pipeline-task-definition` |
| ECS container | — | `{component}` — bare, scoped to its task definition | `pipeline` |
| ECR repository | `aws_ecr_repository` | `uif-{stack}-{env}-{component}-ecr-repo` | `uif-beacon-dev-pipeline-ecr-repo` |
| Step Functions | `aws_sfn_state_machine` | `uif-{stack}-{env}-{operation}` | `uif-beacon-dev-export` |

## One cluster per stack, component tokens once there are several

An ECS cluster carries no component token — there is one per stack. A service takes one
as soon as a stack runs more than a single service. A container name is bare: it is
already scoped by its task definition, so repeating the stack and env there is noise.

## Name the target explicitly

Any resource that could write to more than one storage destination names that
destination. Required only where sibling resources differ by destination.

✅ `uif-echo-dev-silver-transform-bq` and `uif-echo-dev-silver-transform-gcs`
❌ `uif-echo-dev-silver-bq-native` — hides that it is a transform.

## Step Functions are named by operation, not by pattern

✅ `uif-beacon-dev-export` ❌ `uif-beacon-dev-export-state-machine` — the type is
already in the ARN.
