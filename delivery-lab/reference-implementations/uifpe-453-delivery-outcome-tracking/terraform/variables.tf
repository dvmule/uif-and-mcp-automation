variable "region" {
  description = "AWS region (dev eventing stack lives in eu-west-1)"
  type        = string
  default     = "eu-west-1"
}

variable "prefix" {
  description = "Resource name prefix — poc namespace, never the real uif-event-{env} one"
  type        = string
  default     = "uif-event-poc"
}

variable "domain" {
  type    = string
  default = "product"
}

variable "detail_type" {
  description = "The single detail-type both POC subscriptions listen to"
  type        = string
  default     = "product.created.v1"
}

# One entry per POC consumer. target_type drives which target resource is
# wired AND which channel the subscription's rule filters on
# (local.target_type_channel), so pmc and pdz receive DIFFERENT copies of one
# occurrence — the production behaviour, confirmed against the live pmc/pdz
# rows on product#product.updated.v1.
#
# subscription_id is a UUID because the provisioner generates one, and it also
# composes policy_sid. consumer_principal_arn is seeded because the live rows
# carry it (both currently with status_reason CONSUMER_PRINCIPAL_INVALID — the
# principal is a placeholder, which does NOT affect EventBridge delivery, only
# the consumer's own read access).
variable "customers" {
  type = map(object({
    target_type            = string # SQS | KINESIS | API
    subscription_id        = string
    consumer_principal_arn = string
  }))
  default = {
    pmc = {
      target_type            = "SQS"
      subscription_id        = "9d9bd9f9-63af-4ce8-9cea-d594dfb0eaa3"
      consumer_principal_arn = "arn:aws:iam::111122223333:role/pmc-event-consumer"
    }
    pdz = {
      target_type            = "KINESIS"
      subscription_id        = "6b1f4d2e-8c53-4a9b-b0e7-2f9d6c81a345"
      consumer_principal_arn = "arn:aws:iam::283556840805:root"
    }
  }

  validation {
    condition = alltrue([for c in values(var.customers) :
    contains(["SQS", "KINESIS", "API"], c.target_type)])
    error_message = "target_type must be SQS, KINESIS or API."
  }
}

# ── Channel model ───────────────────────────────────────────────────────────
# WHICH CHANNELS A RUN PUBLISHES IS DATA-DRIVEN IN PRODUCTION, not fixed. The
# processor emits a copy only for channels whose hash moved AND whose published
# data schema is active in Apicurio (event_fabric_processor.py:340 `if
# mapping_channel not in row_channels: continue`, then _event_schema gates the
# payload). In dev today that is ['event'] alone — so one occurrence is ONE bus
# entry, and EventBridge fans that single entry to every matching rule.
#
# This POC briefly defaulted to emitting event+message copies, which modelled a
# fan-out the deployment does not currently produce. Default corrected to
# ['event'] on 2026-10-01; the multi-channel path is kept because it is real
# whenever a second schema goes active.
variable "publish_channels" {
  description = "Channels the publisher emits a copy for, per occurrence"
  type        = list(string)
  # ["event"] — the eventing channel. `channel` describes which SYSTEM a copy
  # is a candidate for: "event" is eventing, "message" is beacon. So the
  # eventing path publishes the event copy, and one occurrence is ONE bus
  # entry that every matching rule then receives.
  # Owner decision, 2026-10-01 (Srini): channel is informational, not routing.
  default = ["event"]

  validation {
    condition = length(var.publish_channels) > 0 && alltrue([
    for c in var.publish_channels : contains(["event", "message", "api"], c)])
    error_message = "publish_channels must be a non-empty subset of event, message, api."
  }
}

# Whether subscription rules filter on detail.channel as well as detail-type.
#
# OFF. OWNER DECISION, 2026-10-01 (Srini): routing is detail-type ONLY. The
# `channel` attribute says which system a copy is a candidate for ("event" =
# eventing, "message" = beacon); it is not a routing discriminator, so putting
# it in the rule pattern narrows delivery for no reason.
#
# The deployed rules currently DO carry it — read off the live pdz rule:
#   {"detail-type": ["product.updated.v1"], "detail": {"channel": ["message"]}}
# which is why pdz (Kinesis -> "message") matched while pmc (SQS -> "event")
# may have been matching nothing at all. Pani is changing the provisioner so
# the pattern is detail-type only; subscription_provisioner.py:313 and :431 are
# the two places that add it. This POC already models the corrected behaviour.
variable "rule_channel_filter" {
  description = "Add detail.channel to each rule's event pattern (deprecated — see note)"
  type        = bool
  default     = false
}

variable "retry_max_attempts" {
  type    = number
  default = 3
}

variable "retry_max_age_seconds" {
  type    = number
  default = 900
}

# ── Event-bus logging ───────────────────────────────────────────────────────
# INFO is the deliberate choice, not a default. The level table in
# https://docs.aws.amazon.com/eventbridge/latest/userguide/eb-event-bus-logs.html
# puts INVOCATION_SUCCESS, INVOCATION_FAILURE, INVOCATION_DLQ and RULE_MATCH at
# INFO, while the per-ATTEMPT records (INVOCATION_ATTEMPT_*) and EVENT_RECEIPT
# are TRACE-only. So INFO carries every terminal outcome this POC needs and
# none of the retry chatter.
variable "bus_log_level" {
  type    = string
  default = "INFO"

  validation {
    condition     = contains(["OFF", "ERROR", "INFO", "TRACE"], var.bus_log_level)
    error_message = "bus_log_level must be OFF, ERROR, INFO or TRACE."
  }
}

# FULL is load-bearing: it is what puts target_input on the INVOCATION_*
# records, and because the rules deliver with InputPath = $.detail, target_input
# IS the envelope — which carries the event_id already ledgered in Table 3.
# That is the whole reason this design needs NO new column and no change to
# event_fabric_processor.py. With NONE, target_input is absent and the only
# correlation left would be persisting the bus-assigned EventId.
# Trade-off recorded rather than solved: FULL puts the envelope in every
# invocation record, and target_input is the FIRST field EventBridge drops if a
# record hits the 1 MB cap. Harmless for POC-sized envelopes; revisit at
# production payload sizes.
variable "bus_log_include_detail" {
  type    = string
  default = "FULL"

  validation {
    condition     = contains(["FULL", "NONE"], var.bus_log_include_detail)
    error_message = "bus_log_include_detail must be FULL or NONE."
  }
}

# Retention is now a CORRECTNESS bound, not housekeeping. The poller's
# catch-up story is "re-read the window", so the log group's retention IS the
# maximum age of a delivery outcome that can still be recovered. 14 days is
# generous for a POC; production should set it from how long a missing
# DELIVERED row may plausibly go unnoticed.
variable "bus_log_retention_days" {
  description = "Retention on the bus log group — also the poller's catch-up horizon"
  type        = number
  default     = 14
}

# ── Delivery poller ─────────────────────────────────────────────────────────
# Replaces the CloudWatch Logs subscription filter. Measured 2026-09-30 at
# 10,000 events x 2 consumers, the push design cost 7,666 invocations for
# 20,020 records — an average batch of 2.6, because a subscription filter
# flushes eagerly rather than accumulating. A scheduled poll is a fixed number
# of invocations a day whatever the volume, and — the real reason — it can be
# re-run over a past window, which makes a missing log record recoverable
# instead of merely undetectable.

variable "poll_rate" {
  description = "Schedule expression for the delivery-poller"
  type        = string
  default     = "rate(15 minutes)"
}

# THE MOST IMPORTANT KNOB HERE. FilterLogEvents selects on a record's own
# timestamp, but a record is only visible once ingested, and vended delivery
# from the bus lags. Anything ingested after the poller has already moved its
# watermark past that timestamp is lost permanently, so the window deliberately
# trails live by this much. The poller reports the max ingestion lag it
# actually observed on every run — set this from that number, not from a guess.
variable "poll_lag_seconds" {
  description = "How far behind live the poll window trails, to absorb log-ingestion lag"
  type        = number
  default     = 600
}

variable "poll_overlap_seconds" {
  description = "Re-read this much before the watermark; writes are idempotent so overlap is free"
  type        = number
  default     = 60
}

# Bounds the work a single run can be handed, so a backlog drains window by
# window with guaranteed forward progress. A run that cannot finish its window
# commits nothing (it cannot safely checkpoint mid-window — records interleave
# across log streams) and reports that this value needs lowering.
#
# MUST BE LARGER THAN THE SCHEDULE INTERVAL, or there is no catch-up capacity
# at all. Each run advances (max_window - overlap) of new ground while the
# schedule interval of wall clock passes, so with max_window == rate the poller
# gains nothing per cycle and in fact loses `overlap` every time. It was 900
# against rate(15 minutes) until 2026-10-01, which meant a poller that fell
# behind — after an outage, or from the cold-start lookback — stayed behind
# permanently.
#
# At 3600 against rate(15 minutes) a run drains 3540s of backlog per 900s of
# wall clock, so roughly 4x real time: a one-hour outage recovers in ~20
# minutes. The ceiling on this is how much one invocation can actually finish
# inside poll_timeout_seconds; PollerIncompleteRuns alarms if it is too large.
variable "poll_max_window_seconds" {
  description = "Maximum span one poller run will process — MUST exceed the schedule interval"
  type        = number
  default     = 3600
}

# How far back a FIRST run (no watermark) reaches.
#
# Keep it <= poll_max_window_seconds. If it is larger, the very first window is
# capped and the watermark starts life behind the lag floor — a self-inflicted
# backlog before a single event has been read.
variable "poll_cold_start_lookback_seconds" {
  description = "Lookback for a run with no stored watermark"
  type        = number
  default     = 900

  validation {
    condition     = var.poll_cold_start_lookback_seconds <= var.poll_max_window_seconds
    error_message = "poll_cold_start_lookback_seconds must be <= poll_max_window_seconds, or the first window is capped and the watermark starts behind."
  }
}

variable "poll_concurrency" {
  description = "Thread-pool width for lineage reads and conditional writes"
  type        = number
  default     = 24
}

variable "poll_timeout_seconds" {
  type    = number
  default = 300
}

variable "poll_memory_mb" {
  description = "Memory drives CPU share, which matters for a threaded run — not for footprint"
  type        = number
  default     = 1024
}

locals {
  detail_type_dash = replace(var.detail_type, ".", "-")

  # VERBATIM from subscription_provisioner.py:164. A subscription's channel is
  # fixed by its target type, and every rule the provisioner creates filters on
  # it — so an SQS subscriber and a Kinesis subscriber on the SAME detail-type
  # receive DIFFERENT channel copies of one occurrence, with different envelope
  # `time` values and therefore different Table 4 sort keys.
  target_type_channel = {
    SQS     = "event"
    KINESIS = "message"
    API     = "api"
  }

  # detail_type is {domain}.{operation}.{version}, so the subscription row's
  # own `operation` and `version` attributes are derivable from it — as the
  # provisioner does (subscription_provisioner.py:233).
  detail_type_parts     = split(".", var.detail_type)
  detail_type_operation = local.detail_type_parts[length(local.detail_type_parts) - 2]
  detail_type_version   = local.detail_type_parts[length(local.detail_type_parts) - 1]

  # Verbatim replicas of the real tables (ADR-0007 Tables 3/4/5) — same keys,
  # same indexes, same projections. See dynamodb.tf for why that matters.
  t_publish       = "${var.prefix}-publish-metrics"      # Table 3 replica
  t_delivery      = "${var.prefix}-delivery-metrics"     # Table 4 replica
  t_subscriptions = "${var.prefix}-subscription-configs" # Table 5 replica

  # POC machinery, not a replica — the real stack has no watermark table.
  t_poller_state = "${var.prefix}-poller-state"

  bus_name     = "${var.prefix}-${var.domain}-bus"
  bus_log_name = "/aws/events/${var.prefix}-${var.domain}-bus"

  sqs_customers     = { for k, v in var.customers : k => v if v.target_type == "SQS" }
  kinesis_customers = { for k, v in var.customers : k => v if v.target_type == "KINESIS" }
}

variable "poll_run_history_days" {
  description = "Retention for the poller's per-run history rows in poller-state"
  type        = number
  default     = 30
}
