# Partial backend config: terraform init -backend-config=backends/dev.hcl
# Same state bucket beacon/product already use in this account — one
# bucket per account, one key per stack (mirrors
# stacks/beacon/infrastructure/terraform/backends/dev.hcl exactly).
bucket       = "pearson-ingestion-framework-tfstate-283556840805"
key          = "uif-apicurio/dev/terraform.tfstate"
region       = "eu-west-1"
use_lockfile = true
encrypt      = true
