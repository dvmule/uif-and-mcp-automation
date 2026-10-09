# The three Step Functions executors (diagram step 11). One zip — the whole
# beacon package, staged by CI at code/beacon-lambdas.zip — three functions
# differing only by handler string. Least-privilege: one role per function.

locals {
  lambda_runtime = "python3.12"
  lambda_zip = {
    s3_bucket = aws_s3_bucket.data.bucket
    s3_key    = var.lambda_zip_key
  }
}

variable "lambda_zip_hash" {
  description = "Base64 SHA256 of the staged lambda zip (CI computes it at apply time). Empty = code changes not tracked, so functions keep whatever they run — the UIFPE-200 stale-lambda trap."
  type        = string
  default     = ""
}

variable "lambda_zip_key" {
  description = "S3 key of the beacon-lambdas zip under the data bucket (CI stages it)"
  type        = string
  default     = "code/beacon-lambdas.zip"
}

variable "delivery_destination_arns" {
  description = <<-EOT
    Extra S3 bucket/prefix ARNs the deliver Lambda may write to (author-owned
    destination buckets). The data bucket itself is always allowed. Example:
    ["arn:aws:s3:::partner-bucket", "arn:aws:s3:::partner-bucket/inbound/*"]
  EOT
  type        = list(string)
  default     = []
}

data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

# -- deliver -----------------------------------------------------------------
# uif:rename-approved UIFPE-365
resource "aws_iam_role" "lambda_deliver" {
  # Named for the workload, not the engine: five Lambdas share one engine.
  name               = "${local.name}-deliver-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
  lifecycle {
    create_before_destroy = true
  }
}

# UIFPE-444: email delivery rides the Pearson Notifications API (owner
# decision 2026-09-16) — the SES sender variable and IAM statement went with
# it. The OAuth client credential secret is NOT a Terraform resource: neither
# the fde role nor the deploy role holds secretsmanager:CreateSecret (both
# denied live, 2026-09-16). Instead the API creates and updates it through
# PUT /notifications/credentials — the same trust model as destination
# credentials (accepted, stored in Secrets Manager, never returned), under
# the api_task policy's NotificationsSecretManage statement (iam.tf).

variable "notifications_api_url" {
  description = "Pearson Notifications API base URL (per env: stg for dev)"
  type        = string
  default     = "https://notifications-api.stg-prsn.com"
}

variable "notifications_token_url" {
  description = "PI API v1 tokens endpoint the email adapter mints X-Authorization tokens from (per env: stg for dev)"
  type        = string
  default     = "https://int-piapi-internal.stg-openclass.com/tokens"
}

variable "beacon_panel_url" {
  description = "The Beacon panel's entry URL — email Download buttons deep-link into it"
  type        = string
  # UIFPE-484: the legacy shared RELAY distribution (d2qua7fu7qeqlx) was
  # retired with the Echo estate migration; the panel now rides the new one.
  default = "https://d2dqf4ghal7j09.cloudfront.net/m/beacon/index.html"
}

data "aws_iam_policy_document" "lambda_deliver" {
  statement { # read configs + pickup, write within the data bucket
    actions = ["s3:GetObject", "s3:PutObject", "s3:ListBucket"]
    resources = concat(
      [aws_s3_bucket.data.arn, "${aws_s3_bucket.data.arn}/*"],
      var.delivery_destination_arns,
    )
  }
  statement { # UIFPE-440 live finding: the connection test must clean up its
    # own marker on SAME-account destinations too (the external statement's
    # fence deliberately excludes them, and the write statement above never
    # granted delete). Scoped to the marker filename — delivered data stays
    # undeletable by this role. Buckets added to delivery_destination_arns
    # get their own marker entry when one actually exists.
    sid       = "ConnectionTestMarkerCleanup"
    actions   = ["s3:DeleteObject"]
    resources = ["${aws_s3_bucket.data.arn}/*.beacon-connection-test"]
  }
  statement { # UIFPE-440: self-service EXTERNAL destinations. This statement
    # reaches only buckets OUTSIDE this account (the ResourceAccount fence),
    # and a cross-account write additionally requires the partner's own
    # bucket policy to allow this role — their grant is the real gate, so
    # our side needs no per-destination change. Same-account buckets stay on
    # the explicit delivery_destination_arns allowlist above (self-service
    # must never reach a random internal bucket). GetObject exists for the
    # adapter's copy-verify (HeadObject on the delivered file — the first
    # real cross-account delivery failed exactly there); DeleteObject for
    # the connection test's marker cleanup only.
    sid       = "ExternalDestinationWrite"
    actions   = ["s3:PutObject", "s3:GetObject", "s3:DeleteObject"]
    resources = ["*"]
    condition {
      test     = "StringNotEquals"
      variable = "s3:ResourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }
  }
  statement { # UIFPE-444: email channel — the adapter reads the Pearson
    # Notifications OAuth client credentials at send time. The SES SendEmail
    # statement went with the ses_link adapter.
    sid       = "NotificationsCredentialsRead"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = ["arn:aws:secretsmanager:${var.region}:*:secret:${local.secrets_prefix}/notifications/*"]
  }
  # The SftpDestinationSecrets GetSecretValue statement went at UIFPE-438
  # with the Paramiko path: the CONNECTOR reads the destination secret now
  # (via the transfer-access role in transfer.tf), never this Lambda.
  statement { # UIFPE-360: hand the file to a Transfer Family connector rather
    # than speaking SFTP from this Lambda (UIFPE-339 retires the Paramiko
    # path). Tag-fenced, like the API's connector statements — the connector's
    # ARN carries a random id, so a name prefix cannot fence it.
    # DescribeExecution was dropped at UIFPE-438: it is workflow-scoped (its
    # tag fence keys on workflow tags we never have) and delivery reads its
    # results via ListFileTransferResults only — proven unused live.
    sid       = "TransferFileDelivery"
    actions   = ["transfer:StartFileTransfer", "transfer:ListFileTransferResults"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "aws:ResourceTag/Project"
      values   = [local.project_tag]
    }
  }
}

# UIFPE-438: the deliver Lambda left the VPC with the Paramiko path. It only
# calls AWS APIs now (S3, Transfer Family, SES) — SFTP egress happens from
# each CONNECTOR's service-managed IPs, so the Lambda needs no stable source
# IP, no deliver SG, and no Secrets Manager endpoint ingress rule (the
# secretsmanager_endpoint_sg_id variable went with it). Detaching also
# removed the ENI cold-start tax. Restoring the VPC posture is a git revert
# of the UIFPE-438 detach commit plus an apply.

# UIFPE-444: the deliver Lambda is back INSIDE the VPC's private subnets —
# the Pearson Notifications API resolves to PRIVATE 10.251.x addresses, and
# the VPC's 10.0.0.0/8 route through the corporate virtual private gateway is
# the only path that reaches them (proven live: public egress times out, the
# panel-side curl works). This reverses the UIFPE-438 detach for a new
# reason; the original SG (sg-006fa0949ecbf0f7b, orphaned from state at
# UIFPE-220) was terraform-imported back rather than recreated — the name
# would have collided.
resource "aws_security_group" "deliver" {
  # -sg suffix per the UIF Resource Name Lookup (UIFPE-266). SG names force
  # replacement: create_before_destroy keeps the Lambda attached throughout,
  # and the long delete timeout absorbs Lambda's lazy ENI release (the old
  # group can take up to ~20 minutes to become detachable).
  name   = "${local.name}-deliver-sg"
  vpc_id = var.vpc_id

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  lifecycle {
    create_before_destroy = true
  }

  timeouts {
    delete = "45m"
  }
}

resource "aws_iam_role_policy_attachment" "lambda_deliver_vpc" {
  role       = aws_iam_role.lambda_deliver.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole"
}

# The VPC has a Secrets Manager INTERFACE ENDPOINT with private DNS enabled,
# so every Secrets Manager call from inside the VPC is forced through it —
# the NAT path never applies. The endpoint's security group (owned by the
# Echo/platform side) only admits itself, so the deliver Lambda's secret
# reads time out without this rule (relearned at UIFPE-444; first learned at
# UIFPE-241). One additive ingress rule: HTTPS from OUR deliver SG. The
# platform team should be told Beacon's seat is needed again — the UIFPE-438
# close said it was not.
variable "secretsmanager_endpoint_sg_id" {
  description = "Security group of the VPC's Secrets Manager interface endpoint (empty = no rule; dev: the shared endpoint's SG)"
  type        = string
  default     = ""
}

# The deploy role can Authorize and Revoke rules on this SG but not
# ec2:ModifySecurityGroupRules — so any change to this rule must be a
# REPLACE, never an in-place update; replace_triggered_by keeps that true
# for every future deliver-SG replacement.
resource "aws_vpc_security_group_ingress_rule" "deliver_secretsmanager_access" {
  count                        = var.secretsmanager_endpoint_sg_id != "" ? 1 : 0
  security_group_id            = var.secretsmanager_endpoint_sg_id
  referenced_security_group_id = aws_security_group.deliver.id
  ip_protocol                  = "tcp"
  from_port                    = 443
  to_port                      = 443
  # AWS SG-rule descriptions reject '>' (only a-zA-Z0-9 and . _-:/()#,@[]+=&;{}!$* are legal)
  description = "beacon deliver lambda to secretsmanager vpc endpoint (UIFPE-241/444)"

  lifecycle {
    replace_triggered_by = [aws_security_group.deliver.id]
  }
}

resource "aws_iam_role_policy" "lambda_deliver" {
  name   = "deliver"
  role   = aws_iam_role.lambda_deliver.id
  policy = data.aws_iam_policy_document.lambda_deliver.json
}

resource "aws_lambda_function" "deliver" {
  function_name = "${local.name}-deliver"
  role          = aws_iam_role.lambda_deliver.arn
  runtime       = local.lambda_runtime
  handler       = "beacon_export.lambdas.deliver.handler"
  s3_bucket     = local.lambda_zip.s3_bucket
  s3_key        = local.lambda_zip.s3_key
  # the key is fixed, so this hash is the ONLY change signal for new code
  source_code_hash = var.lambda_zip_hash != "" ? var.lambda_zip_hash : null
  timeout          = 300 # large batches: many copy+verify round trips
  memory_size      = 512

  vpc_config { # UIFPE-444: private-subnet egress — VGW to Pearson-internal
    # endpoints (Notifications API), NAT for public ones (Pearson IAM, AWS)
    subnet_ids         = var.private_subnet_ids
    security_group_ids = [aws_security_group.deliver.id]
  }

  environment {
    variables = {
      # UIFPE-444: Pearson Notifications wiring — the secret VALUE is created
      # by an admin (aws secretsmanager create-secret), never by Terraform
      BEACON_NOTIFICATIONS_SECRET    = "${local.secrets_prefix}/notifications/api"
      BEACON_NOTIFICATIONS_API_URL   = var.notifications_api_url
      BEACON_NOTIFICATIONS_TOKEN_URL = var.notifications_token_url
      BEACON_PANEL_URL               = var.beacon_panel_url
    }
  }
}

# -- record_run ----------------------------------------------------------------
# uif:rename-approved UIFPE-365
resource "aws_iam_role" "lambda_record_run" {
  name               = "${local.name}-record-run-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_iam_role_policy" "lambda_record_run" {
  name = "record-run"
  role = aws_iam_role.lambda_record_run.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["dynamodb:PutItem"]
        Resource = aws_dynamodb_table.runs.arn
      },
      { # UIFPE-168: the success branch advances the incremental watermark —
        # a monotonic conditional update, the design's single commit point
        Effect   = "Allow"
        Action   = ["dynamodb:UpdateItem"]
        Resource = aws_dynamodb_table.extraction_state.arn
      },
      { # UIFPE-168: read the export config + the transform's report.json
        # (<canonical>/_report/) to merge row counts into the run row
        Effect   = "Allow"
        Action   = ["s3:GetObject"]
        Resource = "${aws_s3_bucket.data.arn}/*"
      },
    ]
  })
}

resource "aws_lambda_function" "record_run" {
  function_name    = "${local.name}-record-run"
  role             = aws_iam_role.lambda_record_run.arn
  runtime          = local.lambda_runtime
  handler          = "beacon_export.lambdas.record_run.handler"
  s3_bucket        = local.lambda_zip.s3_bucket
  s3_key           = local.lambda_zip.s3_key
  source_code_hash = var.lambda_zip_hash != "" ? var.lambda_zip_hash : null
  timeout          = 30

  environment {
    variables = {
      BEACON_TABLE_RUNS             = aws_dynamodb_table.runs.name
      BEACON_TABLE_EXTRACTION_STATE = aws_dynamodb_table.extraction_state.name
    }
  }
}

# -- emit_event ----------------------------------------------------------------
# uif:rename-approved UIFPE-365
resource "aws_iam_role" "lambda_emit_event" {
  name               = "${local.name}-emit-event-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
  lifecycle {
    create_before_destroy = true
  }
}

data "aws_caller_identity" "current" {}

resource "aws_iam_role_policy" "lambda_emit_event" {
  name = "emit-event"
  role = aws_iam_role.lambda_emit_event.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["events:PutEvents"]
      Resource = "arn:aws:events:${var.region}:${data.aws_caller_identity.current.account_id}:event-bus/default"
    }]
  })
}

resource "aws_lambda_function" "emit_event" {
  function_name    = "${local.name}-emit-event"
  role             = aws_iam_role.lambda_emit_event.arn
  runtime          = local.lambda_runtime
  handler          = "beacon_export.lambdas.emit_event.handler"
  s3_bucket        = local.lambda_zip.s3_bucket
  s3_key           = local.lambda_zip.s3_key
  source_code_hash = var.lambda_zip_hash != "" ? var.lambda_zip_hash : null
  timeout          = 30

  environment {
    variables = { BEACON_EVENT_BUS = "default" }
  }
}

# CloudWatch logs for all three
resource "aws_cloudwatch_log_group" "lambdas" {
  for_each = {
    deliver    = aws_lambda_function.deliver.function_name
    record_run = aws_lambda_function.record_run.function_name
    emit_event = aws_lambda_function.emit_event.function_name
  }
  name              = "/aws/lambda/${each.value}"
  retention_in_days = 30
}

resource "aws_iam_role_policy_attachment" "lambda_logs" {
  for_each = {
    deliver    = aws_iam_role.lambda_deliver.name
    record_run = aws_iam_role.lambda_record_run.name
    emit_event = aws_iam_role.lambda_emit_event.name
  }
  role       = each.value
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}
