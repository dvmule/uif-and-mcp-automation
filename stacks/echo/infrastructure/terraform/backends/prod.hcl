# EDS greenfield state — PROD account 216064088366 (isolated from dev/uat bucket).
#
# Key is eds/prod/, NOT echo/prod/: prod predates the EDS -> Echo rename and
# was never migrated. echo/prod/terraform.tfstate does not exist, and pointing
# here at it yields an EMPTY state — every plan then proposes creating a
# parallel echo-prod-* stack beside the live one. Verified 2026-09-04: the only
# prod state object is eds/prod/terraform.tfstate.
bucket         = "pearson-ingestion-framework-tfstate-216064088366"
key            = "eds/prod/terraform.tfstate"
region         = "eu-west-1"
use_lockfile   = true
encrypt        = true
