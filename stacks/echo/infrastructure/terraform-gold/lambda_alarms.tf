# Errors alarms for the gold Lambdas that had none.
#
# UIFPE-725. Three functions in this root carried an Errors alarm -- the
# dispatcher, the serving sync and the serving API. The other six did not, and
# compile_preview being one of them is how UIFPE-723 stayed invisible for
# fifteen days: it returned Runtime.ImportModuleError on every invocation, the
# Errors metric counted every one of them, and nothing was pointed at it.
#
# THE METRIC WAS NEVER THE PROBLEM. An import failure increments AWS/Lambda
# Errors exactly as an unhandled exception does -- measured on compile_preview,
# 20 invocations produced 20 errors. So this needs no metric filter and no
# instrumentation, only an alarm per function.
#
# Why a map rather than a fileset: alerts.tf derives its per-entity alarms with
# fileset() over the domain YAMLs, which is the better pattern, but Terraform
# cannot enumerate its own resources and src/apps/lambdas/ also holds the NON
# gold root's functions, so a fileset here would over-collect. The completeness
# guarantee therefore lives in a test -- qe/gold/test_gold_lambda_alarms.py
# fails if this root declares a Lambda that no Errors alarm covers.

locals {
  # gold_id-style key -> the function it watches and what the mail should say.
  # Keys are alarm-name fragments, so changing one renames a live alarm.
  lambda_error_alarms = {
    compile_preview = {
      function = aws_lambda_function.compile_preview.function_name
      meaning = join(" ", [
        "The intake compile-preview Lambda raised, so the console's preview",
        "returned nothing to whoever asked for it. A preview is read-only and",
        "writes no artifact, so nothing is corrupted -- but the wizard is",
        "unusable while this repeats. An import error here means its zip is",
        "missing a module the compiler imports (UIFPE-723); anything else is",
        "in the traceback.",
      ])
    }
    gold_announce = {
      function = aws_lambda_function.gold_announce.function_name
      meaning = join(" ", [
        "The gold announce Lambda raised, so a GoldMergeCompleted event was",
        "not published for a run that finished. Consumers will not learn about",
        "that merge and nothing retries it -- the transform has already moved",
        "its bookmark. Check which run, then decide whether to republish.",
      ])
    }
    grain_probe = {
      function = aws_lambda_function.grain_probe.function_name
      meaning = join(" ", [
        "The grain probe Lambda raised. It is a read-only diagnostic, so",
        "nothing downstream is affected, but the console's grain answer is",
        "unavailable while this repeats.",
      ])
    }
    submit = {
      function = aws_lambda_function.submit.function_name
      meaning = join(" ", [
        "The intake submit Lambda raised, so a submission from the console was",
        "not accepted. The submitter sees a failure and can retry; nothing is",
        "half-written, because the compile and the PR are separate steps.",
      ])
    }
    ai_draft = {
      function = aws_lambda_function.ai_draft.function_name
      meaning = join(" ", [
        "The AI draft Lambda raised, so the console could not offer a drafted",
        "mapping. Drafting is optional and a human can still author the intake",
        "by hand, so this degrades the wizard rather than blocking it.",
      ])
    }
    # ALARMS ON THE ALERTER ITSELF, and the limit of that is worth stating.
    # local.alert_targets IS this function, so the alarm's own action invokes
    # the thing that just failed. That works for a PARTIAL failure -- one bad
    # event, a throttle, a failed subscriber-table read -- because the next
    # invocation succeeds and delivers the mail. It cannot work when the
    # function is wholly down, and in that case NO gold alarm delivers at all.
    # Closing that needs a second channel not routed through this Lambda (an
    # SNS topic with its own subscription); there is no SNS topic in this root
    # today and adding one is its own change. Recorded on UIFPE-725.
    gold_alert_notify = {
      function = aws_lambda_function.gold_alert_notify.function_name
      meaning = join(" ", [
        "The gold alert-notify Lambda raised, which is the function every other",
        "gold alarm invokes to send its mail. While this repeats, other alarms",
        "may be firing and telling nobody -- check the CloudWatch alarm states",
        "directly rather than trusting your inbox. If this alarm's own mail",
        "arrived, the failure was partial and delivery is working again.",
      ])
    }
  }
}

resource "aws_cloudwatch_metric_alarm" "lambda_errors" {
  for_each = local.lambda_error_alarms

  alarm_name          = "${local.name_prefix}-${replace(each.key, "_", "-")}-errors"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "Errors"
  namespace           = "AWS/Lambda"
  period              = 300
  statistic           = "Sum"
  threshold           = 0

  # notBreaching, not missing: these functions are invoked on demand, so most
  # periods have no datapoint at all. Treating that as insufficient-data would
  # leave every one of them permanently out of OK and make the set worthless.
  treat_missing_data = "notBreaching"

  dimensions = {
    FunctionName = each.value.function
  }

  alarm_description = each.value.meaning

  alarm_actions = local.alert_targets
  ok_actions    = local.alert_targets
  tags          = local.common_tags
}
