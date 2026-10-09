# The Beacon export state machine (design decision D14: ONE parent machine
# per export pattern; diagram step 8). This is the runtime flow written down:
#
#   Init -> Transform (Glue 5.0 PySpark job, startJobRun.sync, skip_delivery)
#        -> Deliver (Lambda, S3 channel)
#        -> RecordRun (Lambda -> DynamoDB runs)      } success branch
#        -> EmitCompleted (Lambda -> EventBridge)    }
#   any failure (incl. a quality-gate breach failing the job run)
#        -> RecordFailure -> EmitFailed -> Fail
#
# The deliver Lambda sits on the success branch ONLY — a gate breach can
# never reach the destination bucket (fail-closed, D4).
#
# Input contract:
#   {"export_config_s3_path": "s3://.../export.json",
#    "job_config": {"worker_type": "G.1X", "workers": 2, "timeout_minutes": 60},
#    "trigger": "manual" | "scheduled",
#    "watermark": "" | "YYYY-MM-DD HH:MM:SS[.ffffff]"}
# The batch id is the execution name (unique per execution; a caller that
# wants a specific batch id names the execution accordingly). trigger says
# who started the batch (UIFPE-206) and lands on the run record — like
# job_config, every starter must supply it; a hand-started execution without
# it fails loudly at Init rather than recording a lie. watermark (UIFPE-168)
# is the incremental feed's stored high-water mark, read from the
# extraction-state table by whoever starts the run; "" = full extract (or an
# incremental feed's baseline). Every starter supplies it, same posture.
#
# UIFPE-720: both record states also pass the execution's own start time
# ($$.Execution.StartTime, from the context object — no starter supplies it),
# so every finished run row carries started_at beside recorded_at and the
# panel can show how long a run took.
#
# job_config is the transform's sizing, read from the beacon-<env>-glue-job-
# config table by whoever starts the run (the API today) so it can be retuned
# without a deploy. Every starter must supply it — a hand-started execution
# without it fails loudly at Transform rather than silently running the wrong
# size.

resource "aws_sfn_state_machine" "export" {
  name     = "${local.name}-export"
  role_arn = aws_iam_role.sfn.arn

  definition = jsonencode({
    Comment = "Beacon export batch: transform (Glue) -> deliver (Lambda) -> record -> event"
    StartAt = "Init"
    States = {
      Init = {
        Type = "Pass"
        Parameters = {
          "export_config_s3_path.$" = "$.export_config_s3_path"
          "batch_id.$"              = "$$.Execution.Name"
          "job_config.$"            = "$.job_config"
          "trigger.$"               = "$.trigger"
          "watermark.$"             = "$.watermark"
        }
        Next = "Transform"
      }

      # UIFPE-209: the transform runs as a managed Glue job. The
      # Fargate task definition still exists (ecs_pipeline.tf) so reverting
      # this one state is a one-commit operation during probation.
      #
      # Failure semantics are preserved: the Glue wrapper raises on a non-zero
      # engine result, which fails the job run, which fails this state, which
      # the catch below routes to RecordFailure — so a quality-gate breach
      # still never reaches Deliver (D4 fail-closed).
      Transform = {
        Type     = "Task"
        Resource = "arn:aws:states:::glue:startJobRun.sync"
        Parameters = {
          JobName = aws_glue_job.transform.name
          Arguments = {
            "--export_config_s3_path.$" = "$.export_config_s3_path"
            "--batch_id.$"              = "$.batch_id"
            # delivery is the deliver Lambda's job, never the transform's
            "--skip_delivery" = "true"
            # UIFPE-168: the incremental window's lower bound; "" = full.
            # Glue arguments are strings, so the empty value is the wrapper's
            # signal to drop the argument entirely.
            "--watermark.$" = "$.watermark"
          }
          # sizing overrides from the config table (see the input contract) —
          # the job definition's own values are only the fallback
          "WorkerType.$"      = "$.job_config.worker_type"
          "NumberOfWorkers.$" = "$.job_config.workers"
          "Timeout.$"         = "$.job_config.timeout_minutes"
        }
        ResultPath = null # keep the Init payload; the run report is in the job's log
        Next       = "Deliver"
        Catch = [{
          ErrorEquals = ["States.ALL"]
          ResultPath  = "$.error_info"
          Next        = "RecordFailure"
        }]
      }

      Deliver = {
        Type     = "Task"
        Resource = "arn:aws:states:::lambda:invoke"
        Parameters = {
          FunctionName = aws_lambda_function.deliver.arn
          Payload = {
            "export_config_s3_path.$" = "$.export_config_s3_path"
            "batch_id.$"              = "$.batch_id"
          }
        }
        ResultSelector = { "result.$" = "$.Payload" }
        ResultPath     = "$.deliver"
        Next           = "RecordRun"
        Catch = [{
          ErrorEquals = ["States.ALL"]
          ResultPath  = "$.error_info"
          Next        = "RecordFailure"
        }]
      }

      RecordRun = {
        Type     = "Task"
        Resource = "arn:aws:states:::lambda:invoke"
        Parameters = {
          FunctionName = aws_lambda_function.record_run.arn
          Payload = {
            "job_id.$"     = "$.deliver.result.job_id"
            "batch_id.$"   = "$.batch_id"
            status         = "succeeded"
            "trigger.$"    = "$.trigger"
            "started_at.$" = "$$.Execution.StartTime" # UIFPE-720
            "summary.$"    = "$.deliver.result"
            # UIFPE-168: lets record_run find the transform's report.json
            # (<canonical>/_report/) — row counts merged into the run row,
            # incremental watermark advanced. Success branch only: the
            # failure branch must never advance extraction state.
            "export_config_s3_path.$" = "$.export_config_s3_path"
          }
        }
        ResultPath = "$.record"
        Next       = "EmitCompleted"
      }

      EmitCompleted = {
        Type     = "Task"
        Resource = "arn:aws:states:::lambda:invoke"
        Parameters = {
          FunctionName = aws_lambda_function.emit_event.arn
          Payload = {
            "job_id.$"    = "$.deliver.result.job_id"
            "batch_id.$"  = "$.batch_id"
            status        = "succeeded"
            "delivered.$" = "$.deliver.result.delivered"
          }
        }
        End = true
      }

      RecordFailure = {
        Type     = "Task"
        Resource = "arn:aws:states:::lambda:invoke"
        Parameters = {
          FunctionName = aws_lambda_function.record_run.arn
          Payload = {
            # UIFPE-200: the config may not have been readable, so no job_id
            # here — the lambda derives it from the config PATH instead
            "export_config_s3_path.$" = "$.export_config_s3_path"
            "batch_id.$"              = "$.batch_id"
            status                    = "failed"
            "trigger.$"               = "$.trigger"
            "started_at.$"            = "$$.Execution.StartTime" # UIFPE-720
            "error.$"                 = "States.JsonToString($.error_info)"
          }
        }
        ResultPath = "$.record"
        Next       = "EmitFailed"
      }

      EmitFailed = {
        Type     = "Task"
        Resource = "arn:aws:states:::lambda:invoke"
        Parameters = {
          FunctionName = aws_lambda_function.emit_event.arn
          Payload = {
            "export_config_s3_path.$" = "$.export_config_s3_path"
            "batch_id.$"              = "$.batch_id"
            status                    = "failed"
            "error.$"                 = "States.JsonToString($.error_info)"
          }
        }
        Next = "BatchFailed"
      }

      BatchFailed = {
        Type  = "Fail"
        Error = "BeaconExportFailed"
        Cause = "Transform or delivery failed; see the runs table and task logs"
      }
    }
  })
}

data "aws_iam_policy_document" "sfn" {
  statement { # UIFPE-209: the transform runs as a Glue job. .sync polls the run.
    sid       = "GlueTransform"
    actions   = ["glue:StartJobRun", "glue:GetJobRun", "glue:GetJobRuns", "glue:BatchStopJobRun"]
    resources = ["arn:aws:glue:${var.region}:${data.aws_caller_identity.current.account_id}:job/${aws_glue_job.transform.name}"]
  }
  statement {
    actions = ["lambda:InvokeFunction"]
    resources = [
      aws_lambda_function.deliver.arn,
      aws_lambda_function.record_run.arn,
      aws_lambda_function.emit_event.arn,
    ]
  }
}

# uif:rename-approved UIFPE-365
resource "aws_iam_role" "sfn" {
  # `sfn` was the engine; the workload is the export.
  name = "${local.name}-export-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRole"
      Principal = { Service = "states.amazonaws.com" }
    }]
  })
  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_iam_role_policy" "sfn" {
  name   = "beacon-export"
  role   = aws_iam_role.sfn.id
  policy = data.aws_iam_policy_document.sfn.json
}
