# UIFPE-339: AWS Transfer Family SFTP connectors replace the in-Lambda
# Paramiko path. The CONNECTORS themselves are runtime data — one per SFTP
# destination, created/updated/deleted by the API alongside the destination
# lifecycle (same posture as schedules and destination secrets; see the
# TransferConnector* statements in iam.tf). What Terraform owns is the ONE
# shared piece every connector needs: the access role Transfer Family assumes
# to read the pickup files and the credential secret.
#
# The name must stay inside the API role's TransferPassRole fence
# (role/${local.name}-*) — that statement is what lets the API hand this role
# to the service at CreateConnector time.

data "aws_iam_policy_document" "transfer_access_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["transfer.amazonaws.com"]
    }
    # confused-deputy guard: only connectors in THIS account may assume it
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }
  }
}

resource "aws_iam_role" "transfer_access" {
  name               = "${local.name}-transfer-access-role"
  assume_role_policy = data.aws_iam_policy_document.transfer_access_trust.json
}

data "aws_iam_policy_document" "transfer_access" {
  statement { # outbound only: connectors READ the batch's pickup files.
    # GetObjectVersion rides along because Transfer Family requests it even
    # on unversioned reads (documented service behavior).
    sid       = "PickupRead"
    actions   = ["s3:GetObject", "s3:GetObjectVersion"]
    resources = ["${aws_s3_bucket.data.arn}/pickup/*"]
  }
  statement {
    sid       = "PickupList"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.data.arn]
  }
  statement { # the partner credential the connector logs in with (D16: the
    # secret is the only place it exists)
    sid       = "DestinationSecretRead"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = ["arn:aws:secretsmanager:${var.region}:*:secret:${local.secrets_prefix}/destinations/*"]
  }
}

resource "aws_iam_role_policy" "transfer_access" {
  name   = "transfer-access"
  role   = aws_iam_role.transfer_access.id
  policy = data.aws_iam_policy_document.transfer_access.json
}
