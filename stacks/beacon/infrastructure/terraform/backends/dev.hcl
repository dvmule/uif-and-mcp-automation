# Partial backend config: terraform init -backend-config=backends/dev.hcl
# Same state bucket the product domain already uses in this account
# (echo-gold/dev/…) — one bucket per account, one key per domain stack.
bucket       = "pearson-ingestion-framework-tfstate-283556840805"
key          = "uif-beacon/dev/terraform.tfstate"
region       = "eu-west-1"
use_lockfile = true
encrypt      = true
