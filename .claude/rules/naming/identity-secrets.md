---
paths:
  - "**/infra/iam*.tf"
  - "**/infrastructure/iam*.tf"
  - "stacks/*/infrastructure/**/iam*.tf"
  - "domains/*/infrastructure/**/iam*.tf"
  - "**/infrastructure/**/*.tf"
  - "**/scripts/*secret*.py"
  - "**/lambda/**/*.py"
---
# Identity and secret names

Vocabulary and length limits live in `00-core.md`.

| Resource | Terraform type | Form | Example |
|---|---|---|---|
| IAM role | `aws_iam_role` | `uif-{stack}-{env}-{service}-role` | `uif-echo-dev-glue-role` |
| IAM policy — managed | `aws_iam_policy` | `uif-{stack}-{env}-{service}-policy` | `uif-echo-dev-glue-policy` |
| IAM policy — inline | `aws_iam_role_policy` | `uif-{stack}-{env}-{service}-policy` | `uif-apicurio-dev-bff-task-policy` |
| Secrets Manager secret | `aws_secretsmanager_secret` | `uif/{stack}/{env}/{system-type}/{system}` | `uif/echo/dev/db/tep`, `uif/echo/uat/cloud/gcp` |

## Keep the role/policy pairing

A role and the policy attached to it differ by their last token only, so the pair is
obvious in a console listing and in a diff. `uif-echo-dev-glue-role` takes
`uif-echo-dev-glue-policy` — not `uif-echo-dev-glue-job-policy`.

**This holds for inline policies too.** An inline policy is namespaced by its role, so a bare
short name (`deliver`) is unambiguous to AWS — but it is not unambiguous to a reader scanning
a plan, and it lets the pair drift apart unnoticed. The Apicurio stack shipped
`uif-apicurio-dev-bff-execution-policy` attached to the **task** role, which reads as a
grant on the execution role and is not one; under the pairing rule the name has to be
`uif-apicurio-dev-bff-task-policy`, and the mismatch becomes visible.

Existing inline policies across `echo` and `beacon` predate this rule and are non-compliant
**by design** until migrated — `00-core.md`'s legacy rule covers them. Renaming one loses no
data, but it is a delete-and-create: the role holds neither policy for the duration of the
apply, so migrate deliberately rather than opportunistically.

`PolicyName` accepts 1–128 characters from `[\w+=,.@-]`, so every hyphenated `uif-` name is
legal.

## Name the workload, not the engine

`{service}` is the workload that assumes the role — the thing being granted access. It is
not the AWS engine the workload happens to run on.

The distinction only shows up when a stack runs several workloads on one engine. Each
needs its own role, so an engine token cannot tell them apart: five functions on one
engine would all be `...-lambda-role`. Name each for what it is instead, and the engine
token becomes redundant.

Where a stack has exactly one identity for an engine — a shared execution role, one per
environment — the engine name *is* the workload name, and keeping it is correct.

✅ `uif-echo-dev-glue-role` — one shared execution role per environment, so `glue` names
the workload
✅ `uif-beacon-dev-deliver-role` · `uif-beacon-dev-export-role` — several workloads on one
engine
❌ `uif-beacon-dev-lambda-deliver-role` — carries the engine *and* the workload
❌ `uif-beacon-dev-lambda-role`, where several workloads each need a role — the name cannot
be unique

Renaming a role to drop an engine token replaces it. See `00-core.md`, "Legacy names — do
not rename": apply this to newly created roles, and to existing ones only through a
planned migration.

## Every token boundary in a secret is a slash

```
uif/{stack}/{env}/{system-type}/{system}
```

✅ `uif/echo/dev/db/tep` · `uif/echo/uat/cloud/gcp`
❌ `uif/echo/uat/cloud/gcp-ipm` — a policy scoped to `.../cloud/*` no longer matches it

A hyphen where a slash belongs silently drops the secret out of every wildcard-scoped IAM
policy. Nothing fails at creation — the secret resolves by full name — so it fails later
at `GetSecretValue`, from a job, in an environment nobody was watching.

There is **no `gip` segment.** The previous convention had one
(`uif/echo/dev/gip/db/tep`); the current standard does not.

## When adding a secret

1. Name it in slash form, one token per segment.
2. Check the resource ARNs in the stack's IAM policy actually cover the new path prefix.
   Existing statements scope by prefix — a new `uif/echo/*` secret is not covered by a
   statement scoped to `secret:echo/*` until one is added.
3. Reference it from config by ARN, never by reconstructing the name in code.
