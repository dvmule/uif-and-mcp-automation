---
paths:
  - "**/infra/**/*.tf"
  - "**/infrastructure/**/*.tf"
  - "stacks/*/infrastructure/**/*.tf"
  - "domains/*/infrastructure/**/*.tf"
---
# Networking and edge names

Vocabulary and length limits live in `00-core.md`.

| Resource | Terraform type | Form | Example |
|---|---|---|---|
| Application Load Balancer | `aws_lb` | `uif-{stack}-{env}-{purpose}-alb` | `uif-echo-dev-api-alb` |
| ALB target group | `aws_lb_target_group` | `uif-{stack}-{env}-{purpose}-tg` | `uif-echo-dev-api-tg` |
| API Gateway HTTP API | `aws_apigatewayv2_api` | `uif-{stack}-{env}-[{purpose}-]api` | `uif-echo-dev-authentication-api` |
| CloudFront distribution | `aws_cloudfront_distribution` | `uif-{stack}-{env}-{purpose}` | `uif-beacon-dev-ui` |
| CloudFront function | `aws_cloudfront_function` | `uif-{stack}-{env}-{operation}` | `uif-beacon-dev-strip-api-prefix` |
| CloudFront origin access control | `aws_cloudfront_origin_access_control` | `uif-{stack}-{env}-{purpose}` | `uif-apicurio-dev-ui` |
| Security group | `aws_security_group` | `uif-{stack}-{env}-{purpose}-sg` | `uif-echo-dev-eni-sg` |
| VPC endpoint | `aws_vpc_endpoint` | `uif-{stack}-{env}-{service}-endpoint` | `uif-echo-dev-secretsmanager-endpoint` |
| API Gateway VPC link | `aws_apigatewayv2_vpc_link` | `uif-{stack}-{env}-{purpose}-vpc-link` | `uif-apicurio-dev-bff-vpc-link` |
| API Gateway authorizer | `aws_apigatewayv2_authorizer` | `uif-{stack}-{env}-{purpose}-authorizer` | `uif-apicurio-dev-bff-authorizer` |

## The 32-character cap bites here first

`aws_lb` and `aws_lb_target_group` are capped at **32 characters**, the tightest limit in
the estate. `uif-` plus stack, env, purpose and `-alb` leaves twelve characters for
`{purpose}` once the stack is `beacon` and the env is `prod` — thirteen for
`aws_lb_target_group`, whose `-tg` suffix is one character shorter than `-alb`. Count
before committing, and shorten the purpose — never drop `env`.

## Two resources have no name attribute

A **CloudFront distribution** has no `name`; put the name in its `comment` field. A **VPC
endpoint** is named through its `Name` tag, not a name argument. The hook reads neither, so
these two are convention-only — the rule is the enforcement, and neither carries a row in the
hook's `PATTERNS`. A row for `aws_vpc_endpoint` existed until 2026-09-14 and could never
fire; it was removed rather than left implying a check.

Do not confuse the distribution with its **origin access control**, which does take a real
`name` and is enforced.

A VPC endpoint's `{service}` is the AWS service it fronts — there is one endpoint per
service per VPC, so the service name is also the workload name, and
`identity-secrets.md`'s workload rule gives the same answer here.

## Two names AWS owns

An API Gateway **stage** (`aws_apigatewayv2_stage`) named `$default` is AWS's reserved value
for the auto-deployed stage, and the convention does not apply to it — the same exemption
`eventing.md` records for the account's `default` event bus. A stage you name yourself takes
`uif-{stack}-{env}-{purpose}`.

An **ALB listener**, **API Gateway route** and **integration** take no name at all, so there
is nothing to govern.
