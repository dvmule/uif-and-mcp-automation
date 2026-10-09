# dev — ESB dev account (283556840805), eu-west-1, deployed by GitHub Actions
# via the OIDC deploy role (.github/workflows/deploy-beacon-dev.yml).
#
# Network surveyed 2026-08-18 from the account (ReadOnly):
#   VPC  gip-uif-nonprod-vpc-euw1 (10.116.48.0/21)
#   The parked Fargate transform task (UIFPE-215) runs in the private
#   self-service subnets (private-ss-az1/az2), NAT egress, no public IPs.
#   The API's ECS service and ALB were deleted at UIFPE-226's probation exit
#   (2026-08-28) — the API is a Lambda behind this stack's own API Gateway.
env    = "dev"
region = "eu-west-1"
vpc_id = "vpc-0287e62dc748ffab8" # gip-uif-nonprod-vpc-euw1

# gip-uif-nonprod-vpc-euw1-private-ss-az1 / -az2 ("ss" = self-service)
private_subnet_ids = ["subnet-059a43797031bfb59", "subnet-0084a87a82e01e8d8"]

# api_gateway_domain is deliberately unset: CloudFront wires itself to this
# stack's own API Gateway in one apply. Set it only if a platform gateway is
# ever interposed in front.

# CORS for the shared RELAY distribution serving /m/beacon/ (UIFPE-484: the
# legacy distribution d2qua7fu7qeqlx was retired with the Echo estate migration)
beacon_ui_origins = "https://d2dqf4ghal7j09.cloudfront.net"

# The deliver Lambda is back in the VPC (UIFPE-444): the Pearson
# Notifications API lives on private 10.251.x addresses, reachable only via
# the VPC's corporate gateway route — so the Secrets Manager endpoint seat
# (shared interface endpoint's SG, first used at UIFPE-241, dropped at
# UIFPE-438) is needed again for the Lambda's credential reads.
secretsmanager_endpoint_sg_id = "sg-0b45c554b51638786"

# Gold sources (surveyed 2026-08-18): the echo stack's product database.
# Table `product` (Iceberg at s3://uif-echo-dev-gold/product/) is LF-governed —
# reading it without these grants fails with "Insufficient Lake Formation
# permission(s)", verified live from this account.
# UIFPE-229: this list drives the wizard's domain picker. LF read access is
# tag-based (`uif-echo-gold-layer`, see lakeformation.tf) and already covers the
# whole gold layer — adding a domain here only makes it appear in the UI.
# UIFPE-368 added price and stock (both domain deploys are still latched:
# PRICE_DEPLOY_ENABLED / STOCK_DEPLOY_ENABLED). UIFPE-167: until a domain's
# first load lands, the API answers a configured but not-yet-created database
# with an empty table list (Glue's EntityNotFoundException is tolerated) and
# the wizard says so — not a 503.
gold_databases = ["uif_echo_dev_gold_product", "uif_echo_dev_gold_program", "uif_echo_dev_gold_price", "uif_echo_dev_gold_stock"]

# The buckets those gold databases' Iceberg files live in — Spark reads them
# directly from S3 (no LF credential vending), so the pipeline task role needs
# plain read here too (UIFPE-165).
gold_data_buckets = ["uif-echo-dev-gold"]

# The bucket the domain deploys publish every Gold master to
# (deploy-{product,price,program,stock}-dev.yml → schemas/gold/<entity>/v1/master.json).
# Setting it switches on BOTH master-derived features in the panel: the
# UIFPE-540 channel guard (surrogate columns hidden from the picker and
# refused at submit) and the UIFPE-562 attribute descriptions in the wizard.
# Owner decision 2026-09-24: live in dev.
gold_artifacts_bucket = "uif-echo-dev-gold-artifacts"
