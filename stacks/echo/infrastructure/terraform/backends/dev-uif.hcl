# State for the uif-echo-dev-* greenfield stack.
# Isolated from echo/dev/terraform.tfstate (live echo-dev-*) and from
# pearson-ingestion-framework-tfstate-* legacy keys.
#
# State lives in the shared data bucket under tfstate/ (see rollout plan Phase 5).
# Bootstrap: create uif-echo-dev-data before first init.
bucket       = "uif-echo-dev-data"
key          = "tfstate/infra-terraform.tfstate"
region       = "eu-west-1"
use_lockfile = true
encrypt      = true
