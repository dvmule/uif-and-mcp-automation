# The API's runtime permissions. The api_task POLICY DOCUMENT below is what
# the running code may do — the "sole IAM role" of D19; the browser never
# holds AWS credentials. Since UIFPE-226's probation exit it is attached to
# the API Lambda's role (apigateway.tf); the ECS task role and its service
# were deleted 2026-08-28, and the last ECS resources (the parked Fargate
# transform, its cluster and execution role) at UIFPE-215's probation exit.
# The document keeps its api_task name so the permission history stays
# greppable.

data "aws_iam_policy_document" "api_task" {
  statement { # configs + code prefixes — read/write
    sid     = "S3Configs"
    actions = ["s3:GetObject", "s3:PutObject", "s3:ListBucket"]
    resources = [
      aws_s3_bucket.data.arn,
      "${aws_s3_bucket.data.arn}/configs/*",
      "${aws_s3_bucket.data.arn}/code/*",
    ]
  }
  statement { # UIFPE-558: staged partner XSDs — the wizard uploads here, the
    # publisher reads the staged file back to freeze it into configs/<job>/vN/
    # (the freeze itself writes under configs/*, already granted above).
    sid       = "S3SchemaUploads"
    actions   = ["s3:GetObject", "s3:PutObject"]
    resources = ["${aws_s3_bucket.data.arn}/schemas/uploads/*"]
  }
  statement { # pickup: READ ONLY — presigned download links are signed with
    # this role, so GetObject here is what makes the Download feature work.
    # Writing to pickup stays pipeline-owned.
    sid       = "S3PickupRead"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.data.arn}/pickup/*"]
  }
  statement { # live source-attribute introspection: CSV headers (raw/) —
    # the mapping tab reads columns from the source, not the registry
    sid       = "S3RawHeaderRead"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.data.arn}/raw/*"]
  }
  statement { # live source-attribute introspection: Iceberg schemas via the
    # Glue catalog (metadata only; needs a Lake Formation DESCRIBE grant too).
    # GetPartition(s) since UIFPE-441: Athena resolves the previewed tables
    # through the same catalog (grain_probe.tf carries the same four).
    sid = "GlueCatalogRead"
    actions = ["glue:GetDatabase", "glue:GetTable", "glue:GetTables",
    "glue:GetPartition", "glue:GetPartitions"]
    resources = ["arn:aws:glue:${var.region}:${data.aws_caller_identity.current.account_id}:catalog",
      "arn:aws:glue:${var.region}:${data.aws_caller_identity.current.account_id}:database/*gold*",
    "arn:aws:glue:${var.region}:${data.aws_caller_identity.current.account_id}:table/*gold*/*"]
  }
  # ---- UIFPE-441: the wizard's live filter preview runs on Athena -----------
  # The control panel reads Gold DATA for the first time here: value
  # suggestions and the matched-rows preview. Bounded by what the code sends
  # (engine-validated configs only, 10-row samples, our marker on every
  # query) and by lakeformation.tf's SELECT grant; results are written ONLY to
  # the preview/ prefix, which s3.tf expires after a day.
  statement {
    sid = "AthenaPreview"
    actions = ["athena:StartQueryExecution", "athena:GetQueryExecution",
      "athena:BatchGetQueryExecution", "athena:GetQueryResults",
    "athena:StopQueryExecution"]
    resources = ["arn:aws:athena:${var.region}:${data.aws_caller_identity.current.account_id}:workgroup/${var.athena_workgroup}"]
  }
  dynamic "statement" { # Athena reads the Iceberg metadata + data files straight
    # from S3 with this role's credentials (glue.tf's S3GoldRead is the same
    # read for Spark). Empty list = no statement rather than one with no resource.
    for_each = length(var.gold_data_buckets) > 0 ? [1] : []
    content {
      sid     = "S3GoldRead"
      actions = ["s3:GetObject", "s3:ListBucket", "s3:GetBucketLocation"]
      resources = concat([for b in var.gold_data_buckets : "arn:aws:s3:::${b}"],
      [for b in var.gold_data_buckets : "arn:aws:s3:::${b}/*"])
    }
  }
  dynamic "statement" { # UIFPE-540: the gold masters, read to decide which
    # columns a config may export. Beacon is the message channel, and a column
    # the master marks `x-channels: []` belongs to no channel at all — the API
    # reads the master rather than keeping its own list, which is what let the
    # ppid/product_id spelling drift in the first place.
    #
    # Read-only, and scoped to the schemas/ prefix: the same bucket also holds
    # mapping artifacts and compiled SQL the panel has no business reading.
    for_each = var.gold_artifacts_bucket == "" ? [] : [1]
    content {
      sid       = "S3GoldMastersRead"
      actions   = ["s3:GetObject"]
      resources = ["arn:aws:s3:::${var.gold_artifacts_bucket}/schemas/*"]
    }
  }
  statement { # the ONLY prefix Athena may write. The API reads preview results
    # back through GetQueryResults; GetObject here is for Athena's own result
    # writer AND, since UIFPE-490, the presigned 15-minute GET on a download's
    # CSV (HeadObject for its size) — the one place the API points a browser
    # at S3. Multipart pairs: Athena switches to multipart for larger result
    # writes (grain_probe.tf carries the same).
    sid = "S3PreviewResults"
    actions = ["s3:PutObject", "s3:GetObject", "s3:AbortMultipartUpload",
    "s3:ListMultipartUploadParts"]
    resources = ["${aws_s3_bucket.data.arn}/${local.preview_prefix}/*"]
  }
  statement { # S3Configs grants ListBucket; Athena's result writer wants these too
    sid       = "S3PreviewBucket"
    actions   = ["s3:GetBucketLocation", "s3:ListBucketMultipartUploads"]
    resources = [aws_s3_bucket.data.arn]
  }
  statement { # credential vending for the LF-governed gold tables Athena reads;
    # not resource-scopable (same statement on every Athena role in the repo)
    sid       = "LakeFormationDataAccess"
    actions   = ["lakeformation:GetDataAccess"]
    resources = ["*"]
  }
  statement {
    sid = "DynamoDB"
    actions = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem",
    "dynamodb:Query", "dynamodb:Scan"]
    resources = [
      aws_dynamodb_table.registries.arn,
      aws_dynamodb_table.drafts.arn, "${aws_dynamodb_table.drafts.arn}/index/*",
      aws_dynamodb_table.catalog.arn,
      aws_dynamodb_table.runs.arn,
      aws_dynamodb_table.glue_job_config.arn, # transform sizing, read at run-start
      aws_dynamodb_table.schedules.arn,       # per-export schedule records (UIFPE-206)
    ]
  }
  statement { # UIFPE-168: the API only READS the watermark (run-start, panel).
    # Advancing it is the record_run Lambda's job alone — a monotonic
    # conditional update on the success branch. Keeping UpdateItem/PutItem
    # out of this role is what makes that single-writer invariant real, not
    # just documented; reset is the DeleteItem below.
    sid       = "DynamoDBExtractionStateRead"
    actions   = ["dynamodb:GetItem"]
    resources = [aws_dynamodb_table.extraction_state.arn]
  }
  statement { # deletable rows only: schedule records (UIFPE-206),
    # reset-watermark state (UIFPE-168), and destination records (UIFPE-337 —
    # missing registries here was the 500 on the first live delete: the
    # secret's deletion succeeded, the row's was denied). Catalog/runs/drafts
    # stay delete-free.
    sid     = "DynamoDBRowDelete"
    actions = ["dynamodb:DeleteItem"]
    resources = [aws_dynamodb_table.schedules.arn,
      aws_dynamodb_table.extraction_state.arn,
    aws_dynamodb_table.registries.arn]
  }
  statement { # UIFPE-206: the API manages one Scheduler schedule per export —
    # runtime data, so the API owns them, not Terraform (same posture as the
    # glue-job-config rows). The shared default group holds other teams'
    # schedules too, so this is scoped to OUR name prefix, never the group.
    sid = "SchedulerManage"
    actions = ["scheduler:CreateSchedule", "scheduler:UpdateSchedule",
    "scheduler:DeleteSchedule", "scheduler:GetSchedule"]
    resources = ["arn:aws:scheduler:${var.region}:${data.aws_caller_identity.current.account_id}:schedule/${local.scheduler_group}/${local.schedule_name_prefix}*"]
  }
  statement { # creating a schedule requires handing Scheduler its invoke role
    sid       = "SchedulerPassRole"
    actions   = ["iam:PassRole"]
    resources = [aws_iam_role.scheduler_invoke.arn]
    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["scheduler.amazonaws.com"]
    }
  }
  statement { # UIFPE-440: the s3 connection test runs INSIDE the deliver
    # Lambda so it executes under the deliver role — the identity partners
    # grant in their bucket policy; testing under this role would prove
    # nothing. This narrowly restores the API→deliver invoke that UIFPE-438
    # removed with the Paramiko tester.
    sid       = "InvokeDeliverConnectionTest"
    actions   = ["lambda:InvokeFunction"]
    resources = [aws_lambda_function.deliver.arn]
  }
  statement { # UIFPE-360: one Transfer Family connector per SFTP destination.
    # Runtime data like the schedules above, so the API owns them. Connector
    # ARNs carry random ids, so they cannot be fenced by name the way the
    # schedules are — the fence is the Project tag instead.
    sid = "TransferConnectorManage"
    # TagResource belongs HERE, behind aws:ResourceTag -- a re-tag is only
    # legitimate on a connector that is already ours. It was originally granted
    # only in the create statement below, which both opened an escalation and
    # (once that was closed) left no grant for re-tagging our own connectors.
    actions = ["transfer:UpdateConnector", "transfer:DeleteConnector",
      "transfer:DescribeConnector", "transfer:TagResource",
    "transfer:UntagResource", "transfer:TestConnection"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "aws:ResourceTag/Project"
      values   = [local.project_tag]
    }
  }
  statement { # the tag is mandatory on create, not best-effort: without the
    # condition below a connector could be created untagged, and every
    # statement above would then deny access to it. The API must tag at
    # creation and surface the denial rather than pass on a bare 403.
    #
    # CreateConnector ONLY. TagResource stays in the statement above, behind
    # aws:ResourceTag: granting it here, behind aws:RequestTag alone, let this
    # role re-tag a FOREIGN connector to our value and then Update/Delete it
    # (found in the UIFPE-360 deploy-policy review). Create-time tags are
    # authorised by CreateConnector + aws:RequestTag on their own.
    sid       = "TransferConnectorCreate"
    actions   = ["transfer:CreateConnector"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "aws:RequestTag/Project"
      values   = [local.project_tag]
    }
  }
  statement { # not resource-scopable in Transfer's IAM model
    sid       = "TransferConnectorList"
    actions   = ["transfer:ListConnectors"]
    resources = ["*"]
  }
  statement { # handing the connector its access role
    sid       = "TransferPassRole"
    actions   = ["iam:PassRole"]
    resources = ["arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/${local.name}-*"]
    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["transfer.amazonaws.com"]
    }
  }
  statement { # destinations flow: create/rotate partner secrets, and delete
    # them WITH their destination (UIFPE-337 — the 7-day recovery window is
    # the undo; an orphaned credential was the failure mode this prevents).
    # GetSecretValue (UIFPE-241) exists ONLY for the connection-test flow.
    # Everything stays scoped to Beacon's own destinations prefix.
    sid = "Secrets"
    actions = ["secretsmanager:CreateSecret", "secretsmanager:PutSecretValue",
      "secretsmanager:DescribeSecret", "secretsmanager:GetSecretValue",
    "secretsmanager:DeleteSecret"]
    resources = ["arn:aws:secretsmanager:${var.region}:*:secret:${local.secrets_prefix}/destinations/*"]
  }
  statement { # UIFPE-444 TEMPORARY BOOTSTRAP (owner decision 2026-09-16):
    # the Pearson Notifications OAuth credential is created and set THROUGH
    # the API (PUT /notifications/credentials — the destination-credential
    # trust model: accepted, stored in Secrets Manager, never returned),
    # because neither the fde nor the deploy role can touch Secrets Manager.
    # REVERT this statement and the endpoints once the credential exists —
    # rotation afterwards goes through the permissions asked of Reyas
    # (PutSecretValue for gip-uif-fde on the notifications path). Fenced to
    # the notifications path; no DeleteSecret.
    sid = "NotificationsSecretManage"
    actions = ["secretsmanager:CreateSecret", "secretsmanager:PutSecretValue",
    "secretsmanager:DescribeSecret", "secretsmanager:GetSecretValue"]
    resources = ["arn:aws:secretsmanager:${var.region}:*:secret:${local.secrets_prefix}/notifications/*"]
  }
  statement { # UIFPE-487: verify RELAY shell session tokens. Read-only, and
    # the code uses only the secret's token_secret key — the users map is the
    # shell's business. The secret lives under Echo's prefix because the shell
    # owns login; Beacon is a verifier, not an issuer.
    sid       = "ShellLoginSecretRead"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = ["arn:aws:secretsmanager:${var.region}:*:secret:${var.shell_login_secret_name}-*"]
  }
  statement { # Run-now: start + observe the export flow
    sid       = "StepFunctions"
    actions   = ["states:StartExecution"]
    resources = [aws_sfn_state_machine.export.arn]
  }
  statement { # UIFPE-561: list RUNNING executions so a refreshed page still
    # shows in-flight runs — the runs table only has rows for finished ones
    sid       = "StepFunctionsListRunning"
    actions   = ["states:ListExecutions"]
    resources = [aws_sfn_state_machine.export.arn]
  }
  statement {
    sid       = "StepFunctionsDescribe"
    actions   = ["states:DescribeExecution"]
    resources = ["arn:aws:states:${var.region}:${data.aws_caller_identity.current.account_id}:execution:${local.name}-export:*"]
  }
  statement { # UIFPE-203: Stop button — abort the execution and find its task
    sid       = "StepFunctionsStop"
    actions   = ["states:StopExecution", "states:GetExecutionHistory"]
    resources = ["arn:aws:states:${var.region}:${data.aws_caller_identity.current.account_id}:execution:${local.name}-export:*"]
  }
  statement { # UIFPE-203: the aborted execution does NOT stop its Spark work —
    # the Glue job run must be stopped too.
    sid       = "GlueStopJobRun"
    actions   = ["glue:BatchStopJobRun", "glue:GetJobRun"]
    resources = ["arn:aws:glue:${var.region}:${data.aws_caller_identity.current.account_id}:job/${aws_glue_job.transform.name}"]
  }
}

# The direct S3 read on the gold buckets lives on the GLUE transform role
# (glue.tf): Spark's Iceberg reader (S3FileIO) fetches the table's metadata +
# data files STRAIGHT from S3 with the job role's own credentials — Lake
# Formation credential vending is not in play, so lakeformation.tf's SELECT
# grant cannot authorize the read on its own (UIFPE-165: every gold export
# failed with s3:GetObject AccessDenied until this read existed).
variable "gold_data_buckets" {
  description = "S3 buckets holding the Gold tables' Iceberg data + metadata files (e.g. uif-echo-dev-gold). Read-only for the transform. Empty = no direct gold reads."
  type        = list(string)
  default     = []
}

# UIFPE-540. The domain deploys already publish every gold master here
# (deploy-{product,price,program,stock}-dev.yml sync schemas/gold/<entity>/),
# so beacon derives channel eligibility from them instead of restating it.
# Empty = the guard is INERT: the picker and the submit gate behave exactly as
# before. That is deliberate, so this ships without a flag day — set the
# variable to turn the guard on, per environment.
variable "gold_artifacts_bucket" {
  description = "S3 bucket holding the published Gold schema artifacts (e.g. uif-echo-dev-gold-artifacts). Read-only, schemas/ prefix only. Empty disables channel-eligibility checks."
  type        = string
  default     = ""
}
