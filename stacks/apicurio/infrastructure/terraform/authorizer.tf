# Phase 1 auth stop-gap — a source-IP allowlist authorizer in front of the public
# HTTP API (UIFPE-36, ../../DEPLOY-DEV.md "Phase 1 · Auth stop-gap").
#
# HTTP APIs support neither WAF nor API keys, so a REQUEST Lambda authorizer is the
# only lock available at this edge. Two credential paths, for two callers that cannot
# share one:
#
#   * a browser authenticates by network position (`var.api_allowed_cidrs`, the
#     EXTERNAL connect.pearson.com range API Gateway sees — not `var.vpn_client_cidrs`,
#     which is the VPN adapter's own address and only the internal ALBs ever see it),
#     because anything the public SPA carries is not a secret;
#   * CI authenticates by a shared header secret, because GitHub-hosted runner
#     addresses move and cannot be allowlisted.
#
# Neither is user identity. This authenticates a network location and a shared
# secret — it yields no principal, no per-user authorization, and no audit trail of
# who changed which contract. Every file in this stack that claims authentication
# must keep saying that until Entra federation replaces it.
#
# WHOLE-FILE DELETE. This file, `authorizer/index.py`, the two `authorization_type`
# blocks in apigw.tf, the archive provider in providers.tf and the output in
# outputs.tf are the entire control. When the Entra authorizer lands, that is one
# revert, not an excavation — which is the reason for every packaging choice below.

locals {
  # Header the CI smoke job presents instead of an allowlisted address. Lower-case
  # because payload format 2.0 lower-cases every header key before the event reaches
  # the function; HTTP header names are case-insensitive on the wire either way.
  #
  # Deliberately NOT `Authorization`: that name is reserved for the bearer token
  # Entra will carry, and reusing it now would mean the cutover has to distinguish
  # two unrelated credentials in one header.
  authorizer_ci_header = "x-uif-ci-key"
}

# ── The CI shared secret ──────────────────────────────────────────────────────
#
# The CONTAINER ONLY. There is deliberately no `aws_secretsmanager_secret_version`:
# a Terraform-generated value would be written to state and printed in full by
# `terraform show -json` on any saved plan, and this is the credential that bypasses
# the IP allowlist. The operator sets the value once, by hand:
#
#   aws secretsmanager put-secret-value \
#     --secret-id uif/apicurio/dev/auth/ci --secret-string "$(openssl rand -hex 32)"
#
# Until they do, `GetSecretValue` returns ResourceNotFoundException and the function
# treats the header path as unavailable — the source-IP path is unaffected, so the
# stack is usable before the secret exists.
#
# Slash form per identity-secrets.md (`uif/{stack}/{env}/{system-type}/{system}`);
# a hyphen anywhere in a segment would drop the secret out of every wildcard-scoped
# IAM policy. Note DEPLOY-DEV.md Phase 3 gap 5: the deploy role's `CreateSecret`
# wildcard is `secret:uif-*-dev*`, with hyphens, and does not match this path.
resource "aws_secretsmanager_secret" "authorizer_ci" {
  name        = "uif/apicurio/${var.env}/auth/ci"
  description = "CI shared secret for the Apicurio BFF API Gateway authorizer. Value set by hand, never by Terraform."

  # No recovery window. A deleted secret normally lingers for 30 days and holds its
  # name, so destroying and re-applying this stop-gap inside that window fails with
  # "already scheduled for deletion" — unacceptable for a control whose whole point
  # is to be deleted in one commit. The value is a regenerable random string, so
  # there is nothing to recover.
  recovery_window_in_days = 0

  tags = { component = "authorizer" }
}

# ── The function ──────────────────────────────────────────────────────────────
#
# Packaged with `archive_file` rather than beacon's S3-upload pattern
# (stacks/beacon/infrastructure/terraform/lambda.tf, which takes `lambda_zip_key` and
# `lambda_zip_hash` from CI after an out-of-band build). Beacon's pattern is right for
# a real application: the zip has dependencies, CI builds it, and Terraform must not
# be the build system. It is wrong here. This is ~30 lines of stdlib plus boto3, which
# the runtime already provides, and the tradeoff runs the other way — `archive_file`
# means the source is a reviewable file in this directory with no CI variables to wire
# up and nothing to un-wire on the way out. `terraform plan` builds the zip itself, so
# the control is complete in one commit and gone in one revert.
data "archive_file" "authorizer" {
  type        = "zip"
  source_file = "${path.module}/authorizer/index.py"
  output_path = "${path.module}/.build/authorizer.zip"

  # Fixed mode so the archive hash depends on the source, not on the umask of
  # whichever machine ran plan. Without it, CI and a laptop produce different
  # `source_code_hash` values and every apply shows a spurious function update.
  output_file_mode = "0644"
}

data "aws_iam_policy_document" "authorizer_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

# `{service}` is the workload, not the engine (identity-secrets.md): `authorizer`,
# never `lambda`.
resource "aws_iam_role" "authorizer" {
  name               = "${local.name}-authorizer-role"
  assume_role_policy = data.aws_iam_policy_document.authorizer_assume.json

  tags = { component = "authorizer" }
}

data "aws_iam_policy_document" "authorizer" {
  # The CI secret and nothing else. Not a prefix, not `secret:uif/apicurio/*` — this
  # function has one secret to read and the ARN is known at plan time.
  statement {
    sid       = "ReadCiSecret"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [aws_secretsmanager_secret.authorizer_ci.arn]
  }

  # Streams inside its own log group only. No `logs:CreateLogGroup`: the group is
  # declared below so its retention is actually set, and granting the creation right
  # would let the function silently recreate it without retention if it were ever
  # deleted — the exact failure this stack declares the group to avoid.
  statement {
    sid       = "WriteOwnLogs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.authorizer.arn}:*"]
  }
}

# One inline policy per role, named for the role it sits on with `-role` replaced by
# `-policy` (identity-secrets.md). Two `aws_iam_role_policy` resources sharing a name
# on one role is not an error Terraform reports — the second PutRolePolicy overwrites
# the first with no plan diff — so both statements live in one document.
resource "aws_iam_role_policy" "authorizer" {
  name   = "${local.name}-authorizer-policy"
  role   = aws_iam_role.authorizer.id
  policy = data.aws_iam_policy_document.authorizer.json
}

# Declared, not left to Lambda's implicit creation. An implicitly created group has
# retention "Never expire", and 30 days matches every other group in this stack
# (apigw.tf, bff.tf, ecs.tf).
resource "aws_cloudwatch_log_group" "authorizer" {
  name              = "/aws/lambda/${local.name}-authorizer"
  retention_in_days = 30

  tags = { component = "authorizer" }
}

resource "aws_lambda_function" "authorizer" {
  function_name = "${local.name}-authorizer"
  role          = aws_iam_role.authorizer.arn
  runtime       = "python3.12"
  handler       = "index.handler"

  filename         = data.archive_file.authorizer.output_path
  source_code_hash = data.archive_file.authorizer.output_base64sha256

  # 3s is generous for one Secrets Manager call on a cold start and a CIDR
  # comparison. API Gateway fails the request if the authorizer does not answer, so a
  # long timeout buys a slow 500 rather than a fast denial.
  timeout     = 3
  memory_size = 128

  # No `vpc_config`: this function talks to Secrets Manager and nothing inside the
  # VPC. Keeping it out avoids ENI cold-start latency on every request to the API and
  # removes any dependency on the single-AZ Secrets Manager interface endpoint the
  # ECS tasks need.

  environment {
    variables = {
      # Comma-joined, parsed back in the function. An empty list means the function
      # allows no source address at all, matching the variable's fail-closed default.
      ALLOWED_CIDRS    = join(",", var.api_allowed_cidrs)
      CI_SECRET_ARN    = aws_secretsmanager_secret.authorizer_ci.arn
      CI_SECRET_HEADER = local.authorizer_ci_header
    }
  }

  tags = { component = "authorizer" }

  # The log group must exist first or Lambda creates it implicitly and Terraform's
  # own create then fails as already-existing. The policy must exist first or the
  # first cold start cannot read the secret.
  depends_on = [
    aws_cloudwatch_log_group.authorizer,
    aws_iam_role_policy.authorizer,
  ]
}

# ── The authorizer ────────────────────────────────────────────────────────────
resource "aws_apigatewayv2_authorizer" "bff" {
  api_id          = aws_apigatewayv2_api.bff.id
  name            = "${local.name}-bff-authorizer"
  authorizer_type = "REQUEST"
  authorizer_uri  = aws_lambda_function.authorizer.invoke_arn

  # Payload format 2.0 with simple responses: the function returns
  # {"isAuthorized": true|false} instead of an IAM policy document. There is one
  # decision to express here — allowed or not — and an IAM policy document would be a
  # second place for a per-route mistake to hide.
  authorizer_payload_format_version = "2.0"
  enable_simple_responses           = true

  # ── Why caching is OFF, not just short ──
  #
  # Caching is keyed on the authorizer's identity sources and NOTHING ELSE — not the
  # source IP, unless the source IP is itself an identity source. With a simple
  # response, a cached decision "fully allows or denies all API requests that match
  # the cached identity source values"
  # (docs.aws.amazon.com/apigateway/latest/developerguide/http-api-lambda-authorizer.html).
  # So a TTL above zero on this authorizer has one specific failure mode: one allowed
  # request from an allowlisted address caches an allow, and every request matching
  # the same identity-source values — from ANY address — is then allowed for the TTL
  # without the function running. The control is bypassed for up to an hour by
  # anybody who arrives after a legitimate user.
  #
  # The obvious fix, listing the CI header as an identity source, is worse: "If you
  # specify identity sources, clients must include them in the request. If the
  # client's request doesn't include the identity sources, API Gateway doesn't invoke
  # your Lambda authorizer, and the client receives a 401" (same page). Every browser
  # request — which carries no CI header — would be rejected without the function
  # ever seeing its source IP.
  #
  # Hence: no identity sources, and TTL 0. The function runs on every request, which
  # is the only configuration in which its decision is actually the one enforced. The
  # cost is one 128MB invocation of ~30 lines per request, which for a governance UI
  # is not a cost.
  identity_sources                 = []
  authorizer_result_ttl_in_seconds = 0
}

# API Gateway may invoke this function only as THIS api's THIS authorizer. Not a
# bare `principal = apigateway.amazonaws.com`, which would let any API Gateway in any
# account invoke it.
resource "aws_lambda_permission" "authorizer" {
  statement_id  = "AllowApiGatewayAuthorizerInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.authorizer.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.bff.execution_arn}/authorizers/${aws_apigatewayv2_authorizer.bff.id}"
}
