# State for the uif-echo-uat-* greenfield stack.
# Isolated from echo/uat/terraform.tfstate (live echo-uat-*) and from
# pearson-ingestion-framework-tfstate-* legacy keys.
#
# Same shape as backends/dev-uif.hcl: state in this stack's own data bucket
# under tfstate/, not in the shared legacy tfstate bucket. Two stacks in one
# account are isolated by name alone, so keeping each state inside the stack it
# describes means a bucket policy or a lifecycle rule can never reach across.
#
# BOOTSTRAP ORDER, AND IT IS CIRCULAR. `terraform init` needs this bucket, and
# Terraform is what declares it (aws_s3_bucket.artifacts, from
# artifacts_bucket = "uif-echo-uat-data"). So uif-echo-uat-data must exist
# BEFORE the first init, and is then imported so the first apply does not fail
# with BucketAlreadyOwnedByYou -- exactly what
# uif_echo_import_phase5_artifacts() already does for dev's data bucket, and
# what the dev release bucket needed on 2026-09-23. Creating it belongs in
# phase 3's bootstrap-environment.sh, alongside the deploy role.
#
# Enable versioning on it at creation. The dev release bucket had to be created
# by hand with versioning on because stage-artifacts.sh fails closed without a
# version id, and the same discipline applies to a state bucket for a different
# reason: without versioning a corrupted state has no previous copy.
bucket       = "uif-echo-uat-data"
key          = "tfstate/infra-terraform.tfstate"
region       = "eu-west-1"
use_lockfile = true
encrypt      = true
