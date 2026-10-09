locals {
  name = "${var.name_prefix}-${var.env}"

  # The stack token, bare — `uif-` is the resource-name stem, not part of the
  # stack's identity (00-core.md's vocabulary is {echo, beacon, event, harbor}).
  # This is BOTH the provider's default Project tag and the value the Transfer
  # Family IAM conditions match on (UIFPE-360), so it is declared once: a tag
  # and a condition that disagree fail as an opaque 403 at runtime.
  project_tag = "beacon"

  # UIFPE-266: Secrets Manager names follow the UIF Resource Name Lookup's
  # SLASH convention — uif/{stack}/{env}/... — unlike every hyphenated
  # resource name above. Settled BEFORE the Keeper migration (UIFPE-259):
  # Keeper Universal Secret Sync matches vault records to AWS secrets by
  # exact name, so the name had to be final first.
  secrets_prefix = "uif/beacon/${var.env}"

  # One data bucket, prefix-per-zone (same layout the demo and the beacon
  # export config contract use: configs/, code/, staging/, pickup/, rejected/)
  data_bucket = "${local.name}-data"
  ui_bucket   = "${local.name}-ui"
  # UIFPE-441: where Athena writes the wizard's preview results — one name for
  # the IAM statement, the lifecycle rule and the API's env var
  preview_prefix = "preview"

  ddb_tables = {
    registries = "${local.name}-registries" # sources/formats/channels/destinations
    drafts     = "${local.name}-drafts"     # drafts + lifecycle state
    catalog    = "${local.name}-catalog"    # immutable published versions
    runs       = "${local.name}-runs"       # batch run reports

    # Owner-set naming convention (2026-08-26): <project>-<env>-<operation>,
    # which drops the "uif-" that local.name carries. New tables follow this;
    # the four above keep their names because renaming a live table orphans
    # its data.
    glue_job_config  = "beacon-${var.env}-glue-job-config"  # transform sizing
    schedules        = "beacon-${var.env}-export-schedules" # per-export schedules (UIFPE-206)
    extraction_state = "beacon-${var.env}-extraction-state" # incremental watermarks (UIFPE-168)
  }
}
