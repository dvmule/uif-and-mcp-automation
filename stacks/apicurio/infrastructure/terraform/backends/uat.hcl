# Partial backend config: terraform init -backend-config=backends/uat.hcl
#
# Same state bucket as dev, because uat is in the SAME ACCOUNT (283556840805)
# and the convention here is one bucket per account, one key per stack and
# environment. The key is what isolates uat's state from dev's -- mirroring
# stacks/echo/infrastructure/terraform/backends/uat.hcl, which uses echo/uat/
# against this same bucket.
bucket       = "pearson-ingestion-framework-tfstate-283556840805"
key          = "uif-apicurio/uat/terraform.tfstate"
region       = "eu-west-1"
use_lockfile = true
encrypt      = true
