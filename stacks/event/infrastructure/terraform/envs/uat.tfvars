environment           = "uat"
aws_region            = "eu-west-1"
gold_bucket           = "uif-echo-uat-gold"
gold_artifacts_bucket = "uif-echo-uat-gold-artifacts"

# uif-event-uat-bus exists (echo gold root, announce.tf) -- confirmed in the
# EventBridge console of 283556840805 on 2026-09-30, so the GoldMergeCompleted
# rule is created from the first converge. See docs/uif-event-uat-rollout-plan.md.
enable_gold_merge_trigger = true
